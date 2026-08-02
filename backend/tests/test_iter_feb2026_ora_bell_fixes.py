"""
Feb 2026 · Regression tests for the three fixes shipped this session:

1. ORA proactive-caveat rule injected into the ORA chat SYSTEM prompt
   (safety.py). The canary detection logic (canary.py) now cross-
   checks file mentions vs caveat markers per meta_gaps-family reply.
2. NotificationBell per-row mark-read endpoint
   (admin_health.py — POST /notifications/{notif_id}/mark-read).
3. NotificationBell sound-toggle client contract — surfaced through
   the persisted `notif_id` on new rows written by
   services/health_notifier.py.

Zero mocks — hits real Mongo directly.  No LLM calls (canary detection
logic is pure regex on a synthetic reply, so we can assert the guard
without paying the LLM tax on every test run).
"""
from __future__ import annotations

import re

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient   # noqa: E402


# ── 1. ORA proactive-caveat rule wiring ──────────────────────────────

def test_ora_system_prompt_carries_proactive_caveat_rule():
    """The AUREM_CONTEXT block must publish the proactive-caveat rule
    so the model sees it on every turn."""
    from services.ora_chat.safety import SYSTEM_PROMPT
    for marker in (
        "Proactive-caveat rule",
        "inferred from naming pattern",
        "CATASTROPHIC failure of this rule",
        "IN THE SAME RESPONSE",
    ):
        assert marker in SYSTEM_PROMPT, (
            f"proactive-caveat marker missing from ORA system prompt: {marker!r}"
        )


def test_canary_detects_caveat_missing_when_files_named():
    """Pure-logic assertion on the canary's proactive-caveat gate — no
    LLM call needed. Constructs a reply that names three .py files with
    NO caveat, and asserts the guard flags it as `proactive_ok = False`."""
    from services.ora_chat.canary import (
        _FILE_MENTION_RE, _PROACTIVE_CAVEAT_MARKERS,
    )
    bad = (
        "Sure — humare gaps yeh hain: test_iter212m88_stripe_stress.py "
        "misses the refund path, cache_orchestrator.py needs a TTL "
        "guard, frontend/src/hooks/useDB.js has a race."
    )
    files = _FILE_MENTION_RE.findall(bad)
    assert len(files) == 3, f"regex should find 3 files, got: {files}"
    low = bad.lower()
    caveat = any(m in low for m in _PROACTIVE_CAVEAT_MARKERS)
    assert not caveat, "bad reply must not trip caveat detection"
    # Emulates the row["proactive_ok"] contract inside run_canary().
    proactive_ok = (not files) or caveat
    assert proactive_ok is False


def test_canary_accepts_caveat_when_files_flagged():
    """Same three-file reply but WITH the caveat inline must pass."""
    from services.ora_chat.canary import (
        _FILE_MENTION_RE, _PROACTIVE_CAVEAT_MARKERS,
    )
    good = (
        "Gaps I'm inferring from the index (unverified — I haven't read "
        "these this turn): test_iter212m88_stripe_stress.py, "
        "cache_orchestrator.py, frontend/src/hooks/useDB.js. Want me to "
        "/read one to confirm?"
    )
    files = _FILE_MENTION_RE.findall(good)
    assert len(files) >= 3
    low = good.lower()
    caveat = any(m in low for m in _PROACTIVE_CAVEAT_MARKERS)
    assert caveat, f"good reply must trip caveat detection · low={low[:200]}"
    proactive_ok = (not files) or caveat
    assert proactive_ok is True


def test_canary_vacuous_ok_when_no_files_named():
    """A reply that names ZERO files is safest — the rule is vacuously
    satisfied regardless of caveat presence."""
    from services.ora_chat.canary import (
        _FILE_MENTION_RE, _PROACTIVE_CAVEAT_MARKERS,
    )
    minimal = "I don't have a confident code match — want me to /find first?"
    files = _FILE_MENTION_RE.findall(minimal)
    assert not files
    low = minimal.lower()
    caveat = any(m in low for m in _PROACTIVE_CAVEAT_MARKERS)
    proactive_ok = (not files) or caveat
    assert proactive_ok is True


# ── 2. NotificationBell per-row mark-read (real Mongo) ───────────────

@pytest.mark.asyncio
async def test_per_row_mark_read_targets_only_one_notification(monkeypatch):
    """Insert 2 unread rows → mark ONE by notif_id → count drops by 1,
    the other stays unread."""
    import os
    import uuid
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient

    # Real Mongo per zero-mocks rule.
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Seed 2 rows so we can prove the endpoint touches only one.
    now = datetime.now(timezone.utc).isoformat()
    id_a = uuid.uuid4().hex[:12]
    id_b = uuid.uuid4().hex[:12]
    tag_a = f"__test_a_{id_a}"
    tag_b = f"__test_b_{id_b}"
    await db.health_notifications.delete_many(
        {"check_id": {"$in": [tag_a, tag_b]}})
    await db.health_notifications.insert_many([
        {"notif_id": id_a, "check_id": tag_a, "name": "A", "category": "guard",
         "from_state": "green", "to_state": "red", "detail": "test A",
         "created_at": now, "read": False},
        {"notif_id": id_b, "check_id": tag_b, "name": "B", "category": "guard",
         "from_state": "green", "to_state": "red", "detail": "test B",
         "created_at": now, "read": False},
    ])
    try:
        # /admin/status/* is guarded by require_admin_dep — mint a real
        # admin JWT via the same helper the canary uses (zero mocks).
        from services.usage import founder_emails
        from cto_services.auth import create_token
        founder = await db.dev_users.find_one(
            {"email": {"$in": list(founder_emails())}},
            {"user_id": 1, "email": 1, "_id": 0})
        if not founder:
            founder = await db.dev_users.find_one({"is_founder": True},
                                                    {"user_id": 1, "email": 1, "_id": 0})
        assert founder, "no founder row available for admin JWT"
        tok = create_token(user_id=founder["user_id"],
                           email=founder["email"], is_admin=True)
        headers = {"Authorization": f"Bearer {tok}"}

        from server import app
        with TestClient(app) as tc:
            r = tc.post(
                f"/api/aurem-dev/admin/status/notifications/{id_a}/mark-read",
                headers=headers,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True
            assert body["modified"] == 1
            assert isinstance(body["unread_count"], int)

        # Direct Mongo re-read confirms the write.
        a = await db.health_notifications.find_one({"notif_id": id_a})
        b = await db.health_notifications.find_one({"notif_id": id_b})
        assert a and a["read"] is True,  "target row must be marked read"
        assert b and b["read"] is False, "sibling row must stay unread"
    finally:
        await db.health_notifications.delete_many(
            {"check_id": {"$in": [tag_a, tag_b]}})


@pytest.mark.asyncio
async def test_per_row_mark_read_legacy_composite_fallback():
    """Legacy rows written before Feb 2026 don't have `notif_id`; the
    endpoint must accept the composite `<check_id>|<created_at>` string
    the /notifications GET synthesises for them."""
    import os
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    ts = datetime.now(timezone.utc).isoformat()
    tag = f"__legacy_row_{ts}"
    await db.health_notifications.delete_many({"check_id": tag})
    await db.health_notifications.insert_one({
        # DELIBERATELY no notif_id — legacy path.
        "check_id": tag, "name": "legacy", "category": "guard",
        "from_state": "green", "to_state": "red", "detail": "legacy row",
        "created_at": ts, "read": False,
    })
    try:
        from services.usage import founder_emails
        from cto_services.auth import create_token
        founder = await db.dev_users.find_one(
            {"email": {"$in": list(founder_emails())}},
            {"user_id": 1, "email": 1, "_id": 0})
        if not founder:
            founder = await db.dev_users.find_one({"is_founder": True},
                                                    {"user_id": 1, "email": 1, "_id": 0})
        tok = create_token(user_id=founder["user_id"],
                           email=founder["email"], is_admin=True)
        headers = {"Authorization": f"Bearer {tok}"}

        composite = f"{tag}|{ts}"
        from server import app
        with TestClient(app) as tc:
            r = tc.post(
                f"/api/aurem-dev/admin/status/notifications/{composite}/mark-read",
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert r.json()["modified"] == 1
        row = await db.health_notifications.find_one({"check_id": tag})
        assert row and row["read"] is True
    finally:
        await db.health_notifications.delete_many({"check_id": tag})


@pytest.mark.asyncio
async def test_get_notifications_synthesises_notif_id_for_legacy_rows():
    """GET /notifications must attach a `notif_id` to every row so the
    UI can call the per-row endpoint uniformly (persisted uuid on new
    rows; composite fallback on legacy rows)."""
    import os
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    ts = datetime.now(timezone.utc).isoformat()
    tag = f"__nid_gen_{ts}"
    await db.health_notifications.delete_many({"check_id": tag})
    await db.health_notifications.insert_one({
        "check_id": tag, "name": "no-notif-id", "category": "guard",
        "from_state": "green", "to_state": "red", "detail": "",
        "created_at": ts, "read": False,
    })
    try:
        from services.usage import founder_emails
        from cto_services.auth import create_token
        founder = await db.dev_users.find_one(
            {"email": {"$in": list(founder_emails())}},
            {"user_id": 1, "email": 1, "_id": 0})
        if not founder:
            founder = await db.dev_users.find_one({"is_founder": True},
                                                    {"user_id": 1, "email": 1, "_id": 0})
        tok = create_token(user_id=founder["user_id"],
                           email=founder["email"], is_admin=True)
        headers = {"Authorization": f"Bearer {tok}"}

        from server import app
        with TestClient(app) as tc:
            r = tc.get(
                "/api/aurem-dev/admin/status/notifications?limit=50",
                headers=headers,
            )
            assert r.status_code == 200, r.text
            rows = r.json().get("notifications") or []
        hit = next((x for x in rows if x.get("check_id") == tag), None)
        assert hit, "test row must appear in /notifications output"
        assert hit["notif_id"] == f"{tag}|{ts}", (
            f"legacy row must get composite notif_id, got {hit['notif_id']!r}"
        )
    finally:
        await db.health_notifications.delete_many({"check_id": tag})
