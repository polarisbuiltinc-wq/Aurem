"""Iter 371 — Test setup: signup a user for wizard scenario testing."""
import os, sys, requests, time, secrets, json

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else "https://launch-pad-237.preview.emergentagent.com"
API = f"{BASE}/api/aurem-dev"

email = f"TEST_wiz_{int(time.time())}_{secrets.token_hex(3)}@aurem.dev"
pw = "AuremTest2026!"

# form_age_ms must be reasonable
r = requests.post(f"{API}/auth/signup", json={
    "email": email, "password": pw, "name": "wiz test",
    "honeypot": "", "form_age_ms": 5000,
})
print("signup:", r.status_code, r.text[:300])
r.raise_for_status()

# Login to get JWT
r2 = requests.post(f"{API}/auth/login", json={"email": email, "password": pw})
print("login:", r2.status_code, r2.text[:200])
r2.raise_for_status()
data = r2.json()
token = data.get("token") or data.get("access_token") or data.get("jwt")
if not token:
    # Look for a nested field
    print("Login payload keys:", list(data.keys()))
    sys.exit(1)

# Save data
out = {"email": email, "password": pw, "token": token}
with open("/app/test_reports/iter371_wizard/user.json", "w") as f:
    json.dump(out, f)
print("saved user:", email)
