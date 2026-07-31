"""
Iter 212m-118 — Tests for:
  1. Diagnose-first (RepairAgent pattern) in loop_execute._localize_change_target
  2. litellm.Router wiring (services/llm_router.py)
"""
from __future__ import annotations

import os
import pytest


# ─── 1. Diagnose-first localization ───────────────────────────────────
@pytest.mark.asyncio
async def test_localize_change_target_returns_context_block():
    """When the localizer LLM returns valid JSON pointing at a line +
    function, the helper must produce a 20-line context block."""
    from services import loop_execute as ge

    content = "\n".join(f"line_{i}" for i in range(1, 51))

    async def fake_llm(*, system, user, **kw):
        return {"content": '{"line": 25, "function": "do_thing", "reason": "old impl"}'}

    res = await ge._localize_change_target(
        path="src/x.py",
        current=content,
        plan={"title": "T", "bullets": ["a"]},
        user_message="fix the thing",
        user_id="u1",
        call_llm_with_meta=fake_llm,
    )
    assert res is not None
    assert res["has_localization"] is True
    assert res["line"] == 25
    assert res["function"] == "do_thing"
    # 20-line context window around line 25.
    assert res["snippet_end"] - res["snippet_start"] <= 21
    # Block contains the function name + reason.
    assert "do_thing" in res["context_block"]
    assert "old impl" in res["context_block"]


@pytest.mark.asyncio
async def test_localize_change_target_returns_none_on_entire_file_signal():
    """ENTIRE_FILE signal must skip extra context (fall back to
    full-file rewrite path)."""
    from services import loop_execute as ge

    async def fake_llm(*, system, user, **kw):
        return {"content": '{"line": 0, "function": "ENTIRE_FILE", "reason": "broad"}'}

    res = await ge._localize_change_target(
        path="x.py", current="x = 1\n" * 100,
        plan={"bullets": []}, user_message="rewrite all",
        user_id="u1", call_llm_with_meta=fake_llm,
    )
    assert res is None


@pytest.mark.asyncio
async def test_localize_change_target_returns_none_on_tiny_file():
    """Files <100 bytes don't need localization."""
    from services.loop_execute import _localize_change_target
    async def fake_llm(**kw): return {"content": ""}
    res = await _localize_change_target(
        path="x.py", current="x=1", plan={}, user_message="x",
        user_id="u", call_llm_with_meta=fake_llm,
    )
    assert res is None


@pytest.mark.asyncio
async def test_localize_change_target_returns_none_on_invalid_json():
    """Malformed JSON from the LLM must NOT crash — fall back to None
    so the caller goes through full-file rewrite."""
    from services.loop_execute import _localize_change_target
    async def fake_llm(**kw):
        return {"content": "not json at all"}
    res = await _localize_change_target(
        path="x.py", current="line\n" * 50, plan={},
        user_message="x", user_id="u", call_llm_with_meta=fake_llm,
    )
    assert res is None


@pytest.mark.asyncio
async def test_localize_change_target_strips_code_fences():
    """LLMs love wrapping JSON in ``` despite the system prompt — must
    still parse correctly."""
    from services.loop_execute import _localize_change_target
    async def fake_llm(**kw):
        return {"content": '```json\n{"line": 5, "function": "f", "reason": "r"}\n```'}
    res = await _localize_change_target(
        path="x.py", current="x\n" * 100, plan={},
        user_message="x", user_id="u", call_llm_with_meta=fake_llm,
    )
    assert res is not None
    assert res["line"] == 5
    assert res["function"] == "f"


@pytest.mark.asyncio
async def test_generate_one_inner_uses_localization_when_available(monkeypatch):
    """When localization succeeds, the rewrite prompt must contain the
    'DIAGNOSE-FIRST LOCALIZATION' marker."""
    from services import loop_execute as ge

    captured_prompt: list[str] = []
    async def fake_fetch(client, owner, repo, path, token):
        return "\n".join(f"line_{i}" for i in range(1, 80))
    async def fake_llm(*, system, user, max_tokens=None, mode=None,
                       user_id=None, review_mode=None):
        if review_mode == "swift":      # localizer call
            return {"content": '{"line": 40, "function": "g", "reason": "old"}'}
        captured_prompt.append(user)    # rewrite call
        return {"content": "rewritten content\n"}

    res = await ge._generate_one_inner(
        client=None, idx=1, total=1, path="src/a.py",
        plan={"title": "T", "bullets": []},
        user_message="fix",
        owner="o", repo="r", branch="main", token="t",
        user_id="u1",
        fetch_file=fake_fetch,
        call_llm_with_meta=fake_llm,
    )
    assert res is not None
    assert res["path"] == "src/a.py"
    # The rewrite prompt must include the localization block.
    assert captured_prompt, "rewrite LLM must have been called"
    assert "DIAGNOSE-FIRST LOCALIZATION" in captured_prompt[0]
    assert "function: g" in captured_prompt[0]
    assert "line:     40" in captured_prompt[0]


@pytest.mark.asyncio
async def test_generate_one_inner_falls_back_when_localizer_fails(monkeypatch):
    """Localizer failure must NOT break the rewrite — just skip the
    extra context block."""
    from services import loop_execute as ge

    captured_prompt: list[str] = []
    async def fake_fetch(*a, **k):
        return "\n".join(f"line_{i}" for i in range(1, 80))
    async def fake_llm(*, system, user, max_tokens=None, mode=None,
                       user_id=None, review_mode=None):
        if review_mode == "swift":
            raise RuntimeError("localizer down")
        captured_prompt.append(user)
        return {"content": "ok\n"}

    res = await ge._generate_one_inner(
        client=None, idx=1, total=1, path="src/a.py",
        plan={"title": "T", "bullets": []},
        user_message="fix",
        owner="o", repo="r", branch="main", token="t",
        user_id="u1",
        fetch_file=fake_fetch,
        call_llm_with_meta=fake_llm,
    )
    assert res is not None
    # Rewrite happened despite localizer failure.
    assert "DIAGNOSE-FIRST LOCALIZATION" not in captured_prompt[0]


# ─── 2. litellm router — REMOVED Iter 367 ─────────────────────────────
# The litellm.Router opt-in path (services/llm_router.py) has been
# deleted after the deep-codebase audit found it was permanently
# gated behind LITELLM_ROUTER_ENABLED=1 — an env flag that was
# never set anywhere. The 167-line module was dead code; these
# tests exercised behavior that could never fire in production.
# The legacy multi-provider chain in services/llm.py IS the
# production LLM path (verified live for years).
