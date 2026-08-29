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


# ── 2026-08-28 · P0-4/P2-A fix — export REACT_APP_BACKEND_URL for tests ──
# Backend .env doesn't have this key (it's a frontend-only var), but
# ~20 test files do `os.environ.get("REACT_APP_BACKEND_URL", "")`
# expecting it to be set to the running preview's own URL, to hit the
# live server for e2e checks. Without it, standalone/CI runs of these
# files hit `MissingSchema`/`KeyError` — NOT a real bug in the feature
# under test (root-caused via test_p2a_notification_bell.py: all 4
# tests pass once this is set — confirmed the bell itself is correct,
# only the test harness's env was incomplete).
if "REACT_APP_BACKEND_URL" not in os.environ:
    _frontend_env = Path("/app/frontend/.env")
    if _frontend_env.is_file():
        for _line in _frontend_env.read_text().splitlines():
            _line = _line.strip()
            if _line.startswith("REACT_APP_BACKEND_URL="):
                os.environ["REACT_APP_BACKEND_URL"] = _line.split("=", 1)[1].strip()
                break


# ── Iter 345 — legacy quarantine (founder ruling 2026-07-29) ─────────
# The pre-existing failures across iter36–iter267-era files are
# DEFERRED, not fixed and not deleted. Exact nodeids live in three
# reviewable text files (Batch-4g/4h split — Feb 2026):
#
#   • legacy_quarantine.txt        — contract-drift still up for refresh
#   • legacy_removed_features.txt  — asserted surface deleted from runtime
#                                    (kept for recoverability, one-line
#                                    reason per entry).
#   • legacy_deferred_db_fixtures  — the DB-fixture batch, reserved for
#     .txt                           a dedicated task-quota-refactor
#                                    session.
#
# All three are unioned into the same @pytest.mark.legacy set so CI
# stays green while remediation continues.
import pytest as _pytest


@_pytest.fixture(autouse=True)
def _x1_mock_llm_boot_deterministic(monkeypatch):
    """X1 hardening (2026-08-30, overnight-loop-2 P0) — services/llm/_meta.py
    (the orchestrator/loop/Council gateway) and ora_chat_v2's llm_client now
    both honour MOCK_LLM, and llm_client reads it ONCE at process import
    (immutable per-process by design — see REPORT-x1-crossproject.md §X1).
    That import-order sensitivity made hundreds of pre-existing tests that
    never anticipated a mock gate on this path flaky/order-dependent
    (whichever test imports llm_client FIRST in the whole pytest session
    "wins" the cached value for everyone after it). This autouse fixture
    forces the cached value to `False` before EVERY test, deterministically,
    restoring the pre-X1 baseline assumption ("MOCK_LLM has no effect
    unless a test explicitly asks for it") suite-wide. Tests that want to
    exercise the mock branch itself call
    `monkeypatch.setattr(llm_client, "_MOCK_LLM_AT_BOOT", True)` in their
    own body — that happens strictly AFTER this fixture runs and wins, and
    monkeypatch's teardown unwinds both cleanly regardless of order."""
    try:
        from services.ora_chat_v2 import llm_client
        monkeypatch.setattr(llm_client, "_MOCK_LLM_AT_BOOT", False, raising=False)
    except Exception:
        pass
    yield

_LEGACY_LISTS = (
    Path(__file__).parent / "legacy_quarantine.txt",
    Path(__file__).parent / "legacy_removed_features.txt",
    Path(__file__).parent / "legacy_deferred_db_fixtures.txt",
)


def _load_legacy_nodeids():
    ids = set()
    for lst in _LEGACY_LISTS:
        try:
            text = lst.read_text()
        except FileNotFoundError:
            continue
        for ln in text.splitlines():
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            # Strip inline `# reason` comments used in the
            # removed_features / db_fixtures files.
            nodeid = s.split("#", 1)[0].strip()
            if nodeid:
                ids.add(nodeid)
    return ids


# ── 2026-08-24 Step 3 CI triage — live-URL test lane ─────────────────
# Tests in tests/live_env_quarantine.txt hit the LIVE deployment at
# REACT_APP_BACKEND_URL. In GitHub Actions no server exists (the
# committed frontend/.env URL is a stale preview host → ingress 404 /
# connection refused). Probe {BASE}/api/health once; when unreachable,
# SKIP those tests with an explicit reason instead of failing.
# Locally (server up) they run and block exactly as before.
_LIVE_ENV_LIST = Path(__file__).parent / "live_env_quarantine.txt"


def _load_live_env_entries():
    files, nodes = set(), set()
    try:
        text = _LIVE_ENV_LIST.read_text()
    except FileNotFoundError:
        return files, nodes
    for ln in text.splitlines():
        s = ln.split("#", 1)[0].strip()
        if not s:
            continue
        (nodes if "::" in s else files).add(s)
    return files, nodes


def _resolve_live_base() -> str:
    base = (os.environ.get("REACT_APP_BACKEND_URL") or "").strip()
    if not base:
        envf = Path("/app/frontend/.env")
        if envf.is_file():
            for ln in envf.read_text().splitlines():
                if ln.strip().startswith("REACT_APP_BACKEND_URL="):
                    base = ln.split("=", 1)[1].strip()
                    break
    return base.rstrip("/")


def _live_server_reachable() -> bool:
    base = _resolve_live_base()
    if not base:
        return False
    try:
        import requests
        return requests.get(f"{base}/api/health", timeout=5).status_code == 200
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    legacy = _load_legacy_nodeids()
    live_files, live_nodes = _load_live_env_entries()
    live_items = []
    for item in items:
        if item.nodeid in legacy:
            item.add_marker(_pytest.mark.legacy)
        if item.nodeid in live_nodes or item.nodeid.split("::", 1)[0] in live_files:
            item.add_marker(_pytest.mark.live_env)
            live_items.append(item)
    if live_items and not _live_server_reachable():
        base = _resolve_live_base() or "<unset>"
        skip = _pytest.mark.skip(
            reason=f"live_env: {base}/api/health unreachable — no live "
                   "deployment in this environment (see tests/live_env_quarantine.txt)")
        for item in live_items:
            item.add_marker(skip)


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


# ── 2026-08-25 — general in-memory rate-limiter isolation ────────────
#
# Separate root cause from Iter 367 above: services.rate_limiter._buckets
# is a PROCESS-WIDE in-memory dict backing every /auth/login (and other
# decorator-based) rate-limit check across the WHOLE pytest session.
# Live CI evidence (quality-gate.yml "Fitness-function invariants" job,
# commit 4a9cd8ddc321): once ~10 tests in one pytest process had each
# called a rate-limited endpoint, every subsequent test in that process
# started getting real 429s regardless of its own test's intent —
# cascading into unrelated failures/errors. Same fix philosophy as
# above: reset accumulated STATE between tests, keep the real
# production-shaped check (limits, windows, Redis-fallback logic)
# fully intact — do NOT set RATE_LIMIT_DISABLED.
@_pytest.fixture(autouse=True)
def reset_in_memory_rate_limit_buckets():
    """AUTOUSE — clear services.rate_limiter's in-memory sliding-window
    buckets before every test so one test's rate-limited calls can't
    trip a 429 in a completely unrelated test later in the same run."""
    from services.rate_limiter import reset_buckets_for_tests
    reset_buckets_for_tests()
    yield

