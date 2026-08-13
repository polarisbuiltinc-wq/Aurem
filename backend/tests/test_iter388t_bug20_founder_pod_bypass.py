"""
Iter 388t — Bug 20 root-cause fix regression tests.

Bug 20 (Rule 5b refusal): a founder asking "Run `ls /app/backend/routers/
| head -20` on the pod" got the literal refusal:

    "I work with your repository only. I don't have access to my own
     system files or credentials."

Diagnosis in the previous session was WRONG — the refusal wasn't
LLM safety RLHF, it was our own `ORA_BOUNDARY_NO_REPO_RULE` template
in services/ora_context.py:110-142 which LITERALLY tells the LLM to
reply with that phrase.  On top of the prompt, the execute_bash
server-side gate in local_tools.py also refuses /app/* for founder
Home chats because bin_ctx is None (no debug_mode plumbing).

Deterministic fix:
  1. `is_founder_pod_chat_session(is_founder, project_id)` helper —
     True iff is_founder AND (no project OR project == "home").
  2. `ORA_FOUNDER_POD_DEBUG_RULE` template — permissive variant used
     when founder_pod_mode=True.  Does NOT contain the refusal line.
  3. `render_ora_boundary_prompt(ctx, founder_pod_mode=…)` — routes
     to the pod-debug template when the caller is a founder-on-home.
  4. `validate_founder_pod_command(cmd)` — extra safety layer that
     runs BEFORE the ora-boundary refusal when founder_pod_mode is
     set.  Blocks `;`/`&&`/`||` chaining, `..` traversal, and paths
     outside /app, /tmp, /var, /etc, /usr.  Also blocks a secret
     denylist (/.env files, /etc/shadow, /root/.ssh, /home).
  5. Orchestrator populates `local_ctx["founder_pod_mode"]`; the
     execute_bash gate honours it as an escape hatch on top of the
     existing debug_mode escape hatch.

This test file proves each layer works INDEPENDENTLY (no LLM call)
and that the exact user command from the Bug 20 report now runs
end-to-end through execute_bash and returns real stdout.
"""

from __future__ import annotations

import asyncio
import pytest

from services.ora_context import (
    ORA_FOUNDER_POD_DEBUG_RULE,
    ORA_BOUNDARY_NO_REPO_RULE,
    is_founder_pod_chat_session,
    render_ora_boundary_prompt,
    validate_founder_pod_command,
)


# ── Session detector ──────────────────────────────────────────────


class TestFounderPodChatSessionDetector:
    def test_founder_home_chat_qualifies(self):
        assert is_founder_pod_chat_session(True, "home") is True
        assert is_founder_pod_chat_session(True, "") is True
        assert is_founder_pod_chat_session(True, None) is True
        assert is_founder_pod_chat_session(True, "  HOME  ") is True

    def test_founder_project_chat_does_not_qualify(self):
        # Founder on a customer project chat MUST NOT unlock pod mode.
        assert is_founder_pod_chat_session(True, "proj_abc123") is False
        assert is_founder_pod_chat_session(True, "some-other-pid") is False

    def test_non_founder_never_qualifies(self):
        assert is_founder_pod_chat_session(False, "home") is False
        assert is_founder_pod_chat_session(False, None) is False
        assert is_founder_pod_chat_session(False, "proj_x") is False


# ── System prompt routing ─────────────────────────────────────────


class TestBoundaryPromptRouting:
    def test_founder_pod_mode_emits_permissive_template(self):
        out = render_ora_boundary_prompt(None, founder_pod_mode=True)
        assert "FOUNDER POD-DEBUG MODE" in out
        # The refusal line MUST be absent — that's what was training
        # the LLM to refuse Bug 20 prompts.
        assert "I work with your repository only" not in out
        assert "I don't have access to my own system files" not in out

    def test_home_no_project_without_founder_pod_stays_strict(self):
        out = render_ora_boundary_prompt(None, founder_pod_mode=False)
        # Original Home-chat lockdown text intact — non-founder users
        # still see the strict refusal template.
        assert "OFF-LIMITS" in out
        assert "I work with your repository only" in out

    def test_founder_pod_template_permits_execute_bash(self):
        out = render_ora_boundary_prompt(None, founder_pod_mode=True)
        # The permissive template must explicitly authorise the tool.
        assert "execute_bash" in out
        assert "/app" in out and "/tmp" in out and "/var" in out
        assert "/etc" in out and "/usr" in out

    def test_founder_pod_template_still_blocks_secrets(self):
        # Founder-mode is NOT a secret-exfil escape hatch.
        out = render_ora_boundary_prompt(None, founder_pod_mode=True)
        assert "AUREM_MASTER_KEY" in out
        assert "JWT_SECRET" in out
        assert ".env" in out


# ── Command validator ─────────────────────────────────────────────


class TestFounderPodCommandValidator:
    def test_user_bug20_exact_command_passes(self):
        # This is the EXACT command from the user's Bug 20 report.
        ok, reason = validate_founder_pod_command(
            "ls /app/backend/routers/ | head -20"
        )
        assert ok is True, f"Bug 20 exact command was refused: {reason!r}"

    def test_pipe_between_allowlisted_binaries_ok(self):
        ok, _ = validate_founder_pod_command("cat /app/backend/main.py | head -50")
        assert ok is True
        ok, _ = validate_founder_pod_command("grep -rn TODO /app/backend | wc -l")
        assert ok is True

    def test_command_chaining_semicolon_refused(self):
        ok, reason = validate_founder_pod_command("ls /app ; cat /etc/passwd")
        assert ok is False
        assert "chaining" in reason.lower()

    def test_command_chaining_and_refused(self):
        ok, reason = validate_founder_pod_command("ls /app && rm -rf /tmp/x")
        assert ok is False
        assert "chaining" in reason.lower()

    def test_command_chaining_or_refused(self):
        ok, reason = validate_founder_pod_command("ls /app || cat /root/.env")
        assert ok is False
        assert "chaining" in reason.lower()

    def test_path_traversal_refused(self):
        ok, reason = validate_founder_pod_command("cat /app/../etc/shadow")
        assert ok is False
        assert "traversal" in reason.lower()

    def test_absolute_path_outside_allowlist_refused(self):
        # /opt is not in the allowed pod paths.
        ok, reason = validate_founder_pod_command("cat /opt/private/secret.txt")
        assert ok is False
        assert "outside" in reason.lower() or "denylist" in reason.lower()

    def test_env_file_denylist(self):
        ok, reason = validate_founder_pod_command("cat /app/backend/.env")
        assert ok is False
        assert "denylist" in reason.lower()

    def test_etc_shadow_denylist(self):
        ok, reason = validate_founder_pod_command("cat /etc/shadow")
        assert ok is False
        assert "denylist" in reason.lower()

    def test_home_ssh_denylist(self):
        ok, reason = validate_founder_pod_command("ls /root/.ssh/")
        assert ok is False
        assert "denylist" in reason.lower()

    def test_relative_path_arguments_pass_through(self):
        # A repo-relative path like `backend/main.py` (no leading `/`)
        # is not the validator's concern — the caller decides where
        # `cwd` lives.  Just make sure we don't reject it.
        ok, _ = validate_founder_pod_command("cat backend/main.py")
        assert ok is True

    def test_empty_command_refused(self):
        ok, _ = validate_founder_pod_command("")
        assert ok is False
        ok, _ = validate_founder_pod_command("   ")
        assert ok is False


# ── End-to-end: execute_bash honours founder_pod_mode ────────────


@pytest.mark.asyncio
async def test_execute_bash_founder_pod_mode_runs_bug20_command(tmp_path):
    """The exact Bug 20 command should run end-to-end with
    founder_pod_mode=True and return real stdout — no refusal, no
    boundary-violation error."""
    from services.local_tools import execute_bash

    # Simulate the founder-on-home chat: is_founder=True, no bin_ctx,
    # founder_pod_mode=True (as set by orchestrator's local_ctx).
    ctx = {
        "user_id": "founder-user-id",
        "project_id": None,
        "is_founder": True,
        "bin_ctx": None,
        "founder_pod_mode": True,
    }
    args = {"command": "ls /app/backend/routers/ | head -20"}
    result = await execute_bash(ctx, args)

    assert result.get("ok") is True, (
        f"execute_bash refused a founder-pod command: {result!r}"
    )
    stdout = result.get("stdout", "")
    # Real evidence: the routers directory contains admin.py and
    # auth.py — both appear in the first 20 entries alphabetically.
    # (chat.py would need `head -30` to include; `admin.py` proves
    # the command actually ran against the real /app/backend/routers/
    # filesystem — no refusal, no boundary violation.)
    assert "admin.py" in stdout, (
        f"execute_bash returned unexpected stdout: {stdout!r}"
    )
    assert "auth.py" in stdout, (
        f"execute_bash returned unexpected stdout: {stdout!r}"
    )
    # No boundary refusal, no template phrase.
    assert "I work with your repository only" not in stdout
    assert result.get("error_class") != "ora_boundary_violation"


@pytest.mark.asyncio
async def test_execute_bash_non_founder_still_refused_on_app_path():
    """A regular (non-founder) user must NOT get the pod-mode
    escape hatch even if the ctx is spoofed with founder_pod_mode.
    The outer is_founder gate must fire first."""
    from services.local_tools import execute_bash

    ctx = {
        "user_id": "regular-user-id",
        "project_id": None,
        "is_founder": False,               # not a founder
        "bin_ctx": None,
        "founder_pod_mode": True,           # spoofed — must not help
    }
    args = {"command": "ls /app/backend/routers/"}
    result = await execute_bash(ctx, args)
    assert result.get("ok") is False
    err = (result.get("error") or "").lower()
    assert "founder" in err or "restricted" in err


@pytest.mark.asyncio
async def test_execute_bash_founder_pod_blocks_secret_command():
    """Even in founder-pod mode, secret paths like /app/backend/.env
    must refuse.  The validator's denylist catches this before the
    binary allowlist would allow `cat`."""
    from services.local_tools import execute_bash

    ctx = {
        "user_id": "founder-user-id",
        "project_id": None,
        "is_founder": True,
        "bin_ctx": None,
        "founder_pod_mode": True,
    }
    args = {"command": "cat /app/backend/.env"}
    result = await execute_bash(ctx, args)
    assert result.get("ok") is False
    assert result.get("error_class") == "founder_pod_validation"
    err = (result.get("error") or "").lower()
    assert "denylist" in err


@pytest.mark.asyncio
async def test_execute_bash_founder_pod_blocks_chained_command():
    """Command chaining is refused in founder-pod mode even when
    each individual token would be safe."""
    from services.local_tools import execute_bash

    ctx = {
        "user_id": "founder-user-id",
        "project_id": None,
        "is_founder": True,
        "bin_ctx": None,
        "founder_pod_mode": True,
    }
    args = {"command": "ls /app ; ls /tmp"}
    result = await execute_bash(ctx, args)
    assert result.get("ok") is False
    assert result.get("error_class") == "founder_pod_validation"
    assert "chaining" in (result.get("error") or "").lower()


@pytest.mark.asyncio
async def test_execute_bash_founder_project_chat_still_boundary_locked():
    """A founder chatting on a CUSTOMER project (project_id set,
    founder_pod_mode=False) MUST still hit the ora-boundary refusal
    for /app/* — the pod-debug escape hatch is only for the
    founder's own no-project workspace."""
    from services.local_tools import execute_bash

    ctx = {
        "user_id": "founder-user-id",
        "project_id": "customer-project-abc",
        "is_founder": True,
        "bin_ctx": None,                    # simulate customer chat
        "founder_pod_mode": False,           # orchestrator would set this
    }
    args = {"command": "ls /app/backend/routers/"}
    result = await execute_bash(ctx, args)
    assert result.get("ok") is False
    # Boundary violation, not the founder-pod validator.
    assert result.get("error_class") == "ora_boundary_violation"
