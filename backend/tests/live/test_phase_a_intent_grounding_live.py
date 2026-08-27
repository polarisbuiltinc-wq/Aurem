"""Live API smoke tests for Phase A — P1 intent grounding + P2 plan-scan
consistency wiring at /api/aurem-dev/loop/start.

Complements the unit test suite already in
tests/test_iter2026_08_27_intent_grounding_plan_scan.py by hitting the
actual preview API end-to-end.
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL"):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


@pytest.fixture(scope="module")
def auth():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/aurem-dev/auth/login",
               json={"email": EMAIL, "password": PASSWORD}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    data = r.json()
    token = data.get("access_token") or data.get("token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    return s


def _post_start(sess, **payload):
    return sess.post(f"{BASE_URL}/api/aurem-dev/loop/start",
                     json=payload, timeout=30)


# ── P1 — confirmatory reply with NO pending scan ─────────────────────
def test_bare_yes_with_no_pending_scan_returns_no_pending_message(auth):
    sid = f"test_phase_a_{uuid.uuid4().hex[:12]}"
    r = _post_start(auth, user_message="yes", session_id=sid)
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    assert j.get("needs_clarification") is True
    assert j.get("reason") == "no_pending_proposal"
    msg = (j.get("message") or "").lower()
    assert "don't have a pending proposal" in msg or "pending proposal" in msg
    # Must NOT be the old "too broad" clarification
    assert "too broad" not in msg and "bit broad" not in msg


# ── Ambiguity gate STILL applies for real vague asks ─────────────────
@pytest.mark.parametrize("msg", ["fix it", "improve the app"])
def test_genuinely_vague_ask_still_gated_as_broad(auth, msg):
    sid = f"test_phase_a_{uuid.uuid4().hex[:12]}"
    r = _post_start(auth, user_message=msg, session_id=sid)
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    assert j.get("needs_clarification") is True
    # NOT the new no_pending_proposal path
    assert j.get("reason") != "no_pending_proposal"
    text = (j.get("message") or "").lower()
    assert "broad" in text or "specific" in text or "which" in text


# ── P1 grounding — pending_scan resolves scope, ambiguity gate SKIPPED
def test_bare_yes_with_pending_scan_resolves_to_grounded_task(auth):
    """Seed a pending_scan directly on the chat_sessions doc (mirrors
    what routers/chat.py's Mode E block does) then send a bare 'yes'
    and verify:
      * loop actually starts (loop_id non-null)
      * ambiguity gate is NOT triggered
      * pending_scan is consumed (cleared)
    """
    import motor.motor_asyncio
    import asyncio
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not (mongo_url and db_name):
        pytest.skip("MONGO_URL/DB_NAME not available in this environment")

    sid = f"test_phase_a_{uuid.uuid4().hex[:12]}"
    project_id = "p_2d30ef16d1"  # per credentials — real GH-App connected

    async def seed_and_verify():
        client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        # Find the user_id for test@aurem.dev
        u = await db.dev_users.find_one({"email": EMAIL}, {"_id": 0, "user_id": 1, "id": 1})
        uid = (u or {}).get("user_id") or (u or {}).get("id")
        assert uid, "test user not found in DB"
        finding = {
            "filepath": "backend/server.py", "line": 1,
            "description": "Synthetic finding for phase-A live test",
            "severity": "low", "fix": "n/a",
        }
        await db.chat_sessions.update_one(
            {"session_id": sid, "user_id": uid},
            {"$set": {"pending_scan": {
                "findings": [finding], "project_id": project_id,
                "created_at": time.time(),
            }, "session_id": sid, "user_id": uid}},
            upsert=True,
        )
        return uid

    uid = asyncio.run(seed_and_verify())

    r = _post_start(auth, user_message="yes", session_id=sid,
                    project_id=project_id)
    # 200 = loop started; 403 = project ownership/GitHub App check kicked in;
    # 409 = loop already running. All three prove we got past the P1
    # ambiguity gate + grounded the scope (would otherwise be 200 with
    # needs_clarification/no_pending_proposal, which is what we're
    # explicitly checking is NOT happening).
    assert r.status_code in (200, 403, 409), r.text[:400]
    if r.status_code == 200:
        j = r.json()
        assert not j.get("needs_clarification"), \
            f"grounding failed, got clarification: {j}"
        assert j.get("loop_id"), f"expected loop_id, got: {j}"
    else:
        # Downstream error — verify it is NOT a grounding failure.
        assert "no_pending_proposal" not in r.text
        assert "too broad" not in r.text.lower()

    # Regardless of downstream outcome, verify pending_scan was consumed —
    # grounding code path was reached and executed.
    async def check_cleared():
        client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        s = await db.chat_sessions.find_one(
            {"session_id": sid, "user_id": uid}, {"_id": 0, "pending_scan": 1},
        )
        return s

    sess_doc = asyncio.run(check_cleared())
    assert sess_doc is not None
    assert "pending_scan" not in sess_doc, \
        f"pending_scan not cleared: {sess_doc}"


# ── Regression — normal loop start with concrete task not affected ───
def test_normal_concrete_task_unaffected(auth):
    sid = f"test_phase_a_{uuid.uuid4().hex[:12]}"
    r = _post_start(auth,
                    user_message="fix the login bug in Signup.jsx",
                    session_id=sid)
    assert r.status_code in (200, 400, 403, 409), r.text[:300]
    if r.status_code != 200:
        # Downstream check kicked in — proves grounding+ambiguity gate
        # let this concrete task through.
        assert "no_pending_proposal" not in r.text
        assert "too broad" not in r.text.lower()
        return
    j = r.json()
    # Not a confirmation reply; ambiguity gate should let a concrete
    # scoped task through — expect either a loop_id or possibly a
    # non-grounding-related error (e.g. project_id required). The
    # important thing: NOT the no_pending_proposal path.
    assert j.get("reason") != "no_pending_proposal"
