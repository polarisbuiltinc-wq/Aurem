"""
Tests for the 2026-08-22 Prompt Starter Panel + onboarding auto-scan feature.

Covers:
- GET /api/aurem-dev/findings/starter-suggestions (auth, shape, ownership)
- POST /api/aurem-dev/cto/projects/add (still returns promptly with new bg task,
  invalid repo doesn't crash the endpoint)
"""
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://launch-pad-237.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

FOUNDER_EMAIL = "test@aurem.dev"
FOUNDER_PASSWORD = "AuremTest2026!"
FOUNDER_PROJECT_ID = "p_demo_a"  # owned by test_admin_001 (== founder login user_id)


@pytest.fixture(scope="module")
def founder_token():
    r = requests.post(f"{API}/auth/login", json={
        "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD
    }, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    assert data.get("ok") is True and data.get("token")
    return data["token"]


@pytest.fixture(scope="module")
def headers(founder_token):
    return {"Authorization": f"Bearer {founder_token}", "Content-Type": "application/json"}


# ── /findings/starter-suggestions ────────────────────────────────────
class TestStarterSuggestions:
    def test_requires_auth(self):
        r = requests.get(f"{API}/findings/starter-suggestions",
                         params={"project_id": FOUNDER_PROJECT_ID}, timeout=10)
        # missing Authorization header -> current_dev should reject
        assert r.status_code in (401, 403), f"expected 401/403 for missing auth, got {r.status_code}: {r.text[:200]}"

    def test_owned_project_returns_5_plus_suggestions(self, headers):
        r = requests.get(f"{API}/findings/starter-suggestions",
                         params={"project_id": FOUNDER_PROJECT_ID},
                         headers=headers, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ok") is True
        assert data.get("project_id") == FOUNDER_PROJECT_ID
        sug = data.get("suggestions")
        assert isinstance(sug, list)
        assert len(sug) >= 5, f"expected >=5 suggestions, got {len(sug)}"
        required_keys = {"slug", "icon_hint", "label", "example", "personalized"}
        for s in sug:
            missing = required_keys - set(s.keys())
            assert not missing, f"suggestion missing keys {missing}: {s}"
            assert isinstance(s["slug"], str) and s["slug"]
            assert isinstance(s["example"], str) and s["example"]

    def test_personalized_appear_first_when_present(self, headers):
        r = requests.get(f"{API}/findings/starter-suggestions",
                         params={"project_id": FOUNDER_PROJECT_ID, "limit": 20},
                         headers=headers, timeout=15)
        assert r.status_code == 200
        sug = r.json()["suggestions"]
        # find the index of the last personalized item and the first generic
        pers_idxs = [i for i, s in enumerate(sug) if s.get("personalized")]
        gen_idxs = [i for i, s in enumerate(sug) if not s.get("personalized")]
        if pers_idxs and gen_idxs:
            assert max(pers_idxs) < min(gen_idxs), \
                f"personalized items must precede generic ones; got personalized={pers_idxs}, generic={gen_idxs}"
        # generic pool always present as pad -> at least 5 generics
        assert len(gen_idxs) >= 5

    def test_nonexistent_project_returns_404(self, headers):
        r = requests.get(f"{API}/findings/starter-suggestions",
                         params={"project_id": "p_does_not_exist_zzzzz"},
                         headers=headers, timeout=10)
        assert r.status_code == 404
        assert "project_not_found" in r.text

    def test_other_users_project_returns_403(self, headers):
        """Create a fake project doc owned by a different user and confirm
        403 not_your_project. Uses direct DB access via the running backend
        by creating one via the same collection through a separate
        insert—but since we can't insert directly, look for any existing
        project owned by a non-test_admin_001 user first."""
        # Query DB via a known project we cannot own: we don't have a
        # generic listing across users. Instead, try a well-known
        # project id that shouldn't be ours. If none is available, skip.
        candidate = "p_other_user_zzz"
        # We can't easily create an alien project via API. Attempt
        # ownership boundary via mongo shell if available.
        import subprocess
        db_name = os.environ.get("DB_NAME", "test_database")
        mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
        try:
            subprocess.run(
                ["mongosh", mongo_url + "/" + db_name, "--quiet", "--eval",
                 f'db.cto_projects.insertOne({{project_id:"{candidate}",user_id:"someone_else_999",github_owner:"o",github_repo:"r",created_at:new Date()}})'],
                check=False, capture_output=True, timeout=10
            )
        except Exception as e:
            pytest.skip(f"mongosh unavailable, cannot set up other-user project: {e}")

        try:
            r = requests.get(f"{API}/findings/starter-suggestions",
                             params={"project_id": candidate},
                             headers=headers, timeout=10)
            assert r.status_code == 403, f"expected 403, got {r.status_code}: {r.text[:200]}"
            assert "not_your_project" in r.text
        finally:
            # cleanup
            try:
                subprocess.run(
                    ["mongosh", mongo_url + "/" + db_name, "--quiet", "--eval",
                     f'db.cto_projects.deleteOne({{project_id:"{candidate}"}})'],
                    check=False, capture_output=True, timeout=10
                )
            except Exception:
                pass


# ── POST /projects/add still responds promptly with new bg task ──────
class TestProjectAddOnboardingScan:
    def test_add_project_invalid_repo_does_not_crash(self, headers):
        """Even with an invalid repo/PAT the endpoint should not 5xx
        because run_onboarding_scan is fire-and-forget with swallowed errors.
        The endpoint may still 400/403 on PAT verification, but never 500."""
        suffix = uuid.uuid4().hex[:6]
        payload = {
            "github_url": f"https://github.com/aurem-demo/does-not-exist-{suffix}",
            "auth_method": "pat",
            "pat": "ghp_invalid_dummy_token_for_test_only_1234567890",
            "branch": "main",
            "tech_stack": "react-fastapi",
        }
        t0 = time.time()
        r = requests.post(f"{API}/cto/projects/add", json=payload,
                          headers=headers, timeout=30)
        elapsed = time.time() - t0
        assert r.status_code < 500, f"5xx from projects/add: {r.status_code} {r.text[:300]}"
        # PAT will fail verification, expect 400/403 — anything but 5xx OK
        # Timing: bg task must not block; allow up to 20s (PAT verify does a network call).
        assert elapsed < 25, f"projects/add too slow ({elapsed:.1f}s); bg task may be blocking"

    def test_add_project_response_is_prompt_when_success(self, headers):
        """Try to add a project using a fake PAT that will fail; the response
        must come back quickly regardless (indexing + onboarding-scan tasks
        are fire-and-forget)."""
        suffix = uuid.uuid4().hex[:8]
        payload = {
            "github_url": "https://github.com/owner/TEST_repo_" + suffix,
            "auth_method": "pat",
            "pat": "ghp_dummy_pat_will_fail_" + suffix,
            "branch": "main",
            "tech_stack": "react-fastapi",
        }
        t0 = time.time()
        r = requests.post(f"{API}/cto/projects/add", json=payload,
                          headers=headers, timeout=25)
        elapsed = time.time() - t0
        # We don't assert 200 here; we assert non-5xx and prompt timing.
        assert r.status_code < 500, f"5xx: {r.status_code} {r.text[:300]}"
        assert elapsed < 25, f"too slow: {elapsed:.1f}s"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
