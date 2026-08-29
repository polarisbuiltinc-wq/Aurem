"""
W2 Step 1 — decisive diagnostic (ZERO LLM spend, I1/I9 compliant).

Exercises the REAL code path a bare "approve" reply hits when a prior
turn already carries a real aurem-handoff fence (the founder's exact
P0-B repro shape), end-to-end through:
  1. core.intent_gateway.classify() — REAL heuristic, no LLM cost.
  2. services.orchestrator.chat_with_tools() — REAL function, REAL
     Mongo history load, with ONLY the network-touching
     `call_llm_with_meta` monkeypatched (never any real httpx call).
  3. The exact "empty content fallback" + response_confidence guard
     logic chat_stream applies downstream (replicated inline here,
     since it lives inline in routers/chat.py, not a helper).

Three scenarios, matching the three realistic upstream outcomes:
  A. LLM re-emits a fresh valid fence (the "everything works" case).
  B. LLM returns ok=False / content="" (a real upstream failure).
  C. LLM raises (network exception / timeout).
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/backend/.env")

FENCE_REPLY = (
    "Root cause: the README is missing a license line.\n"
    "```aurem-handoff\nIn `README.md` add a license line at the "
    "bottom.\n```"
)


async def main():
    from motor.motor_asyncio import AsyncIOMotorClient
    import services.orchestrator as orch
    from core.intent_gateway import classify as classify_intent
    from services.response_confidence import (
        prior_turn_had_fix_signal, apply_no_false_success_guard,
        response_seems_mismatched, has_ship_suggestion, FALLBACK_MESSAGE,
    )

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    user_id = f"diag-{uuid.uuid4().hex[:8]}"
    session_id = f"diag-sess-{uuid.uuid4().hex[:8]}"
    prompt = "approve"

    # Simulate turn 1 already landed: user asked for a fix, assistant
    # replied with a real fence. This is the exact shape
    # chat.py::_persist_turn writes to db.chat_sessions.
    await db.chat_sessions.insert_one({
        "session_id": session_id, "user_id": user_id,
        "turns": [
            {"role": "user", "content": "fix the README license line"},
            {"role": "assistant", "content": FENCE_REPLY, "provider": "deepseek"},
        ],
    })

    # Step A — REAL intent classification (no LLM, heuristic-only path
    # since "approve" is a bare ack — high confidence, no escalation).
    _prior_fix_signal = await prior_turn_had_fix_signal(db, session_id, user_id)
    intent_result = await classify_intent(
        prompt, history=[], db=db, user_id=user_id, project_id=None,
        pending_fix=_prior_fix_signal,
    )
    print(f"LOG_LINE_0 prior_fix_signal={_prior_fix_signal} "
          f"intent_tier={intent_result.get('tier')} "
          f"confidence={intent_result.get('confidence')} "
          f"signals={intent_result.get('signals')}")

    tier = intent_result.get("tier")
    if tier not in ("casual", "clarify"):
        # Confirmed: this repro shape routes to the AGENTIC pipeline,
        # NOT the casual_direct_reply short-circuit. Exercise
        # chat_with_tools for real (network boundary only mocked).
        for scenario, stub in [
            ("A_fresh_fence", _stub_returns(FENCE_REPLY)),
            ("B_empty_content", _stub_returns("")),
            ("C_raises", _stub_raises()),
        ]:
            orch.call_llm_with_meta = stub
            try:
                result = await orch.chat_with_tools(
                    prompt=prompt, jwt_token="diag-fake-jwt",
                    session_id=session_id, user_id=user_id,
                    project_id=None, mode="swift", is_founder=False,
                    bin_ctx=None, max_iters=2,
                )
            except Exception as e:
                result = {"content": "", "error": f"raised: {e!r}", "ok": False}

            content = (result.get("content") or "") if isinstance(result, dict) else ""
            provider = (result.get("provider") or "") if isinstance(result, dict) else ""

            # Replicate chat_stream's exact downstream chain (lines
            # ~3022-3157 of routers/chat.py) on this content, since
            # that logic lives inline, not in a reusable function.
            content2 = content or ""
            empty_fallback_fired = False
            if not content2.strip():
                empty_fallback_fired = True
                content2 = (
                    "_(I wasn't able to produce a reply for this agentic "
                    "request. Please rephrase or try again — the chat "
                    "itself is healthy.)_"
                )
            mismatch = response_seems_mismatched(prompt, content2, _prior_fix_signal)
            if mismatch:
                content2 = FALLBACK_MESSAGE
            content2 = apply_no_false_success_guard(prompt, content2, _prior_fix_signal)
            has_fence = has_ship_suggestion(content2)
            print(
                f"LOG_LINE_{scenario} raw_provider={provider!r} "
                f"raw_content_len={len(content)} raw_content_head={content[:120]!r} "
                f"empty_fallback_fired={empty_fallback_fired} mismatch={mismatch} "
                f"final_content_len={len(content2)} final_content_head={content2[:160]!r} "
                f"has_aurem_handoff_fence={has_fence}"
            )
    else:
        print(f"LOG_LINE_UNEXPECTED tier={tier} — casual short-circuit WOULD fire; "
              "diagnosing that branch instead was out of scope for this run.")

    await db.chat_sessions.delete_one({"session_id": session_id})
    client.close()


def _stub_returns(text):
    async def _f(*a, **k):
        return {"ok": bool(text.strip()), "provider": "diag-stub", "content": text,
                "tool_calls_run": 0, "iterations": 1}
    return _f


def _stub_raises():
    async def _f(*a, **k):
        raise RuntimeError("diag-stub: simulated upstream failure — NO real network call made")
    return _f


if __name__ == "__main__":
    asyncio.run(main())
