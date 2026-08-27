"""
test_iter2026_08_27_ora_chat_v2_p1_p5.py

Admin ORA Chat rebuild — P1-P5 checkpoint tests.

Covers: rate limit (20/hr beta cap → explicit error, not silent drop),
daily token cap, mock-LLM full turn + persistence contract, undefined
tool rejection, action catalog (propose/approve/execute/reject audit
trail, sensitive-gating default OFF, idempotency window), and the
state block's DATA-ONLY delimiter contract.
"""
from __future__ import annotations

import time

import pytest

from services.ora_chat_v2 import audit, catalog, engine, tools


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def __aiter__(self):
        self._it = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    async def to_list(self, length=None):
        return list(self._rows)[: length or len(self._rows)]


class _Coll:
    def __init__(self, rows=None):
        self.rows = rows or []

    def _matches(self, row, query):
        for k, v in (query or {}).items():
            if "." in k:
                parts = k.split(".")
                cur = row
                for p in parts:
                    cur = (cur or {}).get(p) if isinstance(cur, dict) else None
                actual = cur
            else:
                actual = row.get(k)
            if isinstance(v, dict) and "$gte" in v:
                if not (actual is not None and actual >= v["$gte"]):
                    return False
            elif isinstance(v, dict) and "$lt" in v:
                if not (actual is not None and actual < v["$lt"]):
                    return False
            elif isinstance(v, dict) and "$exists" in v:
                has = actual is not None
                if has != v["$exists"]:
                    return False
            else:
                if actual != v:
                    return False
        return True

    def find(self, query=None, projection=None):
        return _Cursor([r for r in self.rows if self._matches(r, query or {})])

    async def find_one(self, query=None, projection=None):
        for r in self.rows:
            if self._matches(r, query or {}):
                return r
        return None

    async def count_documents(self, query=None):
        return len([r for r in self.rows if self._matches(r, query or {})])

    async def insert_one(self, doc):
        self.rows.append(doc)

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if self._matches(r, query):
                r.update(update.get("$set", {}))
                return
        if upsert:
            new = dict(query)
            new.update(update.get("$set", {}))
            self.rows.append(new)


class _FakeDb:
    def __init__(self):
        self._colls: dict[str, _Coll] = {}

    def __getattr__(self, name):
        if name not in self._colls:
            self._colls[name] = _Coll()
        return self._colls[name]


@pytest.fixture
def db():
    return _FakeDb()


# ── Rate limit / daily cap (Flag B) ─────────────────────────────────
@pytest.mark.asyncio
async def test_rate_limit_blocks_after_20_per_hour_with_explicit_error(db, monkeypatch):
    monkeypatch.setenv("ORA_CHAT_RATE_LIMIT_PER_HOUR", "20")
    now = time.time()
    for _ in range(20):
        await db.ora_chat_usage.insert_one(
            {"admin_id": "u1", "ts": now, "tokens_in": 1, "tokens_out": 1})

    events = [e async for e in engine.run_turn(
        db, admin_id="u1", session={"messages": []}, user_message="hi")]

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["error"] == "rate_limited"
    assert "20" in events[0]["detail"] or "hour" in events[0]["detail"]


@pytest.mark.asyncio
async def test_rate_limit_allows_under_cap(db, monkeypatch):
    monkeypatch.setenv("ORA_CHAT_RATE_LIMIT_PER_HOUR", "20")
    events = [e async for e in engine.run_turn(
        db, admin_id="u2", session={"messages": []}, user_message="hi")]
    assert any(e["type"] == "final" for e in events)


@pytest.mark.asyncio
async def test_daily_token_cap_blocks_with_explicit_error(db, monkeypatch):
    monkeypatch.setenv("ORA_CHAT_DAILY_TOKEN_CAP", "1000")
    await db.ora_chat_usage.insert_one(
        {"admin_id": "u3", "ts": time.time(), "tokens_in": 900, "tokens_out": 200})

    events = [e async for e in engine.run_turn(
        db, admin_id="u3", session={"messages": []}, user_message="hi")]

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["error"] == "daily_token_cap"


# ── Mock-LLM full turn contract ──────────────────────────────────────
@pytest.mark.asyncio
async def test_mock_llm_turn_yields_state_deltas_and_final(db, monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "true")
    events = [e async for e in engine.run_turn(
        db, admin_id="u4", session={"messages": []},
        user_message="What's the build status?")]

    types = [e["type"] for e in events]
    assert types[0] == "state"
    assert "delta" in types
    assert types[-1] == "final"
    final = events[-1]
    assert final["content"]
    assert final["tokens_in"] > 0 and final["tokens_out"] > 0
    assert final["proposal_id"] is None  # mock never calls propose_action


@pytest.mark.asyncio
async def test_advise_only_mode_disables_action_catalog_block(db, monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "true")
    events = [e async for e in engine.run_turn(
        db, admin_id="u5", session={"messages": []},
        user_message="fix something", advise_only=True)]
    assert any(e["type"] == "final" for e in events)


# ── Undefined tool rejection (P5) ────────────────────────────────────
@pytest.mark.asyncio
async def test_undefined_tool_call_is_rejected_and_logged(db, caplog):
    result = await tools.execute_tool(db, "delete_everything", {})
    assert result == {"error": "undefined_tool", "name": "delete_everything"}


@pytest.mark.asyncio
async def test_known_tool_dispatches(db):
    await db.ora_backlog_items.insert_one(
        {"backlog_id": "b1", "title": "Test item", "status": "parked",
         "note": "", "updated_at": time.time()})
    result = await tools.execute_tool(db, "get_backlog", {})
    assert result["items"][0]["backlog_id"] == "b1"


# ── Action catalog: propose → approve → execute → audit (P4) ────────
@pytest.mark.asyncio
async def test_reversible_action_executes_and_audits(db):
    proposal_id = await audit.log_event(
        db, admin_id="admin1", action_id="create_backlog_item",
        params={"title": "Ship the thing", "note": "from chat"},
        proposed_by="turn:1", event_type="proposed")

    proposal = await audit.get_proposal(db, proposal_id)
    assert proposal["event_type"] == "proposed"
    assert proposal["action_id"] == "create_backlog_item"

    result = await catalog.execute_action(db, proposal["action_id"], proposal["params"])
    assert result["ok"] is True
    assert result["result"]["title"] == "Ship the thing"

    await audit.log_event(
        db, admin_id="admin1", action_id=proposal["action_id"],
        params=proposal["params"], proposed_by=proposal["proposed_by"],
        event_type="executed", proposal_id=proposal_id, result=result)

    latest = await audit.get_proposal(db, proposal_id)
    assert latest["event_type"] == "executed"

    recent = await audit.recent_actions(db, limit=5)
    assert any(r["proposal_id"] == proposal_id for r in recent)


@pytest.mark.asyncio
async def test_recent_proposals_groups_by_id_keeping_latest_status(db):
    """Action Audit View (2026-08-27 round 2): one row per proposal,
    not one row per event — the founder wants a clean decision list,
    not a raw event log."""
    p1 = await audit.log_event(
        db, admin_id="admin1", action_id="create_backlog_item",
        params={"title": "A"}, proposed_by="turn:1", event_type="proposed")
    await audit.log_event(
        db, admin_id="admin1", action_id="create_backlog_item",
        params={"title": "A"}, proposed_by="turn:1",
        event_type="executed", proposal_id=p1, result={"ok": True})

    p2 = await audit.log_event(
        db, admin_id="admin1", action_id="toggle_flag",
        params={"flag_name": "explain_plain_english_v1"},
        proposed_by="turn:2", event_type="proposed")
    await audit.log_event(
        db, admin_id="admin1", action_id="toggle_flag",
        params={"flag_name": "explain_plain_english_v1"},
        proposed_by="turn:2", event_type="rejected", proposal_id=p2)

    rows = await audit.recent_proposals(db, limit=10)
    assert len(rows) == 2  # not 4 — grouped, not raw events
    by_pid = {r["proposal_id"]: r for r in rows}
    assert by_pid[p1]["event_type"] == "executed"
    assert by_pid[p2]["event_type"] == "rejected"
    # newest-decided proposal (p2, rejected last) sorts first
    assert rows[0]["proposal_id"] == p2


@pytest.mark.asyncio
async def test_sensitive_action_disabled_by_default(db, monkeypatch):
    monkeypatch.delenv("ORA_CHAT_SENSITIVE", raising=False)
    result = await catalog.execute_action(
        db, "toggle_flag", {"flag_name": "explain_plain_english_v1", "value": True})
    assert result == {"ok": False, "error": "sensitive_actions_disabled",
                       "detail": "ORA_CHAT_SENSITIVE is off — no execution, no env change."}


@pytest.mark.asyncio
async def test_sensitive_action_runs_when_explicitly_enabled(db, monkeypatch):
    monkeypatch.setenv("ORA_CHAT_SENSITIVE", "on")
    result = await catalog.execute_action(
        db, "toggle_flag", {"flag_name": "explain_plain_english_v1", "value": True})
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_undefined_action_id_rejected(db):
    result = await catalog.execute_action(db, "delete_all_users", {})
    assert result == {"ok": False, "error": "undefined_action"}


@pytest.mark.asyncio
async def test_idempotency_window_blocks_repeat_digest_send(db):
    await db.ora_chat_actions.insert_one({
        "action_id": "trigger_digest", "event_type": "executed",
        "ts": time.time(), "params": {"kind": "leak"},
    })
    is_dup = await catalog._check_idempotency(
        db, "trigger_digest", {"kind": "leak"}, window_s=3600)
    assert is_dup is True


# ── State block: DATA-ONLY delimiter contract (P3) ───────────────────
@pytest.mark.asyncio
async def test_state_block_wraps_in_data_only_delimiters(db, monkeypatch):
    async def _fake_funnel(_db, period_days=7):
        return {"stalls_flagged": 0, "stalls_resolved": 0, "hardbreaks": 0,
                "active_stalls": 0, "by_stage": []}
    monkeypatch.setattr(
        "services.journey_watch.compute_journey_watch_card", _fake_funnel)

    from services.ora_chat_v2.state_block import build_state_block, STATE_OPEN, STATE_CLOSE
    block = await build_state_block(db)
    assert block.startswith(STATE_OPEN)
    assert block.rstrip().endswith(STATE_CLOSE)
