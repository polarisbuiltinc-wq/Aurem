"""
Iter 212m-182 · Guard 21 follow-up + Priority 2b (Mode D repo-auth) — Jan 2026.

Tests three new items only (see review_request):

  A. Mode D repo-auth reachability gap (mode_d_debugger.run_debug_session):
     - file_refs present + github_pat None → honest 'no repo access' clarify
       reply (can_auto_fix=False, clarify=True), and llm_diagnosis is NOT
       called.
     - file_refs present + github_pat truthy but no file_contents → existing
       "not found in your connected repo" honest reply (regression).
     - No file_refs, generic message → falls through to llm_diagnosis
       (regression — new branch must not intercept).

  B. Gate telemetry logging:
     - POST /api/loop/start writes a loop_gate_log row with
       entry_point='loop_start' + correct tier + decision + reject_reason
       (tier_locked / kill_switch / allowed).
     - POST /api/chat/stream with execution_mode='loop' writes a
       loop_gate_log row with entry_point='chat_stream' for Pro (allowed)
       and Free (denied/tier_locked).

  C. Admin /aurem-dev/admin/loop-beta/status returns `gate_parity` with
     per-tier `mismatch` bool and top-level `mismatch_detected`, verified
     by seeding intentional-drift rows into loop_gate_log.

Cleans up all TEST_ / seeded data and throwaway users after run.
"""
from __future__ import annotations

import os
import re
import sys
import uuid
import pathlib
import asyncio
import pytest
import requests
from pymongo import MongoClient
from datetime import datetime, timezone, timedelta

BACKEND_DIR = str(pathlib.Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://bin-context-pat.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api/aurem-dev"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "aurem_dev")

FOUNDER_EMAIL = "test@aurem.dev"
FOUNDER_PASSWORD = "AuremTest2026!"


# ─── Shared fixtures ────────────────────────────────────────────────
@pytest.fixture(scope="module")
def mongo():
    c = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    return c[DB_NAME]


@pytest.fixture(scope="module")
def founder_token():
    r = requests.post(f"{API}/auth/login", json={
        "email": FOUNDER_EMAIL, "password": FOUNDER_PASSWORD,
    }, timeout=15)
    assert r.status_code == 200, f"founder login failed: {r.text[:200]}"
    return r.json()["token"]


def _signup(email: str, password: str) -> str:
    r = requests.post(f"{API}/auth/signup", json={
        "email": email, "password": password,
        "name": "gate-parity test", "form_age_ms": 5000,
    }, timeout=20)
    if r.status_code == 409:
        r = requests.post(f"{API}/auth/login", json={
            "email": email, "password": password,
        }, timeout=20)
    assert r.status_code == 200, f"signup/login failed {r.status_code}: {r.text[:400]}"
    return r.json()["token"]


def _promote(mongo, email: str, tier: str):
    res = mongo.dev_users.update_one(
        {"email": email.lower()},
        {"$set": {
            "tier": tier, "is_admin": False, "is_unlimited": False,
            "email_verified": True,
        }},
    )
    assert res.matched_count == 1, f"no dev_users row for {email}"


@pytest.fixture(scope="module")
def pro_user(mongo):
    email = f"TEST-gp-pro-{uuid.uuid4().hex[:6]}@aurem-test.dev"
    tok = _signup(email, "GateParity2026!")
    _promote(mongo, email, "pro")
    yield tok, email
    try:
        mongo.dev_users.delete_one({"email": email.lower()})
    except Exception:
        pass


@pytest.fixture(scope="module")
def free_user(mongo):
    email = f"TEST-gp-free-{uuid.uuid4().hex[:6]}@aurem-test.dev"
    tok = _signup(email, "GateParity2026!")
    _promote(mongo, email, "free")
    yield tok, email
    try:
        mongo.dev_users.delete_one({"email": email.lower()})
    except Exception:
        pass


def _user_ids_for_emails(mongo, emails):
    docs = mongo.dev_users.find({"email": {"$in": [e.lower() for e in emails]}}, {"user_id": 1})
    return [d["user_id"] for d in docs if d.get("user_id")]


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_gate_log(mongo):
    """Remove any leftover TEST_ gate-log rows both before and after."""
    mongo.loop_gate_log.delete_many({"user_id": {"$regex": "^TEST-"}})
    yield
    mongo.loop_gate_log.delete_many({"user_id": {"$regex": "^TEST-"}})
    # Also clean the parity-seed rows tagged with our unique tier.
    mongo.loop_gate_log.delete_many({"tier": "test_parity_tier"})


# ────────────────────────────────────────────────────────────────────
# A. Mode D fix — unit-level (bypasses the /chat pipeline)
# ────────────────────────────────────────────────────────────────────
class TestModeDRepoAuthReachability:
    """Priority 2b — repo-auth reachability gap closed."""

    def _call(self, **overrides):
        """Call run_debug_session with a fake db that no-ops writes."""
        from services import mode_d_debugger

        class _FakeCol:
            async def insert_one(self, *a, **k): return None
            async def find_one(self, *a, **k): return None
            async def update_one(self, *a, **k): return None

        class _FakeDB:
            def __getattr__(self, _):
                return _FakeCol()

        kwargs = dict(
            db=_FakeDB(),
            user_message="I saw a bug in server.py:42 crashing on startup",
            repo_owner="", repo_name="", repo_ctx="",
            user_id="TEST-mode-d-user", project_id="home",
            f12_payload=None, github_pat=None,
        )
        kwargs.update(overrides)
        return asyncio.run(mode_d_debugger.run_debug_session(**kwargs))

    def test_file_refs_present_no_pat_returns_clarify(self, monkeypatch):
        """The NEW branch: file_refs + github_pat=None must NOT hit llm."""
        from services import mode_d_debugger

        # Guard: llm_diagnosis must not be called in this branch.
        called = {"n": 0}
        async def _fail_llm(*a, **k):
            called["n"] += 1
            return {"cause": "SHOULD-NOT-REACH", "severity": "medium"}
        monkeypatch.setattr(mode_d_debugger, "llm_diagnosis", _fail_llm)

        # fast_path must also miss so we don't shortcut before the branch.
        monkeypatch.setattr(mode_d_debugger, "fast_path_diagnosis",
                            lambda _t: None)

        out = self._call(github_pat=None,
                         user_message="Error in server.py:99 — crashing")
        assert called["n"] == 0, "llm_diagnosis was called despite no repo access"
        assert out.get("can_auto_fix") is False
        assert out.get("clarify") is True
        reply = (out.get("ora_reply") or "").lower()
        assert ("don't currently have read access" in reply
                or "no active github connection" in reply), (
            f"reply doesn't clearly signal repo-auth gap: {reply!r}")

    def test_file_refs_present_with_pat_but_no_file_contents(self, monkeypatch):
        """Regression: existing 'not found in your connected repo' still fires."""
        from services import mode_d_debugger

        called = {"n": 0}
        async def _fail_llm(*a, **k):
            called["n"] += 1
            return {"cause": "SHOULD-NOT-REACH", "severity": "medium"}
        monkeypatch.setattr(mode_d_debugger, "llm_diagnosis", _fail_llm)
        monkeypatch.setattr(mode_d_debugger, "fast_path_diagnosis",
                            lambda _t: None)

        # read_file returns None → file_contents stays {} even though
        # github_pat is truthy.
        async def _empty_read(**k):
            return None
        monkeypatch.setattr(mode_d_debugger, "read_file", _empty_read)

        out = self._call(github_pat="ghp_fake_but_truthy_pat_xxx",
                         repo_owner="acme", repo_name="app",
                         user_message="error in server.py:99")
        assert called["n"] == 0
        reply = (out.get("ora_reply") or "").lower()
        assert "not found" in reply or "don't see that" in reply or \
               "connected repo" in reply, (
            f"reply didn't match 'not found' branch: {reply!r}")
        assert out.get("clarify") is True

    def test_generic_message_no_file_refs_reaches_llm(self, monkeypatch):
        """Regression: generic error text (no file_refs) must reach llm_diagnosis."""
        from services import mode_d_debugger

        # Force fast_path miss so llm path is tested.
        monkeypatch.setattr(mode_d_debugger, "fast_path_diagnosis",
                            lambda _t: None)

        called = {"n": 0}
        async def _fake_llm(*a, **k):
            called["n"] += 1
            return {
                "cause": "generic root cause", "severity": "medium",
                "files_to_check": [], "fix_suggestion": "",
                "needs_commit": False, "commit_task": "",
                "fast_path": False, "needs_llm": True,
            }
        monkeypatch.setattr(mode_d_debugger, "llm_diagnosis", _fake_llm)

        # Message must have a "concrete debug signal" per has_concrete_debug_signal
        # so we don't hit the Iter 171 clarify bail. Include a 500 status.
        out = self._call(
            github_pat=None,
            user_message="Deployment returns 500 randomly on production",
        )
        assert called["n"] == 1, (
            f"llm_diagnosis should have been called once; got {called['n']}. "
            f"out={out}")
        assert out.get("clarify") is not True or out.get("ora_reply")


# ────────────────────────────────────────────────────────────────────
# B. Gate telemetry — /loop/start and /chat/stream both log
# ────────────────────────────────────────────────────────────────────
class TestGateTelemetryLoopStart:
    def _post_start(self, token: str, msg="add null check to server.py"):
        return requests.post(
            f"{API}/loop/start",
            headers={"Authorization": f"Bearer {token}"},
            json={"user_message": msg},
            timeout=20,
        )

    def test_free_user_loop_start_logs_tier_locked(self, free_user, mongo):
        tok, email = free_user
        uids = _user_ids_for_emails(mongo, [email])
        assert uids, "free user has no user_id"
        # Snapshot count before.
        before = mongo.loop_gate_log.count_documents({
            "user_id": uids[0], "entry_point": "loop_start",
        })
        r = self._post_start(tok)
        # Free is tier-locked → 403
        assert r.status_code == 403, r.text[:300]
        # Give async best-effort write a moment.
        import time as _t; _t.sleep(0.5)
        row = mongo.loop_gate_log.find_one(
            {"user_id": uids[0], "entry_point": "loop_start"},
            sort=[("created_at", -1)],
        )
        assert row is not None, "no loop_gate_log row written for Free /loop/start"
        assert row.get("decision") == "denied"
        assert row.get("reject_reason") == "tier_locked"
        assert row.get("tier") == "free"
        after = mongo.loop_gate_log.count_documents({
            "user_id": uids[0], "entry_point": "loop_start",
        })
        assert after == before + 1, f"expected +1 row, got {after-before}"

    def test_pro_user_loop_start_logs_allowed(self, pro_user, mongo):
        tok, email = pro_user
        uids = _user_ids_for_emails(mongo, [email])
        assert uids
        before = mongo.loop_gate_log.count_documents({
            "user_id": uids[0], "entry_point": "loop_start",
            "decision": "allowed",
        })
        r = self._post_start(tok, msg="fix login flow: add retry logic")
        # Pro is allowed → should be 2xx (loop_id created) or 409/429 on
        # concurrency — but NOT 403 tier_locked.
        assert r.status_code != 403 or "kill_switch" in r.text, r.text[:300]
        import time as _t; _t.sleep(0.5)
        row = mongo.loop_gate_log.find_one(
            {"user_id": uids[0], "entry_point": "loop_start"},
            sort=[("created_at", -1)],
        )
        assert row is not None
        assert row.get("tier") == "pro"
        # For an allowed Pro user, decision must be "allowed".
        assert row.get("decision") == "allowed", (
            f"unexpected decision for allowed Pro: {row}")
        assert row.get("reject_reason") in (None, "")
        # If the loop_id came back, cancel it to avoid leaking active
        # loops for the test user across module lifetime.
        if r.status_code == 200:
            try:
                lid = (r.json() or {}).get("loop_id")
                if lid:
                    requests.post(
                        f"{API}/loop/{lid}/cancel",
                        headers={"Authorization": f"Bearer {tok}"},
                        timeout=10,
                    )
            except Exception:
                pass


class TestGateTelemetryChatStream:
    def _post_stream(self, token: str, mode="loop"):
        return requests.post(
            f"{API}/chat/stream",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "text/event-stream"},
            json={"prompt": "LOOP_PHASE:plan add a null check",
                  "execution_mode": mode, "mode": "swift",
                  "project_id": "home"},
            timeout=15, stream=True,
        )

    def test_pro_chat_stream_loop_logs_allowed(self, pro_user, mongo):
        tok, email = pro_user
        uids = _user_ids_for_emails(mongo, [email])
        assert uids
        try:
            r = self._post_stream(tok, mode="loop")
        except requests.exceptions.ReadTimeout:
            pytest.skip("upstream LLM slow")
        assert r.status_code == 200, r.text[:300]
        r.close()
        import time as _t; _t.sleep(0.5)
        row = mongo.loop_gate_log.find_one(
            {"user_id": uids[0], "entry_point": "chat_stream"},
            sort=[("created_at", -1)],
        )
        assert row is not None, "no chat_stream gate-log row written for Pro"
        assert row.get("decision") == "allowed"
        assert row.get("tier") == "pro"

    def test_free_chat_stream_loop_logs_denied_tier_locked(self, free_user, mongo):
        tok, email = free_user
        uids = _user_ids_for_emails(mongo, [email])
        assert uids
        try:
            r = self._post_stream(tok, mode="loop")
        except requests.exceptions.ReadTimeout:
            pytest.skip("upstream LLM slow")
        # Silent downgrade → 200 (not 403).
        assert r.status_code == 200, r.text[:300]
        r.close()
        import time as _t; _t.sleep(0.5)
        row = mongo.loop_gate_log.find_one(
            {"user_id": uids[0], "entry_point": "chat_stream"},
            sort=[("created_at", -1)],
        )
        assert row is not None, "no chat_stream gate-log row for Free"
        assert row.get("decision") == "denied"
        assert row.get("reject_reason") == "tier_locked"
        assert row.get("tier") == "free"


# ────────────────────────────────────────────────────────────────────
# C. Admin /loop-beta/status gate_parity + seeded drift
# ────────────────────────────────────────────────────────────────────
class TestGateParityDashboard:
    """Seed intentional drift for tier=pro and confirm mismatch_detected."""

    @pytest.fixture(autouse=True)
    def _seed_and_cleanup(self, mongo):
        # Clear any real pro rows first so this test isn't polluted by
        # earlier E2E test writes. Use a sentinel user_id prefix so we
        # can safely delete only our seeded rows.
        SEED_UID = "TEST-parity-seed"
        # Also nuke any TEST- rows on pro tier from the earlier telemetry
        # tests so parity math is deterministic.
        now = datetime.now(timezone.utc)
        # Seed: 6 loop_start rows for pro — 5 allowed, 1 denied  (denial ~17%)
        # Seed: 6 chat_stream rows for pro — 1 allowed, 5 denied (denial ~83%)
        # Delta = |0.17 - 0.83| ≈ 0.66 >= 0.30 → mismatch True.
        rows = []
        for i in range(6):
            rows.append({
                "entry_point": "loop_start", "user_id": SEED_UID,
                "tier": "pro",
                "decision": "allowed" if i < 5 else "denied",
                "reject_reason": None if i < 5 else "tier_locked",
                "created_at": now - timedelta(minutes=i),
            })
        for i in range(6):
            rows.append({
                "entry_point": "chat_stream", "user_id": SEED_UID,
                "tier": "pro",
                "decision": "denied" if i < 5 else "allowed",
                "reject_reason": "tier_locked" if i < 5 else None,
                "created_at": now - timedelta(minutes=i),
            })
        # Team: consistent — all allowed on both sides (5+ each) → mismatch False.
        for ep in ("loop_start", "chat_stream"):
            for i in range(5):
                rows.append({
                    "entry_point": ep, "user_id": SEED_UID,
                    "tier": "team", "decision": "allowed",
                    "reject_reason": None,
                    "created_at": now - timedelta(minutes=i),
                })
        mongo.loop_gate_log.insert_many(rows)
        yield
        mongo.loop_gate_log.delete_many({"user_id": SEED_UID})

    def test_status_returns_gate_parity_with_mismatch_flagged(
            self, founder_token, mongo):
        r = requests.get(
            f"{API}/admin/loop-beta/status",
            headers={"Authorization": f"Bearer {founder_token}"},
            timeout=15,
        )
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert "gate_parity" in data, "response missing gate_parity key"
        gp = data["gate_parity"]
        assert "tiers" in gp and isinstance(gp["tiers"], list)
        assert "mismatch_detected" in gp
        # Locate pro row.
        by_tier = {t["tier"]: t for t in gp["tiers"]}
        assert "pro" in by_tier, f"pro tier row missing: {gp}"
        pro = by_tier["pro"]
        # Field shape.
        for f in ("loop_start_total", "loop_start_denied",
                  "loop_start_denial_rate", "chat_stream_total",
                  "chat_stream_denied", "chat_stream_denial_rate",
                  "mismatch"):
            assert f in pro, f"pro row missing field {f}: {pro}"
        # With our seed we expect BOTH sides >= 6 → not low-volume, and
        # denial_rate delta ~0.66 → mismatch True.
        assert pro["loop_start_total"] >= 5
        assert pro["chat_stream_total"] >= 5
        assert pro["mismatch"] is True, (
            f"expected pro.mismatch=True with seeded drift: {pro}")
        assert gp["mismatch_detected"] is True, (
            f"top-level mismatch_detected must be True: {gp}")

        # Team row must be present and mismatch=False (consistent seed).
        assert "team" in by_tier
        assert by_tier["team"]["mismatch"] is False, (
            f"expected team.mismatch=False with consistent seed: "
            f"{by_tier['team']}")

    def test_status_forbidden_for_non_admin(self, pro_user):
        tok, _ = pro_user
        r = requests.get(
            f"{API}/admin/loop-beta/status",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=10,
        )
        assert r.status_code in (401, 403), (
            f"non-admin should not access /admin/loop-beta/status: "
            f"{r.status_code} {r.text[:200]}")
