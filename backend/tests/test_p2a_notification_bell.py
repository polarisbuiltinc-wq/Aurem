"""
tests/test_p2a_notification_bell.py — P2-A (2026-08-28), user-facing
notification bell.

Named tests:
  t_bell_event_fires                    — emit_notification writes a
                                           real row for >=3 event types
                                           (scan_done, ship_failed,
                                           repo_revoked — the exact 3
                                           this phase wired into real
                                           call sites: loop_engine.py,
                                           fix_pipeline.py, github_app.py).
  t_bell_persistent_error_not_auto_cleared — persistent types (payment_
                                           failed/ship_failed/repo_revoked)
                                           are flagged `persistent: true`
                                           and stay unread until
                                           explicitly marked.
  t_bell_mark_read                      — mark_read flips read_at,
                                           unread_count drops.
  t_bell_renders_and_counts             — GET /notifications live
                                           endpoint returns the real
                                           shape + correct unread_count.
"""
import os
import uuid

import pytest
import pytest_asyncio
import requests
from motor.motor_asyncio import AsyncIOMotorClient

from services import notifications as notif

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[DB_NAME]
    yield database
    client.close()


@pytest_asyncio.fixture
async def test_user_id(db):
    uid = f"p2a-test-{uuid.uuid4().hex[:10]}"
    yield uid
    await db.notifications.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_bell_event_fires(db, test_user_id):
    await notif.emit_notification(db, user_id=test_user_id, type="scan_done", text="Scan complete.")
    await notif.emit_notification(db, user_id=test_user_id, type="ship_failed", text="Ship crashed.")
    await notif.emit_notification(db, user_id=test_user_id, type="repo_revoked", text="Access revoked.")
    rows = await notif.list_notifications(db, test_user_id)
    types = {r["type"] for r in rows}
    assert {"scan_done", "ship_failed", "repo_revoked"}.issubset(types)


@pytest.mark.asyncio
async def test_bell_persistent_error_not_auto_cleared(db, test_user_id):
    await notif.emit_notification(db, user_id=test_user_id, type="payment_failed", text="Card declined.")
    await notif.emit_notification(db, user_id=test_user_id, type="scan_done", text="Scan complete.")
    rows = {r["type"]: r for r in await notif.list_notifications(db, test_user_id)}
    assert rows["payment_failed"]["persistent"] is True
    assert rows["scan_done"]["persistent"] is False
    # Neither auto-clears on its own — both still unread until acted on.
    assert rows["payment_failed"]["read_at"] is None
    assert rows["scan_done"]["read_at"] is None


@pytest.mark.asyncio
async def test_bell_mark_read(db, test_user_id):
    await notif.emit_notification(db, user_id=test_user_id, type="ship_done", text="Shipped 3/3.")
    rows = await notif.list_notifications(db, test_user_id)
    assert await notif.unread_count(db, test_user_id) == 1
    ok = await notif.mark_read(db, test_user_id, rows[0]["notif_id"])
    assert ok is True
    assert await notif.unread_count(db, test_user_id) == 0
    # Idempotent — marking an already-read row again is a no-op, not an error.
    ok2 = await notif.mark_read(db, test_user_id, rows[0]["notif_id"])
    assert ok2 is False


def test_bell_renders_and_counts():
    r = requests.post(f"{API}/auth/login", json={"email": "test@aurem.dev", "password": "AuremTest2026!"}, timeout=15)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    r2 = requests.get(f"{API}/notifications", headers={"Authorization": f"Bearer {token}"}, timeout=15)
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert data["ok"] is True
    assert isinstance(data["items"], list)
    assert isinstance(data["unread_count"], int)
    assert data["unread_count"] == sum(1 for i in data["items"] if not i.get("read_at"))
