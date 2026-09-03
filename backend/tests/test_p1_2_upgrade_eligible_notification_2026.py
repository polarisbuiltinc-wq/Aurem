"""R3 P1-2 (overnight round) — `upgrade_eligible` notification, unit test.

Verifies assert_can_fix() emits a deduped `upgrade_eligible` notification
the FIRST time a user hits their monthly quota wall, and does NOT emit a
second one for the same user in the same month (no bell spam).
"""
import time
from unittest.mock import AsyncMock, patch

import pytest

from services import scan_fix_quota


class _FakeCollection:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query):
        return self.docs.get(query.get("_id"))

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = doc


class _FakeDB:
    def __init__(self):
        self.notif_dedupe = _FakeCollection()


@pytest.mark.asyncio
async def test_upgrade_eligible_emitted_once_per_month():
    fake_db = _FakeDB()
    user = {"user_id": f"u_test_{int(time.time())}"}

    with patch.object(scan_fix_quota, "require_db", return_value=fake_db), \
         patch.object(scan_fix_quota, "get_fix_quota", new=AsyncMock(return_value={
             "tier": "starter", "fix_tools": ["vanguard-scan"], "bulk_fix": False,
             "monthly_task_limit": 10, "tasks_remaining": 0, "is_unlimited": False,
         })), \
         patch("services.notifications.emit_notification", new=AsyncMock()) as emit_mock:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc1:
            await scan_fix_quota.assert_can_fix(user, "vanguard-scan", count=1)
        assert exc1.value.status_code == 402
        assert emit_mock.await_count == 1
        assert emit_mock.await_args.kwargs["type"] == "upgrade_eligible"

        # Second hit, same user, same month — must NOT re-notify.
        with pytest.raises(HTTPException):
            await scan_fix_quota.assert_can_fix(user, "vanguard-scan", count=1)
        assert emit_mock.await_count == 1
