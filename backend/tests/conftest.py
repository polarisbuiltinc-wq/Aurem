"""
backend/tests/conftest.py

Loads /app/backend/.env into the test process before any test imports.

Why this exists: tests that talk to Mongo / Redis / external services
read connection strings from `os.environ`. The backend server (run by
supervisor) gets these via the supervisor environment; pytest does
not. Without this file, tests like `test_token_enforcement.py` fail
with `KeyError: 'MONGO_URL'` even though the backend itself works
fine.

Placed at the `tests/` directory level so it applies to every test
file but does not affect runtime imports of the app.
"""

import os
from pathlib import Path

# Load /app/backend/.env into the current process. We do this manually
# (no dotenv dep needed) so it works even on a stripped CI image.
_env_file = Path(__file__).resolve().parents[1] / ".env"
if _env_file.is_file():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        # Don't override anything already set (CI / supervisor wins).
        if k and k not in os.environ:
            # Strip surrounding quotes if any
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            os.environ[k] = v


# ── Iter 345 — legacy quarantine (founder ruling 2026-07-29) ─────────
# The 259 pre-existing failures (238 FAILED + 21 ERRORS across ~114
# iter36–iter267-era files) are DEFERRED, not fixed and not deleted.
# Exact nodeids live in tests/legacy_quarantine.txt; this hook tags
# each with @pytest.mark.legacy at collection time so no test file
# needs editing and the list stays reviewable in one place.
import pytest as _pytest

_LEGACY_LIST = Path(__file__).parent / "legacy_quarantine.txt"


def _load_legacy_nodeids():
    try:
        return {
            ln.strip() for ln in _LEGACY_LIST.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        }
    except FileNotFoundError:
        return set()


def pytest_collection_modifyitems(config, items):
    legacy = _load_legacy_nodeids()
    if not legacy:
        return
    for item in items:
        if item.nodeid in legacy:
            item.add_marker(_pytest.mark.legacy)


# ── Iter 367 · Session 5 Item 4 · Signup rate-limit test isolation ────
#
# 14 of the 22 "deferred CI-lane failures" ALL had the same root cause:
# tests call `POST /auth/signup` repeatedly, the backend enforces
# `SIGNUP_RATE_LIMIT_PER_IP=3 / 24h`, and every test after the first 3
# gets HTTP 429. The tests aren't wrong; the shared state between
# runs pollutes them. Since tests always originate from 127.0.0.1 /
# testclient, we clear the accumulated signup rows for those IPs
# BEFORE the test session starts — production rate-limit stays
# fully intact for real IPs.
#
# Real fix (not "loosen the assertion") — restore per-test clean state.
_TEST_IPS = ("127.0.0.1", "testclient", "localhost", "::1", "")


def _clear_signup_state_for_test_ips() -> None:
    """Sync helper — connects with pymongo, wipes test-IP signup rows."""
    mongo_url = os.environ.get("MONGO_URL")
    db_name   = os.environ.get("DB_NAME")
    if not (mongo_url and db_name):
        return
    try:
        from pymongo import MongoClient
        client = MongoClient(mongo_url, serverSelectionTimeoutMS=2000)
        db = client[db_name]
        # Wipe dev_users rows created from test IPs — that's what the
        # rate-limit counter reads. Only touches rows with signup_ip
        # in _TEST_IPS so real user rows are untouched.
        db.dev_users.delete_many({"signup_ip": {"$in": list(_TEST_IPS)}})
        # Also clear the abuse log for those IPs (cosmetic, no
        # behaviour impact but keeps admin dashboard clean).
        db.signup_abuse_log.delete_many({"ip": {"$in": list(_TEST_IPS)}})
        client.close()
    except Exception:
        # Best-effort — a Mongo hiccup here must never break the test
        # collection phase. Individual tests that need the reset will
        # fail loud on their own if it didn't work.
        pass


def pytest_sessionstart(session):  # noqa: D401
    """Autouse: wipe signup state for the test-IP set once per session."""
    _clear_signup_state_for_test_ips()


@_pytest.fixture(autouse=True)
def clean_signup_rate_limit():
    """AUTOUSE — clear signup-count state before every test to keep
    the per-IP counter from bleeding across tests.  ~1 ms cost per
    test (single Mongo delete_many with a small indexed set)."""
    _clear_signup_state_for_test_ips()
    yield

