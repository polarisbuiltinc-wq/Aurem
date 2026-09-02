"""Iter 337 — /auth/me must never leak auth secrets (found LIVE on
prod during founder-session verification: response contained the TOTP
mfa_secret, hashed mfa_backup_codes and the raw GitHub access_token).

2026-09-08 Wave-1 baseline triage found the SAME leak class for the
transient enrollment-in-progress fields (`mfa_secret_pending`,
`mfa_backup_codes_pending`, set by routers/mfa.py::enroll-start and
left on the user doc until enroll-verify/disable clears them) — fixed
in routers/auth.py::me the same day.

2026 audit Risk #2 follow-up: this file had NO test coverage at all
for the pending-fields leak (only the original 3 secrets were
covered). Added below — this is exactly the kind of "fix shipped,
coverage didn't" gap the audit flagged for the whole auth surface.
"""
import os
import secrets as _secrets

import pytest
from pathlib import Path


def test_me_strips_secrets_source_lock():
    src = Path("/app/backend/routers/auth.py").read_text()
    seg = src.split('@router.get("/me")')[1].split("@router.get")[0]
    assert '"mfa_secret"' in seg and '"mfa_backup_codes"' in seg
    assert 'gh.pop("access_token", None)' in seg
    # 2026-09-08 fix — the pending-enrollment fields must ALSO be
    # stripped, not just the confirmed mfa_secret/backup_codes.
    assert '"mfa_secret_pending"' in seg
    assert '"mfa_backup_codes_pending"' in seg


def _unique_test_ip() -> str:
    """See test_jwt_revocation.py's helper of the same name — avoids
    sharing the /auth/login IP rate-limit bucket with other test
    files in a full-suite run."""
    return f"10.{_secrets.randbelow(255)}.{_secrets.randbelow(255)}.{_secrets.randbelow(255)}"


def test_me_live_response_has_no_secrets():
    import httpx
    base = "http://localhost:8001/api/aurem-dev"
    r = httpx.post(f"{base}/auth/login", json={
        "email": "test@aurem.dev", "password": "AuremTest2026!"},
        headers={"X-Forwarded-For": _unique_test_ip()}, timeout=20)
    assert r.status_code == 200, r.text
    tok = r.json().get("token")
    assert tok, f"login gave no token: {r.json()}"
    me = httpx.get(f"{base}/auth/me",
                   headers={"Authorization": f"Bearer {tok}"}, timeout=20)
    assert me.status_code == 200
    user = me.json()["user"]
    assert "mfa_secret" not in user
    assert "mfa_backup_codes" not in user
    assert "password" not in user
    gh = user.get("github") or {}
    assert "access_token" not in gh


@pytest.mark.asyncio
async def test_me_live_response_strips_pending_mfa_enrollment_secrets():
    """Reproduces the exact 2026-09-08 live leak: a user with an
    UNFINISHED MFA enrollment (mfa_secret_pending / mfa_backup_codes_
    pending still on the doc, as routers/mfa.py::enroll-start leaves
    them until enroll-verify/disable clears them) must not have
    either field returned by /auth/me."""
    from motor.motor_asyncio import AsyncIOMotorClient
    import bcrypt
    import httpx

    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[
        os.environ.get("DB_NAME", "aurem_dev")
    ]
    email = f"pending-mfa-test-{_secrets.token_hex(4)}@aurem.test"
    pw_hash = bcrypt.hashpw(b"TestPass2026!", bcrypt.gensalt()).decode()
    user_id = f"u_pending_mfa_{_secrets.token_hex(6)}"
    await db.dev_users.insert_one({
        "user_id": user_id, "email": email, "password": pw_hash,
        "name": "Pending MFA Test", "tier": "free",
        # Exactly the shape routers/mfa.py::enroll-start leaves behind.
        "mfa_secret_pending": "JBSWY3DPEHPK3PXP",
        "mfa_backup_codes_pending": ["$2b$fakehash1", "$2b$fakehash2"],
    })
    try:
        base = "http://localhost:8001/api/aurem-dev"
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(f"{base}/auth/login", json={
                "email": email, "password": "TestPass2026!",
            }, headers={"X-Forwarded-For": _unique_test_ip()})
            assert r.status_code == 200, r.text
            tok = r.json()["token"]
            me = await c.get(f"{base}/auth/me",
                             headers={"Authorization": f"Bearer {tok}"})
            assert me.status_code == 200
            user = me.json()["user"]
            assert "mfa_secret_pending" not in user, (
                "LIVE LEAK: /auth/me returned the plaintext pending "
                "TOTP secret"
            )
            assert "mfa_backup_codes_pending" not in user, (
                "LIVE LEAK: /auth/me returned pending backup-code hashes"
            )
    finally:
        await db.dev_users.delete_one({"user_id": user_id})
