"""
services/ora_context.py — Iter 212m-170  (ORAContext hardening — Layer 0)

ORA-specific extension of BINContext that adds the FINAL boundary layer:
ORA System files (the AUREM codebase itself under /app/, /tmp/,
/var/, /etc/, /usr/, /root/, /home/, and the string tokens auremcto /
aurem-cto / auremdev) are OFF-LIMITS to every session — even founder
sessions in normal chat mode.

Layers recap:
  Layer 0  — ORAContext  (this file)  — ORA system files off-limits
  Layer 1  — BINContext  (bin_id)     — user_id JWT boundary
  Layer 2  — Project     (pid)        — user_id+project_id ownership
  Layer 3  — Request     lifecycle    — built once, dies with request

Why a NEW type instead of adding fields to BINContext?
  • Semantic clarity — ORAContext IS-A BINContext plus the ORA
    system-boundary layer.  A test that says
    "assert isinstance(ctx, ORAContext)" reads as
    "this ctx carries the ORA-boundary hardening" — clearer than
    "assert bin_ctx.ora_boundary_active is True".
  • Backwards compat — every existing caller keeps sending `bin_ctx=…`
    into chat_with_tools / LoopEngine / tools.  Since ORAContext IS-A
    BINContext (subclass of the same frozen dataclass), all downstream
    ctx["bin_ctx"].pat / .repo_owner / .repo_name access keeps
    working unchanged.
  • Future extensions — the next hardening layer (per-repo secrets
    vault, per-repo rate limits) plugs into ORAContext without
    touching BINContext's contract.

This module never touches vault.py or JWT internals.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from fastapi import HTTPException

from services.bin_context import BINContext, build_bin_context

logger = logging.getLogger(__name__)


# ── ORA system boundary — path allowlist / denylist ────────────────

# Absolute pod-local paths that must NEVER be inspected via any tool
# that a user session drives.  Path checks are prefix + boundary
# (either "/app" exact or "/app/…") so we don't false-positive on the
# user's repo-relative paths like `backend/main.py` inside their own
# GitHub repo.
ORA_SYSTEM_PATHS: frozenset[str] = frozenset({
    "/app",
    "/app/backend",
    "/app/frontend",
    "/app/services",
    "/app/tests",
    "/tmp",
    "/var",
    "/var/log",
    "/etc",
    "/usr",
    "/root",
    "/home",
})

# Case-insensitive substrings that indicate the AUREM internal
# codebase.  Any command arg that contains one of these MUST be
# refused even for founders in normal mode (debug_mode is the
# only escape hatch, and only a founder can enable it — see below).
ORA_SYSTEM_STRINGS: frozenset[str] = frozenset({
    "auremcto",
    "aurem-cto",
    "auremdev",
    "aurem_master_key",
    "jwt_secret",
})

# Words that the LLM must NEVER mention when talking to an end-user.
# Used in the system-prompt boundary block below — pure documentation
# for the model, no runtime enforcement (models are not deterministic
# so we back this up with the catalog filter + dispatch refusal
# already installed in Iters 212m-168 / 212m-169).
ORA_SYSTEM_TERMS: frozenset[str] = frozenset({
    "parliament.py",
    "parliament",
    "loop_engine.py",
    "loop_engine",
    "orchestrator.py",
    "orchestrator",
    "vault.py",
    "vault",
    "llm.py",
    "chat.py",
    "local_tools.py",
    "/app/backend",
    "/app/frontend",
    "AUREM_MASTER_KEY",
    "JWT_SECRET",
    "OPENROUTER_API_KEY",
    "LANGFUSE",
    "auremcto",
})


# ── ORA boundary system prompt block ───────────────────────────────

ORA_BOUNDARY_SYSTEM_RULE_TEMPLATE = """\
=== ORA ABSOLUTE BOUNDARY (non-negotiable) ===

1. You are ORA.  You work EXCLUSIVELY on the user's connected
   repository: {repo_slug}  (branch: {branch}).

2. You have NO access to the AUREM server pod's local filesystem.
   Any path starting with `/app`, `/tmp`, `/var`, `/etc`, `/usr`,
   `/root`, `/home` refers to AUREM's OWN internal server and is
   OFF-LIMITS.  Never read, list, quote, or reference those paths.

3. If the user asks about ORA's internal code (parliament, loop_engine,
   orchestrator, vault, llm.py, chat.py, local_tools.py) or about
   AUREM secrets (AUREM_MASTER_KEY, JWT_SECRET, OPENROUTER_API_KEY,
   LANGFUSE keys), reply exactly:
     "I work with your repository only.  I don't have access to my
      own system files or credentials."

4. If the user asks "which repo are you working on?", answer with
   `{repo_slug}` ONLY.  Never mention `auremcto`, `/app/backend`,
   `/app/frontend`, or any AUREM-internal directory.

5. All file reads MUST go through the repo-scoped tools:
   `read_repo_file`, `read_repo_files`, `list_repo_files`,
   `search_repo`, `semantic_search_repo`, `get_repo_structure`,
   `get_repo_info`, `write_repo_file`, `get_commit_diff`.
   Every one of these is scoped to `{repo_slug}` — never claim to have
   run a local shell command against `/app/*` or any pod path.

6. Never invent shell output, never claim to have inspected a
   `/app/…` path, never quote from a file whose path begins with `/`.

=== END ORA ABSOLUTE BOUNDARY ===

"""

# When Home casual chat (no project connected), we can't say
# "you work on {repo_slug}" but we can still enforce the pod-filesystem
# lockdown.
ORA_BOUNDARY_NO_REPO_RULE = """\
=== ORA ABSOLUTE BOUNDARY (non-negotiable — no project connected) ===

1. You are ORA.  No GitHub repository is currently connected to
   this chat.  Direct the user to connect one via Settings → GitHub
   or the "Add repository" option in the sidebar.

2. You have NO access to the AUREM server pod's local filesystem.
   Paths under `/app`, `/tmp`, `/var`, `/etc`, `/usr`, `/root`,
   `/home` refer to AUREM's OWN internal server and are OFF-LIMITS.
   Never read, list, quote, or reference them.

3. If the user asks about ORA's internal code (parliament, loop_engine,
   orchestrator, vault, llm.py, chat.py) or about AUREM secrets
   (AUREM_MASTER_KEY, JWT_SECRET, OPENROUTER_API_KEY, LANGFUSE keys),
   reply exactly:
     "I work with your repository only.  I don't have access to my
      own system files or credentials."

4. Never invent shell output, never claim to have inspected a
   `/app/…` path, never quote from a file whose path begins with `/`.

=== END ORA ABSOLUTE BOUNDARY ===

"""


# Iter 388t — Bug 20 root-cause fix.
#
# The Home-chat boundary above ships the LLM the LITERAL refusal
# phrase "I work with your repository only.  I don't have access to
# my own system files or credentials."  When a founder asks
# "Run `ls /app/backend/routers/ | head -20` on the pod" the model
# obediently returns that exact refusal — NOT because of RLHF safety
# training but because our own system prompt told it to.
#
# This template is the founder-only counterpart.  It is emitted ONLY
# when the caller is is_founder=True AND no project is attached
# (Home chat, project_id in {None, "", "home"}).  A founder on a
# CUSTOMER project chat still sees the strict rule above so an
# accidental "cat /app/backend/main.py" mid customer-facing session
# still refuses — the escape hatch is ONLY for the founder's own
# no-project workspace, not for third-party project sessions.
#
# The template:
#   • Permits execute_bash on the whitelisted read-only pod paths
#     (/app, /tmp, /var, /var/log, /etc, /usr) via the read-only
#     command allowlist.
#   • Does NOT contain the "reply exactly with 'I work with your
#     repository only'" line — that is what was causing the founder
#     refusal loop.
#   • Still forbids inventing shell output, still requires the tool
#     to actually be called before quoting any path.
ORA_FOUNDER_POD_DEBUG_RULE = """\
=== FOUNDER POD-DEBUG MODE (this session only) ===

1. You are ORA speaking to an AUREM founder in their internal
   workspace (no customer project attached).  This session is
   permitted to inspect the AUREM server pod's local filesystem
   for legitimate development work.

2. When the founder asks you to run a terminal command on the pod
   (phrasings like "run … on the pod", "ls /app/…", "cat /app/…",
   "show me /var/log/…"), you MUST call the `execute_bash` tool
   with the exact command.  Do NOT reply with a refusal template;
   do NOT say "I only work with your repository" in this session.

3. `execute_bash` is READ-ONLY (allowlisted binaries: cat, head,
   tail, grep, find, ls, wc, sed, awk, echo, pwd, stat, tree, file,
   which, whereis, basename, dirname, sort, uniq, cut, tr).  It
   accepts commands scoped to `/app`, `/tmp`, `/var`, `/var/log`,
   `/etc`, `/usr`.  Command chaining with `;` or `&&` is refused
   server-side; pipes (`|`) between allowlisted binaries are fine.

4. Return the tool's stdout VERBATIM inside a fenced code block.
   Never invent shell output; if the tool refuses, quote the exact
   refusal so the founder can adjust the command.

5. This mode DOES NOT unlock secret exfiltration.  AUREM_MASTER_KEY,
   JWT_SECRET, OPENROUTER_API_KEY, LANGFUSE keys, and any `.env`
   file contents must NEVER be printed, even by a founder-issued
   command.  If a command would surface a secret token, refuse and
   explain which token would leak.

=== END FOUNDER POD-DEBUG MODE ===

"""


# ── ORAContext dataclass ───────────────────────────────────────────


@dataclass(frozen=True)
class ORAContext(BINContext):
    """Extended, request-scoped context that carries the ORA system-
    boundary flag on top of the BINContext (user + project + PAT).

    Additional fields:
      ora_boundary_active — always True by default.  A founder can
                            set it to False (via `debug_mode`) to
                            unlock /app/* inspection for AUREM dev
                            work.  A NON-founder attempting to set
                            this to False is refused at build time
                            (see build_ora_context).
      debug_mode          — Founder-only escape hatch.  When True,
                            execute_bash allows /app/* paths.  When
                            False (default), execute_bash refuses
                            /app/* even for founders.
    """
    ora_boundary_active: bool = True
    debug_mode: bool = False

    @property
    def repo_full_name(self) -> str:
        """Convenience — 'owner/repo' for log lines and system prompts."""
        return f"{self.repo_owner}/{self.repo_name}"


# ── Path / string boundary checks ──────────────────────────────────


def path_hits_ora_boundary(candidate: str) -> Optional[str]:
    """Return the first ORA_SYSTEM_PATHS entry the candidate matches,
    else None.  Match is prefix + boundary — `/app/backend` matches
    `/app`, `/app/backend`, `/app/backend/main.py`, but NOT `backend/`.

    We also scan for ORA_SYSTEM_STRINGS (case-insensitive substring —
    catches `cat /root/.env`, `grep AUREM_MASTER_KEY`, etc.).
    """
    if not candidate or not isinstance(candidate, str):
        return None
    cand = candidate.strip()
    if not cand:
        return None

    # 1) Path prefix / exact match check.
    for p in ORA_SYSTEM_PATHS:
        # Exact match, or path with a trailing "/" or the whole thing
        # is a token bounded by shell separators (whitespace / | & ;).
        if cand == p or cand.startswith(p + "/"):
            return p
        # Also scan the candidate as a shell arg blob — `cat /app/x`
        # contains the token `/app/x` after tokenisation.  Use a
        # simple regex that finds the path as a word boundary.
        if re.search(rf"(?:^|[\s|&;<>])({re.escape(p)})(?:[\s/|&;<>]|$)", cand):
            return p

    # 2) String allowlist — case-insensitive substring.
    low = cand.lower()
    for s in ORA_SYSTEM_STRINGS:
        if s in low:
            return s

    return None


# ── Factory ────────────────────────────────────────────────────────


async def build_ora_context(
    user_id:      str,
    project_id:   Optional[str],
    db,
    is_founder:   bool = False,
    debug_mode:   bool = False,
) -> ORAContext:
    """Factory — wraps `build_bin_context` and adds the ORA system-
    boundary flag.  Same 400/403 semantics as the parent (missing
    project → 400, wrong-user / bad-PAT → 403).

    debug_mode:
      • Founder-only.  Non-founder callers pass debug_mode=True →
        we silently coerce back to False (never trust caller-supplied
        privileges).
      • When True AND is_founder=True → ora_boundary_active=False
        so execute_bash allows /app/* paths (founder ORA-on-AUREM
        development mode).  Even in this mode the LLM still sees
        the boundary rule in its system prompt — the model is asked
        to opt back in explicitly by the founder.
    """
    # Reuse the vetted BINContext factory — ALL its checks (ownership,
    # decrypt, missing repo cols) fire here first.
    bc = await build_bin_context(
        user_id=user_id,
        project_id=project_id,
        db=db,
        is_founder=is_founder,
    )

    # Coerce debug_mode: only founders may enable it.  Anyone else
    # sending debug_mode=True gets it silently stripped — do NOT
    # raise, that would leak the existence of the flag to attackers.
    eff_debug = bool(debug_mode) and bool(is_founder)
    boundary_active = not eff_debug   # False only in founder debug mode.

    return ORAContext(
        bin_id=bc.bin_id,
        pid=bc.pid,
        repo_owner=bc.repo_owner,
        repo_name=bc.repo_name,
        branch=bc.branch,
        pat=bc.pat,
        is_founder=bc.is_founder,
        ora_boundary_active=boundary_active,
        debug_mode=eff_debug,
    )


async def build_ora_context_optional(
    user_id:    str,
    project_id: Optional[str],
    db,
    is_founder: bool = False,
    debug_mode: bool = False,
) -> Optional[ORAContext]:
    """Same as `build_ora_context` but returns None for blank/"home"
    project_id.  Used at chat entry points that also serve Home
    casual chat.  All other failure modes (wrong user, decrypt fail)
    still raise — this helper only softens "no project selected"."""
    pid_clean = (project_id or "").strip()
    if not pid_clean or pid_clean == "home":
        return None
    return await build_ora_context(
        user_id, project_id, db, is_founder, debug_mode
    )


def render_ora_boundary_prompt(
    ctx: Optional[ORAContext],
    *,
    founder_pod_mode: bool = False,
) -> str:
    """Return the ORA boundary system-prompt block to prepend for
    THIS session.  When ctx has a repo, we inject the repo slug
    into the boundary; when there's no ctx (Home chat), we emit the
    no-repo variant.

    Iter 388t — Bug 20 fix.  When `founder_pod_mode=True` (caller
    verified the session is is_founder=True AND has no project
    attached), emit the FOUNDER_POD_DEBUG_RULE instead of the
    Home-chat lockdown.  This variant does not contain the "I only
    work with your repository" refusal line, so a founder inspection
    prompt no longer trains the LLM to refuse.

    Founder + project-attached (customer chat) still gets the
    strict repo-scoped rule — no debug mode there.  Non-founder
    callers who somehow set founder_pod_mode=True are ignored (the
    execute_bash server-side gate still refuses them).

    Founders in debug_mode still see the boundary block — the model
    is told to reset its default, and the founder can override via
    a specific "run bash on /app" phrasing that is then permitted
    at dispatch time.  We don't remove the prompt block; we just
    lift the execute_bash gate.
    """
    if founder_pod_mode and ctx is None:
        return ORA_FOUNDER_POD_DEBUG_RULE
    if ctx is None:
        return ORA_BOUNDARY_NO_REPO_RULE
    return ORA_BOUNDARY_SYSTEM_RULE_TEMPLATE.format(
        repo_slug=ctx.repo_full_name,
        branch=ctx.branch or "main",
    )


# ── Founder pod-debug mode helpers ─────────────────────────────────
#
# Iter 388t — Bug 20 fix.  Two-part determinstic bypass:
#
#   1. `is_founder_pod_chat_session` — decides whether the caller
#      qualifies for founder-pod-debug (is_founder AND no project).
#      Called from chat.py at build time and from orchestrator.py
#      before rendering the boundary block.
#
#   2. `validate_founder_pod_command` — extra safety validator that
#      runs BEFORE execute_bash's ora-boundary check when founder-
#      pod-mode is active.  Blocks command chaining (`;`, `&&`,
#      `||`), path traversal (`..`), and confines path arguments to
#      the documented pod paths (/app, /tmp, /var, /etc, /usr).
#      Returns (ok: bool, reason: str).

# Documented pod paths a founder may inspect via execute_bash while
# in founder-pod-debug mode.  Any absolute path outside this set is
# refused even for founders — matches the whitelist in the system
# prompt above so the LLM and the dispatch layer agree.
FOUNDER_POD_ALLOWED_PATHS: tuple[str, ...] = (
    "/app", "/tmp", "/var", "/var/log", "/etc", "/usr",
)

# Secret paths that must NEVER be surfaced even for founders.  Any
# absolute path matching (prefix or equality) is refused.  This is
# a defence-in-depth on top of the general secret-string filter in
# path_hits_ora_boundary(); we duplicate it explicitly so the
# founder-pod validator is self-contained and readable.
FOUNDER_POD_BLOCKED_PATHS: tuple[str, ...] = (
    "/app/backend/.env",
    "/app/frontend/.env",
    "/app/.env",
    "/root/.env",
    "/etc/shadow",
    "/etc/passwd-",
    "/root/.ssh",
    "/home",
)


def is_founder_pod_chat_session(
    is_founder: bool,
    project_id: Optional[str],
) -> bool:
    """True iff the caller is a founder AND has no project attached.

    A founder chatting ABOUT a customer project still gets the
    strict boundary — we only unlock the pod-debug template on the
    founder's own no-project workspace (Home chat).
    """
    if not bool(is_founder):
        return False
    pid = (project_id or "").strip().lower()
    return pid in ("", "home")


def validate_founder_pod_command(cmd: str) -> tuple[bool, str]:
    """Extra safety check for founder-pod-mode execute_bash.  Runs
    BEFORE the existing binary allowlist so the founder can't
    accidentally issue a chained command that pipes an allowlisted
    binary into a NON-allowlisted one via `;` or `&&`, and can't
    traverse out of the documented pod paths via `../..`.

    The existing binary allowlist in local_tools.py already gates
    the FIRST token; this validator adds three extra rules:

      • No `;`, `&&`, `||`, `|`, backtick, or `$(` chaining /
        substitution (SEC-001 2026-01-22: pipes used to be allowed
        here but that was inconsistent with the downstream
        execute_bash() gate, which now hard-rejects all shell
        metacharacters and runs via argv-exec with no shell).
      • No `..` path traversal in any argument.
      • Every absolute path argument (starts with `/`) must be
        under FOUNDER_POD_ALLOWED_PATHS and NOT match any
        FOUNDER_POD_BLOCKED_PATHS entry.

    Returns (True, "") on pass and (False, reason) on refuse.
    """
    if not cmd or not isinstance(cmd, str):
        return False, "empty command"
    s = cmd.strip()
    if not s:
        return False, "empty command"

    # Rule 1: no command chaining or substitution.  We look for the
    # raw operators OUTSIDE quoted regions — a naive scan is enough
    # because the allowlisted binaries never legitimately need these
    # in their arguments; if they did the founder should invoke them
    # separately.
    # SEC-001 fix (2026-01-22): pipes are NO LONGER allowed here either
    # — the downstream execute_bash() gate in local_tools.py now hard
    # -rejects any shell metacharacter and runs via argv-exec (no
    # shell), so allowing `|` past THIS validator only to be blocked
    # later was inconsistent and this validator alone was previously
    # the only line of defence for the founder-pod path. Also blocks
    # backtick / `$(` command substitution, which was never checked.
    if any(op in s for op in (";", "&&", "||", "|", "`", "$(")):
        return False, "command chaining/substitution (;, &&, ||, |, `, $()) is refused in founder-pod mode"

    # Rule 2: no path traversal.  Any `..` token outside quotes is
    # refused.  Again a scan is sufficient — allowlisted binaries
    # don't need `..` in real founder workflows on pod paths.
    if ".." in s:
        return False, "path traversal (..) is refused in founder-pod mode"

    # Rule 3: absolute path arguments must live under an allowed
    # prefix and outside the secret-file denylist.  We use shlex to
    # tokenise so a quoted path with spaces still parses.
    import shlex
    try:
        tokens = shlex.split(s, posix=True)
    except ValueError as e:
        return False, f"shell parse error: {e}"

    for tok in tokens:
        if not tok.startswith("/"):
            continue
        # Explicit denylist first — blocks even nested reads.
        for bad in FOUNDER_POD_BLOCKED_PATHS:
            if tok == bad or tok.startswith(bad + "/"):
                return False, f"path `{tok}` is on the founder-pod denylist"
        # Allowlist match.
        ok = False
        for good in FOUNDER_POD_ALLOWED_PATHS:
            if tok == good or tok.startswith(good + "/"):
                ok = True
                break
        if not ok:
            return False, (
                f"absolute path `{tok}` is outside the allowed pod "
                f"paths ({', '.join(FOUNDER_POD_ALLOWED_PATHS)})"
            )

    return True, ""


__all__ = [
    "ORAContext",
    "build_ora_context",
    "build_ora_context_optional",
    "path_hits_ora_boundary",
    "render_ora_boundary_prompt",
    "is_founder_pod_chat_session",
    "validate_founder_pod_command",
    "FOUNDER_POD_ALLOWED_PATHS",
    "FOUNDER_POD_BLOCKED_PATHS",
    "ORA_SYSTEM_PATHS",
    "ORA_SYSTEM_STRINGS",
    "ORA_SYSTEM_TERMS",
    "ORA_BOUNDARY_SYSTEM_RULE_TEMPLATE",
    "ORA_BOUNDARY_NO_REPO_RULE",
    "ORA_FOUNDER_POD_DEBUG_RULE",
]
