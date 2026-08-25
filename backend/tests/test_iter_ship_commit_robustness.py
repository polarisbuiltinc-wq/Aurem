"""Ship/Commit Robustness — Blocked-vs-Failed + delivery-honesty.

Root cause recap (audit, file:line evidence):
  - The original `'str' object has no attribute 'get'` crash site
    (routers/cto_projects.py, `_test_touched = [p for p in edits ...]`
    region) was ALREADY fixed in a prior session (git commit
    d88b0a1, "Resilience Layer Phase 1") — confirmed via `git log`.
    That fix is a correct, narrow patch: `edits` is a {path: content}
    dict, so iterating it directly yields path STRINGS, not per-file
    dicts; the old code called `.get("path")` on those strings.
  - This session extends the SAME boundary discipline
    (core/boundaries.coerce) to a sibling site found in the same
    ship area: services/loop_engine.py's `files_to_commit` → dict
    conversion loop, which had the identical `(f or {}).get(...)`
    pattern — safe only as long as every list element is already a
    dict or None, unguarded against a bare truthy string.
  - The REAL gap this session closes is BLOCKED ≠ FAILED: the
    backend's `status: "blocked"` (routers/cto_projects.py, Iter 286
    test-file lock) was already distinct from "failed" at the DB
    level, but `/chat/task-followup` (routers/chat.py) and the
    frontend (TaskProgressCard.jsx, LiveTaskPopup.jsx) both collapsed
    it into failure-shaped rendering. Fixed in all three.
  - Delivery honesty: `_build_failed_followup` always said "nothing
    was committed" even when `task.get("commit_sha")` was populated
    (post-push-verify-failure case) — never true. Added a typed
    `PushFailedError` (core/errors.py) so a push/ref-update rejection
    AFTER a real commit object exists is distinguishable from "no
    commit ever happened."
"""
from __future__ import annotations
import os

import pytest

BACKEND = os.path.dirname(os.path.dirname(__file__))


# ── T1 — crash-at-the-boundary: str where dict is expected ─────────────

def test_t1_coerce_raises_contracterror_not_attributeerror_on_str():
    """The exact failure mode of the original bug — `.get()` called on
    a string — must now be impossible to hit raw: `coerce()` either
    parses the string as JSON into a dict, or raises a clean
    `ContractError` (never lets a bare AttributeError escape)."""
    from core.boundaries import coerce
    from core.errors import ContractError

    with pytest.raises(ContractError) as exc_info:
        coerce("backend/tests/test_dynamic_30_percent_discount.py", dict,
               context="test_t1")
    # Never a raw AttributeError leaking through.
    assert not isinstance(exc_info.value, AttributeError)
    assert "test_t1" in str(exc_info.value)


def test_t1_loop_engine_files_to_commit_loop_uses_coerce():
    """Sibling site (services/loop_engine.py) found in the same ship
    area as the original bug — must route through `coerce()`, not a
    bare `(f or {}).get(...)`."""
    src = open(os.path.join(BACKEND, "services", "loop_engine.py")).read()
    idx = src.find("Convert [{path, content}] → {path: content}")
    assert idx > -1
    region = src[idx: idx + 900]
    assert "coerce(f, dict" in region
    assert "except ContractError" in region


def test_t1_files_to_commit_loop_never_raises_on_bad_element():
    """Behavioral proof: replicate the exact loop body with a list
    containing a valid dict, a bare string, and None — must not raise,
    must skip the bad elements, must keep the good one."""
    from core.boundaries import coerce
    from core.errors import ContractError

    files_to_commit = [
        {"path": "a.py", "content": "x = 1"},
        "backend/tests/test_dynamic_30_percent_discount.py",  # bad: bare str
        None,                                                  # bad: None
    ]
    files_dict: dict[str, str] = {}
    for f in files_to_commit:
        try:
            f = coerce(f, dict, context="test_t1_loop")
        except ContractError:
            continue
        p = f.get("path")
        c = f.get("content")
        if p and c is not None:
            files_dict[str(p)] = str(c)
    assert files_dict == {"a.py": "x = 1"}


def test_t1_do_ship_real_call_skips_bad_element_and_still_ships():
    """Real invocation of `LoopEngine._do_ship` (not a replica) with a
    bare string mixed into `submitted_files` — proves the actual
    coerce()/ContractError branch in loop_engine.py executes and the
    ship still proceeds on the one valid file, instead of crashing on
    the malformed element."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from services import loop_engine as le

    class _Coll:
        def __init__(self):
            self.rows: list[dict] = []
        async def insert_one(self, d):
            self.rows.append(dict(d))
        async def update_one(self, q, u, upsert=False):
            pass
        async def find_one(self, q, *_a, **_kw):
            return None
        async def delete_one(self, q):
            pass

    class _DB:
        def __init__(self):
            self.loop_sessions = _Coll()
            self.loop_backups = _Coll()
            self.loop_plans = _Coll()
            self.loop_lock = _Coll()
            self.loop_failures = _Coll()
            self.cto_projects = _Coll()

    eng = le.LoopEngine(db=_DB(), loop_id="lp_test_t1", user_id="u1",
                        project_id="p1", user_message="ship it")
    eng.bin_ctx = type("B", (), {"repo_owner": "acme", "repo_name": "widgets",
                                  "branch": "main", "pat": "tok"})()
    eng.context["submitted_files"] = [
        {"path": "app.py", "content": "print('hi')\n"},
        "a_bare_string_element_not_a_dict",  # must be skipped via coerce()
    ]

    with patch("services.loop_integrity_guard.check_file_integrity",
              return_value=None), \
         patch("services.loop_diff_classifier.classify",
              return_value={"source": ["app.py"], "tests": [],
                            "test_touched": False, "test_lines": []}), \
         patch("services.loop_independent_verifier.verify",
              AsyncMock(return_value={"verdict": "yes"})):
        asyncio.run(eng._do_ship())

    assert eng.state == le.LoopState.PAUSED_FOR_USER
    assert eng.context["ship_pending"]["files"] == {"app.py": "print('hi')\n"}


def test_t1_original_crash_site_still_fixed():
    """Regression guard on the ORIGINAL site (belt-and-suspenders on
    top of tests/test_regression_iter286_mcp_test_file_lock.py) — the
    dict-iteration bug must not silently come back."""
    src = open(os.path.join(BACKEND, "routers", "cto_projects.py")).read()
    idx = src.find("_test_touched = [p for p in edits")
    assert idx > -1, "original fix site must still exist"
    region = src[idx: idx + 200]
    assert ".get(" not in region, (
        "the fixed line must not call .get() on the dict-iteration "
        "result (that was the exact 'str' object has no attribute "
        "'get' bug)"
    )


# ── T2 — blocked rendering: never "Task failed" ─────────────────────────

FAILED_STRING = "Task failed — nothing was committed"


def test_t2_blocked_followup_never_says_task_failed():
    from services.chat_helpers import _build_blocked_followup
    out = _build_blocked_followup(
        original="fix hardcoded password in test file",
        blocked_reason="test_file_lock",
        blocked_paths=["backend/tests/test_dynamic_30_percent_discount.py"],
    )
    assert FAILED_STRING not in out
    assert "❌" not in out
    assert "awaiting your approval" in out.lower()
    assert "Approve in Loop mode" in out
    assert "backend/tests/test_dynamic_30_percent_discount.py" in out


def test_t2_blocked_followup_handles_missing_fields():
    from services.chat_helpers import _build_blocked_followup
    out = _build_blocked_followup(original="", blocked_reason="", blocked_paths=[])
    assert FAILED_STRING not in out
    assert "None" not in out


def test_t2_chat_task_followup_accepts_blocked_status():
    """The endpoint must no longer 409 on status='blocked', and must
    route to the blocked builder, not the failed one."""
    src = open(os.path.join(BACKEND, "routers", "chat.py")).read()
    idx = src.find('@router.post("/task-followup")')
    assert idx > -1
    region = src[idx: idx + 3000]
    assert '"done", "failed", "blocked"' in region
    assert '_build_blocked_followup' in region
    assert 'task.get("status") == "blocked"' in region


def test_t2_frontend_taskprogresscard_has_dedicated_blocked_branch():
    """A blocked task must render its own component, never fall into
    the FailedCard (red) or the infinite-spinner branch."""
    src = open(os.path.join(
        BACKEND, "..", "frontend", "src", "components",
        "TaskProgressCard.jsx")).read()
    assert 'status === "blocked"' in src
    assert "BlockedCard" in src
    assert "return <BlockedCard" in src
    # The running-spinner guard must now explicitly exclude blocked
    # too (not fall through to the raw-status-label spinner forever).
    idx = src.find('status !== "done" && status !== "failed"')
    assert idx > -1
    assert 'status !== "blocked"' in src[idx: idx + 80]


def test_t2_frontend_livetaskpopup_blocked_not_grouped_with_failed():
    src = open(os.path.join(
        BACKEND, "..", "frontend", "src", "components",
        "LiveTaskPopup.jsx")).read()
    assert "Awaiting your approval" in src
    # The old bug: `status === "blocked"` OR'd into the same branch
    # that returns the red ❌ icon.
    idx = src.find('function iconFor')
    region = src[idx: idx + 400]
    error_line = [l for l in region.splitlines() if 'return "❌"' in l][0]
    assert '"blocked"' not in error_line, (
        "blocked must not share the failed/error icon branch"
    )


# ── T3 — real commit_files() logic exercised end-to-end ─────────────────
#
# NOTE (honesty, per platform rules): this Preview environment has no
# writable credential configured for the disposable drill repo
# (services/rollback_drill.py::_resolve_write_token() correctly falls
# back to `GITHUB_ACTIONS_TOKEN`, which is documented as read-only in
# Preview — confirmed live: a real attempt to commit to
# AUREM_DRILL_REPO returned "404 Not Found" reading the branch ref,
# i.e. no write path is reachable from here). So this proof exercises
# the REAL `commit_files()` code — every blob/tree/commit/ref-update
# call, in order, with real base64 encoding and real JSON payloads —
# against a mocked HTTP transport (respx) rather than a live external
# write, the same standard already used for the A4 binary-file fix
# this session. It is a real-code, mocked-transport proof, not a live
# GitHub write — reported as such, not disguised as a live push.

import httpx

def _mock_github_success(respx_mock, owner="acme", repo="widgets",
                          branch="main", head_sha="a1b2c3d4e5f6",
                          new_commit_sha="deadbeef1234567890abcdef"):
    base = f"https://api.github.com/repos/{owner}/{repo}"
    respx_mock.get(f"{base}/git/ref/heads/{branch}").mock(
        return_value=httpx.Response(200, json={"object": {"sha": head_sha}}))
    respx_mock.get(f"{base}/git/commits/{head_sha}").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "treeSHA0"}}))
    respx_mock.post(f"{base}/git/blobs").mock(
        return_value=httpx.Response(201, json={"sha": "blobSHA0"}))
    respx_mock.post(f"{base}/git/trees").mock(
        return_value=httpx.Response(201, json={"sha": "newTreeSHA0"}))
    respx_mock.post(f"{base}/git/commits").mock(
        return_value=httpx.Response(201, json={"sha": new_commit_sha}))
    return base


async def test_t3_real_commit_files_logic_delivers_real_sha(respx_mock):
    from services.github_api_writer import commit_files
    new_commit_sha = "deadbeef1234567890abcdef00001111"
    base = _mock_github_success(respx_mock, new_commit_sha=new_commit_sha)
    respx_mock.patch(f"{base}/git/refs/heads/main").mock(
        return_value=httpx.Response(200, json={"ref": "refs/heads/main"}))

    result = await commit_files(
        owner="acme", repo="widgets", branch="main", token="fake-tok",
        files={"proof.txt": "AUREM T3 live-logic proof\n"},
        commit_message="AUREM T3 proof: real commit+push logic",
        author_email="aurem@example.com", author_name="AUREM",
    )
    print(f"T3 CAPTURED RESULT: {result}")
    assert result["ok"] is True
    assert result["full_sha"] == new_commit_sha
    assert result["sha"] == new_commit_sha[:7]
    assert result["html_url"].endswith(new_commit_sha)


async def test_t4a_push_rejected_after_real_commit_object_created(respx_mock):
    """The exact T4(a) scenario, live-reproduced against mocked
    transport: blob/tree/commit all succeed (a real commit object is
    created), then the ref-update (push) is rejected — must raise
    PushFailedError carrying that real SHA, not a bare
    HTTPStatusError, and must never look like 'nothing was
    committed'."""
    from services.github_api_writer import commit_files
    from core.errors import PushFailedError
    new_commit_sha = "cafebabe00112233445566778899aabbccddeef"
    base = _mock_github_success(respx_mock, new_commit_sha=new_commit_sha)
    respx_mock.patch(f"{base}/git/refs/heads/main").mock(
        return_value=httpx.Response(
            422, json={"message": "Update is not a fast forward"}))

    with pytest.raises(PushFailedError) as exc_info:
        await commit_files(
            owner="acme", repo="widgets", branch="main", token="fake-tok",
            files={"proof.txt": "AUREM T4a proof\n"},
            commit_message="AUREM T4a proof: commit ok, push rejected",
            author_email="aurem@example.com", author_name="AUREM",
        )
    print(f"T4a CAPTURED EXCEPTION: commit_sha={exc_info.value.commit_sha} "
          f"reason={exc_info.value.reason}")
    # The commit object genuinely exists (by SHA) — this is the fact
    # that makes "nothing was committed" dishonest for this case.
    assert exc_info.value.commit_sha == new_commit_sha
    assert "422" in exc_info.value.reason

def test_t3_pushfailederror_carries_orphaned_sha():
    from core.errors import PushFailedError
    exc = PushFailedError(commit_sha="abc1234567890", reason="HTTP 409: conflict")
    assert exc.commit_sha == "abc1234567890"
    assert "abc1234" in str(exc)
    assert "push" in str(exc).lower()


def test_t3_pushfailederror_classified_in_taxonomy():
    from core.errors import classify_exception, ErrorCode, PushFailedError
    exc = PushFailedError(commit_sha="deadbeef", reason="branch protection")
    assert classify_exception(exc) == ErrorCode.PUSH_FAILED


def test_t3_push_failed_i18n_entry_exists():
    import json
    catalog = json.load(open(os.path.join(BACKEND, "i18n", "errors_en.json")))
    assert "PUSH_FAILED" in catalog
    assert catalog["PUSH_FAILED"]["title"]


def test_t3_github_writer_raises_pushfailed_not_raw_httpstatuserror():
    """The ref-update (push) step must raise the typed error, carrying
    the ALREADY-CREATED commit sha, instead of a bare
    `response.raise_for_status()` HTTPStatusError that loses that
    context."""
    src = open(os.path.join(BACKEND, "services", "github_api_writer.py")).read()
    idx = src.find("Advance the branch ref")
    assert idx > -1
    region = src[idx: idx + 1400]
    assert "PushFailedError(" in region
    assert "commit_sha=new_commit_sha" in region


def test_t4a_commit_but_push_failed_message_is_honest():
    """T4(a): commit happened but push failed → 'push FAILED', NOT
    'nothing was committed', NOT 'delivered'."""
    from services.chat_helpers import _build_failed_followup
    out = _build_failed_followup(
        original="fix bug", err="commit abc1234 created but push failed: "
        "HTTP 409: conflict", files=["a.py"], sha="abc1234567", push_failed=True,
    )
    assert FAILED_STRING not in out
    assert "delivered" not in out.lower()
    assert "push FAILED" in out
    assert "abc1234" in out


def test_t4a_pushed_but_verify_uncertain_message_is_honest():
    """The sibling honest state this audit also found: push SUCCEEDED
    (branch was updated) but post-push verification could not confirm
    content — must not say "nothing was committed" either."""
    from services.chat_helpers import _build_failed_followup
    out = _build_failed_followup(
        original="fix bug", err="post-push verify mismatch", files=["a.py"],
        sha="deadbee0000", verify_failed=True,
    )
    assert FAILED_STRING not in out
    assert "deadbee" in out
    assert "check" in out.lower() or "confirm" in out.lower()


def test_t4b_no_commit_at_all_keeps_the_honest_blank_copy():
    """T4(b): no push happened at all → 'nothing was committed' is the
    correct, allowed copy (must not regress to something dishonest in
    the other direction)."""
    from services.chat_helpers import _build_failed_followup
    out = _build_failed_followup(
        original="fix bug", err="clone failed: 404", files=[], sha=None,
    )
    assert FAILED_STRING in out


def test_t4_cto_projects_wires_push_failed_flag_on_persist():
    """The commit-call site must catch PushFailedError specifically and
    delegate to `_persist_push_failed`, which persists commit_sha +
    push_failed=True — not fall into the generic except that would
    silently drop the SHA."""
    src = open(os.path.join(BACKEND, "routers", "cto_projects.py")).read()
    idx = src.find("except PushFailedError as e:")
    assert idx > -1
    region = src[idx: idx + 450]
    assert '_persist_push_failed(task_id, e)' in region
    idx2 = src.find("async def _persist_push_failed(")
    assert idx2 > -1
    region2 = src[idx2: idx2 + 700]
    assert 'commit_sha=e.commit_sha' in region2
    assert 'push_failed=True' in region2
    assert 'status="failed"' in region2


async def test_t4_persist_push_failed_helper_real_call():
    """Real invocation (not a source-string check): `_persist_push_failed`
    must call `_set_status` with the exact honest kwargs — real SHA,
    push_failed=True — and never claim 'nothing was committed'."""
    from unittest.mock import AsyncMock, patch
    from core.errors import PushFailedError
    from routers.cto_projects import _persist_push_failed

    fake_set_status = AsyncMock()
    fake_log = AsyncMock()
    with patch("routers.cto_projects._set_status", fake_set_status), \
         patch("routers.cto_projects._log", fake_log):
        exc = PushFailedError(commit_sha="cafebabe1234567", reason="HTTP 409: conflict")
        err = await _persist_push_failed("t_test123", exc)

    assert "cafebab" in err
    fake_set_status.assert_awaited_once()
    _, kwargs = fake_set_status.call_args
    assert kwargs["status"] == "failed"
    assert kwargs["commit_sha"] == "cafebabe1234567"
    assert kwargs["push_failed"] is True
    fake_log.assert_awaited_once()


def test_t4_cto_projects_wires_verify_failed_flag_on_persist():
    src = open(os.path.join(BACKEND, "routers", "cto_projects.py")).read()
    assert "verify_failed=True" in src


# ── T5 — error-report crash-safety ──────────────────────────────────────

def test_t5_followup_builder_crash_produces_no_raw_attributeerror():
    """Force the exact original crash shape (str where a list of dicts
    is expected) into `_build_failed_followup`'s `files` argument and
    confirm the function is defensive about it (join over strings is
    always safe — this proves the builder itself can't reproduce the
    'str has no .get' crash class even under malformed input)."""
    from services.chat_helpers import _build_failed_followup
    # `files` elements should be strings already, but even a stray
    # non-string element must not blow up formatting.
    out = _build_failed_followup(
        original="x", err="boom", files=["a.py", 123, None],  # malformed
    )
    assert isinstance(out, str)
    assert "boom" in out


def test_t5_global_exception_handler_covers_task_followup_router():
    """Belt-and-suspenders: confirm main.py's crash-proof global
    handler (Resilience Layer Phase 1) is registered for generic
    Exception, so ANY future crash inside chat_task_followup's
    message-building still returns a safe classified envelope, never
    a raw traceback, without needing a bespoke try/except per route."""
    src = open(os.path.join(BACKEND, "main.py")).read()
    assert '@app.exception_handler(Exception)' in src
    assert "build_error_envelope" in src
