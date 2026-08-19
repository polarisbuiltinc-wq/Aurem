"""
test_iter67_recurring_pattern_fixes.py

Locks in fixes for RECURRING_ISSUES.md patterns #1 and #2 with REAL
behavioral tests (not grep-lock).

2026-08-19 — rewritten after a security/codebase audit PROVED the old
grep-lock version was fake: it only checked that certain literal
strings existed anywhere in the source file. Swapping which message
fired for which branch (re-introducing the exact original bug) still
passed 3/3. See /app/memory/CODEBASE_AUDIT.md §"bugs never recur" for
the reproduction. These patterns are now migrated into the structured
`ora_regression_patterns` registry (services/ora_fix_learning.py) —
see test_regression_registry.py for that layer's own tests.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from main import app
from services.orchestrator import build_timeout_message

pytestmark = pytest.mark.asyncio


def _db():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


# ── Pattern #2 — real behavioral test of the extracted decision fn ────

def test_timeout_message_slow_api_below_three_tool_calls():
    content, slow_api = build_timeout_message(2, 90, "recap")
    assert slow_api is True
    assert "Model API was slow" in content
    assert "I cut myself off" not in content


def test_timeout_message_genuine_loop_at_or_above_three_tool_calls():
    content, slow_api = build_timeout_message(3, 90, "recap")
    assert slow_api is False
    assert "I cut myself off" in content
    assert "Model API was slow" not in content


def test_timeout_message_boundary_is_exactly_three():
    # tool_count == 3 must NOT be treated as slow-API (< 3, not <= 3).
    _, slow_at_2 = build_timeout_message(2, 90, "x")
    _, slow_at_3 = build_timeout_message(3, 90, "x")
    assert slow_at_2 is True
    assert slow_at_3 is False


# ── Pattern #1 — real end-to-end test via the actual HTTP route ───────

@pytest.fixture
async def failed_task_fixture(monkeypatch):
    """Real fixture: inserts a genuinely-failed task + its parent
    project, hits the real /retry endpoint over HTTP, and inspects the
    REAL new row written to Mongo — proves the wiring, not just that
    strings exist. `_run_task` (the actual LLM+GitHub worker) is
    stubbed so this test never makes real external calls."""
    import routers.cto_projects as cto_projects_mod

    async def _noop_run_task(*args, **kwargs):
        return None
    monkeypatch.setattr(cto_projects_mod, "_run_task", _noop_run_task)

    db = _db()
    user_id = f"pat1_test_{uuid.uuid4().hex[:8]}"
    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    task_id = f"t_{uuid.uuid4().hex[:8]}"
    email = f"{user_id}@aurem.test"

    await db.dev_users.insert_one({
        "user_id": user_id, "email": email, "tier": "founder",
        "tokens_remaining": 999999, "created_at": time.time(),
    })
    await db.cto_projects.insert_one({
        "project_id": project_id, "user_id": user_id,
        "name": "fixture project", "created_at": time.time(),
    })
    await db.cto_tasks.insert_one({
        "task_id": task_id, "user_id": user_id, "project_id": project_id,
        "task": "implement worker.py", "files": ["worker.py"],
        "context": "", "status": "failed",
        "error": "AI returned suspect edits (refusing to push): "
                 "backend/pillars/command_hub/worker.py — empty file body",
        "steps": [{"step": "Vanguard rejected: empty file body",
                   "status": "error", "ts": time.time()}],
        "created_at": time.time(),
    })

    from cto_services.auth import create_token
    token = create_token(user_id, email, False)

    yield {"token": token, "task_id": task_id, "user_id": user_id}

    await db.dev_users.delete_one({"user_id": user_id})
    await db.cto_projects.delete_one({"project_id": project_id})
    await db.cto_tasks.delete_many({"user_id": user_id})


async def test_retry_endpoint_carries_real_failure_into_new_task(failed_task_fixture):
    db = _db()
    f = failed_task_fixture
    with TestClient(app) as c:
        r = c.post(
            f"/api/aurem-dev/cto/tasks/{f['task_id']}/retry",
            headers={"Authorization": f"Bearer {f['token']}"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["carried_failure_context"] is True
    new_task_id = body["task_id"]
    assert new_task_id != f["task_id"]

    new_task = await db.cto_tasks.find_one({"task_id": new_task_id})
    assert new_task is not None, "retry must actually insert a new task row"
    ctx = new_task["context"]
    # The REAL previous failure text must be carried forward — not a
    # generic "please retry" — proving the augmentation actually ran.
    assert "empty file body" in ctx
    assert "Do NOT repeat that failure" in ctx
    await db.cto_tasks.delete_one({"task_id": new_task_id})


async def test_retry_endpoint_rejects_non_failed_task(failed_task_fixture):
    db = _db()
    f = failed_task_fixture
    await db.cto_tasks.update_one(
        {"task_id": f["task_id"]}, {"$set": {"status": "done"}},
    )
    with TestClient(app) as c:
        r = c.post(
            f"/api/aurem-dev/cto/tasks/{f['task_id']}/retry",
            headers={"Authorization": f"Bearer {f['token']}"},
        )
    assert r.status_code == 400


# ── Sanity — the memory doc still exists (kept from the original) ────

def test_recurring_issues_doc_still_present():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "memory",
                        "RECURRING_ISSUES.md")
    assert os.path.exists(path), "RECURRING_ISSUES.md must never be deleted"
    body = open(path, encoding="utf-8").read()
    for name in ("Pattern #1", "Pattern #2", "Pattern #5"):
        assert name in body


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
