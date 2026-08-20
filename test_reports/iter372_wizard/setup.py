"""Iter 372 — Setup: signup a test user for wizard single-repo auto-select verification."""
import os, sys, time, secrets, json, requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://launch-pad-237.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api/aurem-dev"

email = f"TEST_wiz372_{int(time.time())}_{secrets.token_hex(3)}@aurem.dev"
pw = "AuremTest2026!"

r = requests.post(f"{API}/auth/signup", json={
    "email": email, "password": pw, "name": "wiz test 372",
    "honeypot": "", "form_age_ms": 5000,
})
print("signup:", r.status_code, r.text[:300])
r.raise_for_status()

r2 = requests.post(f"{API}/auth/login", json={"email": email, "password": pw})
print("login:", r2.status_code, r2.text[:200])
r2.raise_for_status()
data = r2.json()
token = data.get("token") or data.get("access_token") or data.get("jwt")
if not token:
    print("Login payload keys:", list(data.keys()))
    sys.exit(1)

# Fetch user_id from /auth/me
r3 = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
print("me:", r3.status_code, r3.text[:200])
user = r3.json()
user_id = user.get("user_id") or user.get("id") or user.get("_id")

out = {"email": email, "password": pw, "token": token, "user_id": user_id}
with open("/app/test_reports/iter372_wizard/user.json", "w") as f:
    json.dump(out, f)
print("saved user:", email, "user_id:", user_id)
