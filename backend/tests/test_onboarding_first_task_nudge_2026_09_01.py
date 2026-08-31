"""
tests/test_onboarding_first_task_nudge_2026_09_01.py

Connect-flow investigation, Bug-1 fix — Justin/Jolene both completed
onboarding then hit silence with no next step. t_onboarding_completes_
with_first_task_nudge: a user who completes connect+scan+project
immediately sees a concrete first-task ORA message, not silence.
"""
import time

import pytest

from services.onboarding_first_task_nudge import (
    build_first_task_nudge, send_onboarding_first_task_nudge,
)


class _FakeSessionsColl:
    def __init__(self):
        self.rows: list[dict] = []

    async def update_one(self, query, update, upsert=False):
        row = next(
            (r for r in self.rows
             if all(r.get(k) == v for k, v in query.items())),
            None,
        )
        if row is None:
            if not upsert:
                return
            row = dict(query)
            row.update(update.get("$setOnInsert") or {})
            row["turns"] = []
            self.rows.append(row)
        row.update(update.get("$set") or {})
        for field, spec in (update.get("$push") or {}).items():
            row.setdefault(field, [])
            items = spec.get("$each", [spec]) if isinstance(spec, dict) and "$each" in spec else [spec]
            row[field].extend(items)


class _FakeDB:
    def __init__(self):
        self.chat_sessions = _FakeSessionsColl()


def test_build_first_task_nudge_names_one_concrete_task_when_clean():
    text = build_first_task_nudge(findings_count=0)
    assert "phone number" in text or "slow on your homepage" in text
    assert "what can i help you with" not in text.lower()


def test_build_first_task_nudge_names_the_findings_when_present():
    text = build_first_task_nudge(findings_count=3)
    assert "3" in text
    assert "start with the first one" in text.lower()


@pytest.mark.asyncio
async def test_t_onboarding_completes_with_first_task_nudge():
    db = _FakeDB()
    await send_onboarding_first_task_nudge(
        db=db, user_id="u_justin", project_id="p1", findings_count=2,
    )
    sess = db.chat_sessions.rows[0]
    assert sess["user_id"] == "u_justin"
    assert sess["project_id"] == "p1"
    assert len(sess["turns"]) == 1
    turn = sess["turns"][0]
    assert turn["role"] == "assistant"
    assert "2 things I can improve" in turn["content"]
    assert turn["content"] != ""


@pytest.mark.asyncio
async def test_nudge_fires_only_once_via_trigger_first_scan_gate(monkeypatch):
    """The nudge piggybacks on trigger_first_scan's existing one-shot
    `dev_users.first_scan_at` gate — a user who already scanned once
    must NOT get a second nudge session created."""
    import services.onboarding_first_scan as ofs

    calls = []

    async def _fake_nudge(*, db, user_id, project_id, findings_count):
        calls.append((user_id, project_id, findings_count))

    class _Coll:
        def __init__(self, rows):
            self.rows = rows

        async def find_one(self, query):
            for r in self.rows:
                if all(r.get(k) == v for k, v in query.items()):
                    return r
            return None

        async def update_one(self, *a, **k):
            return None

    class _DB:
        def __init__(self, dev_user):
            self.dev_users = _Coll([dev_user] if dev_user else [])

    monkeypatch.setattr(
        "services.onboarding_first_task_nudge.send_onboarding_first_task_nudge",
        _fake_nudge,
    )
    # Already scanned once -> trigger_first_scan returns immediately,
    # never reaching the scan or the nudge call.
    db = _DB({"user_id": "u1", "first_scan_at": time.time()})
    await ofs.trigger_first_scan(db=db, user_id="u1", project_id="p2")
    assert calls == []
