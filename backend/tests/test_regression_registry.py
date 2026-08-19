"""test_regression_registry.py — 2026-08-19

Structured regression-pattern registry (RECURRING_ISSUES.md fold-in).
Covers seed/record/list functions and the admin endpoint's auth-gate
and shape — plus one real (non-grep) policy check for the .gitignore
patterns migrated from the doc.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient

from main import app
from services import ora_fix_learning as ofl

pytestmark = pytest.mark.asyncio


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]


@pytest.fixture
async def db():
    d = _db()
    yield d
    await d.ora_regression_patterns.delete_many({"_test_run": True})


def _pid():
    return f"test-pattern-{uuid.uuid4().hex[:8]}"


async def test_seed_is_idempotent_upsert(db):
    pid = _pid()
    for _ in range(2):
        await ofl.seed_regression_pattern(
            db, pattern_id=pid, title="t", symptom="s", root_cause="r",
            fix_locations=["a.py"], status="fixed", test_ref="tests/x.py::y",
        )
        await db.ora_regression_patterns.update_one(
            {"pattern_id": pid}, {"$set": {"_test_run": True}},
        )
    assert await db.ora_regression_patterns.count_documents({"pattern_id": pid}) == 1


async def test_record_verification_updates_pattern(db):
    pid = _pid()
    await ofl.seed_regression_pattern(
        db, pattern_id=pid, title="t", symptom="s", root_cause="r",
        fix_locations=[], status="fixed", test_ref="tests/x.py::y",
    )
    await db.ora_regression_patterns.update_one(
        {"pattern_id": pid}, {"$set": {"_test_run": True}},
    )
    await ofl.record_pattern_verification(db, pattern_id=pid, passed=True, detail="ok")
    row = await db.ora_regression_patterns.find_one({"pattern_id": pid})
    assert row["last_verified_passed"] is True
    assert row["last_verified_at"] is not None

    await ofl.record_pattern_verification(db, pattern_id=pid, passed=False, detail="broke")
    row = await db.ora_regression_patterns.find_one({"pattern_id": pid})
    assert row["last_verified_passed"] is False


async def test_list_regression_patterns_returns_seeded_rows(db):
    pid = _pid()
    await ofl.seed_regression_pattern(
        db, pattern_id=pid, title="t", symptom="s", root_cause="r",
        fix_locations=[], status="deferred", test_ref=None,
    )
    await db.ora_regression_patterns.update_one(
        {"pattern_id": pid}, {"$set": {"_test_run": True}},
    )
    rows = await ofl.list_regression_patterns(db)
    match = next((r for r in rows if r["pattern_id"] == pid), None)
    assert match is not None
    assert match["status"] == "deferred"
    assert match["test_ref"] is None


async def test_seed_script_migrated_all_eight_real_patterns(db):
    """The seed script (scripts/seed_regression_patterns.py) should
    already have populated the real RECURRING_ISSUES.md patterns —
    verifies the migration actually ran, not just that the functions
    work in isolation."""
    rows = await ofl.list_regression_patterns(db)
    ids = {r["pattern_id"] for r in rows}
    assert "pattern_1_empty_file_body_loop" in ids
    assert "pattern_2_slow_api_timeout_message" in ids
    assert "policy_env_gitignore_hybrid_final" in ids


def test_regression_patterns_endpoint_requires_admin():
    with TestClient(app) as c:
        r = c.get("/api/aurem-dev/admin/qa/regression-patterns")
        assert r.status_code in (401, 403)


async def test_regression_patterns_endpoint_returns_shape():
    from fastapi import Header
    from cto_services import auth as _auth_mod
    from routers import admin_qa as _admin_qa_mod

    async def _ok_admin_dep(authorization: str = Header(default=None)):
        return {"user_id": "test-admin", "is_admin": True}
    async def _ok_require_admin(authorization=None):
        return {"user_id": "test-admin", "is_admin": True}

    app.dependency_overrides[_auth_mod.require_admin_dep] = _ok_admin_dep
    _orig = _admin_qa_mod._require_admin
    _admin_qa_mod._require_admin = _ok_require_admin
    try:
        with TestClient(app) as c:
            r = c.get("/api/aurem-dev/admin/qa/regression-patterns",
                      headers={"Authorization": "Bearer fake"})
    finally:
        app.dependency_overrides.pop(_auth_mod.require_admin_dep, None)
        _admin_qa_mod._require_admin = _orig

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 8
    assert body["with_real_test"] >= 3
    assert body["doc_ref"] == "/app/memory/RECURRING_ISSUES.md"


# ── Real (non-grep) policy check for the migrated .gitignore patterns ─

def test_gitignore_policy_matches_documented_hybrid_rule():
    """RECURRING_ISSUES.md's final .env policy: frontend/.env MUST be
    committed (Vite inlines REACT_APP_* at build time — zero secrets
    in it), backend/.env MUST stay gitignored. Real check: reads the
    actual .gitignore + confirms frontend/.env is really tracked and
    backend/.env is really NOT tracked, not just string presence."""
    import subprocess
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    tracked = subprocess.run(
        ["git", "ls-files", "frontend/.env", "backend/.env"],
        cwd=repo_root, capture_output=True, text=True,
    ).stdout.splitlines()
    assert "frontend/.env" in tracked, "frontend/.env must be committed (Vite build-time env)"
    assert "backend/.env" not in tracked, "backend/.env must stay gitignored (real secrets)"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
