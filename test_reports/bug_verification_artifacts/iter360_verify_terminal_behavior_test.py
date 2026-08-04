"""
Focused behavioral verification for Session G Bucket-A self-heal hard cap.
These are QA-agent tests only; they do not modify product code.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class _Result:
    inserted_id = "x"
    modified_count = 1
    deleted_count = 1
    upserted_id = None


class _Coll:
    def __init__(self):
        self.rows = []

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        return _Result()

    async def update_one(self, query, update, upsert=False):
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                row.update(update.get("$set") or {})
                return _Result()
        if upsert:
            self.rows.append({**query, **(update.get("$set") or {})})
        return _Result()

    async def find_one(self, query, *args, **kwargs):
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                return dict(row)
        return None

    async def find_one_and_update(self, *args, **kwargs):
        return None

    async def delete_one(self, *args, **kwargs):
        return _Result()

    async def delete_many(self, *args, **kwargs):
        return _Result()


class _DB:
    def __init__(self):
        for name in (
            "loop_sessions", "loop_backups", "loop_plans", "loop_failures",
            "loop_run_log", "loop_events", "dev_users", "chat_sessions",
            "loop_locks", "cto_projects",
        ):
            setattr(self, name, _Coll())


def _engine(loop_id="iter360_verify_cap"):
    from services.loop_engine import LoopEngine
    return LoopEngine(
        db=_DB(),
        loop_id=loop_id,
        user_id="qa_user",
        project_id="qa_project",
        user_message="make a file that remains syntactically broken",
    )


def _failing_verify_factory():
    calls = []

    async def _verify(files):
        calls.append([f["path"] for f in files])
        return {
            "ok": False,
            "results": [
                {
                    "path": f["path"],
                    "ok": False,
                    "linter": "ruff",
                    "stdout": f"{f['path']}:1:7: SyntaxError: invalid syntax",
                    "stderr": "",
                }
                for f in files
            ],
            "errors": [f"{f['path']}:1:7: SyntaxError" for f in files],
        }

    return _verify, calls


def _patch_failing_verify_and_healer(monkeypatch):
    import services.loop_verify as lv
    from services import loop_engine as le

    monkeypatch.setattr(le, "SELF_HEAL_LLM_TIMEOUT_S", 1)
    fake_verify, verify_calls = _failing_verify_factory()
    monkeypatch.setattr(lv, "verify_files", fake_verify)

    heal_calls = []

    class _Healer:
        async def heal(self, **kwargs):
            heal_calls.append(kwargs)
            return {"status": "escalate"}

    class _Parliament:
        def __init__(self, db=None):
            self.healer = _Healer()

    import core.parliament as parliament_mod
    monkeypatch.setattr(parliament_mod, "Parliament", _Parliament)
    return verify_calls, heal_calls


async def _drain_queue(eng):
    events = []
    while not eng.queue.empty():
        events.append(await eng.queue.get())
    return events


@pytest.mark.asyncio
async def test_verify_cap_hard_fails_once_with_no_pause_behavioral(monkeypatch):
    from services import loop_engine as le

    eng = _engine()
    eng.context["submitted_files"] = [{"path": "broken.py", "content": "broken = "}]
    verify_calls, heal_calls = _patch_failing_verify_and_healer(monkeypatch)

    await eng._do_verify()
    events = await _drain_queue(eng)

    assert eng.state == le.LoopState.FAILED
    assert eng._should_stop() is True
    assert eng.context.get("total_heal_attempts") == le.MAX_SELF_HEALS == 2
    assert len(heal_calls) == le.MAX_SELF_HEALS
    assert len(verify_calls) == 1 + le.MAX_SELF_HEALS

    failed_events = [e for e in events if e.get("state") == le.LoopState.FAILED.value]
    paused_events = [e for e in events if e.get("state") == le.LoopState.PAUSED_FOR_USER.value]
    assert len(failed_events) == 1, events
    assert paused_events == []
    assert failed_events[0].get("requires_user_action") is True
    assert "terminal" in failed_events[0].get("message", "").lower()

    terminal_duplicate_messages = [
        e for e in events
        if e.get("state") == le.LoopState.FAILED.value
        and "Verify failed after 2" in e.get("message", "")
    ]
    assert len(terminal_duplicate_messages) == 1


@pytest.mark.asyncio
async def test_pipeline_stops_after_verify_failed_state_behavioral(monkeypatch):
    from services import loop_engine as le

    eng = _engine("iter360_pipeline_stop")
    verify_calls, heal_calls = _patch_failing_verify_and_healer(monkeypatch)
    scan_called = False
    ship_called = False

    async def fake_execute():
        eng.context["submitted_files"] = [{"path": "broken.py", "content": "broken = "}]

    async def fake_scan():
        nonlocal scan_called
        scan_called = True
        raise AssertionError("scan must not run after verify hard-fails")

    async def fake_ship():
        nonlocal ship_called
        ship_called = True
        raise AssertionError("ship must not run after verify hard-fails")

    monkeypatch.setattr(eng, "_do_execute", fake_execute)
    monkeypatch.setattr(eng, "_do_scan", fake_scan)
    monkeypatch.setattr(eng, "_do_ship", fake_ship)

    await eng._run_pipeline_inner()

    assert eng.state == le.LoopState.FAILED
    assert eng._should_stop() is True
    assert scan_called is False
    assert ship_called is False
    assert len(heal_calls) == le.MAX_SELF_HEALS
    assert len(verify_calls) == 1 + le.MAX_SELF_HEALS


@pytest.mark.asyncio
async def test_pause_response_retry_and_skip_reject_terminal_loop_behavioral(monkeypatch):
    from routers import loop as loop_router
    from services import loop_engine as le

    terminal_engine = SimpleNamespace(
        user_id="qa_user",
        state=le.LoopState.FAILED,
        phase="verify",
        context={},
    )

    async def fake_current_dev(_authorization):
        return {"user_id": "qa_user"}

    async def fake_lookup_or_rehydrate(_db, _loop_id):
        return terminal_engine

    monkeypatch.setattr(loop_router, "current_dev", fake_current_dev)
    monkeypatch.setattr(loop_router, "get_db", lambda: _DB())
    monkeypatch.setattr(loop_router.eng, "lookup_or_rehydrate", fake_lookup_or_rehydrate)

    for action in ("retry", "skip"):
        with pytest.raises(HTTPException) as excinfo:
            await loop_router.pause_response(
                "iter360_terminal_loop",
                loop_router.PauseResponseBody(action=action),
                authorization="Bearer qa",
            )
        assert excinfo.value.status_code == 409
        assert excinfo.value.detail["error"] == "loop_terminal"
        assert excinfo.value.detail["state"] == "failed"
        assert excinfo.value.detail["phase"] == "verify"