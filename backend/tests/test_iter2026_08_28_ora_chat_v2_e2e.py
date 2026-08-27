"""
E2E/integration tests for ORA Chat v2 P1-P5 checkpoint (2026-08-28)

Exercises the LIVE backend at REACT_APP_BACKEND_URL:
  - Auth gate on /message, /action/approve, /action/reject, /actions/recent
  - Session create/list/get with token persistence per assistant msg
  - Full mock-LLM turn: SSE stream state -> delta(s) -> final
  - Rate limit 429 SSE error path (seeded ora_chat_usage)
  - Action approve/reject flow via manually inserted `proposed` doc
  - Sensitive action gating (ORA_CHAT_SENSITIVE=off default)
  - Recent actions audit trail (no MongoDB _id leak)
  - Regression: /founder-offer status pill payload sane
"""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest
import requests

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api/aurem-dev"

EMAIL = "test@aurem.dev"
PASSWORD = "AuremTest2026!"


# ── Fixtures ────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def token() -> str:
    r = requests.post(f"{API}/auth/login",
                       json={"email": EMAIL, "password": PASSWORD},
                       timeout=15)
    assert r.status_code == 200, r.text
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def hdr(token):
    return {"Authorization": f"Bearer {token}",
            "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def session(hdr):
    r = requests.post(f"{API}/ora-chat/sessions",
                       headers=hdr, json={"title": "TEST_e2e"}, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("ok")
    sid = data["session"]["session_id"]
    assert sid
    return sid


# ── Auth gate tests ─────────────────────────────────────────────────
class TestAuthGate:
    def test_message_requires_auth(self):
        # no Authorization header -> should be 401/403
        r = requests.post(f"{API}/ora-chat/message",
                          json={"session_id": "x", "content": "hi"},
                          timeout=10)
        assert r.status_code in (401, 403), r.text

    def test_action_approve_requires_auth(self):
        r = requests.post(f"{API}/ora-chat/action/approve",
                          json={"proposal_id": "x"}, timeout=10)
        assert r.status_code in (401, 403)

    def test_action_reject_requires_auth(self):
        r = requests.post(f"{API}/ora-chat/action/reject",
                          json={"proposal_id": "x"}, timeout=10)
        assert r.status_code in (401, 403)

    def test_actions_recent_requires_auth(self):
        r = requests.get(f"{API}/ora-chat/actions/recent", timeout=10)
        assert r.status_code in (401, 403)


# ── Session persistence ────────────────────────────────────────────
class TestSessions:
    def test_create_and_list_session(self, hdr, session):
        r = requests.get(f"{API}/ora-chat/sessions", headers=hdr, timeout=10)
        assert r.status_code == 200
        sess_ids = [s.get("session_id") for s in r.json().get("sessions", [])]
        assert session in sess_ids

    def test_get_session_transcript(self, hdr, session):
        r = requests.get(f"{API}/ora-chat/sessions/{session}",
                          headers=hdr, timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "messages" in data or "session" in data


# ── SSE mock-LLM turn ──────────────────────────────────────────────
def _stream_message(hdr, session_id, content, think_mode=False,
                     advise_only=False, timeout=30):
    """Consume SSE stream from POST /message, return list of (event, data)."""
    events = []
    with requests.post(
        f"{API}/ora-chat/message",
        headers={**hdr, "Accept": "text/event-stream"},
        json={"session_id": session_id, "content": content,
              "think_mode": think_mode, "advise_only": advise_only},
        stream=True, timeout=timeout,
    ) as resp:
        if resp.status_code != 200:
            return resp.status_code, [(None, resp.text)]
        cur_event = None
        for raw in resp.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            line = raw.strip()
            if not line:
                cur_event = None
                continue
            if line.startswith("event:"):
                cur_event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = line.split(":", 1)[1].strip()
                try:
                    events.append((cur_event, json.loads(payload)))
                except Exception:
                    events.append((cur_event, payload))
                # early break on final
                if cur_event == "final" or cur_event == "error":
                    break
        return resp.status_code, events


class TestMockLLMTurn:
    def test_mock_llm_stream_delivers_final(self, hdr, session):
        status, events = _stream_message(hdr, session,
                                         "Hello ORA, quick smoke test.")
        assert status == 200
        types = [e[0] for e in events]
        # Should include at least one state or delta event and a final
        assert "final" in types, f"no final event: {types}"

    def test_final_carries_tokens(self, hdr, session):
        status, events = _stream_message(hdr, session, "Second smoke turn.")
        assert status == 200
        finals = [e[1] for e in events if e[0] == "final"]
        assert finals, "no final payload"
        f = finals[-1]
        # token accounting fields
        assert "tokens_in" in f or "input_tokens" in f
        assert "tokens_out" in f or "output_tokens" in f

    def test_tokens_persisted_on_transcript(self, hdr, session):
        # after 2 turns above, GET session should show assistant messages
        # with input_tokens / output_tokens fields
        r = requests.get(f"{API}/ora-chat/sessions/{session}",
                          headers=hdr, timeout=10)
        assert r.status_code == 200
        body = r.json()
        msgs = body.get("messages") or body.get("session", {}).get("messages") or []
        assistant = [m for m in msgs if m.get("role") == "assistant"]
        assert assistant, "no assistant messages persisted"
        m = assistant[-1]
        # tolerate either key naming
        it = m.get("input_tokens", m.get("tokens_in"))
        ot = m.get("output_tokens", m.get("tokens_out"))
        assert it is not None and ot is not None, m
        # in mock mode tokens should be small non-negative ints
        assert isinstance(it, int) and it >= 0
        assert isinstance(ot, int) and ot >= 0

    def test_think_and_advise_only_flags_accepted(self, hdr, session):
        status, events = _stream_message(hdr, session, "think+advise on",
                                         think_mode=True, advise_only=True)
        assert status == 200
        assert any(e[0] == "final" for e in events)


# ── Rate limit path ────────────────────────────────────────────────
class TestRateLimit:
    def test_rate_limit_seeded_returns_error(self, hdr, session, token):
        """Seed 20 ora_chat_usage rows for admin in the last hour via
        direct DB write, then send a 21st message and expect an error
        SSE event or 429 response — NOT a silent drop.
        """
        import asyncio
        import sys
        sys.path.insert(0, "/app/backend")
        from motor.motor_asyncio import AsyncIOMotorClient

        mongo_url = os.environ["MONGO_URL"]
        db_name = os.environ["DB_NAME"]

        async def _seed_and_cleanup():
            cli = AsyncIOMotorClient(mongo_url)
            db = cli[db_name]
            now = time.time()
            docs = [{"admin_id": "test_admin_001",
                      "ts": now - i * 60,
                      "tokens_in": 1, "tokens_out": 1,
                      "TEST_MARK": "iter_2026_08_28"}
                     for i in range(21)]
            await db.ora_chat_usage.insert_many(docs)
            return cli, db

        async def _cleanup(cli, db):
            await db.ora_chat_usage.delete_many({"TEST_MARK": "iter_2026_08_28"})
            cli.close()

        loop = asyncio.new_event_loop()
        cli, db = loop.run_until_complete(_seed_and_cleanup())
        try:
            status, events = _stream_message(hdr, session,
                                             "trigger rate limit please")
            # Accept either 200 with SSE error event OR 429 http
            if status == 429:
                return
            assert status == 200
            err_events = [e for e in events if e[0] == "error"]
            assert err_events, f"expected error SSE, got: {[e[0] for e in events]}"
            payload = err_events[0][1]
            if isinstance(payload, dict):
                assert payload.get("error") in (
                    "rate_limited", "rate_limit", "rate_limit_exceeded"), payload
        finally:
            loop.run_until_complete(_cleanup(cli, db))
            loop.close()


# ── Approve / Reject / Recent (audit) ──────────────────────────────
def _seed_proposal(action_id: str, params: dict) -> str:
    """Insert a `proposed` event directly into ora_chat_actions.
    Returns proposal_id.
    """
    import asyncio
    from motor.motor_asyncio import AsyncIOMotorClient
    from services.ora_chat_v2 import audit as v2_audit

    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]

    async def _do():
        cli = AsyncIOMotorClient(mongo_url)
        db = cli[db_name]
        pid = uuid.uuid4().hex[:12]
        await v2_audit.log_event(
            db, admin_id="test_admin_001", action_id=action_id,
            params=params, proposed_by="test_seed",
            event_type="proposed", proposal_id=pid)
        cli.close()
        return pid

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_do())
    finally:
        loop.close()


class TestActionFlow:
    def test_approve_reversible_action(self, hdr):
        pid = _seed_proposal("create_backlog_item",
                              {"title": "TEST_e2e backlog", "note": "auto"})
        r = requests.post(f"{API}/ora-chat/action/approve",
                          headers=hdr, json={"proposal_id": pid}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True, body
        # audit trail should contain proposed -> approved -> executed
        r2 = requests.get(f"{API}/ora-chat/actions/recent",
                           headers=hdr, timeout=10)
        assert r2.status_code == 200
        events = [x for x in r2.json().get("actions", [])
                   if x.get("proposal_id") == pid]
        types = {e.get("event_type") for e in events}
        assert {"proposed", "approved", "executed"}.issubset(types), types
        # no _id leak
        for e in events:
            assert "_id" not in e

    def test_reject_reversible_action(self, hdr):
        pid = _seed_proposal("park_backlog_item",
                              {"id": "TEST_wontfire", "note": "test reject"})
        r = requests.post(f"{API}/ora-chat/action/reject",
                          headers=hdr, json={"proposal_id": pid}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True
        # audit: proposed, rejected — no executed
        r2 = requests.get(f"{API}/ora-chat/actions/recent",
                           headers=hdr, timeout=10)
        events = [x for x in r2.json().get("actions", [])
                   if x.get("proposal_id") == pid]
        types = {e.get("event_type") for e in events}
        assert "rejected" in types
        assert "executed" not in types

    def test_approve_sensitive_action_gated_off(self, hdr):
        # ORA_CHAT_SENSITIVE=off in .env; toggle_flag is sensitive
        pid = _seed_proposal("toggle_flag",
                              {"flag_name": "explain_plain_english_v1",
                               "value": True})
        r = requests.post(f"{API}/ora-chat/action/approve",
                          headers=hdr, json={"proposal_id": pid}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        # execution should fail with sensitive_actions_disabled
        assert body.get("ok") in (False, None), body
        result = body.get("result") or {}
        assert result.get("error") == "sensitive_actions_disabled", body

    def test_approve_unknown_proposal_returns_404(self, hdr):
        r = requests.post(f"{API}/ora-chat/action/approve",
                          headers=hdr, json={"proposal_id": "nonexistent_zz"},
                          timeout=10)
        assert r.status_code == 404


# ── Founder-offer regression smoke ─────────────────────────────────
class TestFounderOfferSmoke:
    def test_status_endpoint_returns_sane_counts(self, hdr):
        # tolerate either path
        for path in ("/founder-offer/status", "/founder-offer/public-status"):
            r = requests.get(f"{API}{path}", headers=hdr, timeout=10)
            if r.status_code == 200:
                body = r.json()
                # basic sanity: no negatives
                for k in ("spots_remaining", "remaining", "spots_left"):
                    if k in body:
                        assert isinstance(body[k], int)
                        assert body[k] >= 0
                return
        pytest.skip("no founder-offer status endpoint reachable")
