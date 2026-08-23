"""Phase 2c coverage wave — backend/routers/codebase_health.py (2026-08-23).

Founder directive: real functional tests, not grep-style assertions,
AND they must actually move the pytest-cov coverage.json number that
ci_check_coverage_ratchet.py enforces in CI.

Root-cause note (found while building this wave, CONFIRMED by direct
measurement): a first draft of this suite used `requests` against the
live preview server (same pattern as test_codebase_health_score.py).
It passed 18/18 and genuinely exercised the real endpoints — but
pytest-cov measures the PYTEST PROCESS's own code execution, and
those endpoint bodies ran in the separate supervisor-managed uvicorn
process, so coverage.json showed 18%, almost entirely from the two
direct-import rate-limit tests. This is why many long-lived routers
tested this way (chat.py, cto_projects.py, this file, etc.) stay
chronically low in coverage.json despite being "tested" via
requests-based suites elsewhere in this repo — those tests give real
end-to-end confidence but don't move THIS metric. Fix: this file uses
FastAPI's TestClient (in-process, same pattern already established in
tests/test_github_app_router.py) with a lightweight fake Mongo and
mocked GitHub/LLM boundaries, so the actual route-handler code runs
inside the pytest process and gets measured. The 3 genuinely
GitHub-dependent real-repo checks moved to
test_phase2c_codebase_health_live_e2e.py (live_env-quarantined) for
end-to-end confidence only — they do not count toward this metric.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch


# ═════════════════════════════════════════════════════════════════════
# Fakes — minimal in-memory Mongo (mirrors tests/test_github_app_router.py)
# ═════════════════════════════════════════════════════════════════════

class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *args, **kwargs):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    async def __aiter__(self):
        for r in self._rows:
            yield r

    async def to_list(self, length=None):
        return list(self._rows[: length if length else len(self._rows)])


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, row, query):
        for k, v in (query or {}).items():
            if isinstance(v, dict):
                if "$gt" in v and not (row.get(k) is not None and row[k] > v["$gt"]):
                    return False
                if "$gte" in v and not (row.get(k) is not None and row[k] >= v["$gte"]):
                    return False
                if "$lt" in v and not (row.get(k) is not None and row[k] < v["$lt"]):
                    return False
                continue
            if row.get(k) != v:
                return False
        return True

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        import types
        return types.SimpleNamespace(inserted_id=doc.get("_id"))

    async def insert_many(self, docs):
        self.rows.extend(dict(d) for d in docs)
        import types
        return types.SimpleNamespace(inserted_ids=[])

    async def find_one(self, query=None, projection=None, sort=None):
        matched = [r for r in self.rows if self._match(r, query)]
        if sort:
            key, direction = sort[0]
            matched.sort(key=lambda r: r.get(key, 0), reverse=(direction < 0))
        return dict(matched[0]) if matched else None

    async def update_one(self, query, update):
        import types
        for r in self.rows:
            if self._match(r, query):
                if "$set" in update:
                    r.update(update["$set"])
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        r[k] = r.get(k, 0) + v
                return types.SimpleNamespace(matched_count=1, modified_count=1)
        return types.SimpleNamespace(matched_count=0, modified_count=0)

    async def delete_many(self, query):
        before = len(self.rows)
        self.rows = [r for r in self.rows if not self._match(r, query)]
        import types
        return types.SimpleNamespace(deleted_count=before - len(self.rows))

    async def count_documents(self, query):
        return sum(1 for r in self.rows if self._match(r, query))

    def find(self, query=None, projection=None):
        return _FakeCursor([dict(r) for r in self.rows if self._match(r, query)])

    def aggregate(self, pipeline):
        # Coverage-only fake: real grouping math isn't the point of
        # this router's tests (that belongs to a dedicated aggregation
        # test if ever needed) — an empty result set still exercises
        # every statement in scanner_feedback() end to end.
        return _FakeCursor([])


class _FakeDB:
    """Any attribute is a fresh in-memory collection on first access —
    mirrors real Mongo's "collections exist implicitly" behavior."""
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]


# ═════════════════════════════════════════════════════════════════════
# App + fixtures
# ═════════════════════════════════════════════════════════════════════

@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def client(fake_db):
    from routers import codebase_health as router_mod

    from cto_services import db as _dbmod
    old_get_db = _dbmod.get_db
    old_router_get_db = router_mod.get_db
    old_current_dev = router_mod.current_dev
    old_require_admin = router_mod.require_admin
    _dbmod.get_db = lambda: fake_db
    router_mod.get_db = lambda: fake_db

    async def _fake_current_dev(auth):
        if not auth or not auth.startswith("Bearer "):
            from fastapi import HTTPException as _HE
            raise _HE(401, "Authorization header missing")
        token = auth.split(" ", 1)[1]
        return {"user_id": token, "is_admin": token == "admin-user",
                "is_unlimited": False, "tier": "pro"}

    async def _fake_require_admin(auth):
        user = await _fake_current_dev(auth)
        if not user.get("is_admin"):
            from fastapi import HTTPException as _HE
            raise _HE(403, "admin only")
        return user

    router_mod.current_dev = _fake_current_dev
    router_mod.require_admin = _fake_require_admin

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/aurem-dev")
    c = TestClient(app)
    yield c

    _dbmod.get_db = old_get_db
    router_mod.get_db = old_router_get_db
    router_mod.current_dev = old_current_dev
    router_mod.require_admin = old_require_admin


def _seed_project(fake_db, project_id="p1", user_id="admin-user"):
    fake_db.cto_projects.rows.append({
        "project_id": project_id, "user_id": user_id,
        "github_owner": "acme", "github_repo": "widgets",
        "auth_method": "github_app", "installation_id": 999,
    })


# ═════════════════════════════════════════════════════════════════════
# POST /scan
# ═════════════════════════════════════════════════════════════════════

class TestScan:
    def test_requires_project_id(self, client):
        r = client.post("/api/aurem-dev/codebase-health/scan",
                        headers={"Authorization": "Bearer admin-user"},
                        json={"categories": ["security"]})
        assert r.status_code == 400
        assert "project_id" in r.text

    def test_requires_known_category(self, client):
        r = client.post("/api/aurem-dev/codebase-health/scan",
                        headers={"Authorization": "Bearer admin-user"},
                        json={"project_id": "p1", "categories": ["nonsense"]})
        assert r.status_code == 400

    def test_unauthenticated(self, client):
        r = client.post("/api/aurem-dev/codebase-health/scan",
                        json={"project_id": "p1"})
        assert r.status_code == 401

    def test_project_not_found(self, client):
        r = client.post("/api/aurem-dev/codebase-health/scan",
                        headers={"Authorization": "Bearer admin-user"},
                        json={"project_id": "does-not-exist",
                              "categories": ["security"]})
        assert r.status_code == 404

    def test_missing_github_linkage_returns_400(self, client, fake_db):
        fake_db.cto_projects.rows.append({
            "project_id": "p_nolink", "user_id": "admin-user",
            "github_owner": "", "github_repo": "", "auth_method": "github_app",
            "installation_id": 999,
        })
        with patch("services.pat_vault.get_repo_token", AsyncMock(return_value="fake-token")):
            r = client.post("/api/aurem-dev/codebase-health/scan",
                            headers={"Authorization": "Bearer admin-user"},
                            json={"project_id": "p_nolink", "categories": ["security"]})
        assert r.status_code == 400

    def test_success_full_categories_persists_and_rate_limited_headers(self, client, fake_db):
        _seed_project(fake_db)
        with patch("services.pat_vault.get_repo_token", AsyncMock(return_value="fake-token")), \
             patch("routers.codebase_health._list_repo_tree_with_sha",
                  AsyncMock(return_value=([{"path": "app.py", "size": 10}], "sha123"))), \
             patch("routers.codebase_health._fetch_file",
                  AsyncMock(return_value="import os\nprint('x' * 1)\n")), \
             patch("routers.codebase_health.get_cached_text_cache", AsyncMock(return_value=None)), \
             patch("routers.codebase_health.put_cached_text_cache", AsyncMock(return_value=None)):
            r = client.post(
                "/api/aurem-dev/codebase-health/scan",
                headers={"Authorization": "Bearer admin-user"},
                json={"project_id": "p1",
                      "categories": ["security", "performance", "code_quality",
                                      "dependencies", "database"]},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["scanned_files"] == 1
        assert set(body["breakdown"].keys()) == {
            "security", "performance", "code_quality", "dependencies", "database",
        }
        assert "X-Scan-Remaining" in r.headers
        # Persisted for /last to read back.
        assert len(fake_db.codebase_health_scans.rows) == 1
        assert fake_db.codebase_health_scans.rows[0]["project_id"] == "p1"

    def test_non_admin_user_is_rate_limited_and_gets_remaining_count(self, client, fake_db):
        _seed_project(fake_db, project_id="p2", user_id="free-user")
        with patch("services.pat_vault.get_repo_token", AsyncMock(return_value="fake-token")), \
             patch("routers.codebase_health._list_repo_tree_with_sha",
                  AsyncMock(return_value=([], "sha0"))), \
             patch("routers.codebase_health.get_cached_text_cache", AsyncMock(return_value=None)), \
             patch("routers.codebase_health.put_cached_text_cache", AsyncMock(return_value=None)):
            r = client.post(
                "/api/aurem-dev/codebase-health/scan",
                headers={"Authorization": "Bearer free-user"},
                json={"project_id": "p2", "categories": ["security"]},
            )
        assert r.status_code == 200, r.text
        assert r.headers["X-Scan-Remaining"] == "9"

    def test_rate_limit_429_after_cap(self, client, fake_db):
        _seed_project(fake_db, project_id="p3", user_id="capped-user")
        with patch("services.pat_vault.get_repo_token", AsyncMock(return_value="fake-token")), \
             patch("routers.codebase_health._list_repo_tree_with_sha",
                  AsyncMock(return_value=([], "sha0"))), \
             patch("routers.codebase_health.get_cached_text_cache", AsyncMock(return_value=None)), \
             patch("routers.codebase_health.put_cached_text_cache", AsyncMock(return_value=None)):
            for _ in range(10):
                r = client.post(
                    "/api/aurem-dev/codebase-health/scan",
                    headers={"Authorization": "Bearer capped-user"},
                    json={"project_id": "p3", "categories": ["security"]},
                )
                assert r.status_code == 200
            r11 = client.post(
                "/api/aurem-dev/codebase-health/scan",
                headers={"Authorization": "Bearer capped-user"},
                json={"project_id": "p3", "categories": ["security"]},
            )
        assert r11.status_code == 429
        assert r11.json()["detail"]["error"] == "scan_rate_limited"

    def test_github_fetch_crash_returns_502(self, client, fake_db):
        _seed_project(fake_db, project_id="p4")
        with patch("services.pat_vault.get_repo_token", AsyncMock(return_value="fake-token")), \
             patch("routers.codebase_health._list_repo_tree_with_sha",
                  AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("routers.codebase_health.get_cached_text_cache", AsyncMock(return_value=None)):
            r = client.post(
                "/api/aurem-dev/codebase-health/scan",
                headers={"Authorization": "Bearer admin-user"},
                json={"project_id": "p4", "categories": ["security"]},
            )
        assert r.status_code == 502
        assert "github_fetch_crashed" in r.text


# ═════════════════════════════════════════════════════════════════════
# GET /last
# ═════════════════════════════════════════════════════════════════════

class TestLastScan:
    def test_requires_project_id(self, client):
        r = client.get("/api/aurem-dev/codebase-health/last",
                       headers={"Authorization": "Bearer admin-user"})
        assert r.status_code == 400

    def test_unauthenticated(self, client):
        r = client.get("/api/aurem-dev/codebase-health/last",
                       params={"project_id": "p1"})
        assert r.status_code == 401

    def test_no_history_returns_null_score(self, client):
        r = client.get("/api/aurem-dev/codebase-health/last",
                       headers={"Authorization": "Bearer admin-user"},
                       params={"project_id": "p_never_scanned"})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "score": None}

    def test_zero_score_zero_total_treated_as_no_scan(self, client, fake_db):
        fake_db.codebase_health_scans.rows.append({
            "user_id": "admin-user", "project_id": "p5",
            "score": 0, "total": 0, "scanned_files": 1,
        })
        r = client.get("/api/aurem-dev/codebase-health/last",
                       headers={"Authorization": "Bearer admin-user"},
                       params={"project_id": "p5"})
        assert r.json() == {"ok": True, "score": None}

    def test_returns_persisted_breakdown(self, client, fake_db):
        fake_db.codebase_health_scans.rows.append({
            "user_id": "admin-user", "project_id": "p6",
            "score": 87, "label": "GOOD", "tone": "good", "total": 3,
            "scanned_files": 10, "summary": "3 issues", "categories": ["security"],
            "breakdown": {"security": {"score": 87}}, "created_at": time.time(),
        })
        r = client.get("/api/aurem-dev/codebase-health/last",
                       headers={"Authorization": "Bearer admin-user"},
                       params={"project_id": "p6"})
        body = r.json()
        assert body["ok"] is True
        assert body["score"] == 87
        assert body["breakdown"] == {"security": {"score": 87}}
        assert body["created_at"] is not None


# ═════════════════════════════════════════════════════════════════════
# GET /cache-stats
# ═════════════════════════════════════════════════════════════════════

class TestCacheStats:
    def test_requires_admin(self, client):
        r = client.get("/api/aurem-dev/codebase-health/cache-stats",
                       headers={"Authorization": "Bearer free-user"})
        assert r.status_code == 403

    def test_shape(self, client):
        r = client.get("/api/aurem-dev/codebase-health/cache-stats",
                       headers={"Authorization": "Bearer admin-user"})
        assert r.status_code == 200
        for k in ("redis_configured", "redis_connected", "ttl_seconds",
                  "hits", "misses", "hit_rate_pct"):
            assert k in r.json()


# ═════════════════════════════════════════════════════════════════════
# GET /scanner-feedback
# ═════════════════════════════════════════════════════════════════════

class TestScannerFeedback:
    def test_requires_admin(self, client):
        r = client.get("/api/aurem-dev/codebase-health/scanner-feedback",
                       headers={"Authorization": "Bearer free-user"})
        assert r.status_code == 403

    def test_shape_and_clamps(self, client):
        r = client.get("/api/aurem-dev/codebase-health/scanner-feedback",
                       headers={"Authorization": "Bearer admin-user"},
                       params={"days": 999})
        assert r.status_code == 200
        body = r.json()
        assert body["window_days"] == 180
        for k in ("total_fps", "by_rule", "by_file", "trend_daily", "recent", "generated_at"):
            assert k in body

        r2 = client.get("/api/aurem-dev/codebase-health/scanner-feedback",
                        headers={"Authorization": "Bearer admin-user"},
                        params={"days": -5})
        assert r2.json()["window_days"] == 1


# ═════════════════════════════════════════════════════════════════════
# POST /fix
# ═════════════════════════════════════════════════════════════════════

class TestRequestFix:
    def test_requires_project_id_and_finding_id(self, client):
        r = client.post("/api/aurem-dev/codebase-health/fix",
                        headers={"Authorization": "Bearer admin-user"},
                        json={"project_id": "p1"})
        assert r.status_code == 400
        r2 = client.post("/api/aurem-dev/codebase-health/fix",
                         headers={"Authorization": "Bearer admin-user"},
                         json={"finding_id": "f1"})
        assert r2.status_code == 400

    def test_unauthenticated(self, client):
        r = client.post("/api/aurem-dev/codebase-health/fix",
                        json={"project_id": "p1", "finding_id": "f1"})
        assert r.status_code == 401

    def test_db_not_connected(self, client, fake_db):
        from routers import codebase_health as router_mod
        router_mod.get_db = lambda: None
        try:
            r = client.post("/api/aurem-dev/codebase-health/fix",
                            headers={"Authorization": "Bearer admin-user"},
                            json={"project_id": "p1", "finding_id": "f1"})
            assert r.status_code == 503
        finally:
            router_mod.get_db = lambda: fake_db

    def test_user_not_found_returns_404(self, client, fake_db):
        with patch("services.scan_fix_quota.assert_can_fix", AsyncMock(return_value=None)):
            r = client.post("/api/aurem-dev/codebase-health/fix",
                            headers={"Authorization": "Bearer admin-user"},
                            json={"project_id": "p1", "finding_id": "f1"})
        assert r.status_code == 404

    def test_success_path_persists_task_and_fixed_finding(self, client, fake_db):
        fake_db.dev_users.rows.append({"user_id": "admin-user", "tokens_remaining": 500})
        fake_res = {
            "ok": True, "commit_sha": "abc123", "full_sha": "abc123def456",
            "html_url": "https://github.com/acme/widgets/commit/abc123",
            "message": "Patched successfully",
        }
        with patch("services.scan_fix_quota.assert_can_fix", AsyncMock(return_value=None)), \
             patch("services.finding_fix_applier.apply_finding_fix",
                  AsyncMock(return_value=fake_res)), \
             patch("services.fixed_findings.record_fixed", AsyncMock(return_value=None)), \
             patch("services.ora_fix_learning.record_fix_outcome", AsyncMock(return_value=None)):
            r = client.post(
                "/api/aurem-dev/codebase-health/fix",
                headers={"Authorization": "Bearer admin-user"},
                json={"project_id": "p1", "finding_id": "f1", "title": "hardcoded secret",
                      "file": "app.py", "line": 10, "message": "found a secret"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["commit_sha"] == "abc123"
        assert body["html_url"] == fake_res["html_url"]
        assert len(fake_db.cto_tasks.rows) == 1
        assert fake_db.cto_tasks.rows[0]["kind"] == "health_fix"

    def test_patch_did_not_resolve_finding_returns_422_and_refunds(self, client, fake_db):
        fake_db.dev_users.rows.append({"user_id": "admin-user", "tokens_remaining": 500})
        with patch("services.scan_fix_quota.assert_can_fix", AsyncMock(return_value=None)), \
             patch("services.finding_fix_applier.apply_finding_fix",
                  AsyncMock(return_value={"ok": False, "error": "patch_did_not_resolve_finding"})), \
             patch("services.ora_fix_learning.record_fix_outcome", AsyncMock(return_value=None)):
            r = client.post(
                "/api/aurem-dev/codebase-health/fix",
                headers={"Authorization": "Bearer admin-user"},
                json={"project_id": "p1", "finding_id": "f1"},
            )
        assert r.status_code == 422
        assert r.json()["detail"]["tokens_refunded"] is True

    def test_github_credentials_missing_returns_401(self, client, fake_db):
        fake_db.dev_users.rows.append({"user_id": "admin-user", "tokens_remaining": 500})
        with patch("services.scan_fix_quota.assert_can_fix", AsyncMock(return_value=None)), \
             patch("services.finding_fix_applier.apply_finding_fix",
                  AsyncMock(return_value={"ok": False, "error": "github_credentials_missing"})), \
             patch("services.ora_fix_learning.record_fix_outcome", AsyncMock(return_value=None)):
            r = client.post(
                "/api/aurem-dev/codebase-health/fix",
                headers={"Authorization": "Bearer admin-user"},
                json={"project_id": "p1", "finding_id": "f1"},
            )
        assert r.status_code == 401

    def test_unhandled_exception_in_apply_fix_is_caught(self, client, fake_db):
        fake_db.dev_users.rows.append({"user_id": "admin-user", "tokens_remaining": 500})
        with patch("services.scan_fix_quota.assert_can_fix", AsyncMock(return_value=None)), \
             patch("services.finding_fix_applier.apply_finding_fix",
                  AsyncMock(side_effect=RuntimeError("kaboom"))), \
             patch("services.ora_fix_learning.record_fix_outcome", AsyncMock(return_value=None)):
            r = client.post(
                "/api/aurem-dev/codebase-health/fix",
                headers={"Authorization": "Bearer admin-user"},
                json={"project_id": "p1", "finding_id": "f1"},
            )
        assert r.status_code == 500


# ═════════════════════════════════════════════════════════════════════
# _check_scan_rate_limit — direct unit test against real local Mongo
# (not reachable via HTTP for an admin account, which is exempt from
# the rate check — this is the only way to genuinely exercise the
# sliding-window cap/denial logic itself end to end).
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_check_scan_rate_limit_caps_at_10_and_denies_11th():
    from motor.motor_asyncio import AsyncIOMotorClient
    from routers.codebase_health import _check_scan_rate_limit, _SCAN_RATE_CAP

    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    mclient = AsyncIOMotorClient(mongo_url)
    db = mclient[db_name]
    user_id = f"test_ratelimit_user_{uuid.uuid4().hex[:8]}"
    category = f"test_phase2c_cat_{uuid.uuid4().hex[:8]}"

    try:
        for i in range(_SCAN_RATE_CAP):
            denied, retry, remaining = await _check_scan_rate_limit(db, user_id, [category])
            assert denied is None, f"call {i+1}/{_SCAN_RATE_CAP} should not be denied yet"
            assert remaining[category] == _SCAN_RATE_CAP - (i + 1)

        denied, retry, remaining = await _check_scan_rate_limit(db, user_id, [category])
        assert denied == category
        assert retry > 0
        assert remaining[category] == 0
    finally:
        await db.scan_rate_limits.delete_many({"user_id": user_id})
        mclient.close()


@pytest.mark.asyncio
async def test_check_scan_rate_limit_multi_category_independent_windows():
    from motor.motor_asyncio import AsyncIOMotorClient
    from routers.codebase_health import _check_scan_rate_limit

    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    mclient = AsyncIOMotorClient(mongo_url)
    db = mclient[db_name]
    user_id = f"test_ratelimit_multi_{uuid.uuid4().hex[:8]}"
    cat_a = f"test_phase2c_a_{uuid.uuid4().hex[:8]}"
    cat_b = f"test_phase2c_b_{uuid.uuid4().hex[:8]}"

    try:
        denied, retry, remaining = await _check_scan_rate_limit(db, user_id, [cat_a, cat_b])
        assert denied is None
        assert remaining[cat_a] == 9 and remaining[cat_b] == 9
    finally:
        await db.scan_rate_limits.delete_many({"user_id": user_id})
        mclient.close()
