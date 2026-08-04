"""Focused QA probe for verify self-heal terminal cap regression.

This is a testing artifact only. It uses deterministic fake Mongo/Auth and
does not modify product code.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _Coll:
    def __init__(self, rows: list[dict] | None = None):
        self.rows = [dict(r) for r in (rows or [])]

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

        class _R:
            inserted_id = "x"

        return _R()

    async def update_one(self, q, u, upsert=False):
        for row in self.rows:
            if all(row.get(k) == v for k, v in q.items() if not isinstance(v, dict)):
                row.update(u.get("$set") or {})

                class _R:
                    modified_count = 1
                    upserted_id = None

                return _R()
        if upsert:
            new_doc = {**q, **(u.get("$setOnInsert") or {}), **(u.get("$set") or {})}
            self.rows.append(new_doc)

        class _R:
            modified_count = 0
            upserted_id = "x" if upsert else None

        return _R()

    async def find_one(self, q, *_args, **_kwargs):
        for row in self.rows:
            if all(row.get(k) == v for k, v in q.items() if not isinstance(v, dict)):
                return dict(row)
        return None

    async def delete_one(self, q):
        for idx, row in enumerate(list(self.rows)):
            if all(row.get(k) == v for k, v in q.items() if not isinstance(v, dict)):
                self.rows.pop(idx)
                break

        class _R:
            deleted_count = 1

        return _R()


class _DB:
    def __init__(self, sessions: list[dict] | None = None):
        self.loop_sessions = _Coll(sessions)
        self.loop_backups = _Coll()
        self.loop_errors = _Coll()
        self.loop_events = _Coll()
        self.loop_failures = _Coll()
        self.loop_locks = _Coll()
        self.dev_users = _Coll()


def _make_engine(db=None, loop_id="iter361_engine"):
    from services import loop_engine as le

    return le.LoopEngine(
        db=db or _DB(),
        loop_id=loop_id,
        user_id="owner_iter361",
        project_id="project_iter361",
        user_message="make a change that remains unverifiable",
    )


def test_verify_cap_terminal_one_failed_event_no_pause(monkeypatch):
    from services import loop_engine as le
    import services.loop_verify as lv
    import core.parliament as parliament_mod

    async def always_fail(files):
        return {
            "ok": False,
            "results": [
                {"path": f["path"], "ok": False, "stdout": "SyntaxError", "stderr": ""}
                for f in files
            ],
            "errors": [f"{f['path']}: SyntaxError" for f in files],
        }

    heal_calls: list[dict[str, Any]] = []

    class _Healer:
        async def heal(self, **kwargs):
            heal_calls.append(kwargs)
            return {"status": "escalate"}

    class _Parliament:
        def __init__(self, db=None):
            self.healer = _Healer()

    monkeypatch.setattr(lv, "verify_files", always_fail)
    monkeypatch.setattr(parliament_mod, "Parliament", _Parliament)
    monkeypatch.setattr(le, "SELF_HEAL_LLM_TIMEOUT_S", 1)

    engine = _make_engine()
    engine.context["submitted_files"] = [{"path": "broken.py", "content": "x = "}]

    emitted: list[dict] = []
    original_emit = engine._emit

    async def spy_emit(state, phase, **kwargs):
        emitted.append({
            "state": state.value if hasattr(state, "value") else str(state),
            "phase": phase,
            "message": kwargs.get("message") or "",
            "requires_user_action": bool(kwargs.get("requires_user_action")),
        })
        return await original_emit(state, phase, **kwargs)

    monkeypatch.setattr(engine, "_emit", spy_emit)

    asyncio.run(engine._do_verify())

    assert le.MAX_SELF_HEALS == 2
    assert engine.state == le.LoopState.FAILED
    assert engine._should_stop() is True
    assert engine.context.get("total_heal_attempts") == le.MAX_SELF_HEALS
    assert len(heal_calls) == le.MAX_SELF_HEALS
    assert [e for e in emitted if e["state"] == "paused_for_user"] == []
    failed_events = [e for e in emitted if e["state"] == "failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["requires_user_action"] is True
    # Exact old duplicate phrase should appear once at most; final terminal
    # event uses a distinct, terminal message with "self-heal" context.
    exact_old_phrase = [e for e in emitted if e["message"] == "Verify failed after 2 attempts"]
    assert len(exact_old_phrase) <= 1, emitted


def test_do_verify_reentry_with_consumed_cap_invokes_no_more_heals(monkeypatch):
    from services import loop_engine as le
    import services.loop_verify as lv
    import core.parliament as parliament_mod

    verify_calls = []

    async def always_fail(files):
        verify_calls.append([f["path"] for f in files])
        return {
            "ok": False,
            "results": [{"path": f["path"], "ok": False, "stdout": "bad", "stderr": ""} for f in files],
            "errors": ["bad"],
        }

    heal_calls: list[dict[str, Any]] = []

    class _Healer:
        async def heal(self, **kwargs):
            heal_calls.append(kwargs)
            return {"status": "retry", "output": "should not be called"}

    class _Parliament:
        def __init__(self, db=None):
            self.healer = _Healer()

    monkeypatch.setattr(lv, "verify_files", always_fail)
    monkeypatch.setattr(parliament_mod, "Parliament", _Parliament)

    engine = _make_engine(loop_id="iter361_reentry")
    engine.context["submitted_files"] = [{"path": "broken.py", "content": "x = "}]
    engine.context["total_heal_attempts"] = le.MAX_SELF_HEALS

    asyncio.run(engine._do_verify())

    assert engine.state == le.LoopState.FAILED
    assert heal_calls == []
    assert len(verify_calls) == 1


def test_pause_response_terminal_live_and_persisted(monkeypatch):
    from routers import loop as loop_router
    from routers.loop import router as loop_api_router
    from services import loop_engine as le

    app = FastAPI()
    app.include_router(loop_api_router, prefix="/api/aurem-dev")

    current_user = {"user_id": "owner_iter361"}

    async def fake_current_dev(*_args, **_kwargs):
        return current_user

    db = _DB([
        {
            "loop_id": "iter361_persisted_failed",
            "user_id": "owner_iter361",
            "state": "failed",
            "phase": "verify",
            "context": {},
        },
        {
            "loop_id": "iter361_stranger_failed",
            "user_id": "actual_owner",
            "state": "failed",
            "phase": "verify",
            "context": {},
        },
    ])

    monkeypatch.setattr(loop_router, "current_dev", fake_current_dev)
    monkeypatch.setattr(loop_router, "get_db", lambda: db)
    le.reset_registry()

    live_engine = _make_engine(db=db, loop_id="iter361_live_failed")
    live_engine.state = le.LoopState.FAILED
    live_engine.phase = "verify"
    le.register(live_engine)

    client = TestClient(app)

    live_resp = client.post(
        "/api/aurem-dev/loop/iter361_live_failed/pause-response",
        json={"action": "retry"},
        headers={"Authorization": "Bearer test"},
    )
    assert live_resp.status_code == 409, live_resp.text
    assert live_resp.json()["detail"]["error"] == "loop_terminal"

    le.reset_registry()
    persisted_resp = client.post(
        "/api/aurem-dev/loop/iter361_persisted_failed/pause-response",
        json={"action": "skip"},
        headers={"Authorization": "Bearer test"},
    )
    assert persisted_resp.status_code == 409, persisted_resp.text
    assert persisted_resp.json()["detail"]["error"] == "loop_terminal"

    stranger_resp = client.post(
        "/api/aurem-dev/loop/iter361_stranger_failed/pause-response",
        json={"action": "retry"},
        headers={"Authorization": "Bearer test"},
    )
    assert stranger_resp.status_code == 403, stranger_resp.text