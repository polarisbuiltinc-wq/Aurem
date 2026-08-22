"""
tests/test_findings_teaser_bridge_2026_08_23.py

Phase 1 backend contract tests for the Findings-to-Fix Bridge teaser
strip (routers/findings.py::backlog_list `matched` field).

Covers:
  1. `matched` returns full finding docs (with rule_id — required by
     the bulk-fix pipeline's LLM re-validation step, never carried by
     the lightweight chat-stream `findings_saved` payload).
  2. IDOR — a caller who doesn't own the project gets 403, not 200
     with someone else's findings.
  3. IDOR — a nonexistent project_id gets 404, not a leaked empty ok.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi import HTTPException


async def _seed_project(db, project_id: str, owner_id: str) -> None:
    await db.cto_projects.insert_one({
        "project_id": project_id, "user_id": owner_id,
        "github_owner": "octocat", "github_repo": "Hello-World",
        "github_branch": "main",
    })


async def _seed_finding(db, *, user_id: str, project_id: str,
                        finding_id: str, rule_id: str) -> None:
    await db.cto_open_findings.insert_one({
        "user_id": user_id, "project_id": project_id,
        "finding_id": finding_id, "rule_id": rule_id,
        "severity": "critical", "status": "open",
        "file": "auth.py", "line": 42,
        "title": "Hardcoded JWT secret fallback",
        "message": "JWT secret falls back to a hardcoded default.",
        "fix_hint": "Require JWT_SECRET env var.",
        "scanner": "ora_chat_audit", "exposure_count": 0,
        "first_seen_at": None, "last_seen_at": None,
    })


def _db():
    # Always build a FRESH Motor client bound to *this* test's event
    # loop. pytest-asyncio (asyncio_mode=auto) gives each test its own
    # loop; reusing a cached global client from a prior test's now-closed
    # loop raises "Event loop is closed" on the very next query.
    from cto_services.db import set_db
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "aurem_dev")]
    set_db(db)
    return db


@pytest.mark.asyncio
async def test_backlog_matched_returns_rule_id_for_teaser():
    from routers.findings import backlog_list

    db = _db()
    owner_id = "pytest_teaser_owner"
    project_id = f"pytest_teaser_proj_{int(time.time())}"
    finding_id = f"ora_chat_audit::auth.py:42:hardcoded-jwt-secret-{int(time.time())}"
    await _seed_project(db, project_id, owner_id)
    await _seed_finding(db, user_id=owner_id, project_id=project_id,
                        finding_id=finding_id, rule_id="hardcoded-jwt-secret")

    with patch("routers.findings.current_dev",
               return_value={"user_id": owner_id}):
        result = await backlog_list(project_id=project_id, ids=finding_id,
                                    authorization="Bearer fake")

    assert result["ok"] is True
    matched = result.get("matched") or []
    assert len(matched) == 1
    row = matched[0]
    assert row["finding_id"] == finding_id
    assert row["id"] == finding_id
    assert row["rule_id"] == "hardcoded-jwt-secret"
    assert row["file"] == "auth.py"
    assert row["line"] == 42
    assert row["severity"] == "critical"
    assert result["tracked_status"][finding_id] == "open"

    await db.cto_open_findings.delete_many({"project_id": project_id})
    await db.cto_projects.delete_many({"project_id": project_id})


@pytest.mark.asyncio
async def test_backlog_idor_wrong_owner_gets_403():
    from routers.findings import backlog_list

    db = _db()
    owner_id = "pytest_teaser_owner_b"
    attacker_id = "pytest_teaser_attacker"
    project_id = f"pytest_teaser_proj_idor_{int(time.time())}"
    await _seed_project(db, project_id, owner_id)

    with patch("routers.findings.current_dev",
               return_value={"user_id": attacker_id}):
        with pytest.raises(HTTPException) as exc_info:
            await backlog_list(project_id=project_id, ids=None,
                               authorization="Bearer fake")
    assert exc_info.value.status_code == 403

    await db.cto_projects.delete_many({"project_id": project_id})


@pytest.mark.asyncio
async def test_backlog_idor_nonexistent_project_gets_404():
    from routers.findings import backlog_list

    _db()
    with patch("routers.findings.current_dev",
               return_value={"user_id": "pytest_teaser_owner_c"}):
        with pytest.raises(HTTPException) as exc_info:
            await backlog_list(project_id="pytest_does_not_exist_project",
                               ids=None, authorization="Bearer fake")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_backlog_matched_excludes_resolved_findings():
    """A finding marked status=fixed must NOT appear in `matched` or
    show as 'open' in tracked_status — this is what makes the teaser
    disappear the moment a fix lands (real external resolution, not
    just the fix-pipeline's own write-back)."""
    from routers.findings import backlog_list

    db = _db()
    owner_id = "pytest_teaser_owner_d"
    project_id = f"pytest_teaser_proj_resolved_{int(time.time())}"
    finding_id = f"ora_chat_audit::x.py:1:resolved-test-{int(time.time())}"
    await _seed_project(db, project_id, owner_id)
    await _seed_finding(db, user_id=owner_id, project_id=project_id,
                        finding_id=finding_id, rule_id="resolved-test")
    await db.cto_open_findings.update_one(
        {"finding_id": finding_id},
        {"$set": {"status": "fixed"}},
    )

    with patch("routers.findings.current_dev",
               return_value={"user_id": owner_id}):
        result = await backlog_list(project_id=project_id, ids=finding_id,
                                    authorization="Bearer fake")

    assert result["matched"] == []
    assert result["tracked_status"][finding_id] == "resolved"

    await db.cto_open_findings.delete_many({"project_id": project_id})
    await db.cto_projects.delete_many({"project_id": project_id})
