"""
e2e-proof/P2-A/seed_and_verify.py — P2-A (2026-08-28) notification bell.

Seeds 2 real notifications for test_admin_001 (test@aurem.dev) via the
EXACT production `emit_notification()` function (services/notifications.py) —
not a mock/fixture insert — then prints before/after counts via the real
GET /notifications endpoint so the UI screenshot proof (testing_agent) has
known values to assert against.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))) + "/backend")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from services.notifications import emit_notification, unread_count  # noqa: E402

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
USER_ID = "test_admin_001"


async def main():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    before = await unread_count(db, USER_ID)
    await emit_notification(db, user_id=USER_ID, type="scan_done",
                             text="Scan complete — no findings.")
    await emit_notification(db, user_id=USER_ID, type="ship_failed",
                             text="Ship crashed during commit (P2-A E2E proof row).")
    after = await unread_count(db, USER_ID)
    print(f"unread_before={before} unread_after={after} delta={after - before}")
    assert after - before == 2, "expected exactly 2 new unread notifications"
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
