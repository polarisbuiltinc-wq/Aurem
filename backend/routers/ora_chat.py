"""
routers/ora_chat.py — Iter 212m-238

API endpoints for the internal admin ORA Chat.

Endpoints (all under `/api/aurem-dev/ora-chat/`):
    POST   /sessions               — create new session
    GET    /sessions               — list sessions for the caller
    GET    /sessions/{session_id}  — full transcript (owner only)
    POST   /message                — send a message; returns SSE stream
    POST   /slash                  — run a slash-command (JSON reply)
    GET    /usage                  — this month's budget + spend snapshot
    GET    /config                 — live route/temperature config (for tests + admin UI)

All endpoints gate on `require_admin` — founder + admin flags only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request, UploadFile, File
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from cto_services.auth import require_admin, create_token
from services.rate_limiter import check_rate_limit_async, client_ip_from_request
from services.ora_chat import cost_tracker, session as ora_session
from services.ora_chat import house_rules as ora_house_rules
from services.ora_chat import deep_research as ora_deep
from services.ora_chat import codebase_index as ora_codebase
from services.ora_chat import grounding_check as ora_grounding
from services.ora_chat import prompt_snapshot as ora_snapshot
from services.ora_chat import adversarial_review as ora_review
from services.ora_chat import hallucination_classifier as ora_halluc
from services.ora_chat.router import (
    classify_intent, resolve, fallback_route, route_config_snapshot,
)
from services.ora_chat.providers import stream_call, one_shot
from services.ora_chat.safety import (
    assemble_system_prompt, KNOWN_COMMANDS, parse_slash_command,
    DEFAULT_HOUSE_RULES, house_rules_soft_warning,
    CORE_SAFETY_RULES, AUREM_CONTEXT,
)
from services.ora_chat import slash_commands as slash_dispatch
from services.ora_chat import intent_router as ora_intent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ora-chat", tags=["ORA Chat (admin)"])


# Iter 212m-239 — Single-user personal context: no hourly message
# cap. Per-minute burst limiter kept as a defensive backstop to catch
# runaway loops / accidental infinite loops, but cost-tier degradation
# (see cost_tracker.budget_status) is the real spend brake.
_BURST_PER_MIN = 20


# ── Session endpoints ──────────────────────────────────────────────
def _valid_tz(tz: Optional[str]) -> Optional[str]:
    """Whitelist-lite validation — IANA TZ names are simple ASCII with
    slashes/underscores/hyphens. Reject anything that could be a
    prompt-injection vector via the header (we're going to render
    this into the LLM prompt, so untrusted).
    """
    if not tz or not isinstance(tz, str):
        return None
    if len(tz) > 64:
        return None
    import re as _re
    if not _re.match(r"^[A-Za-z][A-Za-z0-9/_\-+]{0,63}$", tz):
        return None
    return tz


# ── Iter 264 Fix B — conditional codebase-tree injection ────────────
_TREE_TRIGGER_RE = re.compile(r"/(?:repo-tree|repo-stats|find|read|defs)\b")


def _needs_tree(query: str, labels: Optional[list] = None) -> bool:
    """Inject the compact FILENAME INDEX only when the turn actually
    needs codebase awareness: NEEDS_CODEBASE label OR an inline
    codebase slash-command mention. Everything else gets highlights
    only (~800 tokens/msg saved + removes the fabrication vector)."""
    if labels and "NEEDS_CODEBASE" in labels:
        return True
    return bool(_TREE_TRIGGER_RE.search(query or ""))


async def _codebase_context(query: str,
                             labels: Optional[list] = None) -> tuple:
    """Returns (block_for_prompt, highlights, tree). Highlights are
    ALWAYS injected (curated ground truth); tree is conditional."""
    try:
        highlights = await ora_codebase.system_highlights()
    except Exception:
        highlights = ""
    tree = ""
    if _needs_tree(query, labels):
        try:
            tree = await ora_codebase.compact_tree(max_files=120)
        except Exception:
            tree = ""
    block = f"{highlights}\n\n{tree}".strip() if (highlights or tree) else None
    return block, highlights, tree


class NewSessionBody(BaseModel):
    title: Optional[str] = Field(default="", max_length=80)


@router.post("/sessions")
async def create_session(body: NewSessionBody,
                          authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    doc = await ora_session.create_session(user["user_id"], body.title or "")
    return {"ok": True, "session": doc}


@router.get("/sessions")
async def list_sessions(authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    rows = await ora_session.list_sessions(user["user_id"])
    return {"ok": True, "sessions": rows}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str,
                       authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    doc = await ora_session.get_session(session_id, user["user_id"])
    if not doc:
        raise HTTPException(404, "Session not found")
    return {"ok": True, "session": doc}


# ── Slash command endpoint (deterministic path) ─────────────────────
class SlashBody(BaseModel):
    command: str = Field(..., pattern=r"^/?[a-z][a-z0-9\-]*(?:\s.*)?$")
    session_id: Optional[str] = None
    explain: bool = True  # if True, run the low-temp LLM formatter


@router.post("/slash")
async def run_slash(body: SlashBody,
                     request: Request,
                     authorization: Optional[str] = Header(None),
                     x_client_tz: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    user_tz = _valid_tz(x_client_tz)

    # Rate + budget gates apply to slash-commands too (they still touch DB).
    ip = client_ip_from_request(request)
    if not await check_rate_limit_async(f"ora_chat:min:{user['user_id']}:{ip}", _BURST_PER_MIN):
        raise HTTPException(429, "Burst limit — slow down for a minute")

    parsed = parse_slash_command(body.command.strip())
    if not parsed:
        raise HTTPException(400, {
            "error": "unknown_command",
            "known": list(KNOWN_COMMANDS),
        })
    cmd, args = parsed

    # Budget-mode gates — spike hard-stop is the ONLY thing that fully blocks.
    b_status = await cost_tracker.budget_status()
    if b_status["mode"] == "spike_hard_stop":
        raise HTTPException(402, {
            "error":   "spike_hard_stop",
            "message": (f"Daily spend spike detected "
                         f"(${b_status['day_spent_usd']} > "
                         f"${b_status['spike_cap_usd']}). Chat is paused "
                         "until you override or the day rolls over."),
            "budget":  b_status,
        })
    # Economy mode still runs slash (DB is free) but skips the LLM
    # explain (which would otherwise consume more tokens).
    if body.explain and b_status["mode"] == "economy":
        body.explain = False

    # Run the deterministic query. NEVER let LLM decide the fetch.
    result = await slash_dispatch.run_slash_command(cmd, args, ctx=user)

    summary_text = ""
    llm_meta: dict = {}
    if body.explain and result.get("ok"):
        cfg = resolve("slash_explain")
        # Fetch the caller's house rules so the explain sentence
        # honors their tone preferences too.
        hr_text = await ora_house_rules.get_effective_text(user["user_id"])
        system_prompt = assemble_system_prompt(hr_text, user_tz=user_tz)
        msg = (
            f"A slash-command just ran. Explain this result in ONE crisp "
            f"sentence (Hinglish is fine). Do NOT invent numbers. Do NOT "
            f"suggest running other commands.\n\n"
            f"COMMAND: /{cmd}\n"
            f"RESULT: {json.dumps(result.get('value'))}\n"
            f"METRIC LABEL: {result.get('metric', '')}"
        )
        text, usage, err = await one_shot(
            model=cfg["model"],
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user",   "content": msg}],
            temperature=cfg["temperature"],
            top_p=cfg["top_p"],
            presence_penalty=cfg["presence_penalty"],
            max_tokens=cfg["max_tokens"],
        )
        summary_text = text or ""
        if not err and usage:
            cost = await cost_tracker.log_call(
                user_id=user["user_id"],
                session_id=body.session_id or "slash",
                route=cfg["route"],
                model=cfg["model"],
                temperature=cfg["temperature"],
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )
            llm_meta = {"cost_usd": cost, **usage,
                         "temperature": cfg["temperature"],
                         "model": cfg["model"]}

    # Iter 264 Fix A — grounding hook on the /slash JSON path too.
    grounding = {"fabricated": [], "unverified": []}
    if summary_text:
        g = await ora_grounding.run_post_response_check(
            user_id=user["user_id"],
            session_id=body.session_id or "slash",
            query=body.command, reply=summary_text, route="slash",
            retrieved_context=json.dumps(result.get("value")),
        )
        grounding = {"fabricated": g["fabricated"],
                      "unverified": g["unverified"]}

    # Persist to session transcript (best-effort — slash still works
    # even when session_id is unknown, so ops can eval commands
    # without opening a chat window).
    if body.session_id:
        await ora_session.append_message(
            body.session_id, user["user_id"],
            role="user", content=body.command,
        )
        await ora_session.append_message(
            body.session_id, user["user_id"],
            role="assistant",
            content=summary_text or json.dumps(result.get("value")),
            route=llm_meta.get("route", "slash"),
            model=llm_meta.get("model", ""),
            temperature=llm_meta.get("temperature"),
            input_tokens=llm_meta.get("input_tokens", 0),
            output_tokens=llm_meta.get("output_tokens", 0),
            cost_usd=llm_meta.get("cost_usd", 0.0),
            message_id=uuid.uuid4().hex,
            ungrounded=grounding["fabricated"] or None,
        )

    return {
        "ok":       True,
        "command":  cmd,
        "result":   result,
        "summary":  summary_text,
        "llm":      llm_meta,
        "grounding": grounding,
        "budget_mode": b_status["mode"],
    }


# ── Streaming message endpoint ──────────────────────────────────────
class MessageBody(BaseModel):
    session_id: str
    content: str = Field(..., min_length=1, max_length=4000)


@router.post("/message")
async def send_message(body: MessageBody,
                        request: Request,
                        authorization: Optional[str] = Header(None),
                        x_client_tz: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    user_tz = _valid_tz(x_client_tz)

    # Rate + budget gates.
    ip = client_ip_from_request(request)
    if not await check_rate_limit_async(f"ora_chat:min:{user['user_id']}:{ip}", _BURST_PER_MIN):
        raise HTTPException(429, "Burst limit — slow down for a minute")
    b_status = await cost_tracker.budget_status()
    if b_status["mode"] == "spike_hard_stop":
        # HTTP 402 → frontend renders a clear inline block, not a toast.
        raise HTTPException(402, {
            "error":    "spike_hard_stop",
            "message":  (f"Daily spend spike detected "
                          f"(${b_status['day_spent_usd']} > "
                          f"${b_status['spike_cap_usd']}). Chat is paused "
                          "until you override or the day rolls over."),
            "budget":   b_status,
        })

    # Session ownership check.
    sess = await ora_session.get_session(body.session_id, user["user_id"])
    if not sess:
        raise HTTPException(404, "Session not found")

    # If the message is a slash-command, we short-circuit to /slash
    # semantics but keep the same streaming envelope so the frontend
    # only needs one path.
    parsed = parse_slash_command(body.content.strip())
    if parsed:
        return await _stream_slash_result(user, sess, body.content.strip(), parsed, b_status, user_tz)

    # ── Phase 3 · Feb 2026 — Two-layer intent classification.
    # Compute ONCE here so both the deep-research and regular-chat
    # SSE paths can emit the same verdict.  Regex layer is
    # deterministic + near-zero cost; LLM layer only fires when the
    # regex says UNKNOWN.  Never allowed to raise — falls back to
    # UNKNOWN so downstream code never sees a broken verdict.
    try:
        _intent_verdict = await ora_intent.classify_intent(
            body.content, one_shot_fn=one_shot,
        )
    except Exception as _intent_err:   # noqa: BLE001
        logger.warning("intent classify failed: %r", _intent_err)
        _intent_verdict = {"intent": "UNKNOWN", "source": "empty",
                            "matches": [], "meta": {}}

    # Iter 212m-245 — Auto Deep-Research pre-check.
    # Run the multi-label classifier BEFORE the single-route regex.
    # Only fire the multi-source path if >=2 substantive labels match
    # (or NEEDS_DEEP is explicit). Otherwise fall through to the
    # existing single-route flow — keeps single-topic queries cheap.
    # Skip entirely in `economy` mode (budget-degraded, single-source
    # only) and when the Claude tool_orchestration flag is on (the
    # follow-up will route to Anthropic direct instead — stub returns
    # False today so this branch is inert).
    labels: list = []
    if b_status["mode"] != "economy" and not ora_deep.use_claude_tools():
        try:
            labels = await ora_deep.classify_labels(body.content)
        except Exception as e:
            logger.warning("deep-research classifier failed: %s", e)
            labels = []
        if labels and await ora_deep.should_go_deep(labels):
            return await _stream_deep_research(
                user, sess, body.content, labels, b_status, user_tz,
                intent_verdict=_intent_verdict,
            )
        # Iter 267 GAP 1 — a pasted non-GitHub URL always routes deep
        # (the URL-fetch tool lives in the orchestrator), even when the
        # classifier found no other labels.
        if ora_deep.has_fetchable_url(body.content):
            return await _stream_deep_research(
                user, sess, body.content, labels, b_status, user_tz,
                intent_verdict=_intent_verdict,
            )

    # Regular chat — pick route via keyword rules.
    route_name = classify_intent(body.content)
    # Iter 212m-239 — Economy mode forces GLM-5.2 fallback for all
    # non-slash chat so the assistant NEVER stops working. Full model
    # routing resumes at the next daily rollover.
    if b_status["mode"] == "economy":
        route_name = fallback_route()
    cfg = resolve(route_name)

    # Persist the user turn IMMEDIATELY so the transcript stays honest
    # even if the stream dies mid-response.
    await ora_session.append_message(
        body.session_id, user["user_id"],
        role="user", content=body.content,
    )

    # Refresh rolling summary if the window overflowed with the new turn.
    await ora_session.maybe_update_summary(body.session_id, user["user_id"])

    # Rebuild the LLM message list (system + summary + last 6 turns).
    sess = await ora_session.get_session(body.session_id, user["user_id"])
    llm_messages = await ora_session.build_llm_messages(sess or {})
    # System prompt with layered house rules — safety layer FIRST.
    hr_text = await ora_house_rules.get_effective_text(user["user_id"])
    # Iter 212m-246 — inject compact codebase tree so ORA has
    # baseline awareness of what modules exist without needing a
    # slash-command per question.
    # Iter 212m-249 — ALSO inject the curated system-highlights block
    # so meta-questions ("kya best build hai", "what does AUREM do")
    # get answered from ground truth, not from noisy BM25 hits.
    # Iter 264 Fix B — conditional injection: highlights ALWAYS, the
    # compact FILENAME INDEX only when the turn needs codebase
    # awareness (NEEDS_CODEBASE label / inline slash mention).
    cb_block, cb_highlights, cb_tree_only = \
        await _codebase_context(body.content, labels)
    system_prompt = assemble_system_prompt(hr_text, user_tz=user_tz,
                                            codebase_tree=cb_block)
    llm_messages = [{"role": "system", "content": system_prompt}] + llm_messages

    async def event_stream():
        buf: list[str] = []
        usage: dict = {}
        errored: Optional[str] = None
        fallback_used = False
        # Iter 264 Fix A5 — feature-flagged regen-on-fabrication.
        # When ON, deltas are buffered until the grounding check
        # passes (one silent corrective retry on FABRICATED).
        regen_mode = os.getenv("ORA_REGEN_ON_FABRICATION", "0") == "1"
        # Iter 268 — HIGH_STAKES turns are BUFFERED: draft → hostile
        # review (GLM-5.2) → possible single regen → then stream.
        # Correctness > perceived speed on these ~10-20% of turns.
        high_stakes = "HIGH_STAKES" in (labels or [])
        buffered = regen_mode or high_stakes

        async def _try_stream(model_cfg: dict):
            nonlocal errored, usage
            errored = None
            async for evt in stream_call(
                model=model_cfg["model"],
                messages=llm_messages,
                temperature=model_cfg["temperature"],
                top_p=model_cfg["top_p"],
                presence_penalty=model_cfg["presence_penalty"],
                max_tokens=model_cfg["max_tokens"],
            ):
                if evt["type"] == "delta":
                    buf.append(evt["content"])
                    yield evt
                elif evt["type"] == "usage":
                    usage = {k: evt[k] for k in
                              ("input_tokens", "output_tokens") if k in evt}
                elif evt["type"] == "error":
                    errored = evt.get("error", "unknown")
                    yield evt
                elif evt["type"] == "done":
                    yield evt

        # Announce chosen model up front so the UI can badge it.
        yield {"type": "route", "route": cfg["route"],
                "model": cfg["model"], "temperature": cfg["temperature"],
                "budget_mode": b_status["mode"]}
        # Phase 3 · Feb 2026 — emit the pre-computed intent verdict
        # from send_message() so both deep and regular paths hand the
        # frontend the same event shape.
        if _intent_verdict:
            yield {"type": "intent", **_intent_verdict}
        async for evt in _try_stream(cfg):
            if regen_mode and evt["type"] == "delta":
                continue
            yield evt

        # Fallback path — only if primary produced ZERO content.
        if errored and not buf:
            fb_cfg = resolve(fallback_route())
            fallback_used = True
            yield {"type": "route", "route": fb_cfg["route"],
                    "model": fb_cfg["model"], "temperature": fb_cfg["temperature"],
                    "reason": "primary_failed"}
            async for evt in _try_stream(fb_cfg):
                if buffered and evt["type"] == "delta":
                    continue
                yield evt

        # Persist assistant turn + log usage — even if empty/errored,
        # so the transcript reflects reality.
        final_text = "".join(buf)
        chosen = resolve(fallback_route()) if fallback_used else cfg

        # Iter 264 Fix A — deterministic post-response grounding check
        # (string lookups vs canonical index — milliseconds).
        grounding = await ora_grounding.run_post_response_check(
            user_id=user["user_id"], session_id=body.session_id,
            query=body.content, reply=final_text, route=chosen["route"],
            codebase_tree=cb_tree_only or None,
            system_highlights=cb_highlights or None,
        )

        # Iter 268 — Adversarial review pass. Draft (V3) → hostile
        # reviewer (GLM-5.2, flag-only). Triggers: HIGH_STAKES label
        # (buffered → can regen) OR grounding UNVERIFIED escalation
        # (post-hoc → caveat only). ONE regen max, never chained.
        review = None
        review_caveats: list[str] = []
        review_regen_fired = review_regen_cleared = False
        review_reason = ora_review.trigger_reason(labels, grounding)
        if review_reason:
            review = await ora_review.run_review(
                user_id=user["user_id"], session_id=body.session_id,
                query=body.content, draft=final_text,
                context="\n\n".join(
                    x for x in (cb_highlights, cb_tree_only) if x),
                reason=review_reason,
            )
            if not review.get("skipped"):
                if review["hard"] and buffered:
                    review_regen_fired = True
                    text2, usage2, err2 = await one_shot(
                        model=chosen["model"],
                        messages=llm_messages
                        + [{"role": "assistant", "content": final_text},
                           {"role": "user",
                            "content": ora_review.corrective_prompt(
                                review["hard"])}],
                        temperature=chosen["temperature"],
                        top_p=chosen["top_p"],
                        presence_penalty=chosen["presence_penalty"],
                        max_tokens=chosen["max_tokens"],
                    )
                    if text2 and not err2:
                        final_text = text2
                        for k in ("input_tokens", "output_tokens"):
                            usage[k] = (usage.get(k, 0)
                                        + (usage2 or {}).get(k, 0))
                        grounding = await ora_grounding.run_post_response_check(
                            user_id=user["user_id"],
                            session_id=body.session_id,
                            query=body.content, reply=final_text,
                            route=chosen["route"],
                            codebase_tree=cb_tree_only or None,
                            system_highlights=cb_highlights or None,
                        )
                        review_regen_cleared = not grounding["fabricated"]
                review_caveats = [f["quote"] for f in review["soft"]]
                if review["hard"] and not (review_regen_fired
                                            and review_regen_cleared):
                    review_caveats = ([f["quote"] for f in review["hard"]]
                                      + review_caveats)
            await ora_review.log_metrics(
                user_id=user["user_id"], session_id=body.session_id,
                route=chosen["route"], reason=review_reason, review=review,
                regen_fired=review_regen_fired,
                regen_cleared=review_regen_cleared)
        else:
            logger.info("ora review skipped: no trigger (routine turn)")

        # Iter 264 Fix A5 — ORA_REGEN_ON_FABRICATION=1 (default OFF):
        # ONE silent corrective retry before streaming the buffered text.
        if regen_mode and grounding["fabricated"] and not review_regen_fired:
            corrective = (
                "Your draft cited non-existent files: "
                + ", ".join(grounding["fabricated"])
                + ". Remove them or mark them explicitly as "
                "unverified. Rewrite the full answer."
            )
            text2, usage2, err2 = await one_shot(
                model=chosen["model"],
                messages=llm_messages
                + [{"role": "assistant", "content": final_text},
                   {"role": "user", "content": corrective}],
                temperature=chosen["temperature"],
                top_p=chosen["top_p"],
                presence_penalty=chosen["presence_penalty"],
                max_tokens=chosen["max_tokens"],
            )
            if text2 and not err2:
                final_text = text2
                for k in ("input_tokens", "output_tokens"):
                    usage[k] = usage.get(k, 0) + (usage2 or {}).get(k, 0)
                grounding = await ora_grounding.run_post_response_check(
                    user_id=user["user_id"], session_id=body.session_id,
                    query=body.content, reply=final_text,
                    route=chosen["route"],
                    codebase_tree=cb_tree_only or None,
                    system_highlights=cb_highlights or None,
                )
        if buffered:
            for i in range(0, len(final_text), _DELTA_CHUNK_LEN):
                yield {"type": "delta",
                        "content": final_text[i:i+_DELTA_CHUNK_LEN]}
                await asyncio.sleep(0)

        cost = 0.0
        if usage:
            cost = await cost_tracker.log_call(
                user_id=user["user_id"],
                session_id=body.session_id,
                route=chosen["route"],
                model=chosen["model"],
                temperature=chosen["temperature"],
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                error=errored,
            )

        # Iter 264 Fix C — persist the exact assembled system prompt.
        msg_id = uuid.uuid4().hex
        snap = await ora_snapshot.save_snapshot(
            message_id=msg_id, session_id=body.session_id,
            full_prompt=system_prompt,
            component_sizes={
                "core":        len(CORE_SAFETY_RULES),
                "aurem_ctx":   len(AUREM_CONTEXT),
                "highlights":  len(cb_highlights or ""),
                "tree":        len(cb_tree_only or ""),
                "house_rules": len(hr_text or ""),
                "retrieved":   0,
            })

        await ora_session.append_message(
            body.session_id, user["user_id"],
            role="assistant", content=final_text,
            route=chosen["route"], model=chosen["model"],
            temperature=chosen["temperature"],
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cost_usd=cost,
            message_id=msg_id,
            ungrounded=grounding["fabricated"] or None,
            prompt_sha256=snap["sha256"],
            component_sizes=snap["component_sizes"],
            review=({"reason": review_reason,
                      "skipped": review.get("skipped"),
                      "flags": len(review.get("flags") or []),
                      "types": sorted({f["type"] for f in
                                        review.get("flags") or []}),
                      "regen_fired": review_regen_fired,
                      "regen_cleared": review_regen_cleared,
                      "caveats": review_caveats[:6]}
                     if review is not None else None),
        )

        # Iter 264 Fix A4 — user-facing warning ONLY for FABRICATED
        # (UNVERIFIED = real path, soft log-only — no chip).
        if grounding["fabricated"]:
            yield {"type": "grounding_warning",
                    "ungrounded": grounding["fabricated"]}
        # Iter 268 — review caveats (soft flags / uncleared hard flags).
        if review_caveats:
            yield {"type": "review_caveat", "quotes": review_caveats[:6]}

        yield {"type": "final", "cost_usd": cost,
                "input_tokens":  usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "ungrounded":    grounding["fabricated"],
                "review_caveats": review_caveats[:6],
                "errored": errored}

    async def sse_events():
        async for evt in event_stream():
            yield {"event": evt["type"], "data": json.dumps(evt)}

    return EventSourceResponse(sse_events())


async def _stream_slash_result(user: dict, sess: dict,
                                raw_text: str, parsed: tuple[str, str],
                                b_status: dict,
                                user_tz: Optional[str] = None):
    """Wrap the deterministic slash path in the same SSE envelope so
    the frontend has one code path for messages."""
    cmd, args = parsed

    async def event_stream():
        yield {"type": "route", "route": "slash", "model": "deterministic",
                "temperature": 0.0, "budget_mode": b_status["mode"]}
        try:
            result = await slash_dispatch.run_slash_command(cmd, args, ctx=user)
        except KeyError:
            yield {"type": "error", "error": "unknown_command"}
            return
        # Serialize the DB result deterministically first so the UI
        # can render structured data even if the LLM explain fails.
        yield {"type": "slash_result", "command": cmd, "result": result}

        # Optional low-temp explain sentence (skipped in economy/spike).
        explain_text = ""
        summary_usage: dict = {}
        chosen = resolve("slash_explain")
        if b_status["mode"] not in ("economy", "spike_hard_stop") and result.get("ok"):
            hr_text = await ora_house_rules.get_effective_text(user["user_id"])
            system_prompt = assemble_system_prompt(hr_text, user_tz=user_tz)
            msg = (
                f"A slash-command just ran. Explain this result in ONE "
                f"crisp sentence. Do NOT invent numbers.\n\n"
                f"COMMAND: /{cmd}\nRESULT: {json.dumps(result.get('value'))}\n"
                f"METRIC LABEL: {result.get('metric', '')}"
            )
            text, usage, err = await one_shot(
                model=chosen["model"],
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user",   "content": msg}],
                temperature=chosen["temperature"],
                top_p=chosen["top_p"],
                presence_penalty=chosen["presence_penalty"],
                max_tokens=chosen["max_tokens"],
            )
            explain_text = text or ""
            if not err and usage:
                summary_usage = usage
                yield {"type": "delta", "content": explain_text}

        # Iter 264 Fix A — grounding hook on the slash-explain sentence.
        grounding = {"fabricated": [], "unverified": []}
        if explain_text:
            g = await ora_grounding.run_post_response_check(
                user_id=user["user_id"], session_id=sess["session_id"],
                query=raw_text, reply=explain_text, route="slash",
                retrieved_context=json.dumps(result.get("value")),
            )
            grounding = {"fabricated": g["fabricated"],
                          "unverified": g["unverified"]}

        # Persist both turns.
        await ora_session.append_message(
            sess["session_id"], user["user_id"],
            role="user", content=raw_text,
        )
        cost = 0.0
        if summary_usage:
            cost = await cost_tracker.log_call(
                user_id=user["user_id"],
                session_id=sess["session_id"],
                route=chosen["route"], model=chosen["model"],
                temperature=chosen["temperature"],
                input_tokens=summary_usage.get("input_tokens", 0),
                output_tokens=summary_usage.get("output_tokens", 0),
            )
        await ora_session.append_message(
            sess["session_id"], user["user_id"],
            role="assistant",
            content=explain_text or json.dumps(result.get("value")),
            route=chosen["route"], model=chosen["model"],
            temperature=chosen["temperature"],
            input_tokens=summary_usage.get("input_tokens", 0),
            output_tokens=summary_usage.get("output_tokens", 0),
            cost_usd=cost,
            message_id=uuid.uuid4().hex,
            ungrounded=grounding["fabricated"] or None,
        )
        if grounding["fabricated"]:
            yield {"type": "grounding_warning",
                    "ungrounded": grounding["fabricated"]}
        yield {"type": "final", "cost_usd": cost,
                "ungrounded": grounding["fabricated"],
                **{k: summary_usage.get(k, 0) for k in
                    ("input_tokens", "output_tokens")}}

    async def sse_events():
        async for evt in event_stream():
            yield {"event": evt["type"], "data": json.dumps(evt)}
            await asyncio.sleep(0)

    return EventSourceResponse(sse_events())


# ── Deep Research streaming envelope (Iter 212m-245) ───────────────
_DELTA_CHUNK_LEN = 48  # chars per synthetic delta so the UI streams smoothly


def _labels_to_source_tag(fired: list[str]) -> str:
    """Turn `['github', 'web']` into `github+web` for the route badge."""
    order = ["url", "github", "social", "news", "web"]
    seen: list[str] = []
    for tag in order:
        if tag in fired and tag not in seen:
            seen.append(tag)
    for tag in fired:
        if tag not in seen:
            seen.append(tag)
    return "+".join(seen) if seen else "none"


async def _stream_deep_research(user: dict, sess: dict,
                                 raw_text: str, labels: list[str],
                                 b_status: dict,
                                 user_tz: Optional[str] = None,
                                 intent_verdict: Optional[dict] = None):
    """SSE envelope for the multi-source deep-research path.

    Semantics:
      - Persist the user turn immediately.
      - Emit a `route` event with `route="deep"` + `sources` string.
      - Call `orchestrate()` which fires up to 4 tools in parallel then
        does ONE DeepSeek V3 synthesis pass.
      - Chunk the synthesized text into ~48-char deltas so the UI
        renders progressively (orchestrate itself returns full text —
        it's a one-shot call, not a stream).
      - Log cost via `cost_tracker.log_call` so daily budget accrues.
      - Emit a `final` event with `sources_fired`, `downgraded`,
        `tool_cost_usd` so the frontend badge can show what actually ran.
    """
    session_id = sess["session_id"]

    # Persist user turn before we start — transcript stays honest if
    # any tool fails mid-flight.
    await ora_session.append_message(
        session_id, user["user_id"],
        role="user", content=raw_text,
    )

    hr_text = await ora_house_rules.get_effective_text(user["user_id"])
    # Iter 264 Fix B — conditional tree injection on the deep path too.
    cb_block, cb_highlights, cb_tree_only = \
        await _codebase_context(raw_text, labels)

    async def event_stream():
        # Announce the route up front — sources list will be
        # patched in the `final` event since we don't yet know which
        # tools succeeded until orchestrate returns.
        cfg = resolve("deep")
        yield {"type": "route", "route": "deep",
                "model": cfg["model"], "temperature": cfg["temperature"],
                "labels": labels,
                "budget_mode": b_status["mode"]}
        # Phase 3 · Feb 2026 — surface the intent verdict on the deep
        # path too so the founder sees the same badge/CTA regardless
        # of which route ORA chose.
        if intent_verdict:
            yield {"type": "intent", **intent_verdict}
        if "HIGH_STAKES" in (labels or []):
            yield {"type": "review_status", "state": "verifying",
                    "reason": "high_stakes"}

        try:
            out = await ora_deep.orchestrate(
                query=raw_text, labels=labels,
                house_rules_text=hr_text, user_tz=user_tz,
                codebase_tree=cb_block,
            )
        except Exception as e:
            logger.exception("deep-research orchestrator crashed")
            yield {"type": "error", "error": f"deep_research_failed: {type(e).__name__}"}
            out = {"ok": False, "text": "", "sources_fired": [],
                    "errors": [str(e)], "tool_cost_usd": 0.0,
                    "downgraded": False}

        text = out.get("text") or ""
        sources_fired = out.get("sources_fired") or []
        sources_tag = _labels_to_source_tag(sources_fired)

        # Emit an updated `route` event now that we know which tools
        # fired — frontend uses this to render `deep · github+web`.
        yield {"type": "route", "route": "deep",
                "model": cfg["model"], "temperature": cfg["temperature"],
                "sources": sources_tag,
                "sources_fired": sources_fired,
                "downgraded": bool(out.get("downgraded")),
                "budget_mode": b_status["mode"]}

        # Book the synthesis LLM call against the daily budget.
        synth_usage = out.get("synthesis_usage") or {}
        cost = 0.0
        if synth_usage:
            cost = await cost_tracker.log_call(
                user_id=user["user_id"],
                session_id=session_id,
                route="deep",
                model=out.get("synthesis_model") or cfg["model"],
                temperature=cfg["temperature"],
                input_tokens=synth_usage.get("input_tokens", 0),
                output_tokens=synth_usage.get("output_tokens", 0),
            )

        if not text and not out.get("ok"):
            text = ("Sources didn't respond in time. Try again "
                     "in a moment, or narrow the question.")

        # Iter 264 Fix A — deterministic grounding vs the ACTUAL
        # retrieved excerpts (not just source names) + canonical index.
        # Runs BEFORE streaming (iter 268) — deep text is fully
        # assembled here, so review/regen can happen pre-delivery.
        grounding = await ora_grounding.run_post_response_check(
            user_id=user["user_id"], session_id=session_id,
            query=raw_text, reply=text, route="deep",
            sources_fired=out.get("sources_fired") or [],
            retrieved_context=out.get("retrieved_context") or "",
            codebase_tree=cb_tree_only or None,
            system_highlights=cb_highlights or None,
        )

        # Iter 268 — Adversarial review on the deep path. Deep text is
        # pre-assembled → regen is always possible here (buffered-free).
        review = None
        review_caveats: list[str] = []
        review_regen_fired = review_regen_cleared = False
        review_reason = ora_review.trigger_reason(labels, grounding)
        if review_reason and out.get("ok"):
            review = await ora_review.run_review(
                user_id=user["user_id"], session_id=session_id,
                query=raw_text, draft=text,
                context=(out.get("retrieved_context") or "")
                        + "\n\n" + (cb_highlights or ""),
                reason=review_reason,
            )
            if not review.get("skipped"):
                if review["hard"]:
                    review_regen_fired = True
                    text2, usage2, err2 = await one_shot(
                        model=out.get("synthesis_model") or cfg["model"],
                        messages=[
                            {"role": "system",
                             "content": out.get("system_prompt") or ""},
                            {"role": "user",
                             "content": out.get("synth_prompt") or raw_text},
                            {"role": "assistant", "content": text},
                            {"role": "user",
                             "content": ora_review.corrective_prompt(
                                 review["hard"])},
                        ],
                        temperature=cfg["temperature"],
                        top_p=cfg["top_p"],
                        presence_penalty=cfg["presence_penalty"],
                        max_tokens=cfg["max_tokens"],
                    )
                    if text2 and not err2:
                        text = text2
                        for k in ("input_tokens", "output_tokens"):
                            synth_usage[k] = (synth_usage.get(k, 0)
                                              + (usage2 or {}).get(k, 0))
                        grounding = await ora_grounding.run_post_response_check(
                            user_id=user["user_id"], session_id=session_id,
                            query=raw_text, reply=text, route="deep",
                            sources_fired=out.get("sources_fired") or [],
                            retrieved_context=out.get("retrieved_context") or "",
                            codebase_tree=cb_tree_only or None,
                            system_highlights=cb_highlights or None,
                        )
                        review_regen_cleared = not grounding["fabricated"]
                review_caveats = [f["quote"] for f in review["soft"]]
                if review["hard"] and not (review_regen_fired
                                            and review_regen_cleared):
                    review_caveats = ([f["quote"] for f in review["hard"]]
                                      + review_caveats)
            await ora_review.log_metrics(
                user_id=user["user_id"], session_id=session_id,
                route="deep", reason=review_reason, review=review,
                regen_fired=review_regen_fired,
                regen_cleared=review_regen_cleared)

        # Chunk the (now reviewed) response into synthetic deltas so
        # the UI paints progressively.
        if text:
            for i in range(0, len(text), _DELTA_CHUNK_LEN):
                yield {"type": "delta", "content": text[i:i+_DELTA_CHUNK_LEN]}
                await asyncio.sleep(0)

        # Iter 264 Fix C — persist the exact assembled prompt.
        msg_id = uuid.uuid4().hex
        full_prompt = ((out.get("system_prompt") or "") + "\n\n" +
                        (out.get("synth_prompt") or "")).strip()
        snap = await ora_snapshot.save_snapshot(
            message_id=msg_id, session_id=session_id,
            full_prompt=full_prompt,
            component_sizes={
                "core":        len(CORE_SAFETY_RULES),
                "aurem_ctx":   len(AUREM_CONTEXT),
                "highlights":  len(cb_highlights or ""),
                "tree":        len(cb_tree_only or ""),
                "house_rules": len(hr_text or ""),
                "retrieved":   len(out.get("retrieved_context") or ""),
            })

        await ora_session.append_message(
            session_id, user["user_id"],
            role="assistant", content=text,
            route="deep",
            model=out.get("synthesis_model") or cfg["model"],
            temperature=cfg["temperature"],
            input_tokens=synth_usage.get("input_tokens", 0),
            output_tokens=synth_usage.get("output_tokens", 0),
            cost_usd=cost,
            message_id=msg_id,
            ungrounded=grounding["fabricated"] or None,
            prompt_sha256=snap["sha256"],
            component_sizes=snap["component_sizes"],
            review=({"reason": review_reason,
                      "skipped": review.get("skipped"),
                      "flags": len(review.get("flags") or []),
                      "types": sorted({f["type"] for f in
                                        review.get("flags") or []}),
                      "regen_fired": review_regen_fired,
                      "regen_cleared": review_regen_cleared,
                      "caveats": review_caveats[:6]}
                     if review is not None else None),
        )

        # Iter 264 Fix A4 — user-facing warning ONLY for FABRICATED.
        if grounding["fabricated"]:
            yield {"type": "grounding_warning",
                    "ungrounded": grounding["fabricated"]}
        # Iter 268 — review caveats.
        if review_caveats:
            yield {"type": "review_caveat", "quotes": review_caveats[:6]}

        yield {"type": "final",
                "cost_usd":       cost,
                "tool_cost_usd":  round(float(out.get("tool_cost_usd") or 0.0), 6),
                "sources":        sources_tag,
                "sources_fired":  sources_fired,
                "downgraded":     bool(out.get("downgraded")),
                "errors":         out.get("errors") or [],
                "ungrounded":     grounding["fabricated"],
                "review_caveats": review_caveats[:6],
                "input_tokens":   synth_usage.get("input_tokens", 0),
                "output_tokens":  synth_usage.get("output_tokens", 0)}

    async def sse_events():
        async for evt in event_stream():
            yield {"event": evt["type"], "data": json.dumps(evt)}
            await asyncio.sleep(0)

    return EventSourceResponse(sse_events())
@router.get("/usage")
async def usage(authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    return {"ok": True, "budget": await cost_tracker.budget_status()}


@router.get("/config")
async def config(authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    return {
        "ok":            True,
        "routes":        route_config_snapshot(),
        "known_commands": list(KNOWN_COMMANDS),
        "burst_per_min": _BURST_PER_MIN,
    }


# ── House Rules endpoints (Iter 212m-239) ──────────────────────────
class HouseRulesBody(BaseModel):
    rules_text: str = Field(..., max_length=4000)


@router.get("/house-rules")
async def get_house_rules(authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    current = await ora_house_rules.get_current(user["user_id"])
    return {
        "ok":            True,
        "current":       current,
        "effective_text": await ora_house_rules.get_effective_text(user["user_id"]),
        "default_text":  DEFAULT_HOUSE_RULES,
        "max_len":       ora_house_rules.MAX_LEN,
    }


@router.put("/house-rules")
async def put_house_rules(body: HouseRulesBody,
                           authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    try:
        out = await ora_house_rules.update(user["user_id"], body.rules_text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return out


@router.get("/house-rules/history")
async def get_house_rules_history(authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    hist = await ora_house_rules.list_history(user["user_id"])
    return {"ok": True, "history": hist}


@router.post("/house-rules/restore/{version}")
async def restore_house_rules(version: int,
                                authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    try:
        out = await ora_house_rules.restore(user["user_id"], version)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return out


@router.post("/house-rules/reset")
async def reset_house_rules(authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    return await ora_house_rules.reset_to_default(user["user_id"])


# ── PIN login for the public /ora quick-access route (Iter 212m-241) ──
# The route auremcto.com/ora is deliberately unauthenticated at the
# HTML layer so the founder can bookmark it on any device and reach
# ORA in one tap. Security lives in this endpoint:
#   1. Rate-limited by IP — 5 attempts / hour, then hard 429
#   2. PIN compared to `ORA_QUICK_PIN` env (constant-time hmac.compare_digest)
#   3. On success, a real admin JWT is minted (7-day expiry, same as
#      login flow) bound to the founder account resolved from Mongo.
#   4. If no founder row is found (fresh install) we refuse — never
#      auto-privilege escalate.
class PinLoginBody(BaseModel):
    pin: str = Field(..., min_length=1, max_length=16)


@router.post("/pin-login")
async def pin_login(body: PinLoginBody,
                     request: Request):
    ip = client_ip_from_request(request)
    # Coarse hourly counter over `ora_chat_pin_attempts` — one aggregate.
    from cto_services.db import get_db
    import hmac, time as _time
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database unavailable")
    cutoff = _time.time() - 3600
    n_fail = await db.ora_chat_pin_attempts.count_documents({
        "ip": ip, "ok": False, "ts": {"$gte": cutoff},
    })
    if n_fail >= 5:
        raise HTTPException(429, {
            "error":   "too_many_attempts",
            "message": "Too many wrong PIN attempts. Try again in an hour.",
        })

    expected = os.getenv("ORA_QUICK_PIN", "").strip()
    if not expected:
        raise HTTPException(503, "PIN login not configured")
    ok = hmac.compare_digest(body.pin.strip(), expected)

    await db.ora_chat_pin_attempts.insert_one({
        "ip": ip, "ok": ok, "ts": _time.time(),
    })
    if not ok:
        remaining = max(0, 5 - (n_fail + 1))
        raise HTTPException(401, {
            "error":              "invalid_pin",
            "attempts_remaining": remaining,
        })

    # Resolve the founder → mint a real admin JWT tied to that identity.
    # Never falls back to a random admin (privilege-escalation risk).
    #
    # Iter 212m-248 — Production PIN was failing 503 because it only
    # trusted the `is_founder=True` DB flag. That flag isn't reliably
    # backfilled on prod Mongo; the authoritative signal is
    # `FOUNDER_EMAILS` (env, with a hardcoded fallback for the company
    # founder in services/usage.py::founder_emails()). So we now:
    #   1. Look up any dev_users row whose email is in the trusted
    #      founder set.
    #   2. Fall back to the legacy `is_founder=True` flag.
    #   3. If a founder row is found but lacks the flag, backfill it
    #      idempotently so downstream code stays consistent.
    from services.usage import founder_emails as _founder_emails_set
    trusted = list(_founder_emails_set())
    founder = None
    if trusted:
        founder = await db.dev_users.find_one(
            {"email": {"$in": trusted}},
            {"user_id": 1, "email": 1, "is_admin": 1, "is_founder": 1, "_id": 0},
        )
    if not founder:
        founder = await db.dev_users.find_one(
            {"is_founder": True},
            {"user_id": 1, "email": 1, "is_admin": 1, "is_founder": 1, "_id": 0},
        )
    if not founder:
        # Fresh install / seed missing — refuse rather than issue a
        # token that could bind to whoever we pick.
        raise HTTPException(503, "Founder identity not configured")

    # Idempotent backfill: keep the `is_founder` DB flag in sync with
    # the env-declared founder identity. Safe because we only reach
    # here after a valid PIN + trusted-email lookup.
    if not founder.get("is_founder"):
        try:
            await db.dev_users.update_one(
                {"user_id": founder["user_id"]},
                {"$set": {"is_founder": True, "is_admin": True}},
            )
        except Exception as e:                                # noqa: BLE001
            logger.warning("founder flag backfill failed: %r", e)

    token = create_token(
        user_id=founder["user_id"],
        email=founder["email"],
        is_admin=True,
    )
    return {
        "ok":         True,
        "token":      token,
        "expires_in": 86400 * 7,
        "user":       {"email": founder["email"], "is_admin": True},
    }


# ── Iter 212m-255/256 · Hallucination self-improvement loop ────────
#
# The loop is human-in-the-loop by design:
#   1. Every ORA response is auto-checked for ungrounded specific
#      claims (services/ora_chat/grounding_check.py — fires in the
#      SSE `_stream_deep_research` handler above). Positives → Mongo
#      `ora_hallucination_log`.
#   2. Batch classifier reads unreviewed rows, asks DeepSeek V3 for
#      recurring patterns (>=3 cases). Candidates land in
#      `ora_hallucination_patterns` with `status: "pending"`.
#   3. Founder REVIEWS candidates via the endpoints below and
#      explicitly APPROVES / REJECTS. Only approved rules are appended
#      to house_rules. NO auto-application.


class ApprovePatternBody(BaseModel):
    slug: str
    new_rule_text: Optional[str] = None  # override the LLM-proposed text


class RejectPatternBody(BaseModel):
    slug: str
    reason: Optional[str] = None


@router.get("/hallucination-patterns")
async def hallucination_patterns_list(authorization: Optional[str] = Header(None)):
    """List pending candidate rules for founder review."""
    await require_admin(authorization)
    pending = await ora_halluc.list_pending_patterns()
    unreviewed = await ora_halluc.unreviewed_count()
    return {"ok": True, "pending": pending, "unreviewed_log_rows": unreviewed}


@router.post("/hallucination-patterns/classify-now")
async def hallucination_patterns_classify(force: bool = True,
                                            authorization: Optional[str] = Header(None)):
    """Manual trigger — run the classifier over current unreviewed log rows."""
    await require_admin(authorization)
    r = await ora_halluc.classify_batch(force=force)
    return r


@router.post("/hallucination-patterns/approve")
async def hallucination_patterns_approve(body: ApprovePatternBody,
                                           authorization: Optional[str] = Header(None)):
    """Founder-approved promotion of a candidate pattern into house rules."""
    user = await require_admin(authorization)
    r = await ora_halluc.approve_pattern(
        slug=body.slug,
        user_id=user["user_id"],
        admin_email=user["email"],
        new_rule_text=body.new_rule_text,
    )
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "approve_failed"))
    return r


@router.post("/hallucination-patterns/reject")
async def hallucination_patterns_reject(body: RejectPatternBody,
                                          authorization: Optional[str] = Header(None)):
    """Reject a candidate pattern (won't ever be promoted)."""
    user = await require_admin(authorization)
    r = await ora_halluc.reject_pattern(
        slug=body.slug,
        admin_email=user["email"],
        reason=body.reason,
    )
    if not r.get("ok"):
        raise HTTPException(404, "not_found")
    return r


# ── Iter 264 Fix D — grounding canary (manual trigger) ─────────────
@router.post("/canary/run-now")
async def canary_run_now(authorization: Optional[str] = Header(None)):
    """Fire the grounding canary in the background (same code the
    nightly cron runs — 5 LLM round-trips, too slow for the proxy).
    Poll GET /canary/runs for the report."""
    await require_admin(authorization)
    from services.ora_chat import canary as ora_canary
    asyncio.create_task(ora_canary.run_canary(triggered_by="manual"))
    return {"ok": True, "started": True,
            "note": "Report will appear in GET /canary/runs (newest first)."}


@router.get("/canary/runs")
async def canary_runs(limit: int = 10,
                       authorization: Optional[str] = Header(None)):
    """Recent canary run reports (newest first)."""
    await require_admin(authorization)
    from cto_services.db import get_db
    db = get_db()
    if db is None:
        raise HTTPException(503, "Database unavailable")
    cursor = db.ora_canary_runs.find({}, {"_id": 0}) \
        .sort("started_at", -1).limit(max(1, min(limit, 50)))
    return {"ok": True, "runs": [row async for row in cursor]}



# ── Phase 2 · srcdoc-sandbox preview (Iter 212m-264 · Feb 2026) ─────
# Security contract enforced by this endpoint:
#   1. 16MB hard cap on submitted code (reject 413 before scanning).
#   2. Vanguard regex scanner runs on every submission — any CRITICAL
#      finding blocks the preview render (frontend refuses to build
#      the srcdoc). HIGH findings are surfaced as warnings but do not
#      block.  Same policy the Loop pre-push gate uses.
#   3. Only whitelisted preview langs are ever considered — anything
#      outside {html, htm, jsx, tsx, js, javascript} short-circuits
#      with `renderable=false` so the frontend renders code-only.
#   4. Endpoint is admin-only (require_admin gate) — no anonymous
#      scanning, so this can't be turned into a public scan-oracle.
_PREVIEW_MAX_BYTES = 16 * 1024 * 1024   # 16 MB
_PREVIEW_RENDERABLE = {"html", "htm", "jsx", "tsx", "js", "javascript"}


class PreviewScanBody(BaseModel):
    code: str = Field(..., min_length=0, max_length=_PREVIEW_MAX_BYTES + 1)
    lang: str = Field(..., min_length=1, max_length=32)


@router.post("/preview-scan")
async def preview_scan(body: PreviewScanBody,
                       request: Request,
                       authorization: Optional[str] = Header(None)):
    """Vanguard-gate the srcdoc preview render path.

    Contract: returns `{ ok, renderable, safe, blockers, warnings }`.
      - `renderable`: bool — lang is in the whitelist.
      - `safe`:       bool — no CRITICAL findings.  When false, the
                       frontend MUST refuse to build the srcdoc.
      - `blockers`:   CRITICAL findings (list[dict], severity/name/line/snippet).
      - `warnings`:   HIGH / MEDIUM findings — surfaced but non-blocking.
    """
    user = await require_admin(authorization)
    # Iter 212m-268 · Feb 2026 — rate-limit extension (founder-flagged
    # gap after Phase 2-5).  30 scans/min per user+IP — enough for a
    # rapid streaming preview refresh loop but blocks scan-oracle abuse.
    ip = client_ip_from_request(request)
    if not await check_rate_limit_async(f"ora_chat:preview-scan:{user['user_id']}:{ip}", 30):
        raise HTTPException(429, "Rate limit — slow down for a minute.")
    lang = (body.lang or "").strip().lower()
    code = body.code or ""

    # 16MB cap — reject before we spend regex time.
    if len(code.encode("utf-8", errors="ignore")) > _PREVIEW_MAX_BYTES:
        raise HTTPException(413, "code_too_large")

    if lang not in _PREVIEW_RENDERABLE:
        return {"ok": True, "renderable": False, "safe": False,
                "blockers": [], "warnings": [],
                "reason": "lang_not_renderable"}

    # Pick a filepath extension the scanner recognises so its
    # code-only rules (innerHTML, dangerouslySetInnerHTML) actually fire.
    _ext_map = {"html": ".html", "htm": ".html",
                "jsx": ".jsx", "tsx": ".tsx",
                "js": ".js", "javascript": ".js"}
    fake_path = f"ora_preview{_ext_map[lang]}"

    from services.vanguard_scanner import scan_text
    findings = scan_text(code, filepath=fake_path, include_dangerous=True)

    blockers = [f for f in findings if f.get("severity") == "CRITICAL"]
    warnings = [f for f in findings if f.get("severity") in ("HIGH", "MEDIUM")]

    return {
        "ok": True,
        "renderable": True,
        "safe": len(blockers) == 0,
        "blockers": blockers,
        "warnings": warnings,
    }



# ── Phase 3 · Two-layer intent classifier (Iter 212m-265 · Feb 2026) ──
# Public endpoint for verifying / consuming the intent verdict outside
# the streaming path.  Same contract the /message SSE emits, one JSON
# response.  Admin-only.
class IntentClassifyBody(BaseModel):
    text: str = Field(..., min_length=0, max_length=8000)


@router.post("/intent-classify")
async def intent_classify(body: IntentClassifyBody,
                          request: Request,
                          authorization: Optional[str] = Header(None)):
    """Two-layer intent classify (regex pre-filter → LLM fallback).

    Returns `{intent, source, matches, meta}`. `intent` is one of
    `PREVIEW_ONLY`, `CODE_CHANGE`, or `UNKNOWN`.  See
    services/ora_chat/intent_router.py for the full contract.
    """
    user = await require_admin(authorization)
    # Iter 212m-268 — 60 classifies/min per user+IP.  Higher ceiling
    # than /preview-scan because a legit UI-side "type-to-classify" flow
    # can rack these up fast.
    ip = client_ip_from_request(request)
    if not await check_rate_limit_async(f"ora_chat:intent:{user['user_id']}:{ip}", 60):
        raise HTTPException(429, "Rate limit — slow down for a minute.")
    verdict = await ora_intent.classify_intent(
        body.text or "", one_shot_fn=one_shot,
    )
    return {"ok": True, **verdict}


# ── Phase 4 · Attach + Vision (Iter 212m-266 · Feb 2026) ────────────
# Tier-gated file upload for the /ora chat.  Wraps the existing
# /upload/convert vision + MarkItDown machinery but adds:
#   · 10 MB hard cap (tighter than the generic 25 MB one — protects
#     the chat context window even after markdown compression).
#   · Pro / Team / Founder tier gate — free tier gets 402 with a
#     structured upgrade payload.
#   · Return shape matches /upload/convert so the frontend can share
#     the attachment pill component.
_ORA_UPLOAD_MAX_BYTES = 10 * 1024 * 1024   # 10 MB per Phase 4 brief
_ORA_UPLOAD_ALLOWED_TIERS = {"pro", "team", "founder"}
# Iter 212m-266b · Feb 2026 — founder tightened the Phase 4 whitelist
# to exactly four types (PNG, JPEG, WEBP, PDF).  Everything else the
# generic /upload/convert accepts (docx, xlsx, txt, csv, html, gif,
# bmp, …) must be REFUSED here so ORA-chat context stays predictable
# and cheap.  Both extension AND MIME must match one of the four —
# a `.jpg` file whose MIME is `text/html` is refused as a mismatch.
_ORA_UPLOAD_ALLOWED_MIMES = {
    "image/png", "image/jpeg", "image/jpg", "image/webp", "application/pdf",
}
_ORA_UPLOAD_ALLOWED_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".pdf",
}


@router.post("/upload")
async def ora_upload(
    file: UploadFile = File(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
):
    """Upload + convert-to-markdown for the /ora chat composer.

    Contract:
      · Multipart file (10 MB max).
      · Auth: admin JWT (same as the rest of /ora-chat).
      · Tier gate: Pro / Team / Founder only.  Free / Starter get 402
        with a JSON body the frontend renders as an upgrade nudge.
      · Response: `{ok, kind: "image"|"doc", filename, content_type,
                    original_size, markdown, md_size, truncated}`
        — mirrors /upload/convert exactly so the frontend attachment
        pill uses one code path.
    """
    user = await require_admin(authorization)
    # Iter 212m-268 — 10 uploads/min per user+IP.  Uploads run through
    # vision LLM (images) or MarkItDown (docs); both are heavy, so
    # burst caps here directly protect LLM budget + CPU.
    if request is not None:
        ip = client_ip_from_request(request)
        if not await check_rate_limit_async(f"ora_chat:upload:{user['user_id']}:{ip}", 10):
            raise HTTPException(429, "Upload rate limit — slow down for a minute.")
    tier = (user.get("tier") or "").strip().lower()
    is_founder = bool(user.get("is_founder") or user.get("is_admin")
                       or tier == "founder")
    if not is_founder and tier not in _ORA_UPLOAD_ALLOWED_TIERS:
        # 402 (structured) — same shape the /message endpoint uses for
        # budget stops so the frontend can render both with one branch.
        raise HTTPException(402, {
            "error":   "tier_locked",
            "feature": "file_upload",
            "tier":    tier or "free",
            "message": "File attachments are a Pro / Team feature. "
                        "Upgrade to send images and documents to ORA.",
            "upgrade_url": "/pricing",
        })

    raw = await file.read()
    size = len(raw or b"")
    if size == 0:
        raise HTTPException(400, "Empty upload")
    if size > _ORA_UPLOAD_MAX_BYTES:
        raise HTTPException(413, {
            "error":   "file_too_large",
            "size":    size,
            "max_mb":  _ORA_UPLOAD_MAX_BYTES // (1024 * 1024),
            "message": f"File too large ({size // (1024 * 1024)}MB). "
                        f"Max is {_ORA_UPLOAD_MAX_BYTES // (1024 * 1024)}MB.",
        })

    # ── Phase 4 whitelist gate (Iter 212m-266b) ────────────────────
    # Refuse anything outside {PNG, JPEG, WEBP, PDF}.  Both extension
    # AND declared MIME must sit in their respective allow-lists —
    # ANY mismatch is rejected with a structured 415 so the frontend
    # can render a "not a supported file type" toast without parsing
    # a stringified detail body.
    from pathlib import Path as _P0
    _ext  = _P0(file.filename or "").suffix.lower()
    _mime = (file.content_type or "").lower()
    if _ext not in _ORA_UPLOAD_ALLOWED_EXTS or _mime not in _ORA_UPLOAD_ALLOWED_MIMES:
        raise HTTPException(415, {
            "error":    "file_type_not_allowed",
            "ext":      _ext,
            "mime":     _mime,
            "allowed":  ["png", "jpg", "webp", "pdf"],
            "message":  "Only PNG, JPG, WEBP, and PDF files are supported.",
        })

    # Delegate to the shared conversion helpers so we don't duplicate
    # the vision + MarkItDown paths.
    from routers.upload import (
        _describe_image_via_vision, IMAGE_EXTS, IMAGE_MIMES, MAX_MD_CHARS,
    )
    from pathlib import Path as _P
    import tempfile as _tf

    suffix = _P(file.filename or "").suffix.lower() or ""
    ctype  = (file.content_type or "").lower()

    # ── Image branch — vision LLM (Gemini 2.5 Flash-Lite → GPT-4o-mini) ──
    if suffix in IMAGE_EXTS or ctype in IMAGE_MIMES:
        description = await _describe_image_via_vision(
            raw, ctype, file.filename or "image",
        )
        if not description:
            description = (
                "_(The user attached an image but vision OCR is "
                "unavailable right now. Ask them to paste any visible "
                "text or describe what they see in the image.)_"
            )
        text = description.strip()
        truncated = False
        if len(text) > MAX_MD_CHARS:
            text = text[:MAX_MD_CHARS] + "\n\n... [truncated by server cap]"
            truncated = True
        return {
            "ok":            True,
            "kind":          "image",
            "filename":      file.filename or "image",
            "content_type":  ctype,
            "original_size": size,
            "md_size":       len(text),
            "truncated":     truncated,
            "markdown":      text,
        }

    # ── Document branch — MarkItDown ───────────────────────────────
    with _tf.NamedTemporaryFile(suffix=suffix, delete=True) as tf:
        tf.write(raw)
        tf.flush()
        try:
            from markitdown import MarkItDown
            md_client = MarkItDown()
            result = md_client.convert(tf.name)
        except ImportError:
            raise HTTPException(500, "MarkItDown library missing on server")
        except Exception as e:
            raise HTTPException(415, f"Couldn't convert this file: {e}")

    text = (getattr(result, "text_content", None) or "").strip()
    if not text:
        text = (
            f"_(Attached `{file.filename}` — no extractable text. "
            "Ask the user what they'd like ORA to do with it.)_"
        )
    truncated = False
    if len(text) > MAX_MD_CHARS:
        text = text[:MAX_MD_CHARS] + "\n\n... [truncated by server cap]"
        truncated = True
    return {
        "ok":            True,
        "kind":          "doc",
        "filename":      file.filename or "document",
        "content_type":  ctype,
        "original_size": size,
        "md_size":       len(text),
        "truncated":     truncated,
        "markdown":      text,
    }


# ── Phase 5 · Image Generation (Iter 212m-267 · Feb 2026) ───────────
# Founder-tier ONLY, gpt-image-1 · low quality · 1024².  Gate stack:
#   1. `require_admin` (JWT).
#   2. Founder-tier explicit check (Pro/Team locked OUT until unit
#      economics justify expansion — founder brief 2026-02-08).
#   3. Global $3/day USD cap (services/ora_chat/image_gen.py).
#   4. Per-user 10 images/month cap.
# On upstream failure, both counters are refunded so the founder's
# quota isn't burned by a transient OpenAI hiccup.
from services.ora_chat import image_gen as ora_image_gen


class ImageGenBody(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=1000)


@router.post("/image-generate")
async def image_generate(body: ImageGenBody,
                          request: Request,
                          authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    # Iter 212m-268 — TIGHT rate limit on the endpoint that spends real
    # OpenAI dollars.  6 requests/min per user+IP is enough for a
    # normal human retry loop while making a scripted drain-attack
    # obvious.  This is a belt on top of the $3/day + 10/mo gates.
    ip = client_ip_from_request(request)
    if not await check_rate_limit_async(f"ora_chat:img-gen:{user['user_id']}:{ip}", 6):
        raise HTTPException(429, "Image generation rate limit — try again in a minute.")
    tier = (user.get("tier") or "").strip().lower()
    is_founder = bool(user.get("is_founder") or user.get("is_admin")
                       or tier == "founder")
    if not is_founder:
        # Explicit founder gate (per 2026-02-08 scope narrow). Pro /
        # Team refused with a structured 402 so the eventual expansion
        # only needs to flip this gate, not restructure the payload.
        raise HTTPException(402, {
            "error":   "tier_locked",
            "feature": "image_generation",
            "tier":    tier or "free",
            "message": ("Image generation is currently Founder-only "
                         "during the internal-test phase."),
        })

    from cto_services.db import get_db
    db = get_db()
    user_id = user["user_id"]

    # Reserve capacity BEFORE the OpenAI call — burst-safe.
    try:
        reservation = await ora_image_gen.check_and_reserve(db, user_id)
    except ora_image_gen.ImageGenError as e:
        raise HTTPException(429, {
            "error":   e.kind,
            "message": e.message,
            **e.extra,
        })

    # Actual generation.  On any failure, refund the reservation so a
    # transient upstream 500 doesn't burn the daily $3 budget.
    try:
        result = await ora_image_gen.generate(body.prompt)
    except ora_image_gen.ImageGenError as e:
        await ora_image_gen.refund_reservation(db, user_id)
        code = 402 if e.kind == "missing_key" else 502
        raise HTTPException(code, {"error": e.kind, "message": e.message})
    except Exception as e:  # noqa: BLE001
        await ora_image_gen.refund_reservation(db, user_id)
        raise HTTPException(502, {"error": "generation_failed",
                                     "message": str(e)[:200]})

    # Success — record the event for auditing / cost analysis.
    await db["ora_image_events"].insert_one({
        "user_id":  user_id,
        "prompt":   result["prompt"],
        "model":    result["model"],
        "cost_usd": result["cost_usd"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    daily = await ora_image_gen.daily_status(db)
    month = await ora_image_gen.user_month_status(db, user_id)
    return {
        "ok":            True,
        "image_base64":  result["image_base64"],
        "mime":          result["mime"],
        "cost_usd":      result["cost_usd"],
        "model":         result["model"],
        "prompt":        result["prompt"],
        "daily_status":  daily,
        "user_month_status": month,
    }


@router.get("/image-status")
async def image_status(request: Request,
                       authorization: Optional[str] = Header(None)):
    """Non-generating status peek so the frontend can badge remaining
    quota / daily spend without spending an image."""
    user = await require_admin(authorization)
    # Iter 212m-268 — cheap read-only endpoint, generous ceiling.
    ip = client_ip_from_request(request)
    if not await check_rate_limit_async(f"ora_chat:img-status:{user['user_id']}:{ip}", 60):
        raise HTTPException(429, "Rate limit — slow down for a minute.")
    from cto_services.db import get_db
    db = get_db()
    return {
        "ok":                True,
        "daily_status":      await ora_image_gen.daily_status(db),
        "user_month_status": await ora_image_gen.user_month_status(
            db, user["user_id"]),
        "per_image_usd":     ora_image_gen.GPT_IMAGE_1_LOW_USD_PER_IMAGE,
        "model":             ora_image_gen.ORA_IMAGE_MODEL,
        "quality":           ora_image_gen.ORA_IMAGE_QUALITY,
    }

