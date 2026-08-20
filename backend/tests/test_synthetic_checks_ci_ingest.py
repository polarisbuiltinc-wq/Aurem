"""
2026-08-20 — G1/G15 CI ingest endpoint (POST /admin/synthetic-checks/ingest)

Verifies:
  • Rejects requests when AUREM_CI_INGEST_TOKEN is unset (fail-closed).
  • Rejects a wrong bearer token (401).
  • Accepts a valid bearer + g1/g15 payload, writes a normalised doc.
  • Rejects an unsupported `kind`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class _FakeSyntheticChecks:
    def __init__(self):
        self.docs: list[dict] = []

    async def insert_one(self, doc):
        self.docs.append(doc)
        class _R: inserted_id = "fake_id_123"
        return _R()


class _FakeDB:
    def __init__(self):
        self.synthetic_checks = _FakeSyntheticChecks()

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUREM_CI_INGEST_TOKEN", "test-ci-secret-xyz")
    from main import app
    from cto_services import db as cto_db

    with TestClient(app) as c:
        fake = _FakeDB()
        cto_db.set_db(fake)
        c._fake_db = fake
        yield c


def test_rejects_when_token_unset(monkeypatch):
    monkeypatch.delenv("AUREM_CI_INGEST_TOKEN", raising=False)
    from main import app
    with TestClient(app) as c:
        resp = c.post(
            "/api/aurem-dev/admin/synthetic-checks/ingest",
            json={"kind": "g1_route_sweep", "total": 1, "failed": 0, "results": []},
            headers={"Authorization": "Bearer anything"},
        )
        assert resp.status_code in (401, 403)


def test_rejects_wrong_token(client):
    resp = client.post(
        "/api/aurem-dev/admin/synthetic-checks/ingest",
        json={"kind": "g1_route_sweep", "total": 1, "failed": 0, "results": []},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code in (401, 403)


def test_ingests_g1_result(client):
    resp = client.post(
        "/api/aurem-dev/admin/synthetic-checks/ingest",
        json={"kind": "g1_route_sweep", "base_url": "https://auremcto.com",
              "total": 16, "failed": 0, "results": []},
        headers={"Authorization": "Bearer test-ci-secret-xyz"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    saved = client._fake_db.synthetic_checks.docs[-1]
    assert saved["kind"] == "g1_route_sweep"
    assert saved["total"] == 16
    assert saved["failed"] == 0


def test_ingests_g15_result(client):
    resp = client.post(
        "/api/aurem-dev/admin/synthetic-checks/ingest",
        json={"kind": "g15_dep_scan", "total_findings": 3,
              "high_critical": 0, "findings": []},
        headers={"Authorization": "Bearer test-ci-secret-xyz"},
    )
    assert resp.status_code == 200
    saved = client._fake_db.synthetic_checks.docs[-1]
    assert saved["kind"] == "g15_dep_scan"
    assert saved["high_critical"] == 0


def test_rejects_unsupported_kind(client):
    resp = client.post(
        "/api/aurem-dev/admin/synthetic-checks/ingest",
        json={"kind": "not_a_real_kind"},
        headers={"Authorization": "Bearer test-ci-secret-xyz"},
    )
    assert resp.status_code == 400
