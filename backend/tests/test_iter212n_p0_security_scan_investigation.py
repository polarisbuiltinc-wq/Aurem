"""
test_iter212n_p0_security_scan_investigation.py — 2026-08-23

Founder-reported P0: "Check my code for any security problems" failed
in production, non-deterministically, with two different messages:

  1. "I'm not confident enough in this response ..." — ALREADY fixed
     in response_confidence.py's _FIX_INTENT_TOKENS (audit/security
     tokens) in a prior uncommitted change; see
     test_response_confidence_mismatch_gate.py for coverage.

  2. "I wasn't able to produce a reply for this agentic request ..."
     — root cause: `chat_with_tools`'s `if not calls:` branch accepted
     a genuinely EMPTY LLM completion as a valid final answer
     (ok=True, content="") with no error field, which chat.py then
     rendered as the generic "wasn't able to produce a reply" fallback
     with NO reason attached (matches the exact reported text).

  3. Founder also observed a raw ```tool_call``` fence printed as
     literal text on a "fix" retry — root cause:
     `CitationGuard.enforce()`'s corrective rewrite is a single-shot
     LLM completion with no tool-execution loop behind it; if the
     model still emits a tool_call fence there (habit from the main
     system prompt), nothing ever strips it before it reaches the user.

  4. Underlying trigger for #2/#3's contradictory "files not found"
     behavior: `routers/chat.py`'s own (second, redundant) CitationGuard
     pass built a `_ctx` dict WITHOUT `bin_ctx` — every repo tool
     (`read_repo_file` et al.) hard-requires `ctx["bin_ctx"]` via
     `_repo_ctx_from()` (services/local_tools.py) and refuses with
     `_NO_BIN_CTX_ERROR` otherwise. So ANY citation-guard re-fetch in
     chat.py ALWAYS reported "FILE NOT FOUND" for files that were, in
     fact, correctly read moments earlier with the real (fully-scoped)
     bin_ctx used by the orchestrator's own tool loop.

This file locks in the fixes for #2, #3, and #4.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.orchestrator import chat_with_tools
from services.citation_guard import CitationGuard


# ── Fix #4 — chat.py's CitationGuard ctx must carry bin_ctx ──────────

def test_chat_stream_citation_guard_ctx_includes_bin_ctx():
    """Static guard: the `_ctx` dict chat.py builds right before calling
    `CitationGuard().enforce()` must include `bin_ctx` — without it,
    `read_repo_file` can never succeed on the re-fetch, no matter how
    valid the original read was."""
    import pathlib
    src = pathlib.Path("/app/backend/routers/chat.py").read_text(encoding="utf-8")
    anchor = src.index("from services.citation_guard import CitationGuard")
    window = src[anchor: anchor + 1200]
    assert '"bin_ctx"' in window, (
        "chat.py's CitationGuard `_ctx` is missing `bin_ctx` — every "
        "repo tool refuses without it (_NO_BIN_CTX_ERROR), turning a "
        "correct audit into a false 'files not found' rewrite."
    )


# ── Fix #3 — CitationGuard.enforce() strips leaked tool_call fences ──

@pytest.mark.asyncio
async def test_citation_guard_enforce_strips_leaked_tool_call_fence():
    """The single-shot corrective rewrite has no tool-execution loop.
    If the model still emits a `tool_call` fence in that rewrite, it
    must never leak to the user as raw text."""
    bt = chr(96) * 3
    leaking_reply = (
        "Sure, let me check that.\n"
        f"{bt}tool_call\n"
        '{"tool": "read_repo_file", "args": {"path": "backend/config.py"}}\n'
        f"{bt}"
    )

    async def fake_llm_caller(**kwargs):
        return leaking_reply

    async def fake_read_repo_file(ctx, args):
        return {"ok": True, "content": "SECRET_KEY = 'x'"}

    out = await CitationGuard().enforce(
        response_text="See `backend/config.py` for the key.",
        tool_calls=[],  # nothing read this turn -> triggers the guard
        ctx={"user_id": "u1", "project_id": "p1", "bin_ctx": object()},
        llm_caller=fake_llm_caller,
        original_messages=[],
        read_repo_file=fake_read_repo_file,
    )
    assert out["retried"] is True
    assert "tool_call" not in out["text"], (
        f"raw tool_call fence leaked into the corrected reply: {out['text']!r}"
    )


# ── Fix #2 — empty completion must retry, never silently return blank ─

@pytest.mark.asyncio
async def test_chat_with_tools_retries_on_empty_completion_instead_of_blank():
    """A genuinely empty LLM completion (provider hiccup / truncation —
    more likely on a heavy multi-file security audit's final round)
    must NOT be accepted as the final answer. The orchestrator should
    burn one more iteration and use the real content if the retry
    succeeds, instead of returning ok=True/content='' with no error."""
    calls = {"n": 0}

    async def fake_llm(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # Simulates a provider returning a genuinely empty body.
            return {"ok": True, "provider": "glm", "content": "",
                     "fallback_chain": ["glm"], "mode": "chat"}
        return {"ok": True, "provider": "glm",
                 "content": "Here is the real security audit result.",
                 "fallback_chain": ["glm"], "mode": "chat"}

    with patch("services.orchestrator.call_llm_with_meta",
               new=AsyncMock(side_effect=fake_llm)), \
         patch("services.orchestrator.list_tools",
               new=AsyncMock(return_value=[])), \
         patch("services.orchestrator.extract_tool_calls", return_value=[]):
        result = await chat_with_tools(
            prompt="Check my code for any security problems",
            jwt_token="fake", user_id="u1", project_id="p1",
        )

    assert calls["n"] == 2, "orchestrator must retry once on empty completion"
    assert result["content"].strip(), (
        "chat_with_tools returned blank content instead of retrying — "
        "this is exactly the reported 'wasn't able to produce a reply' bug"
    )
    assert "real security audit" in result["content"]


# ── Direct proof at the exact function boundary ──────────────────────

@pytest.mark.asyncio
async def test_read_repo_file_needs_bin_ctx_and_succeeds_with_it(monkeypatch):
    """Proves the EXACT mechanism of the false 'files not found' bug:
    `read_repo_file` refuses (_NO_BIN_CTX_ERROR) when `ctx["bin_ctx"]`
    is absent — which is what chat.py's citation-guard `_ctx` used to
    look like — and succeeds once a real bin_ctx is present, which is
    what the orchestrator's own tool loop always had. This is why the
    SAME files could be read correctly the first time (real bin_ctx)
    and reported 'not found' moments later on chat.py's re-verify
    pass (missing bin_ctx), before this fix."""
    from services.bin_context import BINContext
    from services import local_tools

    async def fake_fetch_file(owner, repo, path, branch, token):
        return "def handler(): pass\n"

    monkeypatch.setattr(local_tools, "_gh_fetch_file", fake_fetch_file)

    # Without bin_ctx (the pre-fix chat.py shape) — must refuse.
    no_ctx_result = await local_tools.read_repo_file(
        {"user_id": "u1", "project_id": "p1"},
        {"path": "backend/config.py"},
    )
    assert no_ctx_result["ok"] is False
    assert "No project selected" in no_ctx_result["error"]

    # With bin_ctx (the fix) — must actually read the file.
    bin_ctx = BINContext(
        bin_id="u1", pid="p1", repo_owner="acme", repo_name="widgets",
        branch="main", pat="fake-pat", is_founder=False,
    )
    with_ctx_result = await local_tools.read_repo_file(
        {"user_id": "u1", "project_id": "p1", "bin_ctx": bin_ctx},
        {"path": "backend/config.py"},
    )
    assert with_ctx_result.get("ok") is not False
    assert "content" in with_ctx_result
    assert "def handler" in with_ctx_result["content"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
