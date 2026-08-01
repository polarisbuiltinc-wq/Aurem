"""
Iter QA-Hardening — ORA-Learning FUNCTIONAL verify (end-to-end).

Purpose (per founder directive "QA-SYSTEM HARDENING + ORA-LEARNING
FUNCTIONAL VERIFY", Item 1):

    The two silent-catch sites in services/ora_learning.py
    (`maybe_log_ora_escalation`) were fixed earlier with logger.debug /
    logger.warning wiring, but nobody has proved end-to-end that the
    happy path STILL WORKS after those edits. The old tests only assert
    "no exception raised" — that is not enough. This suite proves:

      1. When a low-confidence AUREM reply is fed in, the pipeline
         actually INSERTS a document into MongoDB's
         `ora_learning_logs` collection (real write against the same
         DB the app runs on).
      2. The rate-limit lookup path (fixed silent-catch #1) executes
         and correctly caps writes at ORA_LEARNING_HOURLY_CAP.
      3. When the rate-limit lookup raises, the fail-open branch is
         taken AND the [silent-catch] debug log is emitted (proving
         the fix is wired — the whole point of adding logging was
         to make the outage from Session 4 visible next time).

Zero mocks against the DB: we run against the real Mongo instance
using MONGO_URL from backend/.env. We DO stub `call_ora` at the module
level for one test to isolate the DB-write assertion from live
ORA-upstream flakiness — the ORA HTTP call is not what this test
covers, the DB write is.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

# Ensure /app/backend is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import ora_learning  # noqa: E402


MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "aurem_dev")


@pytest.fixture
def db():
    client = AsyncIOMotorClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    # Guarantee learning isn't disabled by ambient env.
    monkeypatch.delenv("ORA_LEARNING_DISABLED", raising=False)
    yield


async def _cleanup(db, uid):
    await db.ora_learning_logs.delete_many({"user_id": uid})


# ─────────────────────────────────────────────────────────────
# TEST 1 — happy path writes a real document to Mongo.
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_maybe_log_writes_real_document_to_mongo(db, monkeypatch):
    uid = f"qa-hardening-{int(time.time())}"
    await _cleanup(db, uid)

    # Stub the live ORA HTTP call (this test targets the DB write,
    # not the aurem.live upstream). Force is_ora_available() -> True.
    async def _fake_call_ora(**kwargs):
        return {"reply": "ORA fake review reply for QA verify", "ok": True}

    monkeypatch.setattr(ora_learning, "call_ora", _fake_call_ora)
    monkeypatch.setattr(ora_learning, "is_ora_available", lambda: True)

    prompt = (
        "I have a huge backend/services/orchestrator.py issue with "
        "the SSOT model routing that I don't fully understand — can "
        "you help me pinpoint where the drift check should live?"
    )
    aurem_reply = "I'm not sure — can you clarify what you mean?"

    await ora_learning.maybe_log_ora_escalation(
        db=db,
        user_id=uid,
        session_id="sess-qa-1",
        project_id="proj-qa",
        prompt=prompt,
        aurem_response=aurem_reply,
        provider="test-provider",
    )

    rows = await db.ora_learning_logs.find({"user_id": uid}).to_list(None)
    assert len(rows) == 1, (
        f"Expected exactly 1 ora_learning_logs row for uid={uid}, got {len(rows)}"
    )
    row = rows[0]
    # Reason must be one of the low-confidence detectors.
    assert row["reason"].startswith("phrase:") or row["reason"] in (
        "short_answer_on_long_prompt", "clarifying_question_storm",
    ), f"unexpected reason: {row['reason']!r}"
    assert row["prompt"].startswith("I have a huge backend/services")
    assert row["aurem_response"] == aurem_reply
    assert row["provider"] == "test-provider"
    assert row["ora_response"] == "ORA fake review reply for QA verify"
    assert row["ora_error"] is None
    assert row["version"] == 1
    assert isinstance(row["ts"], (int, float))

    await _cleanup(db, uid)


# ─────────────────────────────────────────────────────────────
# TEST 2 — rate-limit lookup path caps writes correctly.
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rate_limit_cap_blocks_further_writes(db, monkeypatch):
    uid = f"qa-hardening-cap-{int(time.time())}"
    await _cleanup(db, uid)

    async def _fake_call_ora(**kwargs):
        return {"reply": "ora reply", "ok": True}

    monkeypatch.setattr(ora_learning, "call_ora", _fake_call_ora)
    monkeypatch.setattr(ora_learning, "is_ora_available", lambda: True)
    monkeypatch.setenv("ORA_LEARNING_HOURLY_CAP", "3")

    prompt = "A" * 250   # long prompt so short reply triggers detector
    aurem_reply = "I'm not sure."

    for _ in range(5):
        await ora_learning.maybe_log_ora_escalation(
            db=db, user_id=uid, session_id="sess-cap", project_id=None,
            prompt=prompt, aurem_response=aurem_reply, provider="cap-test",
        )

    count = await db.ora_learning_logs.count_documents({"user_id": uid})
    # The rate-limit path uses count_documents({"ts": {"$gte": cutoff}})
    # so once we hit `cap` inserts, subsequent calls short-circuit.
    assert count == 3, (
        f"Expected exactly 3 rows (cap=3), got {count}. "
        f"Rate-limit path in maybe_log_ora_escalation is broken."
    )

    await _cleanup(db, uid)


# ─────────────────────────────────────────────────────────────
# TEST 3 — rate-limit-lookup failure emits [silent-catch] log
#           AND still writes (fail-open contract preserved).
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rate_limit_failure_logs_and_fails_open(db, monkeypatch, caplog):
    uid = f"qa-hardening-failopen-{int(time.time())}"
    await _cleanup(db, uid)

    async def _fake_call_ora(**kwargs):
        return {"reply": "ora reply", "ok": True}

    monkeypatch.setattr(ora_learning, "call_ora", _fake_call_ora)
    monkeypatch.setattr(ora_learning, "is_ora_available", lambda: True)

    # Wrap the db object so `ora_learning_logs.count_documents` explodes,
    # but the subsequent `insert_one` still works — this proves the
    # fail-open branch takes over and the write happens anyway.
    real_coll = db.ora_learning_logs

    class _BrokenCounter:
        def __init__(self, real):
            self._real = real

        async def count_documents(self, *a, **kw):
            raise RuntimeError("simulated Mongo count outage")

        async def insert_one(self, *a, **kw):
            return await self._real.insert_one(*a, **kw)

    class _WrappedDB:
        def __init__(self, real_db, broken_coll):
            self._real = real_db
            self.ora_learning_logs = broken_coll

        def __getattr__(self, name):
            return getattr(self._real, name)

    wrapped = _WrappedDB(db, _BrokenCounter(real_coll))

    caplog.set_level(logging.DEBUG, logger="services.ora_learning")

    prompt = "long prompt " * 40
    await ora_learning.maybe_log_ora_escalation(
        db=wrapped, user_id=uid, session_id="sess-fo", project_id=None,
        prompt=prompt, aurem_response="I don't know.",
        provider="failopen-test",
    )

    # 1. The [silent-catch] debug log was emitted -> fix is wired.
    matched = [
        rec for rec in caplog.records
        if "[silent-catch] ora_learning.py:98" in rec.getMessage()
    ]
    assert matched, (
        "Expected [silent-catch] debug log from ora_learning.py:98 "
        f"rate-limit lookup catch. Got records: "
        f"{[r.getMessage() for r in caplog.records]}"
    )

    # 2. Fail-open contract: insert happened anyway.
    count = await real_coll.count_documents({"user_id": uid})
    assert count == 1, (
        f"Fail-open branch should still insert; got {count} rows."
    )

    await _cleanup(db, uid)


# ─────────────────────────────────────────────────────────────
# TEST 4 — end-to-end via chat.py path is background-scheduled.
# ─────────────────────────────────────────────────────────────
def test_chat_py_dispatches_maybe_log_ora_escalation():
    """Static assurance: `routers/chat.py` still fires the shadow-log
    coroutine on the normal chat path. Prevents future refactors from
    silently disconnecting the pipeline."""
    chat_py = Path(__file__).resolve().parent.parent / "routers" / "chat.py"
    src = chat_py.read_text(encoding="utf-8")
    assert "from services.ora_learning import maybe_log_ora_escalation" in src, (
        "routers/chat.py no longer imports maybe_log_ora_escalation — "
        "shadow-learning pipeline is disconnected."
    )
    assert "asyncio.create_task(maybe_log_ora_escalation(" in src, (
        "routers/chat.py no longer schedules maybe_log_ora_escalation as "
        "a background task."
    )
