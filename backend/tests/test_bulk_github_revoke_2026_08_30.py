"""tests/test_bulk_github_revoke_2026_08_30.py — Admin bulk GitHub App
revoke (2026-08-30). Standalone feature, not R9/prod-flip.

Built + MOCK-TESTED ONLY — see /app/memory/GITHUB_BULK_REVOKE_DRILL_VERIFY.md
for why the live drill-repo verify (U1-U6) hasn't run yet, and why
that's fine for this test suite (every named test here specifies a
mock GitHub client, never a real one).

Covers, across three layers:
  services/github_app.py::revoke_installation_verbose  (real DELETE call,
    mocked transport)   -> t_jwt_key_never_exposed
  services/github_bulk_revoke.py::bulk_revoke           (parallel batch)
    -> t_stale_installation_404_counts_as_skipped, t_bulk_revoke_parallel_timeout
  routers/admin_bin.py (github_connections / github_bulk_revoke / github_flag_idle)
    -> t_default_view_excludes_valid, t_dry_run_precedes_github_call,
       t_bulk_revoke_blocks_on_valid_selection, t_real_github_call_not_db_only,
       t_partial_failure_reported, t_audit_logged, t_idle_flag_non_destructive,
       t_user_data_preserved_on_revoke
  routers/github_app.py webhook (installation.deleted)
    -> t_webhook_installation_deleted
"""
from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import httpx

from services import github_app as ga
from services import github_bulk_revoke as gbr
from services.github_app_config import set_runtime_github_app_config


# ═════════════════════════════════════════════════════════════════════
# Shared mock-transport helper (same pattern as test_github_app_service.py)
# ═════════════════════════════════════════════════════════════════════

def _make_mock_client(handler):
    _RealAsyncClient = httpx.AsyncClient

    def factory(*args, **kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler))
    return factory


@pytest.fixture
def rsa_keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, NoEncryption,
    )
    key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption(),
    ).decode()
    return priv_pem


@pytest.fixture
def configured_app(rsa_keypair):
    set_runtime_github_app_config({
        "app_id": "123456", "app_slug": "aurem-test",
        "private_key": rsa_keypair, "webhook_secret": "test-webhook-secret-1234",
    })
    ga._APP_JWT_CACHE = None
    ga._INSTALL_TOKEN_CACHE.clear()
    yield rsa_keypair
    set_runtime_github_app_config(None)
    ga._APP_JWT_CACHE = None
    ga._INSTALL_TOKEN_CACHE.clear()


# ═════════════════════════════════════════════════════════════════════
# services/github_app.py::revoke_installation_verbose — real call shape,
# mocked transport (matches U1/U5 status-code branches).
# ═════════════════════════════════════════════════════════════════════

class TestRevokeInstallationVerbose:
    @pytest.mark.asyncio
    async def test_204_reports_deleted(self, configured_app):
        def handler(request):
            assert request.method == "DELETE"
            assert request.url.path == "/app/installations/42"
            return httpx.Response(204)
        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            out = await ga.revoke_installation_verbose(42)
        assert out == {"outcome": "deleted", "status_code": 204, "error": None}

    @pytest.mark.asyncio
    async def test_404_reports_already_gone_not_failed(self, configured_app):
        """t_stale_installation_404_counts_as_skipped (service layer)."""
        def handler(request):
            return httpx.Response(404, json={"message": "Not Found"})
        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            out = await ga.revoke_installation_verbose(43)
        assert out["outcome"] == "already_gone"
        assert out["status_code"] == 404

    @pytest.mark.asyncio
    async def test_410_also_reports_already_gone(self, configured_app):
        def handler(request):
            return httpx.Response(410, json={"message": "Gone"})
        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            out = await ga.revoke_installation_verbose(44)
        assert out["outcome"] == "already_gone"
        assert out["status_code"] == 410

    @pytest.mark.asyncio
    async def test_500_reports_failed_with_reason(self, configured_app):
        def handler(request):
            return httpx.Response(500, text="internal error")
        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            out = await ga.revoke_installation_verbose(45)
        assert out["outcome"] == "failed"
        assert out["status_code"] == 500

    @pytest.mark.asyncio
    async def test_jwt_key_never_exposed(self, configured_app, rsa_keypair):
        """t_bulk_revoke_key_never_in_response / t_jwt_key_never_exposed —
        even on a failure path, the private key string never leaks into
        the returned result (which is exactly what ends up in the audit
        log / API response)."""
        def handler(request):
            # The Authorization header carries a signed JWT, never the
            # raw PEM.
            auth = request.headers.get("authorization", "")
            assert rsa_keypair not in auth
            return httpx.Response(403, text="Forbidden — bad credentials")
        with patch.object(httpx, "AsyncClient", _make_mock_client(handler)):
            out = await ga.revoke_installation_verbose(46)
        blob = str(out)
        assert rsa_keypair not in blob
        assert "BEGIN PRIVATE KEY" not in blob

    @pytest.mark.asyncio
    async def test_backcompat_wrapper_raises_only_on_failure(self, configured_app):
        def handler_ok(request):
            return httpx.Response(404)
        with patch.object(httpx, "AsyncClient", _make_mock_client(handler_ok)):
            await ga.revoke_installation(47)  # no raise on already-gone

        def handler_fail(request):
            return httpx.Response(500)
        with patch.object(httpx, "AsyncClient", _make_mock_client(handler_fail)):
            with pytest.raises(RuntimeError):
                await ga.revoke_installation(48)


# ═════════════════════════════════════════════════════════════════════
# services/github_bulk_revoke.py::bulk_revoke — parallel batch behavior
# ═════════════════════════════════════════════════════════════════════

class TestBulkRevokeService:
    @pytest.mark.asyncio
    async def test_stale_installation_counts_as_skipped_not_failed(self, monkeypatch):
        async def _fake(iid):
            return {"outcome": "already_gone", "status_code": 404, "error": None}
        monkeypatch.setattr(ga, "revoke_installation_verbose", _fake)
        out = await gbr.bulk_revoke([100])
        assert out[0]["outcome"] == "already_gone"
        assert out[0]["installation_id"] == 100

    @pytest.mark.asyncio
    async def test_parallel_timeout_does_not_stop_batch(self, monkeypatch):
        """t_bulk_revoke_parallel_timeout — one slow call times out,
        the rest of the batch still completes and is reported."""
        import asyncio
        monkeypatch.setattr(gbr, "REVOKE_TIMEOUT_S", 0.05)

        async def _fake(iid):
            if iid == 200:
                await asyncio.sleep(1.0)  # exceeds the 0.05s test timeout
                return {"outcome": "deleted", "status_code": 204, "error": None}
            return {"outcome": "deleted", "status_code": 204, "error": None}

        monkeypatch.setattr(ga, "revoke_installation_verbose", _fake)
        out = await gbr.bulk_revoke([200, 201, 202])
        by_id = {r["installation_id"]: r for r in out}
        assert by_id[200]["outcome"] == "failed"
        assert "timed out" in by_id[200]["error"]
        assert by_id[201]["outcome"] == "deleted"
        assert by_id[202]["outcome"] == "deleted"

    @pytest.mark.asyncio
    async def test_one_exception_does_not_sink_the_batch(self, monkeypatch):
        async def _fake(iid):
            if iid == 300:
                raise RuntimeError("boom")
            return {"outcome": "deleted", "status_code": 204, "error": None}
        monkeypatch.setattr(ga, "revoke_installation_verbose", _fake)
        out = await gbr.bulk_revoke([300, 301])
        by_id = {r["installation_id"]: r for r in out}
        assert by_id[300]["outcome"] == "failed"
        assert "boom" in by_id[300]["error"]
        assert by_id[301]["outcome"] == "deleted"


# ═════════════════════════════════════════════════════════════════════
# Fake Mongo for router-level tests
# ═════════════════════════════════════════════════════════════════════

def _match(row, query):
    for k, v in (query or {}).items():
        if isinstance(v, dict):
            if "$in" in v and row.get(k) not in v["$in"]:
                return False
            if "$nin" in v and row.get(k) in v["$nin"]:
                return False
            if "$exists" in v:
                if v["$exists"] != (row.get(k) is not None):
                    return False
            if "$ne" in v and row.get(k) == v["$ne"]:
                return False
            continue
        if row.get(k) != v:
            return False
    return True


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def __aiter__(self):
        self._it = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    async def to_list(self, length=None):
        return list(self._rows[:length] if length else self._rows)


class _FakeCollection:
    def __init__(self, rows=None):
        self.rows: list[dict] = rows or []

    def find(self, query=None, projection=None):
        return _FakeCursor([r for r in self.rows if _match(r, query)])

    async def find_one(self, query=None, projection=None, sort=None):
        matched = [r for r in self.rows if _match(r, query)]
        if sort:
            key, direction = sort[0]
            matched.sort(key=lambda r: r.get(key) or datetime.min.replace(tzinfo=timezone.utc),
                        reverse=(direction == -1))
        return dict(matched[0]) if matched else None

    async def update_one(self, query, update):
        for r in self.rows:
            if _match(r, query):
                r.update(update.get("$set", {}))
                return types.SimpleNamespace(modified_count=1)
        return types.SimpleNamespace(modified_count=0)

    async def update_many(self, query, update):
        n = 0
        for r in self.rows:
            if _match(r, query):
                r.update(update.get("$set", {}))
                n += 1
        return types.SimpleNamespace(modified_count=n)

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        return types.SimpleNamespace(inserted_id="fake")

    def aggregate(self, pipeline):
        match_stage = pipeline[0].get("$match", {})
        rows = [r for r in self.rows if _match(r, match_stage)]
        group = pipeline[1]["$group"]
        id_field = group["_id"].lstrip("$")
        val_field = group["last"]["$max"].lstrip("$")
        buckets: dict = {}
        for r in rows:
            key = r.get(id_field)
            val = r.get(val_field)
            if key not in buckets or (val is not None and (buckets[key] is None or val > buckets[key])):
                buckets[key] = val
        return _FakeCursor([{"_id": k, "last": v} for k, v in buckets.items()])


class _FakeDB:
    def __init__(self):
        self.cto_projects = _FakeCollection()
        self.dev_users = _FakeCollection()
        self.chat_sessions = _FakeCollection()
        self.loop_sessions = _FakeCollection()
        self.github_installations = _FakeCollection()
        self.admin_audit = _FakeCollection()


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture
def admin_bin_wired(monkeypatch, fake_db):
    """Wire routers/admin_bin.py to the fake db + a fake admin, matching
    the pattern the existing github router tests already use."""
    from routers import admin_bin
    from cto_services import db as _dbmod

    monkeypatch.setattr(_dbmod, "get_db", lambda: fake_db)

    async def _fake_require_admin(authorization):
        return {"email": "admin@aurem.dev", "user_id": "admin-1"}
    monkeypatch.setattr(admin_bin, "_require_admin", _fake_require_admin)
    return admin_bin


def _seed_two_users(fake_db, *, valid_iid=10, broken_iid=11):
    fake_db.dev_users.rows += [
        {"user_id": "u-valid", "email": "working@example.com"},
        {"user_id": "u-broken", "email": "broken@example.com"},
    ]
    fake_db.cto_projects.rows += [
        {"project_id": "p-valid", "user_id": "u-valid", "auth_method": "github_app",
         "github_owner": "octo", "github_repo": "valid-repo",
         "installation_id": valid_iid, "status": "connected", "tasks_done": 0},
        {"project_id": "p-broken", "user_id": "u-broken", "auth_method": "github_app",
         "github_owner": "octo", "github_repo": "broken-repo",
         "installation_id": broken_iid, "status": "connected", "tasks_done": 0},
    ]
    fake_db.github_installations.rows += [
        {"installation_id": valid_iid, "active": True},
        {"installation_id": broken_iid, "active": True},
    ]


def _patch_probe(monkeypatch, status_by_project_id: dict):
    import services.pat_vault as pv

    async def _fake_probe(project):
        return {"pat_status": status_by_project_id.get(project["project_id"], "missing"),
                "pat_last4": None}
    # admin_bin imports probe_pat_status locally inside each function
    # (`from services.pat_vault import probe_pat_status`) — patch the
    # source module so every local import picks up the fake.
    monkeypatch.setattr(pv, "probe_pat_status", _fake_probe)


# ═════════════════════════════════════════════════════════════════════
# routers/admin_bin.py — GET /admin/github/connections
# ═════════════════════════════════════════════════════════════════════

class TestGithubConnections:
    @pytest.mark.asyncio
    async def test_default_view_excludes_valid(self, admin_bin_wired, fake_db, monkeypatch):
        """t_default_view_excludes_valid — default filter shows only
        non-valid (revokable) rows; valid rows hidden until view=all."""
        _seed_two_users(fake_db)
        _patch_probe(monkeypatch, {"p-valid": "valid", "p-broken": "missing"})

        out = await admin_bin_wired.github_connections(authorization="Bearer x")  # default view=revokable
        iids = {r["installation_id"] for r in out["rows"]}
        assert 11 in iids
        assert 10 not in iids

        out_all = await admin_bin_wired.github_connections(authorization="Bearer x", view="all")
        iids_all = {r["installation_id"] for r in out_all["rows"]}
        assert {10, 11} <= iids_all

    @pytest.mark.asyncio
    async def test_idle_view_is_valid_but_zero_tasks(self, admin_bin_wired, fake_db, monkeypatch):
        _seed_two_users(fake_db)
        _patch_probe(monkeypatch, {"p-valid": "valid", "p-broken": "valid"})
        out = await admin_bin_wired.github_connections(authorization="Bearer x", view="idle")
        assert len(out["rows"]) == 2
        assert all(r["classification"] == "idle" for r in out["rows"])


# ═════════════════════════════════════════════════════════════════════
# routers/admin_bin.py — POST /admin/github/bulk-revoke
# ═════════════════════════════════════════════════════════════════════

class TestBulkRevokeRouter:
    @pytest.mark.asyncio
    async def test_dry_run_precedes_github_call(self, admin_bin_wired, fake_db, monkeypatch):
        """t_dry_run_precedes_github_call — dry_run=True never calls
        GitHub and never writes the DB."""
        _seed_two_users(fake_db)
        _patch_probe(monkeypatch, {"p-valid": "valid", "p-broken": "missing"})
        called = AsyncMock(side_effect=AssertionError("must not call GitHub on dry_run"))
        monkeypatch.setattr(gbr, "bulk_revoke", called)

        from routers.admin_bin import GithubBulkRevokeBody
        body = GithubBulkRevokeBody(installation_ids=[10, 11], dry_run=True)
        out = await admin_bin_wired.github_bulk_revoke(body=body, authorization="Bearer x")

        called.assert_not_called()
        assert out["dry_run"] is True
        assert out["valid_count"] == 1
        assert all(r["active"] is True for r in fake_db.github_installations.rows)  # untouched
        assert fake_db.cto_projects.rows[0]["status"] == "connected"  # untouched

    @pytest.mark.asyncio
    async def test_hard_guard_blocks_valid_selection_without_confirm(
        self, admin_bin_wired, fake_db, monkeypatch,
    ):
        """t_bulk_revoke_blocks_on_valid_selection."""
        _seed_two_users(fake_db)
        _patch_probe(monkeypatch, {"p-valid": "valid", "p-broken": "missing"})
        from fastapi import HTTPException
        from routers.admin_bin import GithubBulkRevokeBody
        body = GithubBulkRevokeBody(installation_ids=[10, 11])  # no confirm_text
        with pytest.raises(HTTPException) as exc:
            await admin_bin_wired.github_bulk_revoke(body=body, authorization="Bearer x")
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "hard_guard_blocked"
        assert exc.value.detail["valid_count"] == 1

    @pytest.mark.asyncio
    async def test_gated_by_live_verified_flag_even_with_confirm(
        self, admin_bin_wired, fake_db, monkeypatch,
    ):
        """Master kill-switch — even with the typed REVOKE confirm and
        no valid rows in the selection, the flag being OFF (default)
        blocks the real call. This is the standing gate: real
        destructive use stays UNCERTAIN until the drill-verify."""
        _seed_two_users(fake_db)
        _patch_probe(monkeypatch, {"p-valid": "missing", "p-broken": "missing"})
        from services import feature_flags as ff
        monkeypatch.setattr(ff, "is_enabled", AsyncMock(return_value=False))
        called = AsyncMock(side_effect=AssertionError("must not call GitHub while gated"))
        monkeypatch.setattr(gbr, "bulk_revoke", called)

        from fastapi import HTTPException
        from routers.admin_bin import GithubBulkRevokeBody
        body = GithubBulkRevokeBody(installation_ids=[10, 11], confirm_text="REVOKE")
        with pytest.raises(HTTPException) as exc:
            await admin_bin_wired.github_bulk_revoke(body=body, authorization="Bearer x")
        assert exc.value.status_code == 403
        assert exc.value.detail["error"] == "live_verification_pending"
        called.assert_not_called()

    @pytest.mark.asyncio
    async def test_real_github_call_not_db_only(self, admin_bin_wired, fake_db, monkeypatch):
        """t_bulk_revoke_real_github_call / t_real_github_call_not_db_only
        — a mock GitHub client (bulk_revoke) is genuinely invoked with
        the right installation_id; DB is written only AFTER it reports
        success."""
        _seed_two_users(fake_db)
        _patch_probe(monkeypatch, {"p-valid": "missing", "p-broken": "missing"})
        from services import feature_flags as ff
        monkeypatch.setattr(ff, "is_enabled", AsyncMock(return_value=True))

        recorded = {}
        async def _fake_bulk_revoke(ids):
            recorded["ids"] = list(ids)
            return [{"installation_id": i, "outcome": "deleted", "status_code": 204, "error": None}
                    for i in ids]
        monkeypatch.setattr(gbr, "bulk_revoke", _fake_bulk_revoke)

        from routers.admin_bin import GithubBulkRevokeBody
        body = GithubBulkRevokeBody(installation_ids=[10, 11])
        out = await admin_bin_wired.github_bulk_revoke(body=body, authorization="Bearer x")

        assert sorted(recorded["ids"]) == [10, 11]
        assert len(out["revoked"]) == 2
        assert fake_db.cto_projects.rows[0]["status"] == "disconnected"
        assert fake_db.github_installations.rows[0]["active"] is False

    @pytest.mark.asyncio
    async def test_partial_failure_reported(self, admin_bin_wired, fake_db, monkeypatch):
        """t_partial_failure_reported — 1 ok + 1 fail + 1 already-gone
        -> summary is never swallowed, DB untouched for the failed one."""
        fake_db.dev_users.rows.append({"user_id": "u1", "email": "a@example.com"})
        fake_db.cto_projects.rows += [
            {"project_id": "p1", "user_id": "u1", "auth_method": "github_app",
             "github_owner": "o", "github_repo": "r1", "installation_id": 1,
             "status": "connected", "tasks_done": 0},
            {"project_id": "p2", "user_id": "u1", "auth_method": "github_app",
             "github_owner": "o", "github_repo": "r2", "installation_id": 2,
             "status": "connected", "tasks_done": 0},
            {"project_id": "p3", "user_id": "u1", "auth_method": "github_app",
             "github_owner": "o", "github_repo": "r3", "installation_id": 3,
             "status": "connected", "tasks_done": 0},
        ]
        _patch_probe(monkeypatch, {"p1": "missing", "p2": "missing", "p3": "missing"})
        from services import feature_flags as ff
        monkeypatch.setattr(ff, "is_enabled", AsyncMock(return_value=True))

        async def _fake_bulk_revoke(ids):
            out = []
            for i in ids:
                if i == 1:
                    out.append({"installation_id": i, "outcome": "deleted", "status_code": 204, "error": None})
                elif i == 2:
                    out.append({"installation_id": i, "outcome": "failed", "status_code": 500, "error": "server error"})
                else:
                    out.append({"installation_id": i, "outcome": "already_gone", "status_code": 404, "error": None})
            return out
        monkeypatch.setattr(gbr, "bulk_revoke", _fake_bulk_revoke)

        from routers.admin_bin import GithubBulkRevokeBody
        body = GithubBulkRevokeBody(installation_ids=[1, 2, 3])
        out = await admin_bin_wired.github_bulk_revoke(body=body, authorization="Bearer x")

        assert len(out["revoked"]) == 1
        assert len(out["failed"]) == 1
        assert len(out["skipped"]) == 1
        # Failed installation's project row must NOT be touched.
        proj2 = next(p for p in fake_db.cto_projects.rows if p["installation_id"] == 2)
        assert proj2["status"] == "connected"
        proj1 = next(p for p in fake_db.cto_projects.rows if p["installation_id"] == 1)
        assert proj1["status"] == "disconnected"

    @pytest.mark.asyncio
    async def test_audit_logged(self, admin_bin_wired, fake_db, monkeypatch):
        """t_audit_logged — batch writes who/which/reason/outcome."""
        fake_db.dev_users.rows.append({"user_id": "u1", "email": "a@example.com"})
        fake_db.cto_projects.rows.append(
            {"project_id": "p1", "user_id": "u1", "auth_method": "github_app",
             "github_owner": "o", "github_repo": "r1", "installation_id": 1,
             "status": "connected", "tasks_done": 0},
        )
        _patch_probe(monkeypatch, {"p1": "missing"})
        from services import feature_flags as ff
        monkeypatch.setattr(ff, "is_enabled", AsyncMock(return_value=True))
        monkeypatch.setattr(gbr, "bulk_revoke", AsyncMock(return_value=[
            {"installation_id": 1, "outcome": "deleted", "status_code": 204, "error": None}]))

        from routers.admin_bin import GithubBulkRevokeBody
        body = GithubBulkRevokeBody(installation_ids=[1], reason="cleanup sweep")
        await admin_bin_wired.github_bulk_revoke(body=body, authorization="Bearer x")

        assert len(fake_db.admin_audit.rows) == 1
        row = fake_db.admin_audit.rows[0]
        assert row["action"] == "bulk_github_revoke"
        assert row["actor"] == "admin@aurem.dev"
        assert row["installation_ids"] == [1]
        assert row["reason"] == "cleanup sweep"
        assert row["result"]["revoked"] == 1

    @pytest.mark.asyncio
    async def test_user_data_preserved_on_revoke(self, admin_bin_wired, fake_db, monkeypatch):
        """t_user_data_preserved_on_revoke — the project row stays
        (soft-revoke), only status/installation_active flip. No delete."""
        fake_db.dev_users.rows.append({"user_id": "u1", "email": "a@example.com"})
        fake_db.cto_projects.rows.append(
            {"project_id": "p1", "user_id": "u1", "auth_method": "github_app",
             "github_owner": "o", "github_repo": "r1", "installation_id": 1,
             "status": "connected", "tasks_done": 7, "scan_findings": ["f1", "f2"],
             "chat_history_id": "sess-abc"},
        )
        _patch_probe(monkeypatch, {"p1": "missing"})
        from services import feature_flags as ff
        monkeypatch.setattr(ff, "is_enabled", AsyncMock(return_value=True))
        monkeypatch.setattr(gbr, "bulk_revoke", AsyncMock(return_value=[
            {"installation_id": 1, "outcome": "deleted", "status_code": 204, "error": None}]))

        from routers.admin_bin import GithubBulkRevokeBody
        body = GithubBulkRevokeBody(installation_ids=[1])
        await admin_bin_wired.github_bulk_revoke(body=body, authorization="Bearer x")

        assert len(fake_db.cto_projects.rows) == 1  # row still exists
        proj = fake_db.cto_projects.rows[0]
        assert proj["status"] == "disconnected"
        assert proj["tasks_done"] == 7
        assert proj["scan_findings"] == ["f1", "f2"]
        assert proj["chat_history_id"] == "sess-abc"


# ═════════════════════════════════════════════════════════════════════
# routers/admin_bin.py — POST /admin/github/flag-idle
# ═════════════════════════════════════════════════════════════════════

class TestFlagIdle:
    @pytest.mark.asyncio
    async def test_idle_flag_non_destructive(self, admin_bin_wired, fake_db, monkeypatch):
        """t_idle_flag_non_destructive — sets flag, ZERO GitHub calls,
        revokes nothing."""
        fake_db.cto_projects.rows.append(
            {"project_id": "p1", "user_id": "u1", "installation_id": 10,
             "status": "connected", "tasks_done": 0},
        )
        called = AsyncMock(side_effect=AssertionError("flag-idle must never call GitHub"))
        monkeypatch.setattr(gbr, "bulk_revoke", called)

        from routers.admin_bin import GithubFlagIdleBody
        body = GithubFlagIdleBody(installation_ids=[10], reason="idle-90d")
        out = await admin_bin_wired.github_flag_idle(body=body, authorization="Bearer x")

        called.assert_not_called()
        assert out["flagged"] == 1
        proj = fake_db.cto_projects.rows[0]
        assert proj["re_engage_flagged"] is True
        assert proj["status"] == "connected"  # unchanged — not revoked
        assert len(fake_db.admin_audit.rows) == 1
        assert fake_db.admin_audit.rows[0]["action"] == "flag_idle_reengage"


# ═════════════════════════════════════════════════════════════════════
# installation.deleted webhook — idempotency (t_webhook_installation_deleted)
# ═════════════════════════════════════════════════════════════════════

class TestWebhookInstallationDeletedIdempotent:
    def test_deleted_webhook_twice_does_not_crash(self):
        """Reuses the existing router-test fakes/conventions (rather
        than re-inventing webhook signing) — the SAME installation.
        deleted event delivered under two DIFFERENT delivery ids (so
        the delivery-id dedupe path isn't what makes this pass) must
        be handled gracefully both times."""
        import tests.test_github_app_router as router_tests

        fake_db = router_tests._FakeDB()
        rsa_key = router_tests.rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = rsa_key.private_bytes(
            router_tests.Encoding.PEM, router_tests.PrivateFormat.PKCS8,
            router_tests.NoEncryption(),
        ).decode()
        set_runtime_github_app_config({
            "app_id": "1", "app_slug": "x", "private_key": pem,
            "webhook_secret": router_tests.WEBHOOK_SECRET,
        })
        try:
            from routers import github_app as router_mod
            from cto_services import db as _dbmod
            _dbmod.get_db = lambda: fake_db
            _dbmod.require_db = lambda: fake_db
            router_mod.get_db = lambda: fake_db
            router_mod.require_db = lambda: fake_db

            from fastapi import FastAPI
            from fastapi.testclient import TestClient
            app = FastAPI()
            app.include_router(router_mod.router, prefix="/api/aurem-dev")
            c = TestClient(app)

            fake_db.github_installations.rows.append(
                {"installation_id": 900, "user_id": "user-a", "active": True})
            fake_db.cto_projects.rows.append(
                {"project_id": "p1", "user_id": "user-a", "installation_id": 900,
                 "installation_active": True})

            payload = {"action": "deleted", "installation": {"id": 900}}
            body = router_tests.json.dumps(payload).encode()

            def _post(delivery_id):
                return c.post(
                    "/api/aurem-dev/github/app/webhook",
                    content=body,
                    headers={
                        "X-Hub-Signature-256": router_tests._sign(body, router_tests.WEBHOOK_SECRET),
                        "X-GitHub-Event": "installation",
                        "X-GitHub-Delivery": delivery_id,
                        "Content-Type": "application/json",
                    },
                )

            r1 = _post("d-del-first")
            assert r1.status_code == 200
            assert fake_db.github_installations.rows[0]["active"] is False

            # Simulate our own bulk-revoke already having marked it gone
            # THEN GitHub's webhook arrives a second time (retry/replay
            # with a different delivery id) — must not crash, must stay
            # idempotent.
            r2 = _post("d-del-second")
            assert r2.status_code == 200
            assert fake_db.github_installations.rows[0]["active"] is False
            assert fake_db.cto_projects.rows[0]["installation_active"] is False
        finally:
            set_runtime_github_app_config(None)
