"""test_fabrication_learning_loop.py — 2026-08

Fabrication learning loop (approved scope: per-project + per-route
only, caution injected at >=3 incidents in 30 days).

Covers:
  1. record_fabrication_incident persists a row with the right
     normalized fields (project_id defaults to "home", signature is
     stable/sorted, best-effort on bad input).
  2. recall_fabrication_caution returns "" below the 3-incident
     threshold and a non-empty caution string at/above it — scoped
     strictly to the same (source, project_id, route); a different
     project or route never sees another bucket's incidents.
  3. get_recurring_fabrication_patterns aggregates correctly and
     `caution_active` mirrors the same >=3 threshold.
  4. GET /admin/qa/fabrication-patterns requires admin auth and
     returns the aggregated shape.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

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
    # Cleanup — never leave test rows behind in the shared collection.
    await d.ora_fabrication_incidents.delete_many({"_test_run": True})


def _proj():
    return f"test-proj-{uuid.uuid4().hex[:8]}"


async def _record(db, *, project_id, route, source="customer_chat",
                   paths=None, corrected=True):
    row_paths = paths or ["services/fake_module_x.py"]
    await ofl.record_fabrication_incident(
        db, source=source, project_id=project_id, route=route,
        user_prompt="does fake_module_x exist?",
        unverified_paths=row_paths, corrected=corrected,
        user_id="test-user",
    )
    # Tag the just-inserted row so the fixture can clean it up.
    await db.ora_fabrication_incidents.update_many(
        {"project_id": project_id, "route": route}, {"$set": {"_test_run": True}},
    )


# ── record_fabrication_incident ─────────────────────────────────────

async def test_record_persists_row_with_normalized_fields(db):
    pid = _proj()
    await _record(db, project_id=pid, route="chat_stream",
                  paths=["Services/Foo.PY", "services/foo.py"])
    row = await db.ora_fabrication_incidents.find_one({"project_id": pid})
    assert row is not None
    assert row["source"] == "customer_chat"
    assert row["route"] == "chat_stream"
    assert row["corrected"] is True
    assert row["signature"] == ofl._fabrication_signature(
        ["Services/Foo.PY", "services/foo.py"]
    )


async def test_record_defaults_project_id_to_home(db):
    await ofl.record_fabrication_incident(
        db, source="admin_ora_chat", project_id=None, route="A",
        user_prompt="x", unverified_paths=["a.py"], corrected=False,
    )
    row = await db.ora_fabrication_incidents.find_one(
        {"source": "admin_ora_chat", "route": "A"}, sort=[("created_at", -1)],
    )
    assert row["project_id"] == "home"
    await db.ora_fabrication_incidents.delete_one({"_id": row["_id"]})


async def test_record_noop_on_empty_paths_or_none_db(db):
    pid = _proj()
    await ofl.record_fabrication_incident(
        db, source="customer_chat", project_id=pid, route="chat_stream",
        user_prompt="x", unverified_paths=[], corrected=True,
    )
    assert await db.ora_fabrication_incidents.count_documents({"project_id": pid}) == 0
    # None db must never raise.
    await ofl.record_fabrication_incident(
        None, source="customer_chat", project_id=pid, route="chat_stream",
        user_prompt="x", unverified_paths=["a.py"], corrected=True,
    )


def test_signature_is_stable_regardless_of_order_and_case():
    a = ofl._fabrication_signature(["b.py", "A.py"])
    b = ofl._fabrication_signature(["a.PY", "B.py"])
    assert a == b


# ── recall_fabrication_caution — threshold + scope isolation ────────

async def test_recall_returns_empty_below_threshold(db):
    pid = _proj()
    for _ in range(2):
        await _record(db, project_id=pid, route="chat_stream")
    caution = await ofl.recall_fabrication_caution(
        db, source="customer_chat", project_id=pid, route="chat_stream",
    )
    assert caution == ""


async def test_recall_returns_caution_at_threshold(db):
    pid = _proj()
    for _ in range(3):
        await _record(db, project_id=pid, route="chat_stream")
    caution = await ofl.recall_fabrication_caution(
        db, source="customer_chat", project_id=pid, route="chat_stream",
    )
    assert caution != ""
    assert "LEARNED CAUTION" in caution
    assert "fake_module_x.py" in caution


async def test_recall_scoped_to_same_project_only(db):
    """Per-project isolation — the approved scope explicitly forbids
    cross-project matching to protect customer data boundaries."""
    pid_a = _proj()
    pid_b = _proj()
    for _ in range(3):
        await _record(db, project_id=pid_a, route="chat_stream")
    caution_a = await ofl.recall_fabrication_caution(
        db, source="customer_chat", project_id=pid_a, route="chat_stream",
    )
    caution_b = await ofl.recall_fabrication_caution(
        db, source="customer_chat", project_id=pid_b, route="chat_stream",
    )
    assert caution_a != ""
    assert caution_b == "", "incidents leaked across project boundary"


async def test_recall_scoped_to_same_route_only(db):
    pid = _proj()
    for _ in range(3):
        await _record(db, project_id=pid, route="chat_stream")
    caution_other_route = await ofl.recall_fabrication_caution(
        db, source="customer_chat", project_id=pid, route="other_route",
    )
    assert caution_other_route == ""


async def test_recall_respects_since_days_window(db):
    pid = _proj()
    old_ts = time.time() - (40 * 86400)
    for _ in range(3):
        await ofl.record_fabrication_incident(
            db, source="customer_chat", project_id=pid, route="chat_stream",
            user_prompt="x", unverified_paths=["old.py"], corrected=True,
        )
    await db.ora_fabrication_incidents.update_many(
        {"project_id": pid}, {"$set": {"created_at": old_ts, "_test_run": True}},
    )
    caution = await ofl.recall_fabrication_caution(
        db, source="customer_chat", project_id=pid, route="chat_stream",
        since_days=30,
    )
    assert caution == "", "incidents older than the window must not count"


async def test_recall_fails_open_on_bad_db():
    class _Boom:
        ora_fabrication_incidents = None
    assert await ofl.recall_fabrication_caution(
        None, source="customer_chat", project_id="p", route="r",
    ) == ""


# ── get_recurring_fabrication_patterns — admin aggregation ──────────

async def test_recurring_patterns_aggregates_by_bucket(db):
    pid = _proj()
    for _ in range(4):
        await _record(db, project_id=pid, route="chat_stream", corrected=True)
    patterns = await ofl.get_recurring_fabrication_patterns(db, since_days=30)
    match = next((p for p in patterns if p["project_id"] == pid), None)
    assert match is not None
    assert match["count"] == 4
    assert match["corrected"] == 4
    assert match["caution_active"] is True
    assert "services/fake_module_x.py" in match["sample_paths"]


async def test_recurring_patterns_below_three_not_caution_active(db):
    pid = _proj()
    await _record(db, project_id=pid, route="chat_stream")
    patterns = await ofl.get_recurring_fabrication_patterns(db, since_days=30)
    match = next((p for p in patterns if p["project_id"] == pid), None)
    assert match is not None
    assert match["caution_active"] is False


# ── admin endpoint ───────────────────────────────────────────────────

def test_fabrication_patterns_endpoint_requires_admin():
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as c:
        r = c.get("/api/aurem-dev/admin/qa/fabrication-patterns")
        assert r.status_code in (401, 403)


async def test_fabrication_patterns_endpoint_returns_shape(db):
    from fastapi import Header
    from fastapi.testclient import TestClient
    from main import app
    from cto_services import auth as _auth_mod
    from routers import admin_qa as _admin_qa_mod

    async def _ok_admin_dep(authorization: str = Header(default=None)):
        return {"user_id": "test-admin", "is_admin": True}

    async def _ok_require_admin(authorization=None):
        return {"user_id": "test-admin", "is_admin": True}

    app.dependency_overrides[_auth_mod.require_admin_dep] = _ok_admin_dep
    _orig = _admin_qa_mod._require_admin
    _admin_qa_mod._require_admin = _ok_require_admin

    pid = _proj()
    for _ in range(3):
        await _record(db, project_id=pid, route="chat_stream")

    try:
        with TestClient(app) as c:
            r = c.get(
                "/api/aurem-dev/admin/qa/fabrication-patterns",
                headers={"Authorization": "Bearer fake"},
            )
    finally:
        app.dependency_overrides.pop(_auth_mod.require_admin_dep, None)
        _admin_qa_mod._require_admin = _orig

    assert r.status_code == 200, r.text
    body = r.json()
    assert "patterns" in body
    assert "recurring_count" in body
    assert body["recurring_count"] >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
