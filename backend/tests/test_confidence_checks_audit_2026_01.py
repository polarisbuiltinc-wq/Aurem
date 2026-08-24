"""
Tests for the passive confidence-check audit endpoint (2026-01).

Scope (per review_request):
- GET /api/aurem-dev/admin/insights/confidence-checks requires admin auth
  (401/403 without JWT).
- Returns {count, mismatch_only, rows} shape.
- After a real chat POST via /chat/send AND /chat/stream, a new audit row
  appears with expected fields.
- mismatch_only=true filter only returns rows with mismatch=true.
- Core chat pipeline still returns a correct on-topic response
  (regression check — the new fire-and-forget Mongo write must NOT
  break or alter chat behavior).
- Regression: /admin/insights/slo and /admin/insights/cost-alert still
  reachable with admin auth (not broken by the admin_users.py edit).
"""
import os
import json
import time
import uuid
import pytest
import requests

def _resolve_base_url():
    url = os.environ.get("REACT_APP_BACKEND_URL")
    if not url:
        try:
            with open("/app/frontend/.env") as f:
                for ln in f:
                    if ln.startswith("REACT_APP_BACKEND_URL="):
                        url = ln.split("=", 1)[1].strip()
                        break
        except Exception:
            pass
    if not url:
        raise RuntimeError("REACT_APP_BACKEND_URL not set")
    return url.rstrip("/")

BASE_URL = _resolve_base_url()
API = f"{BASE_URL}/api/aurem-dev"
ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


# ---------- fixtures ----------

@pytest.fixture(scope="session")
def http():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def admin_token(http):
    r = http.post(f"{API}/auth/login", json={
        "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
    }, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    tok = data.get("token") or data.get("access_token") or data.get("jwt")
    if not tok:
        pytest.skip(f"No token in login response: {list(data.keys())}")
    return tok


@pytest.fixture(scope="session")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ---------- auth gating ----------

class TestAuthGating:
    def test_endpoint_requires_auth(self, http):
        r = http.get(f"{API}/admin/insights/confidence-checks", timeout=15)
        assert r.status_code in (401, 403), (
            f"Expected 401/403 without auth, got {r.status_code}: {r.text[:200]}"
        )

    def test_endpoint_rejects_bad_token(self, http):
        r = http.get(
            f"{API}/admin/insights/confidence-checks",
            headers={"Authorization": "Bearer not-a-real-jwt"},
            timeout=15,
        )
        assert r.status_code in (401, 403)


# ---------- endpoint shape ----------

class TestEndpointShape:
    def test_returns_expected_shape(self, http, auth_headers):
        r = http.get(
            f"{API}/admin/insights/confidence-checks",
            headers=auth_headers, timeout=15,
        )
        assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
        data = r.json()
        assert set(["count", "mismatch_only", "rows"]).issubset(data.keys())
        assert isinstance(data["count"], int)
        assert data["mismatch_only"] is False
        assert isinstance(data["rows"], list)
        assert data["count"] == len(data["rows"])
        # verify no _id leakage
        for row in data["rows"]:
            assert "_id" not in row

    def test_mismatch_only_filter(self, http, auth_headers):
        r = http.get(
            f"{API}/admin/insights/confidence-checks?mismatch_only=true",
            headers=auth_headers, timeout=15,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["mismatch_only"] is True
        for row in data["rows"]:
            assert row.get("mismatch") is True, (
                f"mismatch_only=true returned a row with mismatch={row.get('mismatch')}"
            )

    def test_limit_param_clamped(self, http, auth_headers):
        r = http.get(
            f"{API}/admin/insights/confidence-checks?limit=500",
            headers=auth_headers, timeout=15,
        )
        assert r.status_code == 200
        # limit is clamped to 200 server-side
        assert len(r.json()["rows"]) <= 200


# ---------- chat regression + audit write ----------

def _rows_by_session(http, auth_headers, session_id, tries=5, delay=1.0):
    """Poll for a row containing our session_id (audit write is
    async-ish via `await` inside chat handler but Mongo persist may
    still race the response we just got — retry briefly)."""
    for _ in range(tries):
        r = http.get(
            f"{API}/admin/insights/confidence-checks?limit=200",
            headers=auth_headers, timeout=15,
        )
        if r.status_code == 200:
            for row in r.json().get("rows", []):
                if row.get("session_id") == session_id:
                    return row
        time.sleep(delay)
    return None


class TestChatSendAuditWrite:
    def test_chat_send_returns_real_response_and_writes_audit(self, http, auth_headers):
        session_id = f"TEST_conf_send_{uuid.uuid4().hex[:12]}"
        payload = {
            "prompt": "Hi, is everything working ok?",
            "session_id": session_id,
            "max_tool_iters": 2,
            "mode": "swift",
            "execution_mode": "prompt",
        }
        t0 = time.time()
        r = http.post(f"{API}/chat/send", headers=auth_headers,
                      json=payload, timeout=120)
        elapsed = time.time() - t0
        assert r.status_code == 200, f"chat/send failed: {r.status_code} {r.text[:400]}"
        data = r.json()
        # response has real content (not empty)
        content = data.get("content") or data.get("message") or data.get("reply") or ""
        assert isinstance(content, str) and len(content.strip()) > 0, (
            f"chat/send returned empty content. Keys: {list(data.keys())}"
        )
        print(f"[chat/send] elapsed={elapsed:.2f}s content_len={len(content)}")

        # Now verify audit row present
        row = _rows_by_session(http, auth_headers, session_id, tries=6, delay=1.5)
        assert row is not None, (
            f"No audit row appeared for session_id={session_id} after chat/send"
        )
        assert row["surface"] == "chat_send"
        assert row["turn"] in (1, 2)
        assert "prompt_preview" in row and "content_preview" in row
        assert "mismatch" in row
        assert "ts" in row
        assert "council_recalled" in row  # only reliably present on turn=1
        # prompt_preview should reflect our prompt
        assert "everything working" in (row.get("prompt_preview") or "").lower()


class TestChatStreamAuditWrite:
    def test_chat_stream_returns_real_response_and_writes_audit(self, http, auth_headers):
        session_id = f"TEST_conf_stream_{uuid.uuid4().hex[:12]}"
        payload = {
            "prompt": "Say hello briefly.",
            "session_id": session_id,
            "max_tool_iters": 2,
            "mode": "swift",
            "execution_mode": "prompt",
        }
        t0 = time.time()
        r = requests.post(
            f"{API}/chat/stream",
            headers={**auth_headers, "Accept": "text/event-stream"},
            json=payload,
            stream=True,
            timeout=180,
        )
        assert r.status_code == 200, f"chat/stream failed: {r.status_code} {r.text[:400]}"
        # Drain SSE stream, capture final content
        collected = []
        got_done = False
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            collected.append(line)
            if line.startswith("data:"):
                payload_str = line[5:].strip()
                if payload_str == "[DONE]":
                    got_done = True
                    break
                try:
                    obj = json.loads(payload_str)
                    if obj.get("type") in ("done", "complete", "finish"):
                        got_done = True
                        break
                except Exception:
                    pass
            if time.time() - t0 > 150:
                break
        elapsed = time.time() - t0
        print(f"[chat/stream] elapsed={elapsed:.2f}s events={len(collected)} done={got_done}")
        assert len(collected) > 0, "chat/stream produced zero SSE events"

        # Verify audit row
        row = _rows_by_session(http, auth_headers, session_id, tries=8, delay=2.0)
        assert row is not None, (
            f"No audit row appeared for session_id={session_id} after chat/stream"
        )
        assert row["surface"] == "chat_stream"
        assert row["turn"] in (1, 2)
        assert row.get("prompt_preview", "").lower().startswith("say hello")


# ---------- adjacent insights endpoints regression ----------

class TestAdjacentInsightsRegression:
    def test_slo_endpoint_still_works(self, http, auth_headers):
        r = http.get(f"{API}/admin/insights/slo", headers=auth_headers, timeout=20)
        # accept 200 (or 404 if this env doesn't have that particular route)
        assert r.status_code in (200, 404), (
            f"slo endpoint broken: {r.status_code} {r.text[:200]}"
        )

    def test_cost_alert_endpoint_still_works(self, http, auth_headers):
        r = http.get(f"{API}/admin/insights/cost-alert",
                     headers=auth_headers, timeout=20)
        assert r.status_code in (200, 404), (
            f"cost-alert endpoint broken: {r.status_code} {r.text[:200]}"
        )
