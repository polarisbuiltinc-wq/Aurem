"""Backend tests: OPTIONAL phone field on signup + /auth/update-phone.

Covers the founder-approved change from mandatory phone to optional
(2026-08-25). Tests:
  1. Signup with blank phone -> succeeds, /auth/me returns phone=None
  2. Signup with valid phone -> normalized to E.164
  3. Signup with invalid phone -> 400 error, no account created
  4. Signup with no `phone` field at all -> regression pass
  5. Login regression with the seeded test@aurem.dev user
  6. POST /auth/update-phone: set / clear / invalid
"""
import os
import time
import uuid
import requests
import pytest

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api/aurem-dev/auth"

# Preview seeded admin (see /app/memory/test_credentials.md)
SEED_EMAIL = "test@aurem.dev"
SEED_PASSWORD = "AuremTest2026!"


def _fresh_email(tag: str) -> str:
    return f"phone-qa-{tag}-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}@aurem.dev"


@pytest.fixture(scope="module")
def sess() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ------------------------------ Signup flow ------------------------------


def test_signup_no_phone_field_regression(sess: requests.Session):
    """Existing behavior: payload with no `phone` key still works."""
    email = _fresh_email("nofield")
    r = sess.post(f"{API}/signup", json={
        "email": email,
        "password": "TestPass123!",
        "name": "No Phone",
    })
    assert r.status_code == 200, f"{r.status_code}: {r.text}"
    tok = r.json().get("token")
    assert tok

    me = sess.get(f"{API}/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.status_code == 200
    data = me.json()
    # phone should be missing or None
    assert data.get("phone") in (None, "", None)


def test_signup_blank_phone_succeeds(sess: requests.Session):
    email = _fresh_email("blank")
    r = sess.post(f"{API}/signup", json={
        "email": email,
        "password": "TestPass123!",
        "name": "Blank Phone",
        "phone": "",
    })
    assert r.status_code == 200, f"{r.status_code}: {r.text}"
    tok = r.json()["token"]
    me = sess.get(f"{API}/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert me.get("user", {}).get("phone") in (None, "")


def test_signup_valid_phone_normalizes_to_e164(sess: requests.Session):
    email = _fresh_email("valid")
    r = sess.post(f"{API}/signup", json={
        "email": email,
        "password": "TestPass123!",
        "name": "Valid Phone",
        "phone": "+14155552671",
    })
    assert r.status_code == 200, f"{r.status_code}: {r.text}"
    tok = r.json()["token"]
    me = sess.get(f"{API}/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert me.get("user", {}).get("phone") == "+14155552671", f"Expected E.164; got {me.get('phone')!r}"


def test_signup_invalid_phone_rejected(sess: requests.Session):
    email = _fresh_email("invalid")
    r = sess.post(f"{API}/signup", json={
        "email": email,
        "password": "TestPass123!",
        "name": "Bad Phone",
        "phone": "abc123",
    })
    assert r.status_code == 400, f"Expected 400; got {r.status_code}: {r.text}"
    body = r.text.lower()
    assert "phone" in body or "country code" in body or "valid" in body

    # Should NOT have created the account — try login, expect failure.
    login = sess.post(f"{API}/login", json={
        "email": email, "password": "TestPass123!",
    })
    assert login.status_code in (401, 404), (
        f"Account should not exist after invalid-phone signup; login -> {login.status_code}"
    )


# ------------------------------ Login regression ------------------------------


def test_login_seeded_user_regression(sess: requests.Session):
    r = sess.post(f"{API}/login", json={
        "email": SEED_EMAIL, "password": SEED_PASSWORD,
    })
    # MFA might be enabled — accept either straight token or mfa challenge
    assert r.status_code == 200, f"{r.status_code}: {r.text}"
    data = r.json()
    assert data.get("token") or data.get("mfa_token"), f"No token/mfa_token in {data}"


# ------------------------------ /auth/update-phone ------------------------------


@pytest.fixture(scope="module")
def auth_token(sess: requests.Session) -> str:
    """Signup a fresh user (no phone) for update-phone tests."""
    email = _fresh_email("upd")
    r = sess.post(f"{API}/signup", json={
        "email": email,
        "password": "TestPass123!",
        "name": "Update Phone User",
    })
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def test_update_phone_set_valid(sess: requests.Session, auth_token: str):
    r = sess.post(f"{API}/update-phone",
                  json={"phone": "+442071838750"}, headers=_hdr(auth_token))
    assert r.status_code == 200, r.text
    assert r.json().get("phone") == "+442071838750"

    me = sess.get(f"{API}/me", headers=_hdr(auth_token)).json()
    assert me.get("user", {}).get("phone") == "+442071838750"


def test_update_phone_change_to_another_valid(sess: requests.Session, auth_token: str):
    r = sess.post(f"{API}/update-phone",
                  json={"phone": "+14155552671"}, headers=_hdr(auth_token))
    assert r.status_code == 200
    assert r.json().get("phone") == "+14155552671"


def test_update_phone_invalid_rejected_and_previous_preserved(
    sess: requests.Session, auth_token: str,
):
    r = sess.post(f"{API}/update-phone",
                  json={"phone": "not-a-number"}, headers=_hdr(auth_token))
    assert r.status_code == 400, r.text

    me = sess.get(f"{API}/me", headers=_hdr(auth_token)).json()
    # Previous value must still be present.
    assert me.get("user", {}).get("phone") == "+14155552671"


def test_update_phone_clear_with_null(sess: requests.Session, auth_token: str):
    r = sess.post(f"{API}/update-phone",
                  json={"phone": None}, headers=_hdr(auth_token))
    assert r.status_code == 200
    assert r.json().get("phone") in (None, "")

    me = sess.get(f"{API}/me", headers=_hdr(auth_token)).json()
    assert me.get("user", {}).get("phone") in (None, "")


def test_update_phone_clear_with_empty_string(sess: requests.Session, auth_token: str):
    # first set again
    sess.post(f"{API}/update-phone",
              json={"phone": "+14155552671"}, headers=_hdr(auth_token))
    r = sess.post(f"{API}/update-phone",
                  json={"phone": ""}, headers=_hdr(auth_token))
    assert r.status_code == 200
    assert r.json().get("phone") in (None, "")


def test_update_phone_requires_auth(sess: requests.Session):
    r = sess.post(f"{API}/update-phone", json={"phone": "+14155552671"})
    assert r.status_code in (401, 403), f"Expected auth error; got {r.status_code}: {r.text}"
