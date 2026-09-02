"""
tests/test_before_after_preview_wiring_2026_01_09.py

END-TO-END WIRING check for the "Before/After" live-preview feature
(the pure-unit-level helper is already covered by
test_before_after_preview_2026_09_09.py — this file complements it by
verifying the wiring beyond the helper: pending-change API surface,
tasks-submit background-task registration, and direct-DB persistence
against real MongoDB).
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timezone

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback to reading from frontend/.env for local test runs.
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                break

API_PREFIX = f"{BASE_URL}/api/aurem-dev"
LOGIN_EMAIL = "test@aurem.dev"
LOGIN_PASS = "AuremTest2026!"
PROJECT_ID = "p_demo_a"      # already has preview_url set (test seed)


@pytest.fixture(scope="module")
def token():
    r = requests.post(
        f"{API_PREFIX}/auth/login",
        json={"email": LOGIN_EMAIL, "password": LOGIN_PASS},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def mongo_db():
    url = os.environ["MONGO_URL"]
    dbn = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(url)
    return client[dbn]


# ─── 1. pending-change API surface — returns before_receipts ──────────

def test_pending_change_returns_before_receipts_when_shipped(auth_headers, mongo_db):
    """When the latest task on a project has before_receipts AND state
    is shipped_not_deployed, the pending-change API must expose it."""
    task_id = f"t_TEST_{uuid.uuid4().hex[:10]}"
    receipt_key = f"deploy-receipts/{PROJECT_ID}/before-{task_id}.jpg"

    async def _seed_and_check():
        # Seed a "done" task with before_receipts.
        await mongo_db.cto_tasks.insert_one({
            "task_id": task_id,
            "project_id": PROJECT_ID,
            "user_id": "test_admin_001",
            "task": "TEST wiring — pending-change before_receipts surface",
            "files": [], "context": "",
            "status": "done",
            "files_changed_simple": ["src/pages/Home.jsx"],
            "commit_sha": "abcdef1234567890",
            "completed_at": time.time(),
            "created_at": time.time(),
            "before_receipts": {"/": receipt_key},
        })
        # Ensure deploy config exists so state resolves to
        # shipped_not_deployed (not "clean").
        cfg_id = f"TEST_cfg_{uuid.uuid4().hex[:8]}"
        await mongo_db.aurem_cto_deploy_configs.insert_one({
            "_id": cfg_id,
            "user_id": "test_admin_001",
            "project_id": PROJECT_ID,
            "configured": True,
        })
        return cfg_id

    cfg_id = asyncio.get_event_loop().run_until_complete(_seed_and_check())

    try:
        r = requests.get(
            f"{API_PREFIX}/cto/projects/{PROJECT_ID}/preview/pending-change",
            headers=auth_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        # Latest task is our seeded one (sorted by created_at desc).
        assert body["state"] == "shipped_not_deployed", body
        assert "before_receipts" in body
        assert body["before_receipts"] == {"/": receipt_key}
        assert body["task_id"] == task_id
    finally:
        async def _cleanup():
            await mongo_db.cto_tasks.delete_one({"task_id": task_id})
            await mongo_db.aurem_cto_deploy_configs.delete_one({"_id": cfg_id})
        asyncio.get_event_loop().run_until_complete(_cleanup())


def test_pending_change_returns_empty_dict_when_no_before_receipts(auth_headers, mongo_db):
    """No before_receipts on task → API returns {} (not missing key)."""
    task_id = f"t_TEST_{uuid.uuid4().hex[:10]}"

    async def _seed():
        await mongo_db.cto_tasks.insert_one({
            "task_id": task_id,
            "project_id": PROJECT_ID,
            "user_id": "test_admin_001",
            "task": "TEST no before receipts",
            "files": [], "context": "",
            "status": "done",
            "files_changed_simple": ["src/pages/About.jsx"],
            "commit_sha": "1234567890abcdef",
            "completed_at": time.time(),
            "created_at": time.time(),
        })
        cfg_id = f"TEST_cfg_{uuid.uuid4().hex[:8]}"
        await mongo_db.aurem_cto_deploy_configs.insert_one({
            "_id": cfg_id,
            "user_id": "test_admin_001",
            "project_id": PROJECT_ID,
            "configured": True,
        })
        return cfg_id

    cfg_id = asyncio.get_event_loop().run_until_complete(_seed())
    try:
        r = requests.get(
            f"{API_PREFIX}/cto/projects/{PROJECT_ID}/preview/pending-change",
            headers=auth_headers, timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "shipped_not_deployed", body
        # New field always present as empty dict, never missing.
        assert body.get("before_receipts") == {}, body
    finally:
        async def _cleanup():
            await mongo_db.cto_tasks.delete_one({"task_id": task_id})
            await mongo_db.aurem_cto_deploy_configs.delete_one({"_id": cfg_id})
        asyncio.get_event_loop().run_until_complete(_cleanup())


# ─── 2. tasks.submit + _enqueue_cto_task wire the bg capture ─────────

def test_tasks_router_wires_before_capture_bg_task():
    """Static code-wiring proof: both entry points that create a task
    (`submit_task` HTTP handler AND the shared `_enqueue_cto_task`
    helper used by the chat-handoff Mode C trigger) must register
    `capture_before_snapshot_for_task` as a background task iff the
    project has a preview_url set."""
    import inspect
    from routers.cto_projects import tasks as tasks_mod
    src = inspect.getsource(tasks_mod)
    # Two independent code paths wire the same helper.
    assert src.count("capture_before_snapshot_for_task") >= 2, (
        "Expected two call sites (submit_task + _enqueue_cto_task); "
        f"found {src.count('capture_before_snapshot_for_task')}"
    )
    # Both call sites guarded by preview_url presence.
    assert 'proj.get("preview_url")' in src
    # submit_task uses BackgroundTasks.add_task (never asyncio task
    # in the HTTP path — bg is a required param there).
    assert "bg.add_task(capture_before_snapshot_for_task" in src


# ─── 3. Live capture_before_snapshot_for_task → real Mongo persist ────

def test_capture_before_snapshot_persists_to_real_mongo(mongo_db, monkeypatch):
    """Wire the helper into real Mongo (not a FakeDB) and confirm the
    update actually lands on the task doc. Screenshot + R2 are still
    mocked (avoids external network + storage dependencies)."""
    from services import preview_capture as pc

    task_id = f"t_TEST_{uuid.uuid4().hex[:10]}"
    fake_key = f"deploy-receipts/{PROJECT_ID}/before-{task_id}.jpg"

    async def _fake_capture(url, device="phone"):
        assert url.endswith("/"), f"must probe route '/', got {url}"
        return b"fake-jpeg-bytes"

    async def _fake_upload(image_bytes, key_suffix):
        assert image_bytes == b"fake-jpeg-bytes"
        return f"deploy-receipts/{key_suffix}"

    monkeypatch.setattr(pc, "capture_screenshot", _fake_capture)
    monkeypatch.setattr(pc, "upload_receipt", _fake_upload)

    async def _run():
        await mongo_db.cto_tasks.insert_one({
            "task_id": task_id, "project_id": PROJECT_ID,
            "user_id": "test_admin_001", "status": "queued",
            "created_at": time.time(),
        })
        await pc.capture_before_snapshot_for_task(
            mongo_db, PROJECT_ID, "test_admin_001", task_id,
            "https://example.com/test-preview",
        )
        doc = await mongo_db.cto_tasks.find_one({"task_id": task_id}, {"_id": 0})
        return doc

    doc = asyncio.get_event_loop().run_until_complete(_run())
    try:
        assert doc is not None
        assert "before_receipts" in doc, doc
        assert doc["before_receipts"] == {"/": fake_key}, doc["before_receipts"]
    finally:
        async def _cleanup():
            await mongo_db.cto_tasks.delete_one({"task_id": task_id})
        asyncio.get_event_loop().run_until_complete(_cleanup())
