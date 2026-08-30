"""
E2E integration tests for GitHub Bulk Revoke admin feature (2026-08-30).

Testing scope (main agent request):
- GET /admin/github/connections shape + live_verified flag
- POST /admin/github/bulk-revoke server-side hard-guard (400) and
  feature-flag gate (403), plus dry_run preview (no side effects)
- POST /admin/github/flag-idle non-destructive flag

IMPORTANT SAFETY:
- Never call bulk-revoke with dry_run=false + REVOKE confirm without
  the feature flag OFF (server gate should block anyway).
- Do NOT flip the github_bulk_revoke_live_verified flag.
- The shared installation_id 152797252 (polarisbuiltinc-wq/ora-grounding)
  must not be destructively touched.
"""

import os
import pytest
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://bin-context-pat.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    r = requests.post(
        f"{API}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    assert data.get("is_admin") is True, "expected admin tier"
    return data["token"]


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ─── /admin/github/connections ────────────────────────────────────────
class TestGithubConnections:
    def test_default_view_revokable(self, auth_headers):
        r = requests.get(f"{API}/admin/github/connections", headers=auth_headers, timeout=45)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["view"] == "revokable"
        assert "live_verified" in data
        assert isinstance(data["live_verified"], bool)
        # Expected: live_verified should be False since flag is OFF
        assert data["live_verified"] is False, "flag should be OFF for this test env"
        assert isinstance(data["rows"], list)
        for row in data["rows"]:
            assert row["classification"] == "revokable"
            assert row["pat_status"] != "valid"

    def test_idle_view(self, auth_headers):
        r = requests.get(f"{API}/admin/github/connections?view=idle", headers=auth_headers, timeout=45)
        assert r.status_code == 200
        data = r.json()
        assert data["view"] == "idle"
        for row in data["rows"]:
            assert row["classification"] == "idle"
            assert row["pat_status"] == "valid"
            assert row["task_count"] == 0

    def test_all_view_shape(self, auth_headers):
        r = requests.get(f"{API}/admin/github/connections?view=all", headers=auth_headers, timeout=45)
        assert r.status_code == 200
        data = r.json()
        assert data["view"] == "all"
        rows = data["rows"]
        # Row shape sanity
        for row in rows:
            for key in ("email", "repo", "auth_method", "pat_status", "status",
                        "task_count", "last_session_at", "in_flight_work",
                        "re_engage_flagged", "installation_id", "classification"):
                assert key in row, f"missing key {key} in row: {row}"
            # last_session_at must be None or a sane ISO string (NOT 1970)
            lsa = row["last_session_at"]
            if lsa is not None:
                assert isinstance(lsa, str), f"last_session_at must be ISO str, got: {lsa!r}"
                assert not lsa.startswith("1970"), f"1970 epoch bug regression: {lsa}"
            # repo dedup check — must not contain the same repo repeated
            if row["repo"]:
                parts = [p.strip() for p in row["repo"].split("·")]
                assert len(parts) == len(set(parts)), f"repo not deduped: {row['repo']}"


# ─── /admin/github/bulk-revoke ────────────────────────────────────────
class TestBulkRevoke:
    @pytest.fixture(scope="class")
    def sample_ids(self, auth_headers):
        r = requests.get(f"{API}/admin/github/connections?view=all", headers=auth_headers, timeout=45)
        assert r.status_code == 200
        rows = r.json()["rows"]
        ids = [row["installation_id"] for row in rows if row.get("installation_id")]
        if not ids:
            pytest.skip("No installations with installation_id available in DB")
        return ids

    def test_dry_run_preview_no_side_effects(self, auth_headers, sample_ids):
        # Snapshot state BEFORE
        before = requests.get(f"{API}/admin/github/connections?view=all", headers=auth_headers, timeout=45).json()["rows"]
        before_map = {r["installation_id"]: dict(r) for r in before if r.get("installation_id")}

        r = requests.post(
            f"{API}/admin/github/bulk-revoke",
            headers=auth_headers,
            json={"installation_ids": sample_ids[:5], "dry_run": True},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert data["dry_run"] is True
        assert isinstance(data["preview"], list)
        assert data["total"] == len(set(sample_ids[:5]))
        assert "valid_count" in data
        for p in data["preview"]:
            assert "installation_id" in p
            assert "pat_status" in p

        # Verify no DB changes
        after = requests.get(f"{API}/admin/github/connections?view=all", headers=auth_headers, timeout=45).json()["rows"]
        after_map = {r["installation_id"]: dict(r) for r in after if r.get("installation_id")}
        for iid, brow in before_map.items():
            if iid in after_map:
                # status should be unchanged
                assert brow.get("status") == after_map[iid].get("status"), \
                    f"status changed for {iid} after dry_run"

    def test_hard_guard_blocked_400(self, auth_headers, sample_ids):
        """If selection includes a pat_status=='valid' row and no
        confirm_text=REVOKE, server must return 400 hard_guard_blocked
        BEFORE it gets to the feature-flag gate."""
        # Need at least one valid installation_id
        rows = requests.get(f"{API}/admin/github/connections?view=all", headers=auth_headers, timeout=45).json()["rows"]
        valid_ids = [r["installation_id"] for r in rows
                     if r.get("installation_id") and r.get("pat_status") == "valid"]
        if not valid_ids:
            pytest.skip("No valid installations to trigger hard guard")

        r = requests.post(
            f"{API}/admin/github/bulk-revoke",
            headers=auth_headers,
            json={"installation_ids": valid_ids[:2], "dry_run": False},
            timeout=60,
        )
        assert r.status_code == 400, f"expected 400, got {r.status_code} {r.text}"
        detail = r.json()["detail"]
        assert detail["error"] == "hard_guard_blocked"
        assert detail["valid_count"] >= 1

    def test_live_verification_pending_403(self, auth_headers, sample_ids):
        """With feature flag OFF and confirm_text=REVOKE and a revokable
        (non-valid) selection (so hard-guard passes) — expect 403 gate."""
        rows = requests.get(f"{API}/admin/github/connections?view=revokable", headers=auth_headers, timeout=45).json()["rows"]
        revokable_ids = [r["installation_id"] for r in rows if r.get("installation_id")]

        # If no revokable rows exist, use ANY id + REVOKE confirm (this
        # will make valid_count>0 hit hard-guard OR bypass to 403 if
        # nothing is valid). To reliably hit 403 we need selection with
        # valid_count == 0 OR confirm_text==REVOKE. Use REVOKE confirm.
        target_ids = revokable_ids[:2] if revokable_ids else sample_ids[:2]

        r = requests.post(
            f"{API}/admin/github/bulk-revoke",
            headers=auth_headers,
            json={"installation_ids": target_ids,
                  "dry_run": False, "confirm_text": "REVOKE"},
            timeout=60,
        )
        # Must be 403 live_verification_pending (or 429 rate-limited from prior test)
        assert r.status_code in (403, 429), f"expected 403, got {r.status_code} {r.text}"
        if r.status_code == 403:
            detail = r.json()["detail"]
            assert detail["error"] == "live_verification_pending"


# ─── /admin/github/flag-idle ──────────────────────────────────────────
class TestFlagIdle:
    def test_flag_idle_success(self, auth_headers):
        rows = requests.get(f"{API}/admin/github/connections?view=all", headers=auth_headers, timeout=45).json()["rows"]
        ids = [r["installation_id"] for r in rows if r.get("installation_id")]
        if not ids:
            pytest.skip("No installation_ids to flag")

        target = ids[0]
        r = requests.post(
            f"{API}/admin/github/flag-idle",
            headers=auth_headers,
            json={"installation_ids": [target], "reason": "TEST_e2e_test_flag_idle"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert "flagged" in data

        # Verify persistence via connections list
        after = requests.get(f"{API}/admin/github/connections?view=all", headers=auth_headers, timeout=45).json()["rows"]
        row = next((x for x in after if x.get("installation_id") == target), None)
        assert row is not None
        assert row["re_engage_flagged"] is True
        # Status should NOT have flipped to disconnected due to flag-idle
        assert row["status"] != "disconnected" or row.get("status") is None
