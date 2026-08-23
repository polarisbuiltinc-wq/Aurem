"""iter 2026-08-25 (post-378) — Verify:
1. NEW Vanguard/E2B auto-fix-and-reverify loop in cto_projects.py
   `_run_task_via_api` (source-lock, code-shape asserts).
2. Raw-error leak fix #2 in chat.py Mode D exception handler
   (source-lock: `classify_error(_de)['user_message']`, no raw f-string).
3. NEW read-only admin endpoints in admin_users.py:
   GET /admin/users/{user_id}/chat-sessions
   GET /admin/chat-sessions/{session_id}
   — auth guard, shape, 404 for unknown session.

Additionally: parity check — the git-subprocess path
`_run_task_with_git` is the runtime path when git is installed
(and it IS installed in this preview). This test file flags whether
the auto-fix loop exists in that path too.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import httpx
import pytest

BACKEND_LOCAL = "http://localhost:8001/api/aurem-dev"
BACKEND_PUBLIC = (os.environ.get("REACT_APP_BACKEND_URL")
                  or "https://bin-context-pat.preview.emergentagent.com"
                  ).rstrip("/") + "/api/aurem-dev"

CTO_ROUTER = Path("/app/backend/routers/cto_projects.py")
CHAT_ROUTER = Path("/app/backend/routers/chat.py")
ADMIN_USERS_ROUTER = Path("/app/backend/routers/admin_users.py")


# ---------------------------------------------------------------------------
# Section 1 — Vanguard auto-fix source-lock (in _run_task_via_api)
# ---------------------------------------------------------------------------
def _extract_fn(src: str, header: str) -> str:
    """Return the source of an async def function starting with `header`."""
    m = re.search(rf"^async def {re.escape(header)}\(", src, re.MULTILINE)
    assert m, f"function `{header}` not found"
    start = m.start()
    # find next top-level `async def` or `def`
    nxt = re.search(r"^(async def |def )", src[start + 1:], re.MULTILINE)
    end = (start + 1 + nxt.start()) if nxt else len(src)
    return src[start:end]


def test_vanguard_autofix_present_in_run_task_via_api():
    src = CTO_ROUTER.read_text()
    fn = _extract_fn(src, "_run_task_via_api")

    # (1) Trigger step logged when verify_result['pass'] is False
    assert "Vanguard/E2B blocked the commit — attempting one" in fn
    assert 'automatic fix before failing' in fn

    # (2) Findings nudge builds file:line/severity/rule/message list
    assert re.search(r"f\.get\(\s*['\"]file['\"]", fn)
    assert re.search(r"f\.get\(\s*['\"]line['\"]", fn)
    assert re.search(r"f\.get\(\s*['\"]severity['\"]", fn)
    assert re.search(r"f\.get\(\s*['\"]rule['\"]", fn)
    assert re.search(r"f\.get\(\s*['\"]message['\"]", fn)
    # E2B stderr in nudge
    assert "E2B smoke-import failed" in fn

    # (3) LLM called ONE more time with the nudge
    assert 'what="Vanguard auto-fix retry"' in fn
    # nudge string is passed into the LLM messages
    assert "_vg_nudge" in fn

    # (4) Merges corrected files and re-runs verify_patch
    assert "edits.update(_vg_edits)" in fn
    # two verify_patch invocations inside the function
    assert fn.count("await verify_patch(") >= 2, (
        "expected verify_patch to be called twice (initial + auto-fix re-verify)"
    )

    # (5a) success branch — log + fall through (no early return, no 'failed')
    assert "Auto-fix resolved the blocked finding(s)" in fn
    assert "re-verified clean, proceeding to commit." in fn
    # After the success log we set verify_result = verify_result_2 so
    # downstream commit/ship flow proceeds normally.
    assert "verify_result = verify_result_2" in fn

    # (5b) failure branch — translated error text with the required phrase
    assert "auto-fix attempted, still blocked" in fn
    # Failure uses _set_status with status='failed' — never raw log
    assert 'status="failed"' in fn


def test_vanguard_autofix_uses_classify_error_upstream_regression():
    """Regression: the outer except in _run_task_via_api still routes
    through classify_error / error_classifier (iter 378 fix) and never
    _log's the raw exception."""
    src = CTO_ROUTER.read_text()
    fn = _extract_fn(src, "_run_task_via_api")
    # NO raw f"❌ {e}" or f"Failed — {e}" that would leak str(exception)
    assert not re.search(r'_log\([^)]*f["\']❌\s*\{(e|_e)\}', fn), (
        "leak: _log with raw exception f-string found in _run_task_via_api"
    )
    # Sanitizer wired
    assert "classify_error" in fn


# ---------------------------------------------------------------------------
# Section 2 — Parity gap check: git-subprocess path
# ---------------------------------------------------------------------------
def test_run_task_with_git_verify_patch_parity():
    """This is the RUNTIME path on any host with git installed (which
    is true in this preview). Flags whether Vanguard verify + the new
    auto-fix loop exist on this path too. If not, the customer-facing
    fix does NOT fire on the real code path."""
    src = CTO_ROUTER.read_text()
    fn = _extract_fn(src, "_run_task_with_git")
    has_verify = "verify_patch(" in fn
    has_autofix = "attempting one" in fn and "Vanguard auto-fix retry" in fn
    # Report as an assertion failure so main agent sees the parity gap.
    assert has_verify, (
        "PARITY GAP: `_run_task_with_git` (the runtime worker when git "
        "is installed) does NOT call verify_patch(). The Vanguard "
        "verify agent + the new auto-fix loop only exist on the "
        "API-only path (`_run_task_via_api`), which is NOT taken when "
        "`_GIT_AVAILABLE` is True. This means the customer-facing "
        "auto-fix feature does NOT fire on real production runs "
        "where git is available."
    )
    assert has_autofix, (
        "PARITY GAP: `_run_task_with_git` has verify_patch but is "
        "missing the auto-fix retry branch."
    )


def test_run_task_with_git_autofix_full_parity_shape():
    """iter post-379 — deep parity assertions on _run_task_with_git:
    findings-nudge, retry label, edits.update, 2nd verify_patch,
    success-sets-verify_result=verify_result_2, still-blocked phrasing,
    AND early-return BEFORE the git add/commit lines."""
    src = CTO_ROUTER.read_text()
    fn = _extract_fn(src, "_run_task_with_git")

    # (1) Trigger log identical to API-path phrasing
    assert "Vanguard/E2B blocked the commit — attempting one" in fn
    assert "automatic fix before failing" in fn

    # (2) Findings-nudge builds file:line/severity/rule/message list
    for key in ("file", "line", "severity", "rule", "message"):
        assert re.search(rf"f\.get\(\s*['\"]{key}['\"]", fn), f"missing f.get('{key}') in git path"
    assert "E2B smoke-import failed" in fn

    # (3) One retry LLM call with the exact `what=` label
    assert 'what="Vanguard auto-fix retry"' in fn
    assert "_vg_nudge" in fn

    # (4) Merges edits + re-verifies exactly once more
    assert "edits.update(_vg_edits)" in fn
    assert fn.count("await verify_patch(") >= 2, (
        "expected verify_patch to be called twice in git path (initial + auto-fix re-verify)"
    )

    # (5a) success branch reassigns verify_result to the post-fix result
    assert "Auto-fix resolved the blocked finding(s)" in fn
    assert "re-verified clean, proceeding to commit." in fn
    assert "verify_result = verify_result_2" in fn

    # (5b) still-blocked branch: exact phrasing + failed status + early return
    assert "auto-fix attempted, still blocked" in fn
    assert 'status="failed"' in fn

    # (6) CRITICAL: the still-blocked branch must `return` BEFORE the
    # git add/commit/push section — otherwise a blocked patch would still
    # be committed and pushed.
    fail_idx = fn.find("auto-fix attempted, still blocked")
    assert fail_idx > 0
    return_after_fail = fn.find("return", fail_idx)
    git_add_idx = fn.find('"git", "add"', fail_idx)
    git_commit_idx = fn.find('"git", "commit"', fail_idx)
    assert return_after_fail > 0
    assert git_add_idx > return_after_fail, (
        "PARITY BUG: still-blocked branch does NOT return before `git add` — "
        "a blocked patch could still be committed on the git path."
    )
    assert git_commit_idx > return_after_fail

    # (7) The auto-fix block itself is inserted BEFORE the file-write loop.
    # The 'files to update' log must appear before the verify block, and the
    # actual `fp.write_text(content)` must appear AFTER the trigger phrase.
    files_to_update_idx = fn.find("files to update")
    trigger_idx = fn.find("Vanguard/E2B blocked the commit")
    write_idx = fn.find("fp.write_text(content)")
    assert 0 < files_to_update_idx < trigger_idx < write_idx, (
        "ordering bug: verify+auto-fix block is not between 'files to update' "
        "log and the file-write loop"
    )


def test_shape_vanguard_findings_uses_real_findings_on_git_path():
    """Downstream reporting fix: at task completion on the git path,
    shape_vanguard_findings(...) must be called with the REAL findings
    from verify_result (not a hardcoded empty list) and its status
    must reflect the real pass/fail state."""
    src = CTO_ROUTER.read_text()
    fn = _extract_fn(src, "_run_task_with_git")

    # There must be NO hardcoded `shape_vanguard_findings([], status="fixed")`
    assert not re.search(
        r"shape_vanguard_findings\(\s*\[\s*\]\s*,\s*status\s*=\s*['\"]fixed['\"]",
        fn,
    ), "regression: shape_vanguard_findings still called with hardcoded [] on git path"

    # It must be called with verify_result.get('findings', ...) somewhere
    assert re.search(
        r"shape_vanguard_findings\(\s*\(?\s*verify_result\.get\(\s*['\"]findings['\"]",
        fn,
    ), "shape_vanguard_findings does not read real findings from verify_result"

    # And the status arg must be conditional on verify_result.get('pass', ...)
    # Locate the call and grab a wider window since the args span nested parens.
    call_start = fn.find("shape_vanguard_findings(")
    assert call_start > 0, "shape_vanguard_findings call not found on git path"
    call_window = fn[call_start:call_start + 800]
    assert "blocked" in call_window and "fixed" in call_window, (
        "status arg must ternary between 'blocked' and 'fixed' based on real pass"
    )
    assert "verify_result" in call_window and "pass" in call_window


def test_iter378_regressions_intact():
    """Quick spot check: iter 378 fixes are untouched."""
    # openrouter_providers dict-guard (lives under services/llm/)
    op = Path("/app/backend/services/llm/openrouter_providers.py").read_text()
    assert "isinstance" in op, "openrouter_providers.py dict-guard removed"
    # error_classifier module still present
    ec = Path("/app/backend/services/error_classifier.py")
    assert ec.exists(), "error_classifier.py removed"
    assert "classify_error" in ec.read_text()
    # failure_signature repeat-detector still present
    fs = Path("/app/backend/services/failure_signature.py")
    assert fs.exists(), "failure_signature.py removed"


def test_git_available_in_this_environment():
    """Sanity: which worker path will actually execute here?"""
    from routers.cto_projects import _GIT_AVAILABLE
    # If True → _run_task_with_git runs at runtime → auto-fix loop
    # (which currently only lives in _run_task_via_api) will NOT fire.
    assert _GIT_AVAILABLE is True or _GIT_AVAILABLE is False  # informational
    print(f"[env] _GIT_AVAILABLE = {_GIT_AVAILABLE}")


# ---------------------------------------------------------------------------
# Section 3 — Chat.py Mode D leak fix #2 (source-lock)
# ---------------------------------------------------------------------------
def test_chat_mode_d_exception_uses_classify_error():
    src = CHAT_ROUTER.read_text()
    # find the run_debug_session try/except block
    idx = src.find("d_result = await run_debug_session(")
    assert idx > 0, "run_debug_session call not found"
    window = src[idx:idx + 2000]
    # New pattern must use classify_error(_de)['user_message']
    assert "classify_error(_de)" in window, (
        "leak: chat.py Mode D exception handler does not sanitize via "
        "classify_error(_de)"
    )
    # Old raw-embed pattern must be gone
    assert "Couldn't diagnose: {_de}" not in window, (
        "leak: raw f-string 'Couldn\\'t diagnose: {_de}' still present"
    )


# ---------------------------------------------------------------------------
# Section 4 — Admin chat-sessions endpoints (LIVE HTTP)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def admin_token() -> str:
    r = httpx.post(f"{BACKEND_LOCAL}/auth/login", json={
        "email": "test@aurem.dev", "password": "AuremTest2026!"}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.json().get("token")
    assert tok
    return tok


def test_admin_list_chat_sessions_requires_auth():
    r = httpx.get(f"{BACKEND_LOCAL}/admin/users/test_admin_001/chat-sessions",
                  timeout=15)
    assert r.status_code in (401, 403), (
        f"expected 401/403 without auth, got {r.status_code}: {r.text[:200]}"
    )


def test_admin_get_chat_session_requires_auth():
    r = httpx.get(f"{BACKEND_LOCAL}/admin/chat-sessions/anything",
                  timeout=15)
    assert r.status_code in (401, 403)


def test_admin_list_chat_sessions_shape(admin_token: str):
    r = httpx.get(
        f"{BACKEND_LOCAL}/admin/users/test_admin_001/chat-sessions",
        headers={"Authorization": f"Bearer {admin_token}"}, timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    sessions = body.get("sessions")
    assert isinstance(sessions, list)
    # up to 50, sorted newest-first
    assert len(sessions) <= 50
    for s in sessions:
        # required projection fields (some may be None)
        assert "session_id" in s
        assert "turn_count" in s
        assert isinstance(s["turn_count"], int)
        # `turns` array must NOT be leaked in list view
        assert "turns" not in s, "list endpoint must not leak `turns` array"
        # _id must be stripped
        assert "_id" not in s


def test_admin_get_chat_session_404_for_unknown(admin_token: str):
    fake = f"nonexistent_{uuid.uuid4().hex[:10]}"
    r = httpx.get(f"{BACKEND_LOCAL}/admin/chat-sessions/{fake}",
                  headers={"Authorization": f"Bearer {admin_token}"},
                  timeout=15)
    assert r.status_code == 404, (
        f"expected 404 for unknown session_id, got {r.status_code}: {r.text[:200]}"
    )


def test_admin_get_chat_session_shape_when_present(admin_token: str):
    # First list sessions and pick one, if any exist.
    lst = httpx.get(
        f"{BACKEND_LOCAL}/admin/users/test_admin_001/chat-sessions",
        headers={"Authorization": f"Bearer {admin_token}"}, timeout=20).json()
    sessions = lst.get("sessions") or []
    if not sessions:
        pytest.skip("no chat sessions in DB for test_admin_001 — cannot "
                    "exercise the positive path of "
                    "GET /admin/chat-sessions/{session_id}")
    sid = sessions[0]["session_id"]
    r = httpx.get(f"{BACKEND_LOCAL}/admin/chat-sessions/{sid}",
                  headers={"Authorization": f"Bearer {admin_token}"},
                  timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    session = body.get("session")
    assert isinstance(session, dict)
    assert session.get("session_id") == sid
    # Full doc — should contain `turns` array (possibly empty)
    assert "turns" in session
    assert isinstance(session["turns"], list)
    # _id stripped
    assert "_id" not in session


def test_admin_endpoints_registered_at_public_url():
    """Sanity: routes are reachable through the public ingress too."""
    r = httpx.get(f"{BACKEND_PUBLIC}/admin/users/test_admin_001/chat-sessions",
                  timeout=20)
    # Without auth, must be 401/403 (not 404 / 5xx).
    assert r.status_code in (401, 403), (
        f"public URL unreachable / route not registered: {r.status_code} "
        f"{r.text[:200]}"
    )
