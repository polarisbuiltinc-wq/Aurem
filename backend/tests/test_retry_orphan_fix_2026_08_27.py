"""Live acceptance test for the 2026-08-27 orphaned-task fix in
`POST /api/aurem-dev/cto/tasks/{task_id}/retry`.

Founder acceptance bar: when the GitHub App token mint fails, the
retry endpoint must respond 403 AND leave ZERO new task docs in Mongo.

We seed a real cto_projects doc with auth_method='github_app' but no
installation_id, so `get_repo_token_or_error()` raises
`app_installation_missing` locally (no network call needed). We hit the
real live HTTP endpoint via the preview URL and query real Mongo to
verify no orphan doc was inserted.

Also covers the happy-path regression by monkeypatching
`get_repo_token_or_error` to succeed (via a second seeded project) and
confirming a new task doc IS inserted — via the direct in-process
function call, since we can't easily mock across an HTTP boundary.
"""
from __future__ import annotations

import os
import time
import uuid
import asyncio

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # fall back to frontend/.env inline read for standalone runs
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
                    break
    except FileNotFoundError:
        pass
assert BASE_URL, "REACT_APP_BACKEND_URL required"

API = f"{BASE_URL}/api/aurem-dev"
TEST_EMAIL = "test@aurem.dev"
TEST_PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def auth_token():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def user_id(auth_token):
    r = requests.get(
        f"{API}/auth/me",
        headers={"Authorization": f"Bearer {auth_token}"},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    return r.json().get("user_id") or r.json().get("user", {}).get("user_id")


@pytest.fixture(scope="module")
def db():
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    dbname = os.environ.get("DB_NAME", "aurem_dev")
    client = AsyncIOMotorClient(mongo_url)
    return client[dbname]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestRetryOrphanFix:
    """403 on token-mint failure must not create a new cto_tasks doc."""

    def test_forced_token_failure_creates_no_orphan(
        self, auth_token, user_id, db
    ):
        assert user_id, "test user_id unresolved"

        project_id = "p_TEST_orphanfix_" + uuid.uuid4().hex[:8]
        old_task_id = "t_TEST_orphanfix_" + uuid.uuid4().hex[:8]

        # Seed a project with auth_method=github_app but NO installation_id
        # → get_repo_token_or_error() will raise app_installation_missing.
        proj_doc = {
            "project_id": project_id,
            "user_id": user_id,
            "name": "TEST_orphan_fix_project",
            "repo_url": "https://github.com/tjsandhu/does-not-matter",
            "repo_full_name": "tjsandhu/does-not-matter",
            "auth_method": "github_app",
            # installation_id intentionally omitted
            "created_at": time.time(),
        }
        old_task_doc = {
            "task_id": old_task_id,
            "user_id": user_id,
            "project_id": project_id,
            "task": "TEST orphan fix — original failed task",
            "files": [],
            "context": "",
            "status": "failed",
            "error": "TEST seeded failure",
            "created_at": time.time(),
        }

        try:
            _run(db.cto_projects.insert_one(dict(proj_doc)))
            _run(db.cto_tasks.insert_one(dict(old_task_doc)))

            # Count tasks for this project BEFORE retry
            before_count = _run(
                db.cto_tasks.count_documents(
                    {"project_id": project_id, "user_id": user_id}
                )
            )
            assert before_count == 1, f"expected 1 seeded task, got {before_count}"

            # Hit the REAL live HTTP endpoint
            r = requests.post(
                f"{API}/cto/tasks/{old_task_id}/retry",
                headers={"Authorization": f"Bearer {auth_token}"},
                timeout=20,
            )
            print(f"[retry] status={r.status_code} body={r.text[:400]}")

            # (a) 403 GitHub App auth error
            assert r.status_code == 403, (
                f"expected 403 on token-mint failure, got {r.status_code}: {r.text}"
            )
            body = r.text.lower()
            assert (
                "github app auth" in body
                or "app_installation_missing" in body
                or "installation" in body
            ), f"unexpected 403 body: {r.text}"

            # (b) No new task doc created — count must be unchanged
            after_count = _run(
                db.cto_tasks.count_documents(
                    {"project_id": project_id, "user_id": user_id}
                )
            )
            assert after_count == before_count, (
                f"ORPHAN LEAK: task count went {before_count} -> "
                f"{after_count} after failed retry. "
                f"Fix regressed — new 'queued' doc left behind."
            )

            # (c) Original failed task untouched
            still_old = _run(
                db.cto_tasks.find_one(
                    {"task_id": old_task_id, "user_id": user_id}
                )
            )
            assert still_old is not None
            assert still_old.get("status") == "failed"
            assert still_old.get("error") == "TEST seeded failure"
        finally:
            # cleanup
            _run(db.cto_tasks.delete_many(
                {"project_id": project_id, "user_id": user_id}
            ))
            _run(db.cto_projects.delete_many(
                {"project_id": project_id, "user_id": user_id}
            ))

    def test_happy_path_retry_still_works(self, auth_token, user_id, db):
        """Happy-path regression: with a valid token mint, retry
        succeeds and DOES insert a new task doc. We call the REAL
        `retry_task` coroutine in-process (real FastAPI route function,
        real Mongo) with `services.pat_vault.get_repo_token_or_error`
        monkeypatched to succeed and `_run_task` no-op'd so no worker
        thread actually runs. HTTP-level patching across the live
        preview process is not possible, so this is the highest
        realism achievable for the success-path mock (route body still
        runs unchanged)."""
        from unittest.mock import patch
        from routers import cto_projects as router_mod
        from cto_services import db as _dbmod

        # Wire router's require_db() to our motor DB
        _dbmod.set_db(db)
        from fastapi import BackgroundTasks

        project_id = "p_TEST_happy_" + uuid.uuid4().hex[:8]
        old_task_id = "t_TEST_happy_" + uuid.uuid4().hex[:8]
        proj_doc = {
            "project_id": project_id,
            "user_id": user_id,
            "name": "TEST_happy_path_project",
            "repo_url": "https://github.com/tjsandhu/does-not-matter",
            "repo_full_name": "tjsandhu/does-not-matter",
            "auth_method": "github_app",
            "installation_id": 12345,
            "created_at": time.time(),
        }
        old_task_doc = {
            "task_id": old_task_id,
            "user_id": user_id,
            "project_id": project_id,
            "task": "TEST happy path original failed task",
            "files": [],
            "context": "",
            "status": "failed",
            "error": "TEST seeded failure for happy path",
            "created_at": time.time(),
        }

        _run(db.cto_projects.insert_one(dict(proj_doc)))
        _run(db.cto_tasks.insert_one(dict(old_task_doc)))

        try:
            # Patch the token minter and background scheduling in the
            # router so the insert path runs but no real work happens.
            async def _fake_token(_project):
                return "gha_TEST_faketoken", None, None

            async def _fake_run_task(*a, **kw):
                return None

            with patch(
                "services.pat_vault.get_repo_token_or_error",
                side_effect=_fake_token,
            ), patch.object(
                router_mod, "_run_task", new=_fake_run_task
            ):
                # Call the real endpoint function in-process
                bg = BackgroundTasks()
                result = _run(router_mod.retry_task(
                    task_id=old_task_id,
                    bg=bg,
                    authorization=f"Bearer {auth_token}",
                ))
                print(f"[happy retry] result={result}")
                assert isinstance(result, dict)
                new_task_id = result.get("task_id")
                assert new_task_id and new_task_id.startswith("t_")
                # verify new doc persisted
                new_doc = _run(
                    db.cto_tasks.find_one(
                        {"task_id": new_task_id, "user_id": user_id}
                    )
                )
                assert new_doc is not None
                assert new_doc.get("status") == "queued"
                assert new_doc.get("retry_of") == old_task_id
                assert new_doc.get("project_id") == project_id
        finally:
            _run(db.cto_tasks.delete_many(
                {"project_id": project_id, "user_id": user_id}
            ))
            _run(db.cto_projects.delete_many(
                {"project_id": project_id, "user_id": user_id}
            ))
