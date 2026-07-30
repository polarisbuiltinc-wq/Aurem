"""Iter 356 locks — E2E-session sidebar pollution fix + Guard 2 marketing truth.

1. /chat/sessions must exclude prod-e2e-* test-run sessions (live Mongo test).
2. Founder-only cleanup endpoint exists and is admin-gated.
3. /usage/public/stats exposes real_developers + commits_shipped with
   test-account exclusion (real_developers <= raw users).
4. Grep lock: no hardcoded marketing counts (500+/12k+ class) in public pages.
5. Shell.jsx has exactly ONE /chat/sessions refresh effect (double-fetch fix).
"""
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend" / "src"


# ── Static locks ─────────────────────────────────────────────────────────

def test_sessions_list_filters_e2e_prefix():
    src = (BACKEND / "routers" / "chat.py").read_text()
    m = re.search(r"def chat_sessions_list.*?(?=\n@router|\nclass )", src, re.S)
    assert m, "chat_sessions_list not found"
    assert "E2E_SESSION_PREFIX_RE" in m.group(0), \
        "session list must exclude E2E test sessions"


def test_e2e_prefix_regex_matches_known_debris():
    from services.test_accounts import E2E_SESSION_PREFIX_RE
    assert E2E_SESSION_PREFIX_RE.match("prod-e2e-aef123")
    assert E2E_SESSION_PREFIX_RE.match("qa-e2e-x")
    assert not E2E_SESSION_PREFIX_RE.match("s-normal-user-session")
    assert not E2E_SESSION_PREFIX_RE.match("my-prod-e2e-notprefix")


def test_is_test_email_shared_helper():
    from services.test_accounts import is_test_email
    assert is_test_email("auto_1c97d778e0@aurem.test")
    assert is_test_email("qa-scan-bot@aurem.dev")
    assert is_test_email("oauth-pytest-123@aurem.dev")
    assert is_test_email("u_f8839990@aurem.test")
    assert is_test_email(None)
    assert not is_test_email("teji.ss1986@gmail.com")
    assert not is_test_email("customer@company.com")


def test_prod_e2e_suite_has_teardown():
    src = (BACKEND / "tests" / "test_iter212m_prod_e2e_founder.py").read_text()
    assert "_cleanup_e2e_sessions" in src
    assert "e2e_session_ids" in src


def test_cleanup_endpoint_admin_gated():
    src = (BACKEND / "routers" / "admin.py").read_text()
    m = re.search(r"async def cleanup_e2e_sessions.*?(?=\n@router)", src, re.S)
    assert m, "cleanup_e2e_sessions endpoint missing"
    assert "_require_admin" in m.group(0)


def test_no_hardcoded_marketing_stats_in_public_pages():
    """Guard 2 grep lock — hardcoded counts near users/commits/developers
    in public marketing pages = FAIL. Live numbers only."""
    bad = []
    keywords = re.compile(r"developer|users|commits|shipped", re.I)
    num = re.compile(r"\b\d{3,}\s*\+|\b\d+(\.\d+)?k\s*\+", re.I)
    for name in ("Landing.jsx", "Pricing.jsx", "WhyOra.jsx", "VsDevin.jsx",
                 "Signup.jsx", "Login.jsx"):
        p = FRONTEND / "pages" / name
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if num.search(line) and keywords.search(line):
                bad.append(f"{name}:{i}: {line.strip()[:100]}")
    assert not bad, "Hardcoded marketing stats found:\n" + "\n".join(bad)


def test_landing_renders_from_public_stats():
    src = (FRONTEND / "pages" / "Landing.jsx").read_text()
    assert "/usage/public/stats" in src
    assert "real_developers" in src
    assert "commits_shipped" in src
    assert "500+" not in src and "12k+" not in src


def test_shell_single_sessions_refresh_effect():
    src = (FRONTEND / "components" / "Shell.jsx").read_text()
    effects = re.findall(
        r"useEffect\(\(\) => \{\s*if \(token\) refreshSessions\(\);\s*\}", src)
    assert len(effects) == 1, \
        f"expected exactly 1 refreshSessions effect, found {len(effects)}"
    # Iter 356b — both mount-time consumers (session-adopt + sidebar
    # refresh) must go through the shared 2s in-flight cache so
    # identical GETs collapse into one network request.
    assert "fetchSessionsShared" in src
    assert src.count('api.get("/chat/sessions"') == 1, \
        "all Shell session fetches must route through fetchSessionsShared"


def test_route_error_boundary_wired():
    app = (FRONTEND / "App.jsx").read_text()
    assert "RouteErrorBoundary" in app
    comp = (FRONTEND / "components" / "RouteErrorBoundary.jsx").read_text()
    assert "getDerivedStateFromError" in comp
    assert "route-error-retry-btn" in comp


def test_public_stats_has_real_fields():
    src = (BACKEND / "routers" / "usage.py").read_text()
    assert "is_test_email" in src
    assert "real_developers" in src
    assert "commits_shipped" in src


# ── Live locks (preview Mongo + running backend) ─────────────────────────

@pytest.mark.asyncio
async def test_live_sessions_list_excludes_e2e_debris():
    """Seed a prod-e2e session + a real session for a synthetic user and
    assert the list query shape excludes the e2e one."""
    import os
    from dotenv import load_dotenv
    load_dotenv(BACKEND / ".env")
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.test_accounts import E2E_SESSION_PREFIX_RE

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    uid = "iter356-lock-user"
    try:
        await db.chat_sessions.delete_many({"user_id": uid})
        await db.chat_sessions.insert_many([
            {"session_id": "prod-e2e-lock356", "user_id": uid,
             "title": "Project Structure Review", "project_id": "p_lock356",
             "updated_at": "2026-06-29T00:00:00Z", "turns": []},
            {"session_id": "s-real-lock356", "user_id": uid,
             "title": "Real chat", "project_id": "p_lock356",
             "updated_at": "2026-06-29T00:00:00Z", "turns": []},
        ])
        q = {"user_id": uid, "session_id": {"$not": E2E_SESSION_PREFIX_RE},
             "project_id": "p_lock356"}
        rows = await db.chat_sessions.find(q, {"_id": 0, "session_id": 1}).to_list(20)
        ids = {r["session_id"] for r in rows}
        assert "s-real-lock356" in ids
        assert "prod-e2e-lock356" not in ids
    finally:
        await db.chat_sessions.delete_many({"user_id": uid})
        client.close()
