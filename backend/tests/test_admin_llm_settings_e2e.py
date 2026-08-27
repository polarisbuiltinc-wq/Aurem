"""
E2E backend tests for Admin Self-Serve LLM Settings feature (iter 290, 2026-01).
Tests the 6 admin endpoints via public URL with real admin JWT.
"""
import os
import re
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

ADMIN_EMAIL = "test@aurem.dev"
ADMIN_PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="session")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="session")
def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@pytest.fixture(scope="session", autouse=True)
def cleanup(headers):
    """Clean up any TEST_ configs before/after."""
    def _sweep():
        try:
            r = requests.get(f"{API}/admin/llm/configs", headers=headers, timeout=15)
            if r.status_code == 200:
                for c in r.json().get("configs", []):
                    if (c.get("label") or "").startswith("TEST_"):
                        requests.delete(f"{API}/admin/llm/configs/{c['config_id']}", headers=headers, timeout=15)
        except Exception:
            pass
    _sweep()
    yield
    _sweep()


# ── Non-admin access ──────────────────────────────────────────────
class TestNonAdminAccess:
    def test_list_without_auth(self):
        r = requests.get(f"{API}/admin/llm/configs", timeout=15)
        assert r.status_code in (401, 403), r.text

    def test_create_without_auth(self):
        r = requests.post(f"{API}/admin/llm/configs", json={
            "label": "x", "role": "chat", "base_url": "https://x/v1", "model": "m", "api_key": "k"
        }, timeout=15)
        assert r.status_code in (401, 403), r.text

    def test_delete_without_auth(self):
        r = requests.delete(f"{API}/admin/llm/configs/anything", timeout=15)
        assert r.status_code in (401, 403), r.text

    def test_test_without_auth(self):
        r = requests.post(f"{API}/admin/llm/configs/anything/test", timeout=15)
        assert r.status_code in (401, 403), r.text


# ── CRUD & rotation ───────────────────────────────────────────────
class TestCRUD:
    created_id = None
    original_key = "sk-testkey-abcdef9999"
    rotated_key = "sk-rotated-newkey1234"

    def test_list_initial(self, headers):
        r = requests.get(f"{API}/admin/llm/configs", headers=headers, timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        assert isinstance(data.get("configs"), list)

    def test_create(self, headers):
        payload = {
            "label": "TEST_qwen_main",
            "role": "chat",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-plus",
            "api_key": self.original_key,
            "params": {"temperature": 0.7},
        }
        r = requests.post(f"{API}/admin/llm/configs", headers=headers, json=payload, timeout=15)
        assert r.status_code == 200, r.text
        cfg = r.json()["config"]
        assert cfg["label"] == "TEST_qwen_main"
        assert cfg["role"] == "chat"
        assert cfg["model"] == "qwen-plus"
        assert cfg["key_hint"] == "…9999"
        # Full key must never appear in response
        assert self.original_key not in r.text
        assert "api_key" not in cfg
        assert "api_key_enc" not in cfg
        TestCRUD.created_id = cfg["config_id"]

    def test_list_shows_created(self, headers):
        r = requests.get(f"{API}/admin/llm/configs", headers=headers, timeout=15)
        assert r.status_code == 200
        cfgs = r.json()["configs"]
        found = [c for c in cfgs if c["config_id"] == TestCRUD.created_id]
        assert len(found) == 1
        assert found[0]["key_hint"] == "…9999"
        # Body must not contain full key or api_key_enc
        assert self.original_key not in r.text
        assert "api_key_enc" not in r.text

    def test_update_keep_key(self, headers):
        # Update label with blank api_key -> must keep existing key
        payload = {"label": "TEST_qwen_renamed", "api_key": None}
        r = requests.put(f"{API}/admin/llm/configs/{TestCRUD.created_id}", headers=headers, json=payload, timeout=15)
        assert r.status_code == 200, r.text
        cfg = r.json()["config"]
        assert cfg["label"] == "TEST_qwen_renamed"
        assert cfg["key_hint"] == "…9999"  # unchanged

        # Also try empty string
        r = requests.put(f"{API}/admin/llm/configs/{TestCRUD.created_id}", headers=headers,
                         json={"api_key": ""}, timeout=15)
        assert r.status_code == 200
        assert r.json()["config"]["key_hint"] == "…9999"

    def test_update_rekey(self, headers):
        r = requests.put(f"{API}/admin/llm/configs/{TestCRUD.created_id}", headers=headers,
                         json={"api_key": self.rotated_key}, timeout=15)
        assert r.status_code == 200
        cfg = r.json()["config"]
        assert cfg["key_hint"] == "…1234"
        assert self.rotated_key not in r.text

    def test_set_active(self, headers):
        r = requests.post(f"{API}/admin/llm/configs/{TestCRUD.created_id}/set-active",
                          headers=headers, json={"role": "chat"}, timeout=15)
        assert r.status_code == 200
        by_role = r.json()["active_by_role"]
        assert by_role["chat"] == TestCRUD.created_id

        # confirm via list
        r = requests.get(f"{API}/admin/llm/configs", headers=headers, timeout=15)
        cfgs = r.json()["configs"]
        me = [c for c in cfgs if c["config_id"] == TestCRUD.created_id][0]
        assert "chat" in me["is_active_per_role"]

    def test_test_endpoint_fake_key(self, headers):
        """Test with (now rotated) fake key against real DashScope — expect auth failure, no key leak."""
        r = requests.post(f"{API}/admin/llm/configs/{TestCRUD.created_id}/test", headers=headers, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        # The fake key against real DashScope should fail with auth error, but response should be well-formed
        assert "latency_ms" in data
        # Since key is invalid, the "ok" field (from inner result overriding router's "ok": True)
        # should be False, with a categorized error like 'auth' or a network error
        assert data.get("ok") is False, f"expected fake key to fail, got: {data}"
        assert data.get("error") in ("auth", "model_not_found", "network_error", "network_timeout") \
            or (data.get("error") or "").startswith("http_"), f"unexpected error: {data}"
        # Key must NEVER appear in response body
        assert self.rotated_key not in r.text, "SECURITY: rotated key leaked in test response"
        assert self.original_key not in r.text, "SECURITY: original key leaked in test response"

    def test_delete(self, headers):
        r = requests.delete(f"{API}/admin/llm/configs/{TestCRUD.created_id}", headers=headers, timeout=15)
        assert r.status_code == 200

        # confirm gone via list
        r = requests.get(f"{API}/admin/llm/configs", headers=headers, timeout=15)
        cfgs = r.json()["configs"]
        assert not any(c["config_id"] == TestCRUD.created_id for c in cfgs)


# ── Validation ────────────────────────────────────────────────────
class TestValidation:
    def test_invalid_role(self, headers):
        r = requests.post(f"{API}/admin/llm/configs", headers=headers, json={
            "label": "TEST_bad_role", "role": "banana", "base_url": "https://x/v1",
            "model": "m", "api_key": "k"
        }, timeout=15)
        assert r.status_code == 400

    def test_missing_field_label(self, headers):
        r = requests.post(f"{API}/admin/llm/configs", headers=headers, json={
            "label": "", "role": "chat", "base_url": "https://x/v1",
            "model": "m", "api_key": "k"
        }, timeout=15)
        assert r.status_code == 400, r.text

    def test_missing_field_model(self, headers):
        r = requests.post(f"{API}/admin/llm/configs", headers=headers, json={
            "label": "TEST_x", "role": "chat", "base_url": "https://x/v1",
            "model": "", "api_key": "k"
        }, timeout=15)
        assert r.status_code == 400, r.text

    def test_update_nonexistent(self, headers):
        r = requests.put(f"{API}/admin/llm/configs/does-not-exist-xyz", headers=headers,
                         json={"label": "TEST_x"}, timeout=15)
        assert r.status_code == 404

    def test_delete_nonexistent(self, headers):
        r = requests.delete(f"{API}/admin/llm/configs/does-not-exist-xyz", headers=headers, timeout=15)
        assert r.status_code == 404


# ── MOCK_LLM end-to-end via ORA chat ─────────────────────────────
class TestMockOverride:
    def test_ora_chat_message_returns_mock(self, headers):
        # Create session
        r = requests.post(f"{API}/ora-chat/sessions", headers=headers, json={}, timeout=15)
        if r.status_code == 404:
            pytest.skip("ora-chat sessions endpoint not present at expected path")
        assert r.status_code in (200, 201), r.text
        body = r.json()
        session = body.get("session") or body
        session_id = session.get("session_id") or session.get("id")
        assert session_id, r.text

        # Send message
        r = requests.post(f"{API}/ora-chat/message", headers=headers,
                          json={"session_id": session_id, "content": "hello test", "mode": "advise"},
                          timeout=60, stream=False)
        # This may be SSE — accept 200
        assert r.status_code == 200, r.text[:500]
        body_text = r.text
        # In MOCK_LLM mode we expect mock indicator in stream
        assert ("mock" in body_text.lower()) or ("MOCK" in body_text), body_text[:800]
