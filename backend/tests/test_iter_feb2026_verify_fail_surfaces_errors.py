"""
test_iter_feb2026_verify_fail_surfaces_errors.py — Feb 2026 · Iter 362

Founder P1: "When Verify fails after exhausting self-heal attempts,
the chat UI never surfaces the actual lint/type error text — only a
generic 'Verify failed after 2 attempts'. The user can't diagnose or
manually fix without that information."

Fix (backend half):
  `_fail("verify", ...)` now emits the terminal FAILED event with a
  structured `data` payload that includes:
    - failed_files       (list of paths from context.verify_failed_files)
    - errors             (list of top-25 lint/type errors from
                          context.verify_last_errors)
    - max_self_heals     (int — for the card title)
    - kind: "terminal_fail"
    - phase, reason

  Frontend (LoopFailureCard) reads that payload and renders the
  actual errors so the user can copy them, fix manually, or send a
  targeted follow-up ("insert the check right after line X").
"""
from __future__ import annotations

import asyncio
from typing import Any


# ────────────────────── Mongo doubles (reuse pattern) ─────────────
class _Coll:
    def __init__(self): self.rows = []
    async def insert_one(self, d):
        self.rows.append(dict(d))
        class _R: inserted_id = "x"
        return _R()
    async def update_one(self, q, u, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()
                   if not isinstance(v, dict)):
                for k, v in (u.get("$set") or {}).items():
                    r[k] = v
                class _R: modified_count = 1; upserted_id = None
                return _R()
        if upsert:
            self.rows.append({**q, **(u.get("$set") or {})})
        class _R: modified_count = 0
        return _R()
    async def find_one(self, q, *_a, **_kw):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()
                   if not isinstance(v, dict)):
                return dict(r)
        return None
    async def delete_one(self, q):
        class _R: deleted_count = 0
        return _R()


class _DB:
    def __init__(self):
        self.loop_sessions = _Coll()
        self.loop_backups  = _Coll()
        self.loop_plans    = _Coll()
        self.loop_lock     = _Coll()
        self.loop_failures = _Coll()
        self.loop_run_log  = _Coll()


def _make_engine():
    from services import loop_engine as le
    return le.LoopEngine(
        db=_DB(), loop_id="lp_test_surface_errors",
        user_id="u_test", project_id="p_test",
        user_message="add input validation",
    )


# ═══════════════════════════════════════════════════════════════════
# Contract 1 — _fail("verify") emits structured data payload
# ═══════════════════════════════════════════════════════════════════
def test_verify_fail_emits_failing_files_and_errors_in_data(monkeypatch):
    """When _do_verify hard-fails after MAX_SELF_HEALS, the emitted
    FAILED event must carry `data.failed_files` and `data.errors` so
    the frontend LoopFailureCard has real content to render — not
    just a generic message string."""
    from services import loop_engine as le

    monkeypatch.setattr(le, "SELF_HEAL_LLM_TIMEOUT_S", 1)

    eng = _make_engine()
    eng.context["submitted_files"] = [
        {"path": "backend/routers/uptime_webhook_router.py",
         "content": "def uptime_report():\n    from fastapi import HTTPException\n    return {}\n"},
    ]

    async def failing_verify(files):
        return {
            "ok": False,
            "results": [
                {"path": f["path"], "ok": False, "linter": "ruff",
                 "stdout": (f"{f['path']}:12:5: E402 module level "
                            f"import not at top of file"),
                 "stderr": ""}
                for f in files
            ],
            "errors": [
                (f"{f['path']}:12:5: E402 module level import not at "
                 f"top of file")
                for f in files
            ] + [
                "backend/routers/uptime_webhook_router.py:18:9: "
                "F841 local variable 'x' is assigned to but never used",
            ],
        }

    import services.loop_verify as lv
    monkeypatch.setattr(lv, "verify_files", failing_verify)

    # Never-succeeding healer.
    class _Healer:
        async def heal(self, **_kw):
            return {"status": "escalate"}

    class _Parliament:
        def __init__(self, db=None): self.healer = _Healer()

    import core.parliament as _pmod
    monkeypatch.setattr(_pmod, "Parliament", _Parliament)

    emitted: list[dict] = []
    orig_emit = eng._emit

    async def spy_emit(state, phase, **kwargs):
        emitted.append({"state": state, "phase": phase,
                        "message": kwargs.get("message"),
                        "data":    dict(kwargs.get("data") or {})})
        return await orig_emit(state, phase, **kwargs)

    monkeypatch.setattr(eng, "_emit", spy_emit)

    async def go():
        await eng._do_verify()

    asyncio.run(go())

    # Find the terminal FAILED event.
    failed_events = [e for e in emitted
                     if e["state"] == le.LoopState.FAILED]
    assert len(failed_events) == 1, (
        f"expected exactly 1 FAILED event, got {failed_events}"
    )
    fe = failed_events[0]

    # Contract 1a — kind + phase pinned.
    assert fe["data"].get("kind") == "terminal_fail"
    assert fe["data"].get("phase") == "verify"

    # Contract 1b — failing_files present + non-empty.
    assert isinstance(fe["data"].get("failed_files"), list), (
        f"failed_files missing from FAILED event data: {fe['data']}"
    )
    assert len(fe["data"]["failed_files"]) >= 1, (
        f"failed_files must be populated for terminal verify fail: "
        f"{fe['data']}"
    )
    assert any("uptime_webhook_router.py" in p
               for p in fe["data"]["failed_files"]), (
        f"failing file path missing from FAILED event: "
        f"{fe['data']['failed_files']}"
    )

    # Contract 1c — errors list carries the real lint output.
    assert isinstance(fe["data"].get("errors"), list)
    assert len(fe["data"]["errors"]) >= 1, (
        f"errors list must contain the lint/type output. "
        f"Got: {fe['data'].get('errors')}"
    )
    err_blob = " ".join(fe["data"]["errors"])
    assert "E402" in err_blob or "F841" in err_blob, (
        f"real lint codes must survive into the FAILED event. "
        f"Got errors: {fe['data']['errors']}"
    )

    # Contract 1d — max_self_heals surfaces (drives card title).
    assert fe["data"].get("max_self_heals") == le.MAX_SELF_HEALS


# ═══════════════════════════════════════════════════════════════════
# Contract 2 — non-verify _fail also gets a structured data payload
# ═══════════════════════════════════════════════════════════════════
def test_non_verify_fail_still_gets_kind_terminal_fail(monkeypatch):
    """_fail is called from multiple phases (execute/scan/ship). The
    terminal_fail kind should surface uniformly so the frontend can
    render LoopFailureCard for any terminal error, not just verify."""
    from services import loop_engine as le

    eng = _make_engine()

    emitted: list[dict] = []
    orig_emit = eng._emit

    async def spy_emit(state, phase, **kwargs):
        emitted.append({"state": state, "phase": phase,
                        "data": dict(kwargs.get("data") or {})})
        return await orig_emit(state, phase, **kwargs)

    monkeypatch.setattr(eng, "_emit", spy_emit)

    async def go():
        await eng._fail("execute", "boom, something broke")

    asyncio.run(go())
    fe = next(e for e in emitted if e["state"] == le.LoopState.FAILED)
    assert fe["data"].get("kind") == "terminal_fail"
    assert fe["data"].get("phase") == "execute"
    assert fe["data"].get("reason") == "boom, something broke"
    # No verify context → failed_files/errors keys must NOT be
    # spuriously present on non-verify fails.
    assert "failed_files" not in fe["data"]
    assert "errors" not in fe["data"]


# ═══════════════════════════════════════════════════════════════════
# Contract 3 — caller-provided data merges cleanly
# ═══════════════════════════════════════════════════════════════════
def test_fail_caller_data_merges_into_emit(monkeypatch):
    """A caller passing `data={...}` to _fail must have its keys
    merged into the emit's data payload (caller wins on collision)."""
    from services import loop_engine as le

    eng = _make_engine()

    emitted: list[dict] = []
    orig_emit = eng._emit

    async def spy_emit(state, phase, **kwargs):
        emitted.append({"state": state,
                        "data": dict(kwargs.get("data") or {})})
        return await orig_emit(state, phase, **kwargs)

    monkeypatch.setattr(eng, "_emit", spy_emit)

    async def go():
        await eng._fail("ship", "GitHub push failed",
                        data={"http_status": 422,
                              "provider": "github",
                              "kind": "custom_ship_fail"})   # caller override

    asyncio.run(go())
    fe = next(e for e in emitted if e["state"] == le.LoopState.FAILED)
    assert fe["data"].get("http_status") == 422
    assert fe["data"].get("provider") == "github"
    assert fe["data"].get("kind") == "custom_ship_fail", (
        "caller-provided kind should override the default terminal_fail"
    )
    assert fe["data"].get("phase") == "ship"
