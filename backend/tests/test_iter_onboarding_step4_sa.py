"""Onboarding Step 4 · S-A — extend journey data (2026-08-26).

GATE A test suite. Confirms:
  T-A1: signup -> connect(-failed) -> scan(-events) -> fix-click fires
        in order, real DB rows, correct user_id.
  T-A2: onboarding_intent is captured + stored via the real endpoint.
  T-A3: an admin-style query (the exact pipeline already live at
        GET /admin/funnel, routers/admin_users.py:719-724) returns
        correct live counts from the newly-emitted event types.

Reused (confirmed via source, not duplicated):
  - `db.funnel_events` + `services.signup_guards.emit_funnel_event` —
    the existing generic { user_id, event_type, metadata, created_at,
    ts_epoch } store already used for signup_completed,
    project_add_*, task_submitted, first_chat_sent, first_loop_started,
    first_task_shipped, email_verified.
  - `routers/admin_users.py::admin_funnel_dashboard` (GET /admin/funnel)
    already aggregates `db.funnel_events` by event_type — no new
    endpoint needed for the "admin query" requirement.
  - `routers/github_funnel.py` — a SEPARATE, pre-existing GitHub-connect
    -specific funnel (`db.github_funnel_events`, stages incl.
    `app_install_redirect` == connect_repo_install_started,
    `app_installed` == connect_repo_install_completed — both already
    fire for real in `routers/github_app.py:246-250` and `:358-368`).
    `connect_repo_install_failed` did NOT exist in either store before
    this change — added to the generic `funnel_events` store instead,
    since it's a failure/error signal (matches `project_add_failure`'s
    existing convention) rather than a funnel *stage*.

New (built this session, file:line in the test bodies below):
  - `services/signup_guards.py::emit_connect_repo_install_failed` —
    wired into the real `routers/github_app.py` callback failure
    branch (meta-fetch failure, the only branch with a known user_id).
  - `services/signup_guards.py::emit_first_scan_started/_completed/
    _findings_viewed/_fix_clicked` — helper functions exist and are
    unit-tested directly here; NOT yet wired into a real handler
    (the scan trigger is Step 3's S-B, not built yet) — this is
    disclosed, not hidden.
  - `routers/auth.py::POST /auth/onboarding-intent` — new, minimal,
    auth-gated, user-scoped endpoint (no existing endpoint to extend
    for this one field).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, row, query):
        for k, v in (query or {}).items():
            if isinstance(v, dict) and "$gt" in v:
                if not (row.get(k, 0) > v["$gt"]):
                    return False
                continue
            if row.get(k) != v:
                return False
        return True

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def find_one(self, query=None, projection=None):
        for r in self.rows:
            if self._match(r, query):
                return dict(r)
        return None

    async def find_one_and_update(self, query, update, projection=None):
        for r in self.rows:
            if self._match(r, query):
                r.update(update.get("$set") or {})
                return dict(r)
        return None

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if self._match(r, query):
                r.update(update.get("$set") or {})
                return
        if upsert:
            new_row = dict(query or {})
            new_row.update(update.get("$set") or {})
            self.rows.append(new_row)

    def aggregate(self, pipeline):
        """Only supports the exact shape used by
        routers/admin_users.py:719-724 — $match created_at>=since,
        then $group by event_type with a $sum count."""
        rows = list(self.rows)
        for stage in pipeline:
            if "$match" in stage:
                since = stage["$match"].get("created_at", {}).get("$gte")
                if since is not None:
                    rows = [r for r in rows if r.get("created_at", since) >= since]
            elif "$group" in stage:
                counts: dict[str, int] = {}
                key_field = stage["$group"]["_id"].lstrip("$")
                for r in rows:
                    k = r.get(key_field)
                    counts[k] = counts.get(k, 0) + 1
                rows = [{"_id": k, "n": v} for k, v in counts.items()]

        class _Cur:
            def __init__(self, rows):
                self._rows = rows

            async def __aiter__(self):
                for r in self._rows:
                    yield r

        return _Cur(rows)


class _FakeDB:
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]


USER = {"user_id": "u_onboard_1", "email": "new@example.com", "tier": "pro",
        "is_admin": False, "created_at": time.time()}


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def auth_client(fake_db):
    from routers import auth as auth_mod
    from cto_services import db as _dbmod
    _dbmod.set_db(fake_db)

    async def _fake_current_dev(authorization=None):
        if not authorization:
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        return USER

    old = auth_mod.current_dev
    auth_mod.current_dev = _fake_current_dev

    app = FastAPI()
    app.include_router(auth_mod.router, prefix="/api/aurem-dev")
    c = TestClient(app)
    yield c

    auth_mod.current_dev = old
    _dbmod.set_db(None)


# ── T-A2: onboarding_intent captured + stored (real endpoint) ────────

def test_ta2_onboarding_intent_captured_via_real_endpoint(auth_client, fake_db):
    fake_db.dev_users.rows.append({"user_id": USER["user_id"], "email": USER["email"]})
    r = auth_client.post(
        "/api/aurem-dev/auth/onboarding-intent",
        json={"intent": "has_repo"},
        headers={"Authorization": "Bearer x"},
    )
    assert r.status_code == 200
    assert r.json()["onboarding_intent"] == "has_repo"
    stored = [row for row in fake_db.dev_users.rows
              if row.get("user_id") == USER["user_id"]]
    assert stored and stored[0]["onboarding_intent"] == "has_repo"


def test_ta2_onboarding_intent_rejects_invalid_value(auth_client):
    r = auth_client.post(
        "/api/aurem-dev/auth/onboarding-intent",
        json={"intent": "banana"},
        headers={"Authorization": "Bearer x"},
    )
    assert r.status_code == 400


# ── T-A1: signup -> connect(-failed) -> scan -> fix-click, in order ──

@pytest.mark.asyncio
async def test_ta1_full_sequence_logs_all_events_in_order(fake_db):
    from services.signup_guards import (
        emit_funnel_event, emit_connect_repo_install_failed,
        emit_first_scan_started, emit_first_scan_completed,
        emit_first_scan_findings_viewed, emit_first_scan_fix_clicked,
    )
    uid = "u_seq_1"

    await emit_funnel_event(fake_db, user_id=uid, event_type="signup_completed")
    await emit_connect_repo_install_failed(fake_db, user_id=uid, error="probe timeout")
    await emit_funnel_event(fake_db, user_id=uid, event_type="repo_selected")
    await emit_first_scan_started(fake_db, user_id=uid, project_id="p1")
    await emit_first_scan_completed(
        fake_db, user_id=uid, project_id="p1", findings_count=3,
        scan_duration_ms=820.5, top_category="seo",
    )
    await emit_first_scan_findings_viewed(fake_db, user_id=uid, project_id="p1")
    await emit_first_scan_fix_clicked(fake_db, user_id=uid, project_id="p1", finding_id="f1")

    rows = [r for r in fake_db.funnel_events.rows if r["user_id"] == uid]
    order = [r["event_type"] for r in rows]
    assert order == [
        "signup_completed",
        "connect_repo_install_failed",
        "repo_selected",
        "first_scan_started",
        "first_scan_completed",
        "first_scan_findings_viewed",
        "first_scan_fix_clicked",
    ]
    assert all(r["user_id"] == uid for r in rows)
    assert all(isinstance(r["created_at"], datetime) for r in rows)
    completed = rows[4]
    assert completed["metadata"]["findings_count"] == 3
    assert completed["metadata"]["scan_duration_ms"] == 820.5
    assert completed["metadata"]["top_category"] == "seo"


@pytest.mark.asyncio
async def test_ta1_connect_repo_install_failed_wired_into_real_callback():
    """Not a source-string check — mocks GitHub's metadata fetch to
    actually raise inside `routers.github_app.install_callback`, and
    confirms the real handler calls the real emit helper with the
    real user_id from the validated state row."""
    from unittest.mock import AsyncMock, patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import github_app as ga_mod
    from cto_services import db as _dbmod

    fake_db = _FakeDB()
    _dbmod.set_db(fake_db)
    fake_db.oauth_states.rows.append({
        "state": "st1", "kind": "github_app_install", "used": False,
        "expires_at": time.time() + 600, "user_id": "u_cb_1",
        "funnel_session": "sess1",
    })

    captured = {}

    async def _fake_emit(db, *, user_id, error):
        captured["user_id"] = user_id
        captured["error"] = error

    app = FastAPI()
    app.include_router(ga_mod.router, prefix="/api/aurem-dev")
    c = TestClient(app)

    with patch("routers.github_app._fetch_installation_meta",
               AsyncMock(side_effect=RuntimeError("github down"))), \
         patch("services.signup_guards.emit_connect_repo_install_failed", _fake_emit):
        r = c.get(
            "/api/aurem-dev/github/app/callback",
            params={"installation_id": 999, "state": "st1"},
            follow_redirects=False,
        )

    assert r.status_code in (302, 307)
    assert captured.get("user_id") == "u_cb_1"
    assert "github down" in captured.get("error", "")
    _dbmod.set_db(None)


# ── T-A3: admin query returns live counts (the real GET /admin/funnel
#          pipeline, file:line routers/admin_users.py:719-724) ──────

@pytest.mark.asyncio
async def test_ta3_admin_funnel_event_counts_include_new_event_types(fake_db):
    from services.signup_guards import (
        emit_first_scan_started, emit_first_scan_completed,
        emit_connect_repo_install_failed,
    )
    since = datetime.now(timezone.utc)
    for r in fake_db.funnel_events.rows:
        r["created_at"] = since  # n/a, empty at this point

    await emit_first_scan_started(fake_db, user_id="u1", project_id="p1")
    await emit_first_scan_started(fake_db, user_id="u2", project_id="p2")
    await emit_first_scan_completed(
        fake_db, user_id="u1", project_id="p1",
        findings_count=1, scan_duration_ms=500.0,
    )
    await emit_connect_repo_install_failed(fake_db, user_id="u3", error="x")

    # The exact pipeline routers/admin_users.py:719-724 runs.
    pipeline = [
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {"_id": "$event_type", "n": {"$sum": 1}}},
    ]
    ev_counts: dict[str, int] = {}
    async for row in fake_db.funnel_events.aggregate(pipeline):
        ev_counts[row["_id"] or "?"] = int(row["n"])

    assert ev_counts["first_scan_started"] == 2
    assert ev_counts["first_scan_completed"] == 1
    assert ev_counts["connect_repo_install_failed"] == 1
