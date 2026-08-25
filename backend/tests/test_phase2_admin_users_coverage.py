"""tests/test_phase2_admin_users_coverage.py — Phase 2 (2026-08-28)

Targeted coverage wave for routers/admin_users.py (CI floor: 60%,
prior CI measurement 16.0%). Same in-process TestClient + _FakeDB
pattern as tests/test_phase2c_admin_analytics_router.py. Heavier
cross-service calls (get_usage, cascade_delete_user_data,
verify_installation_for_repo, loop_beta gate helpers,
_compute_activation_funnel/_compute_stage_users) are mocked so this
wave stays focused on admin_users.py's own branches rather than
re-testing already-covered collaborator modules.
"""
from __future__ import annotations

import re as _re
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch


# ═════════════════════════════════════════════════════════════════════
# Fakes (same shape as test_phase2c_admin_analytics_router.py)
# ═════════════════════════════════════════════════════════════════════

def _matches(doc, query):
    for k, v in (query or {}).items():
        if k in ("$or", "$and"):
            continue
        if isinstance(v, dict):
            if "$gte" in v and not (doc.get(k) is not None and doc[k] >= v["$gte"]):
                return False
            if "$in" in v and doc.get(k) not in v["$in"]:
                return False
            if "$exists" in v and (k in doc) != v["$exists"]:
                return False
            if "$ne" in v and doc.get(k) == v["$ne"]:
                return False
            if "$regex" in v and not _re.search(v["$regex"], doc.get(k) or "", _re.I):
                return False
            continue
        if doc.get(k) != v:
            return False
    if "$or" in (query or {}) and not any(_matches(doc, sub) for sub in query["$or"]):
        return False
    if "$and" in (query or {}) and not all(_matches(doc, sub) for sub in query["$and"]):
        return False
    return True


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *a, **k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        v = self._rows[self._i]
        self._i += 1
        return v

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def count_documents(self, query=None):
        return sum(1 for r in self.rows if _matches(r, query))

    async def find_one(self, query=None, projection=None, sort=None):
        matched = [r for r in self.rows if _matches(r, query)]
        if sort:
            key, direction = sort[0]
            matched.sort(key=lambda r: (r.get(key) is None, r.get(key, 0)),
                        reverse=(direction < 0))
        return dict(matched[0]) if matched else None

    def find(self, query=None, projection=None, sort=None, limit=None):
        matched = [dict(r) for r in self.rows if _matches(r, query)]
        if sort:
            for key, direction in reversed(list(sort)):
                matched.sort(key=lambda r: (r.get(key) is None, r.get(key, 0)),
                            reverse=(direction < 0))
        if limit:
            matched = matched[:limit]
        return _FakeCursor(matched)

    async def update_one(self, query, update, upsert=False):
        import types
        for r in self.rows:
            if _matches(r, query):
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                for k, v in (update.get("$inc") or {}).items():
                    r[k] = r.get(k, 0) + v
                return types.SimpleNamespace(matched_count=1, modified_count=1)
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    async def update_many(self, query, update):
        import types
        n = 0
        for r in self.rows:
            if not _matches(r, query):
                continue
            if isinstance(update, list):
                for stage in update:
                    for k, v in (stage.get("$set") or {}).items():
                        r[k] = v
            else:
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
            n += 1
        return types.SimpleNamespace(matched_count=n, modified_count=n)

    def aggregate(self, pipeline):
        docs = [dict(r) for r in self.rows]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$group" in stage:
                grp = stage["$group"]
                buckets: dict = {}
                order: list = []
                for d in docs:
                    key = d.get((grp["_id"] or "").lstrip("$")) if isinstance(grp["_id"], str) else None
                    if key not in buckets:
                        buckets[key] = []
                        order.append(key)
                    buckets[key].append(d)
                out = []
                for key in order:
                    row = {"_id": key, "n": len(buckets[key])}
                    out.append(row)
                docs = out
        return _FakeCursor(docs)


class _FakeDB:
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]

    def __getitem__(self, name):
        return self.__getattr__(name)


ADMIN_USER = {"user_id": "admin-1", "is_admin": True, "tier": "founder",
             "email": "founder@aurem.dev", "is_unlimited": True}


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def client(fake_db):
    from routers import admin_users as router_mod
    from cto_services import db as _dbmod
    from fastapi import Header
    _dbmod.set_db(fake_db)

    async def _fake_require_admin_dep(authorization: str = Header(None)):
        if not authorization:
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        return ADMIN_USER

    app = FastAPI()
    app.dependency_overrides[router_mod.require_admin_dep] = _fake_require_admin_dep

    async def _fake_require_admin(authorization):
        if not authorization:
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        return ADMIN_USER

    app.include_router(router_mod.router, prefix="/api/aurem-dev")

    with patch("routers._admin_common.current_dev", AsyncMock(return_value=ADMIN_USER)), \
         patch("routers.admin_users._require_admin", AsyncMock(return_value=ADMIN_USER)):
        c = TestClient(app)
        yield c

    _dbmod.set_db(None)
    app.dependency_overrides.clear()


AUTH = {"Authorization": "Bearer admin-1"}


# ═════════════════════════════════════════════════════════════════════
# GET /admin/me, /admin/github-sync
# ═════════════════════════════════════════════════════════════════════

class TestMeAndGithubSync:
    def test_me(self, client):
        r = client.get("/api/aurem-dev/admin/me", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["is_admin"] is True

    def test_github_sync_status(self, client):
        with patch("services.github_sync.get_github_sync",
                  AsyncMock(return_value={"ok": True})):
            r = client.get("/api/aurem-dev/admin/github-sync", headers=AUTH)
        assert r.status_code == 200


# ═════════════════════════════════════════════════════════════════════
# GET /admin/users, /admin/users/{id}
# ═════════════════════════════════════════════════════════════════════

class TestListAndGetUser:
    def test_list_users_empty(self, client, fake_db):
        r = client.get("/api/aurem-dev/admin/users", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["users"] == []
        assert body["bucket_counts"]["all"] == 0

    def test_list_users_with_search_and_counts(self, client, fake_db):
        now = time.time()
        fake_db.dev_users.rows.append({
            "user_id": "u1", "email": "match@x.com", "name": "Match",
            "created_at": now,
        })
        fake_db.cto_projects.rows.append({"user_id": "u1", "project_id": "p1"})
        fake_db.cto_tasks.rows.append({"user_id": "u1", "task_id": "t1"})
        fake_db.chat_sessions.rows.append({"user_id": "u1", "session_id": "s1"})
        r = client.get("/api/aurem-dev/admin/users", headers=AUTH,
                       params={"search": "match", "window": "24h"})
        assert r.status_code == 200
        users = r.json()["users"]
        assert len(users) == 1
        assert users[0]["project_count"] == 1
        assert users[0]["task_count"] == 1
        assert users[0]["session_count"] == 1

    def test_get_user_not_found(self, client, fake_db):
        r = client.get("/api/aurem-dev/admin/users/nope", headers=AUTH)
        assert r.status_code == 404

    def test_get_user_success_full_shape(self, client, fake_db):
        fake_db.dev_users.rows.append({
            "user_id": "u1", "email": "u1@x.com", "tier": "pro",
            "pro_expires_at": time.time() + 86400,
        })
        fake_db.cto_projects.rows.append({"user_id": "u1", "project_id": "p1"})
        fake_db.cto_tasks.rows.append({"user_id": "u1", "task_id": "t1",
                                       "status": "done", "created_at": time.time()})
        with patch("routers.admin_users.get_usage", AsyncMock(return_value={"remaining": 100})):
            r = client.get("/api/aurem-dev/admin/users/u1", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["project_count"] == 1
        assert body["usage"] == {"remaining": 100}
        assert body["offers"]["tier"] == "pro"
        assert "activity_timeline" in body

    def test_get_user_usage_lookup_failure_is_swallowed(self, client, fake_db):
        fake_db.dev_users.rows.append({"user_id": "u1", "email": "u1@x.com"})
        with patch("routers.admin_users.get_usage", AsyncMock(side_effect=RuntimeError("boom"))):
            r = client.get("/api/aurem-dev/admin/users/u1", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["usage"] is None


# ═════════════════════════════════════════════════════════════════════
# POST /admin/users/{id}/grant-tokens
# ═════════════════════════════════════════════════════════════════════

class TestGrantTokens:
    def test_grant_tokens_success(self, client, fake_db):
        fake_db.dev_users.rows.append({"user_id": "u1"})
        with patch("routers.admin_users.get_usage", AsyncMock(return_value={"remaining": 500})):
            r = client.post("/api/aurem-dev/admin/users/u1/grant-tokens", headers=AUTH,
                           json={"tokens": 500, "reason": "goodwill"})
        assert r.status_code == 200
        assert r.json()["granted"] == 500
        assert fake_db.cto_token_grants.rows[0]["reason"] == "goodwill"

    def test_grant_tokens_negative_rejected(self, client, fake_db):
        r = client.post("/api/aurem-dev/admin/users/u1/grant-tokens", headers=AUTH,
                       json={"tokens": -5})
        assert r.status_code == 400

    def test_grant_tokens_too_large_rejected(self, client, fake_db):
        r = client.post("/api/aurem-dev/admin/users/u1/grant-tokens", headers=AUTH,
                       json={"tokens": 20_000_000})
        assert r.status_code == 400

    def test_grant_tokens_user_not_found(self, client, fake_db):
        r = client.post("/api/aurem-dev/admin/users/nope/grant-tokens", headers=AUTH,
                       json={"tokens": 100})
        assert r.status_code == 404


# ═════════════════════════════════════════════════════════════════════
# Loop beta enable + kill switch + status
# ═════════════════════════════════════════════════════════════════════

class TestLoopBeta:
    def test_enable_loop_beta_user_not_found(self, client, fake_db):
        r = client.post("/api/aurem-dev/admin/users/nope/enable-loop-beta", headers=AUTH,
                       json={"enabled": True})
        assert r.status_code == 404

    def test_enable_loop_beta_success(self, client, fake_db):
        fake_db.dev_users.rows.append({"user_id": "u1", "email": "u1@x.com", "tier": "pro"})
        r = client.post("/api/aurem-dev/admin/users/u1/enable-loop-beta", headers=AUTH,
                       json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["loop_beta_enabled"] is True

    def test_kill_switch_toggle(self, client, fake_db):
        with patch("services.loop_beta.set_kill_switch", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/admin/loop-beta/kill-switch", headers=AUTH,
                           json={"enabled": True, "reason": "incident"})
        assert r.status_code == 200
        assert r.json()["kill_switch_enabled"] is True

    def test_loop_beta_status(self, client, fake_db):
        with patch("services.loop_beta.count_stuck_loops", AsyncMock(return_value=0)), \
             patch("services.loop_beta.gate_parity_check", AsyncMock(return_value={"ok": True})):
            r = client.get("/api/aurem-dev/admin/loop-beta/status", headers=AUTH)
        assert r.status_code == 200
        assert "gate_parity" in r.json()


# ═════════════════════════════════════════════════════════════════════
# GET /admin/funnel
# ═════════════════════════════════════════════════════════════════════

class TestFunnel:
    def test_funnel_empty_cohort(self, client, fake_db):
        r = client.get("/api/aurem-dev/admin/funnel", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["signups"] == 0

    def test_funnel_with_cohort_computes_pcts(self, client, fake_db):
        now = time.time()
        fake_db.dev_users.rows.append({
            "user_id": "u1", "email": "u1@x.com", "created_at": now - 3600,
            "first_chat_at": now - 1800, "first_ship_at": now - 900,
        })
        with patch("services.funnel_nudge_cron.stage_counts",
                  AsyncMock(return_value={"stuck": {}, "nudges_sent": {},
                                         "nudges_sent_total": 0,
                                         "nudges_clicked": {}, "nudges_clicked_total": 0})):
            r = client.get("/api/aurem-dev/admin/funnel", headers=AUTH, params={"days": 7})
        assert r.status_code == 200
        body = r.json()
        assert body["signups"] == 1
        assert body["first_chat_pct"] == 100.0
        assert body["first_ship_pct"] == 100.0

    def test_funnel_excludes_internal_accounts(self, client, fake_db):
        now = time.time()
        fake_db.dev_users.rows.append({
            "user_id": "u1", "email": "founder@x.com", "created_at": now,
            "tier": "founder",
        })
        r = client.get("/api/aurem-dev/admin/funnel", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["signups"] == 0


# ═════════════════════════════════════════════════════════════════════
# POST /admin/users/{id}/suspend, DELETE /admin/users/{id}
# ═════════════════════════════════════════════════════════════════════

class TestSuspendAndDelete:
    def test_suspend_not_found(self, client, fake_db):
        r = client.post("/api/aurem-dev/admin/users/nope/suspend", headers=AUTH,
                       json={"suspend": True})
        assert r.status_code == 404

    def test_suspend_success(self, client, fake_db):
        fake_db.dev_users.rows.append({"user_id": "u1"})
        r = client.post("/api/aurem-dev/admin/users/u1/suspend", headers=AUTH,
                       json={"suspend": True})
        assert r.status_code == 200
        assert r.json()["status"] == "suspended"

    def test_delete_user_not_found(self, client, fake_db):
        r = client.delete("/api/aurem-dev/admin/users/nope", headers=AUTH)
        assert r.status_code == 404

    def test_delete_founder_refused(self, client, fake_db, monkeypatch):
        monkeypatch.setenv("FOUNDER_EMAILS", "founder@x.com")
        fake_db.dev_users.rows.append({"user_id": "u2", "email": "founder@x.com"})
        r = client.delete("/api/aurem-dev/admin/users/u2", headers=AUTH)
        assert r.status_code == 403

    def test_delete_self_refused(self, client, fake_db):
        fake_db.dev_users.rows.append({"user_id": "admin-1", "email": "someone@x.com"})
        r = client.delete("/api/aurem-dev/admin/users/admin-1", headers=AUTH)
        assert r.status_code == 403

    def test_delete_success(self, client, fake_db):
        fake_db.dev_users.rows.append({"user_id": "u3", "email": "u3@x.com"})
        with patch("services.user_deletion.cascade_delete_user_data",
                  AsyncMock(return_value={"deletions": {"dev_users": 1},
                                         "stripe_cancelled": False,
                                         "github_revoked": []})):
            r = client.delete("/api/aurem-dev/admin/users/u3", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["ok"] is True


# ═════════════════════════════════════════════════════════════════════
# POST /admin/users/email-offer
# ═════════════════════════════════════════════════════════════════════

class TestEmailOffer:
    def test_missing_user_ids_rejected(self, client, fake_db):
        r = client.post("/api/aurem-dev/admin/users/email-offer", headers=AUTH,
                       json={"subject": "x", "body_html": "<p>hi</p>"})
        assert r.status_code == 400

    def test_missing_subject_rejected(self, client, fake_db):
        r = client.post("/api/aurem-dev/admin/users/email-offer", headers=AUTH,
                       json={"user_ids": ["u1"], "body_html": "<p>hi</p>"})
        assert r.status_code == 400

    def test_no_matching_emails_404(self, client, fake_db):
        r = client.post("/api/aurem-dev/admin/users/email-offer", headers=AUTH,
                       json={"user_ids": ["ghost"], "subject": "x", "body_html": "<p>hi</p>"})
        assert r.status_code == 404

    def test_dry_run_when_no_resend_key(self, client, fake_db, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        fake_db.dev_users.rows.append({"user_id": "u1", "email": "u1@x.com", "name": "U1"})
        r = client.post("/api/aurem-dev/admin/users/email-offer", headers=AUTH,
                       json={"user_ids": ["u1"], "subject": "Hi", "body_html": "<p>hi {{name}}</p>"})
        assert r.status_code == 200
        body = r.json()
        assert body["dry_run"] is True
        assert body["recipients"] == ["u1@x.com"]

    def test_real_send_success(self, client, fake_db, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
        fake_db.dev_users.rows.append({"user_id": "u1", "email": "u1@x.com", "name": "U1"})

        class _FakeResp:
            status_code = 200
            text = "ok"

        class _FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                return _FakeResp()

        import contextlib

        @contextlib.asynccontextmanager
        async def _fake_ext_client(*a, **k):
            yield _FakeClient()

        with patch("services.http.ext_client", _fake_ext_client):
            r = client.post("/api/aurem-dev/admin/users/email-offer", headers=AUTH,
                           json={"user_ids": ["u1"], "subject": "Hi",
                                "body_html": "<p>hi {{name}}</p>"})
        assert r.status_code == 200
        body = r.json()
        assert body["dry_run"] is False
        assert body["sent"] == 1
        assert fake_db.email_offers.rows[0]["sent_count"] == 1


# ═════════════════════════════════════════════════════════════════════
# Insights: dora / slo / cost-alert / confidence-checks
# ═════════════════════════════════════════════════════════════════════

class TestInsightsEndpoints:
    def test_dora_metrics(self, client, fake_db):
        with patch("services.dora_metrics.compute_dora", AsyncMock(return_value={"ok": True})):
            r = client.get("/api/aurem-dev/admin/insights/dora", headers=AUTH)
        assert r.status_code == 200

    def test_slo_metrics(self, client, fake_db):
        with patch("services.slo_metrics.compute_slo", AsyncMock(return_value={"ok": True})):
            r = client.get("/api/aurem-dev/admin/insights/slo", headers=AUTH)
        assert r.status_code == 200

    def test_cost_alert(self, client, fake_db):
        with patch("services.cost_revenue_alert_cron.compute_cost_revenue_status",
                  AsyncMock(return_value={"ok": True})):
            r = client.get("/api/aurem-dev/admin/insights/cost-alert", headers=AUTH)
        assert r.status_code == 200
        assert "recent_findings" in r.json()

    def test_confidence_checks_mismatch_only(self, client, fake_db):
        fake_db.response_confidence_log.rows.append({"mismatch": True, "ts": time.time()})
        fake_db.response_confidence_log.rows.append({"mismatch": False, "ts": time.time()})
        r = client.get("/api/aurem-dev/admin/insights/confidence-checks", headers=AUTH,
                       params={"mismatch_only": True})
        assert r.status_code == 200
        assert r.json()["count"] == 1


# ═════════════════════════════════════════════════════════════════════
# GitHub App orphan repair
# ═════════════════════════════════════════════════════════════════════

class TestRepairOrphanedInstallations:
    def test_dry_run_reports_repaired_and_broken(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p1", "user_id": "u1", "github_owner": "o", "github_repo": "r",
            "installation_id": 1, "auth_method": "github_app",
        })
        fake_db.cto_projects.rows.append({
            "project_id": "p2", "user_id": "u1", "github_owner": "o2", "github_repo": "r2",
            "installation_id": 2, "auth_method": "github_app", "installation_active": False,
        })

        async def _fake_verify(db, *, user_id, installation_id, owner, repo):
            return (installation_id == 1, None if installation_id == 1 else "err", None)

        with patch("services.github_app.verify_installation_for_repo", _fake_verify):
            r = client.post("/api/aurem-dev/admin/github-app/repair-orphaned-installations",
                           headers=AUTH, params={"dry_run": True})
        assert r.status_code == 200
        body = r.json()
        assert body["scanned"] == 2
        assert body["repaired_count"] == 1
        assert body["still_broken_count"] == 1
        # dry_run must not have written installation_active.
        assert fake_db.cto_projects.rows[0].get("installation_active") is None


# ═════════════════════════════════════════════════════════════════════
# Activation funnel, stage-users, first-message-sample, user-patterns
# ═════════════════════════════════════════════════════════════════════

class TestInsightsAggregations:
    def test_activation_funnel_uses_swr_cache(self, client, fake_db):
        with patch("routers.admin_users._compute_activation_funnel",
                  AsyncMock(return_value={"ok": True, "stage": "signup"})):
            r = client.get("/api/aurem-dev/admin/insights/activation-funnel", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_activation_funnel_stage_users(self, client, fake_db):
        with patch("routers.admin_users._compute_stage_users",
                  AsyncMock(return_value={"users": []})):
            r = client.get("/api/aurem-dev/admin/insights/activation-funnel/stage-users",
                           headers=AUTH, params={"stage": "signup"})
        assert r.status_code == 200

    def test_first_message_sample_empty(self, client, fake_db):
        with patch("services.test_accounts.is_test_email", return_value=False):
            r = client.get("/api/aurem-dev/admin/insights/first-message-sample", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_user_patterns_insights(self, client, fake_db):
        fake_db.ora_patterns.rows.append({
            "user_id": "u1", "hot_files": ["a.py"], "stack_signals": ["react"],
            "session_count": 3,
        })
        r = client.get("/api/aurem-dev/admin/insights/user-patterns", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["users_with_patterns"] == 1
        assert body["total_sessions"] == 3


# ═════════════════════════════════════════════════════════════════════
# dev-users created-at backfill + health
# ═════════════════════════════════════════════════════════════════════

class TestCreatedAtBackfillAndHealth:
    def test_backfill_created_at(self, client, fake_db):
        r = client.post("/api/aurem-dev/admin/dev-users/backfill-created-at", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_created_at_health_empty(self, client, fake_db):
        r = client.get("/api/aurem-dev/admin/dev-users/created-at-health", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["healthy"] is True


# ═════════════════════════════════════════════════════════════════════
# Admin chat-session lookups
# ═════════════════════════════════════════════════════════════════════

class TestAdminChatSessionLookups:
    def test_list_chat_sessions_for_user(self, client, fake_db):
        fake_db.chat_sessions.rows.append({
            "user_id": "u1", "session_id": "s1", "title": "hi",
            "created_at": time.time(), "updated_at": time.time(),
            "turns": [{"role": "user", "content": "hi"}],
        })
        r = client.get("/api/aurem-dev/admin/users/u1/chat-sessions", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["sessions"][0]["turn_count"] == 1

    def test_get_chat_session_not_found(self, client, fake_db):
        r = client.get("/api/aurem-dev/admin/chat-sessions/nope", headers=AUTH)
        assert r.status_code == 404

    def test_get_chat_session_success(self, client, fake_db):
        fake_db.chat_sessions.rows.append({"session_id": "s1", "turns": []})
        r = client.get("/api/aurem-dev/admin/chat-sessions/s1", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["session"]["session_id"] == "s1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
