"""
Iter 212m-27 — Vanguard hot-path hardening.

Two production-critical bug classes closed in one refactor:

A. SLOW REPO LOADING
   - get_repo_context() in chat.py was awaited unbounded; a flaky
     GitHub or stale PAT could hang the entire chat turn for the
     90 s LLM budget.
   - list_tools() upstream HTTP fetch was unbounded too.
   - chat_sessions.find_one() for history was unbounded.

B. VANGUARD SECURITY FAILURES
   1. Cross-user repo access (IDOR): chat.py never validated that
      the caller owns the project_id it asks for. Solved by an
      explicit projects ownership check that 403s on mismatch.
   2. NoSQL injection on session_id: a caller could submit a Mongo
      operator payload (e.g. {"$gt":""}) that would short-circuit
      filters. Solved by a strict regex on session_id.
   3. Privilege escalation on session_id: chat history was looked
      up by session_id alone, so a leaked id from user A could
      reveal user B's transcript. Solved by adding `user_id` to
      the find_one filter.
   4. f-string log injection: variables containing untrusted ids
      were splatted via f-strings into log messages — switched to
      parameterised (%r / %s) logging so Vanguard's regex guard
      stops failing the static scan.

Hard timeouts applied:
   - get_repo_context()   → 12 s
   - list_tools(upstream) → 8 s
   - chat_sessions.find_one() → 3 s
"""
from __future__ import annotations

import os
import re

ROOT     = os.path.join(os.path.dirname(__file__), "..")
CHAT_PY  = os.path.join(ROOT, "routers", "chat.py")
ORCH_PY  = os.path.join(ROOT, "services", "orchestrator.py")


# ── A. chat.py — repo loading hardening ────────────────────────────

def test_chat_send_checks_project_ownership_before_repo_ctx():
    """A user must own the project before we spend a Mongo + GitHub
    round-trip loading its context. The ownership check must include
    `user_id` in the Mongo query so a foreign pid returns no doc.

    Session G · Bucket A — Iter 212m-169/170 changed the response
    contract from a hard 403 to a graceful silent-degrade (empty
    repo_ctx) that matches the ORAContext isolation pattern used
    by tools. The safety invariant — "cto_projects lookup MUST
    include the caller's user_id so a foreign project returns
    nothing" — is preserved. The old strict 403 assertion has been
    replaced with the equivalent negative check (no lookup by
    project_id ALONE without user_id).

    Also — the reference now uses `get_db().cto_projects.find_one`
    instead of the older module-level `_db` alias.
    """
    src = open(CHAT_PY).read()
    # cto_projects (the real project collection) — either alias.
    assert "cto_projects.find_one(" in src
    # Ownership check MUST include user_id AND project_id.
    assert '"user_id": user_id' in src or '"user_id": user["user_id"]' in src
    assert '"project_id":' in src
    # The buggy collection name must NOT be present.
    assert '_db.projects.find_one(' not in src
    assert 'db.projects.find_one({"project_id"' not in src
    # Safety invariant: no cto_projects lookup by project_id alone —
    # every read must scope to user_id so cross-user leaks are
    # impossible.  Grep for any find_one that mentions project_id
    # but NOT user_id in the same 200-char window.
    import re
    for m in re.finditer(r"cto_projects\.find_one\(\s*\{[^}]*\}", src):
        block = m.group(0)
        if '"project_id"' in block:
            assert '"user_id"' in block, (
                "cto_projects.find_one({project_id: ..}) MUST include "
                f"user_id in the same filter — leak risk. Block: {block}"
            )


def test_chat_send_wraps_get_repo_context_in_12s_timeout():
    """A hung GitHub call must not hold the chat turn for the full
    90 s LLM budget. 12 s ceiling with graceful empty-context degrade."""
    src = open(CHAT_PY).read()
    assert "asyncio.wait_for(\n                get_repo_context(" in src
    assert "timeout=12.0," in src
    # Must catch asyncio.TimeoutError (NOT bare except) and degrade.
    assert "except asyncio.TimeoutError:" in src
    # The degrade path must reset repo_ctx to empty so the orchestrator
    # still has *something* to splice into extra_sys.
    block = src[src.find("asyncio.wait_for(\n                get_repo_context("):]
    assert "repo_ctx = \"\"" in block[:1500]


def test_chat_send_uses_parameterised_logging_not_fstrings():
    """Vanguard's static guard fails on f-string log lines carrying
    user-controlled ids. The 212m-27 path must use %s / %r placeholders.

    Session G · Bucket A — Iter 212m-169/170 removed the
    "project ownership lookup failed" log line (that path now
    silent-degrades to empty repo_ctx instead of logging + raising).
    The 12s-exceeded log line remains and still uses the required
    parameterised format. Widened assertion set: every logger.*
    call in chat.py that mentions pid/user_id MUST use %r/%s
    placeholders, never f-strings.
    """
    src = open(CHAT_PY).read()
    # The 12s-exceeded log line (proof that at least one surviving
    # hot-path log line uses parameterised format).
    assert (
        '"get_repo_context exceeded 12s for pid=%r user=%r — "'
    ) in src
    # No logger.<level>(f"...") calls anywhere in chat.py —
    # Vanguard's static rule.  Grep for the exact anti-pattern.
    import re
    fstring_log = re.search(
        r'logger\.(warning|error|info|debug)\(\s*f["\']',
        src,
    )
    assert fstring_log is None, (
        "chat.py contains an f-string log call — Vanguard forbids "
        f"f-string logging on user-controlled ids. Offender: "
        f"{fstring_log.group(0) if fstring_log else ''}"
    )


# ── B. orchestrator.py — session + tools hardening ────────────────

def test_orchestrator_defines_session_id_regex():
    """A compiled regex constant must exist at module scope so the
    validation cost is paid once, not per turn."""
    src = open(ORCH_PY).read()
    assert "_VALID_SESSION_ID_RE = re.compile(" in src
    # Must allow alnum + dash + underscore, hard-cap 128 chars.
    assert "[A-Za-z0-9_\\-]{1,128}" in src


def test_session_id_regex_accepts_legit_ids_and_rejects_injection():
    """Functional verification — load the actual regex and try it
    against UUIDs (allowed), fallback ids (allowed), and Mongo-
    operator payloads (rejected)."""
    import importlib, sys
    if "services.orchestrator" in sys.modules:
        importlib.reload(sys.modules["services.orchestrator"])
    from services.orchestrator import _VALID_SESSION_ID_RE as r
    # crypto.randomUUID() output
    assert r.match("550e8400-e29b-41d4-a716-446655440000")
    # legacy fallback id
    assert r.match("s-l1pdv87u-abc123xy")
    # test session id from our own E2E suites
    assert r.match("iter212m23-smoke-001")
    # plain ascii
    assert r.match("abc123")
    # rejections — operator object as string, JSON, shell meta, too long
    assert not r.match('{"$gt": ""}')
    assert not r.match("a; DROP TABLE users")
    assert not r.match("sid with spaces")
    assert not r.match("a" * 129)
    assert not r.match("")
    # rejections — non-ASCII (Unicode normalization tricks)
    assert not r.match("аbс123")          # Cyrillic а/b/c lookalikes
    assert not r.match("foo\u0000bar")    # null byte


def test_chat_with_tools_validates_session_id_before_db_lookup():
    """Malformed session ids must short-circuit BEFORE Mongo is touched."""
    src = open(ORCH_PY).read()
    # The guard line.
    assert (
        "if not isinstance(session_id, str) or "
        "not _VALID_SESSION_ID_RE.match(session_id):"
    ) in src
    # On guard fail we set doc=None and log via %s / parameterised
    # placeholders — NOT f-strings carrying the raw id.
    block_start = src.find(
        "if not isinstance(session_id, str) or "
        "not _VALID_SESSION_ID_RE.match(session_id):"
    )
    block = src[block_start:block_start + 800]
    assert "logger.warning(" in block
    # No f-string log of the rejected id (Vanguard guard).
    bad = re.search(r'logger\.warning\(\s*f"', block)
    assert bad is None, "must use parameterised logging, not f-strings"


def test_chat_with_tools_filters_session_history_by_user_id():
    """The Mongo find_one MUST scope by user_id so a leaked session id
    from user A can't reveal user B's transcript."""
    src = open(ORCH_PY).read()
    assert '{"session_id": session_id, "user_id": user_id}' in src
    # Old single-key filter must be gone.
    assert 'find_one(\n                            {"session_id": session_id},' not in src


def test_chat_with_tools_caps_session_lookup_at_3s():
    """3 s ceiling on the history lookup — Mongo slow path can't
    block the whole orchestrator budget."""
    src = open(ORCH_PY).read()
    assert "asyncio.wait_for(\n                            db.chat_sessions.find_one(" in src
    assert "timeout=3.0," in src
    block_start = src.find(
        "asyncio.wait_for(\n                            db.chat_sessions.find_one("
    )
    block = src[block_start:block_start + 1500]
    assert "except asyncio.TimeoutError:" in block
    # The timeout log must be parameterised, not f-string.
    bad = re.search(r'logger\.warning\(\s*f"', block)
    assert bad is None


def test_chat_with_tools_caps_list_tools_at_8s():
    """The upstream tool catalog fetch is the slowest single hop in
    the chat hot path. Hard cap at 8 s and fall back to LOCAL_TOOL_SPECS."""
    src = open(ORCH_PY).read()
    assert "asyncio.wait_for(list_tools(jwt_token), timeout=8.0)" in src
    block_start = src.find("asyncio.wait_for(list_tools(jwt_token)")
    block = src[block_start:block_start + 1000]
    assert "except asyncio.TimeoutError:" in block
    # Must use parameterised %r / %s for the error log.
    assert 'logger.warning("list_tools upstream failed: %r", e)' in block


def test_orchestrator_no_fstring_log_with_raw_id_variables():
    """Vanguard regex guard: NO logger call with an f-string that
    contains a bare id variable in the hot path we just rewrote."""
    src = open(ORCH_PY).read()
    # Specifically the two lines from the legacy block.
    legacy_a = 'logger.warning(f"session history load failed (continuing fresh): {e!r}")'
    legacy_b = 'logger.warning(f"list_tools upstream failed: {e!r}")'
    assert legacy_a not in src
    assert legacy_b not in src
