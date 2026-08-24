"""Backend tests for surgical admin dashboard fixes (Jan 2026)."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bin-context-pat.preview.emergentagent.com").rstrip("/")
API_PREFIX = "/api/aurem-dev"
EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def token():
    s = requests.Session()
    # Try common login endpoints
    r = s.post(f"{BASE_URL}{API_PREFIX}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"Login failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    tok = data.get("access_token") or data.get("token") or data.get("data", {}).get("access_token")
    if not tok:
        # Cookie-based
        return {"session": s, "cookies": s.cookies}
    return {"token": tok, "session": s}


def _headers(token):
    if "token" in token:
        return {"Authorization": f"Bearer {token['token']}"}
    return {}


def _get(token, path):
    return token["session"].get(f"{BASE_URL}{API_PREFIX}{path}", headers=_headers(token), timeout=30)


def _post(token, path):
    return token["session"].post(f"{BASE_URL}{API_PREFIX}{path}", headers=_headers(token), timeout=30)


# --- agent-performance ---
def test_agent_performance_returns_real_data(token):
    r = _get(token, "/admin/agent-performance")
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    # Accept list or wrapped
    payload = data.get("data", data)
    items = payload.get("per_model_30d") or payload.get("models") or (data if isinstance(data, list) else [])
    assert isinstance(items, list) and len(items) > 0, f"Expected non-empty per_model list, got: {data}"
    assert payload.get("source") == "customer_chat_cost", f"Expected source=customer_chat_cost, got: {payload.get('source')}"
    models = {i["model"] for i in items}
    assert any("glm" in m for m in models), f"Expected glm-5.2 model in {models}"
    assert any("deepseek" in m for m in models), f"Expected deepseek model in {models}"
    row = items[0]
    for f in ["model", "calls", "total_cost_usd", "avg_input_tokens", "avg_output_tokens"]:
        assert f in row, f"Missing field {f} in row: {row}"


# --- architecture ---
def test_architecture_still_works(token):
    r = _get(token, "/admin/architecture")
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    payload = data.get("data", data)
    assert "integrations" in payload, f"missing integrations: {list(payload.keys())}"
    assert "github_app" in payload["integrations"], f"missing github_app: {payload['integrations']}"
    assert "services" in payload


# --- repair-orphaned-installations dry-run ---
def test_repair_orphaned_dry_run(token):
    r = token["session"].post(
        f"{BASE_URL}{API_PREFIX}/admin/github-app/repair-orphaned-installations?dry_run=true",
        headers=_headers(token), timeout=60
    )
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    payload = data.get("data", data)
    assert payload.get("dry_run") is True
    assert payload.get("repaired_count", -1) == 0
    assert payload.get("repaired") == []
    assert "still_broken" in payload
    assert "scanned" in payload


# --- health-score ---
def test_health_score(token):
    r = _get(token, "/admin/health-score")
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    payload = data.get("data", data)
    cats = payload.get("categories", payload)
    assert "devops_infra" in cats or any("devops" in str(k) for k in cats.keys()), f"missing devops_infra in {list(cats.keys()) if isinstance(cats, dict) else cats}"


# --- no regression ---
@pytest.mark.parametrize("endpoint", [
    "/admin/dashboard",
    "/admin/pulse",
    "/admin/llm-credits",
])
def test_admin_endpoints_no_regression(token, endpoint):
    r = _get(token, endpoint)
    assert r.status_code == 200, f"{endpoint} => {r.status_code}: {r.text[:300]}"
    # Should be valid JSON
    r.json()
