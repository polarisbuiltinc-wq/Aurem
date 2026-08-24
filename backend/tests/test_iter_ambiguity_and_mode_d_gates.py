"""
Tests for 2026-08-25 architectural gates:
  - Ambiguity-gate on POST /cto/tasks/submit
  - Reachability-scope gate in services.mode_d_debugger.run_debug_session()
  - google_oauth callback appends &provider=google

Ref: review_request iteration_ambiguity_reachability_google_oauth.
"""
import os
import sys
import re
import asyncio
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

CREDS = {"email": "test@aurem.dev", "password": "AuremTest2026!"}
PROJECT_ID = "p_norepotest"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json=CREDS, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ── Ambiguity-gate: vague tasks ────────────────────────────────────
class TestAmbiguityGateVague:
    @pytest.mark.parametrize("task_text", [
        "fix it",
        "fix the bugs",
        "make it better",
        "improve the site",
        "help",           # <4 words, no file
        "do stuff",       # <4 words
    ])
    def test_vague_task_returns_needs_clarification(self, auth_headers, task_text):
        r = requests.post(
            f"{API}/cto/tasks/submit",
            headers=auth_headers,
            json={"project_id": PROJECT_ID, "task": task_text},
            timeout=30,
        )
        assert r.status_code == 200, f"expected 200 for vague, got {r.status_code}: {r.text[:300]}"
        data = r.json()
        assert data.get("ok") is False
        assert data.get("needs_clarification") is True
        assert "message" in data and isinstance(data["message"], str) and len(data["message"]) > 0
        # No task_id leaked
        assert "task_id" not in data


# ── Ambiguity-gate: concrete tasks pass through ────────────────────
class TestAmbiguityGateConcrete:
    @pytest.mark.parametrize("task_text", [
        "fix the signup form validation in Signup.jsx",
        "update the header colors in App.css to dark mode",
        'add validation for "email" field on register endpoint',
    ])
    def test_concrete_task_passes_gate(self, auth_headers, task_text):
        r = requests.post(
            f"{API}/cto/tasks/submit",
            headers=auth_headers,
            json={"project_id": PROJECT_ID, "task": task_text},
            timeout=30,
        )
        # Concrete task should NOT return needs_clarification. It may
        # succeed OR hit the known GitHub-App-access error (expected on
        # fixture project p_norepotest per review notes).
        try:
            data = r.json()
        except Exception:
            pytest.fail(f"non-JSON: {r.text[:300]}")
        assert data.get("needs_clarification") is not True, (
            f"concrete task incorrectly flagged as ambiguous: {data}"
        )
        # Expected outcomes: success (task_id), or GitHub-App-access error 4xx
        if r.status_code == 200:
            # Either created a task or hit some other server-handled path
            assert "task_id" in data or "detail" in data
        else:
            assert r.status_code in (400, 402, 403, 404, 409), r.status_code


# ── Reachability-scope gate: direct unit test on run_debug_session ─
class TestModeDReachabilityGate:
    def test_nonexistent_file_ref_returns_clarify(self, monkeypatch):
        sys.path.insert(0, "/app/backend")
        from services import mode_d_debugger as mdd

        # Force read_file to return None (simulate: file not in repo)
        async def _empty_read(*args, **kwargs):
            return None
        monkeypatch.setattr(mdd, "read_file", _empty_read)

        # No fast-path collision: use a synthetic stack-trace line that
        # includes a plausible-but-nonexistent file so file_refs is non-empty
        # AND has_concrete_debug_signal returns True.
        user_msg = (
            "TypeError: Cannot read properties of undefined "
            "at src/pages/DoesNotExist.jsx:42:17"
        )

        class _FakeDB:
            def __getattr__(self, _):
                class _C:
                    async def insert_one(self, *a, **k): return None
                    async def update_one(self, *a, **k): return None
                    async def find_one(self, *a, **k): return None
                return _C()

        result = asyncio.get_event_loop().run_until_complete(
            mdd.run_debug_session(
                db=_FakeDB(),
                user_message=user_msg,
                repo_owner="tjsandhu",
                repo_name="aurem",
                repo_ctx="",
                user_id="test_admin_001",
                project_id=PROJECT_ID,
                f12_payload=None,
                github_pat="ghp_fake_but_truthy_pat_for_gate_check",
            )
        )

        assert result.get("clarify") is True, f"expected clarify=True, got {result}"
        reply = result.get("ora_reply", "")
        assert "don't see that in your connected repo" in reply, (
            f"expected honest 'not in repo' reply, got: {reply[:400]}"
        )
        # Must NOT have fabricated a diagnosis or auto-fix
        assert result.get("can_auto_fix") is False
        assert result.get("commit_task") == ""


# ── google_oauth callback appends &provider=google ────────────────
class TestGoogleOAuthProviderTag:
    def test_source_has_provider_google_tag(self):
        with open("/app/backend/routers/google_oauth.py") as f:
            src = f.read()
        # The success redirect must include &provider=google
        assert "&provider=google" in src, "google_oauth.py missing &provider=google"

    def test_oauth_finish_reads_provider(self):
        with open("/app/frontend/src/pages/OAuthFinish.jsx") as f:
            src = f.read()
        assert 'parts.get("provider")' in src
        assert "google=missing_token" in src
        assert "github=missing_token" in src
        # useRef guard present
        assert "hasRun" in src and "useRef(false)" in src
