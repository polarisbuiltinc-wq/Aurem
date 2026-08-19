"""
2026-08-19 P0 fix — customer chat cost tracking.

Root cause: `routers/chat.py` (the main customer-facing chat path)
never logged a single dollar anywhere. `ora_chat_usage` was 100%
admin-ORA-tool / system-health-check / QA-canary traffic (confirmed
via preview audit: 0 of 2,739 real customer turns had a cost row).

This module (services/customer_cost_tracker.py) fixes that with a
SEPARATE collection (`customer_chat_cost`) so the existing personal
$30/day admin-tool budget guard in services/ora_chat/cost_tracker.py
is never touched by customer volume.
"""
from __future__ import annotations

import pytest


def test_estimate_tokens_basic():
    from services.customer_cost_tracker import estimate_tokens
    assert estimate_tokens("") == 1  # floor of 1, never 0
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("a" * 4) == 1


@pytest.mark.parametrize("provider,expected_model", [
    ("claude-sonnet-openrouter", "anthropic/claude-sonnet-4.5"),
    ("claude-sonnet-maxx-direct", "anthropic/claude-sonnet-4.5"),
    ("glm-5.2+claude-review", "z-ai/glm-5.2"),
    ("glm-5.2-no-review", "z-ai/glm-5.2"),
    ("longcat-2.0", "deepseek/deepseek-chat"),
    ("deepseek", "deepseek/deepseek-chat"),
    ("deepseek-v3-council-c", "deepseek/deepseek-chat"),
    ("deepseek+emergent-watchdog", "deepseek/deepseek-chat"),
])
def test_model_slug_for_provider_known(provider, expected_model):
    from services.customer_cost_tracker import model_slug_for_provider
    assert model_slug_for_provider(provider) == expected_model


def test_model_slug_for_provider_unknown_falls_back():
    from services.customer_cost_tracker import model_slug_for_provider
    from services.ora_chat.cost_tracker import _COST_PER_M_TOKENS
    assert model_slug_for_provider("some-brand-new-model") in _COST_PER_M_TOKENS
    assert model_slug_for_provider("") in _COST_PER_M_TOKENS


class _FakeCollection:
    def __init__(self):
        self.inserts = []

    async def insert_one(self, doc):
        self.inserts.append(dict(doc))


class _FakeDB:
    def __init__(self):
        self.customer_chat_cost = _FakeCollection()


@pytest.mark.asyncio
async def test_log_customer_chat_cost_writes_expected_row(monkeypatch):
    import services.customer_cost_tracker as m
    fake_db = _FakeDB()
    monkeypatch.setattr(m, "get_db", lambda: fake_db)

    cost = await m.log_customer_chat_cost(
        user_id="u1", session_id="s1", project_id="p1",
        route="chat_send", provider="deepseek",
        prompt_text="a" * 400, system_text="b" * 400,
        output_text="c" * 800,
    )
    assert cost > 0
    row = fake_db.customer_chat_cost.inserts[0]
    assert row["user_id"] == "u1"
    assert row["session_id"] == "s1"
    assert row["project_id"] == "p1"
    assert row["route"] == "chat_send"
    assert row["model"] == "deepseek/deepseek-chat"
    assert row["input_tokens"] == 200   # (400+400)/4
    assert row["output_tokens"] == 200  # 800/4
    assert row["estimation_method"] == "char_count_v1"
    assert row["cost_usd"] == cost
    assert "ts_month" in row and "ts_day" in row


@pytest.mark.asyncio
async def test_log_customer_chat_cost_never_raises_on_db_failure(monkeypatch):
    import services.customer_cost_tracker as m

    class _BoomCollection:
        async def insert_one(self, doc):
            raise RuntimeError("mongo down")

    class _BoomDB:
        customer_chat_cost = _BoomCollection()

    monkeypatch.setattr(m, "get_db", lambda: _BoomDB())
    cost = await m.log_customer_chat_cost(
        user_id="u1", session_id="s1", project_id=None,
        route="chat_send", provider="deepseek",
        prompt_text="hi", system_text="", output_text="hello",
    )
    assert cost >= 0  # doesn't raise, still returns a float


@pytest.mark.asyncio
async def test_log_customer_chat_cost_no_db_returns_zero(monkeypatch):
    import services.customer_cost_tracker as m
    monkeypatch.setattr(m, "get_db", lambda: None)
    cost = await m.log_customer_chat_cost(
        user_id="u1", session_id="s1", project_id=None,
        route="chat_send", provider="deepseek",
        prompt_text="hi", system_text="", output_text="hello",
    )
    assert cost == 0.0
