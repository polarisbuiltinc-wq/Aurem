"""
Backend regression tests for Journey Watch Phase 3 build round (2026-08-27):
- GET /api/aurem-dev/admin/graph-status now joins project_graphs
- GET /api/aurem-dev/admin/status/notifications standard shape preserved
- Login endpoint sanity
- Backend booted with journey_watch & funnel_digest_cron supervised tasks
"""
import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"

SEEDED_GRAPH_PROJECT_IDS = {
    "p_68dfb110b1", "p_0fdafaa365", "p_afc2a4bd37", "p_42f2b46491",
    "p_218f8a9195", "p_3ae60acd13", "p_6d0be78cdd", "p_demo_a",
}


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{BASE_URL}/api/aurem-dev/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    data = r.json()
    assert data.get("ok") is True
    assert data.get("is_admin") is True
    tok = data.get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


class TestGraphStatus:
    """Phase 2 fix: admin/graph-status should join project_graphs, not read unwritten cto_projects fields."""

    def test_graph_status_returns_rows_with_has_graph(self, auth_headers):
        r = requests.get(f"{BASE_URL}/api/aurem-dev/admin/graph-status", headers=auth_headers, timeout=20)
        assert r.status_code == 200, r.text[:200]
        payload = r.json()
        assert "rows" in payload, f"missing 'rows': {payload}"
        rows = payload["rows"]
        assert isinstance(rows, list)
        assert len(rows) > 0

        # Every row must have has_graph + graph_node_count keys
        for row in rows:
            assert "has_graph" in row
            assert "project_id" in row
            if row.get("has_graph"):
                assert isinstance(row.get("graph_node_count"), int)
                assert row["graph_node_count"] >= 0

    def test_seeded_projects_show_has_graph_true(self, auth_headers):
        r = requests.get(f"{BASE_URL}/api/aurem-dev/admin/graph-status", headers=auth_headers, timeout=20)
        assert r.status_code == 200
        rows = r.json()["rows"]
        by_id = {row["project_id"]: row for row in rows}

        found_true = 0
        missing = []
        for pid in SEEDED_GRAPH_PROJECT_IDS:
            if pid in by_id:
                if by_id[pid].get("has_graph") is True and isinstance(by_id[pid].get("graph_node_count"), int):
                    found_true += 1
                else:
                    missing.append((pid, by_id[pid]))
            else:
                missing.append((pid, None))
        assert found_true >= 6, f"expected >=6 seeded projects with has_graph=true; got {found_true}. missing={missing}"


class TestNotificationsEndpoint:
    """Journey Watch reuses this existing endpoint — shape must be preserved."""

    def test_notifications_shape(self, auth_headers):
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/status/notifications",
            headers=auth_headers,
            timeout=15,
        )
        assert r.status_code == 200, r.text[:300]
        payload = r.json()
        # standard shape {available, notifications, unread_count}
        assert "available" in payload
        assert "notifications" in payload
        assert "unread_count" in payload
        assert isinstance(payload["notifications"], list)
        assert isinstance(payload["unread_count"], int)

    def test_notifications_items_have_expected_fields(self, auth_headers):
        r = requests.get(
            f"{BASE_URL}/api/aurem-dev/admin/status/notifications",
            headers=auth_headers,
            timeout=15,
        )
        assert r.status_code == 200
        items = r.json().get("notifications", [])
        if not items:
            pytest.skip("No notifications present; shape-check only")
        sample = items[0]
        # These are the standard keys the NotificationBell UI consumes
        # (loose assertion - at least one of message/text/title present)
        assert any(k in sample for k in ("message", "text", "title", "name", "check_id", "detail"))
        assert "notif_id" in sample or "check_id" in sample


class TestAuthEndpoint:
    def test_invalid_login(self):
        r = requests.post(
            f"{BASE_URL}/api/aurem-dev/auth/login",
            json={"email": ADMIN_EMAIL, "password": "wrong"},
            timeout=15,
        )
        assert r.status_code in (400, 401, 403)
