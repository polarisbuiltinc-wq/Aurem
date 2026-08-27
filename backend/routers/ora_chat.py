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
import time
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
from services.ora_chat import codebase_index as ora_codebase
from services.ora_chat import grounding_check as ora_grounding
from services.ora_chat import hallucination_classifier as ora_halluc
from services.ora_chat.router import resolve, route_config_snapshot
from services.ora_chat.providers import one_shot
from services.ora_chat.safety import (
    assemble_system_prompt, KNOWN_COMMANDS, parse_slash_command,
    DEFAULT_HOUSE_RULES,
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


# ── Streaming message endpoint (Admin ORA Chat rebuild — 2026-08-27) ──
# P1: legacy generic-advice pipeline (intent classify → route → deep
# research / regular chat → adversarial review → grounding) removed.
# Replaced with the ora_chat_v2 engine: state-grounded, catalog-only
# actions, bounded tool loop, rate/token capped. Admin guard, session
# persistence (ora_chat_sessions/ora_session.*), and sidebar/bell entry
# are unchanged — old messages in a session remain fully readable.
class MessageBody(BaseModel):
    session_id: str
    content: str = Field(..., min_length=1, max_length=20000)
    think_mode: bool = False
    advise_only: bool = False
    page_inspection: Optional[dict] = None


@router.post("/message")
async def send_message(body: MessageBody,
                        request: Request,
                        authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)

    # Defensive burst backstop (kept from the legacy handler) — the
    # real beta cap (ORA_CHAT_RATE_LIMIT_PER_HOUR) lives in the v2
    # engine and returns a proper 429-style SSE `error` event instead
    # of silently dropping the turn.
    ip = client_ip_from_request(request)
    if not await check_rate_limit_async(f"ora_chat:min:{user['user_id']}:{ip}", _BURST_PER_MIN):
        raise HTTPException(429, "Burst limit — slow down for a minute")

    sess = await ora_session.get_session(body.session_id, user["user_id"])
    if not sess:
        raise HTTPException(404, "Session not found")

    await ora_session.append_message(
        body.session_id, user["user_id"], role="user", content=body.content,
    )
    sess = await ora_session.get_session(body.session_id, user["user_id"])

    from cto_services.db import get_db as _get_db_v2
    from services.ora_chat_v2 import llm_client as _v2_llm
    from services.ora_chat_v2.engine import run_turn as _v2_run_turn

    async def event_stream():
        db = _get_db_v2()
        final_evt: dict = {}
        async for evt in _v2_run_turn(
                db, admin_id=user["user_id"], session=sess or {},
                user_message=body.content, think_mode=body.think_mode,
                advise_only=body.advise_only,
                page_inspection=body.page_inspection):
            if evt.get("type") == "final":
                final_evt = evt
            yield evt

        if final_evt:
            await ora_session.append_message(
                body.session_id, user["user_id"],
                role="assistant", content=final_evt.get("content", ""),
                model=_v2_llm.model_name(),
                input_tokens=final_evt.get("tokens_in", 0),
                output_tokens=final_evt.get("tokens_out", 0),
                message_id=uuid.uuid4().hex,
            )

    async def sse_events():
        async for evt in event_stream():
            yield {"event": evt["type"], "data": json.dumps(evt)}

    return EventSourceResponse(sse_events())


# ── Action catalog — propose/approve/reject (P4) ────────────────────
class ApproveActionBody(BaseModel):
    proposal_id: str


class RejectActionBody(BaseModel):
    proposal_id: str


@router.post("/action/approve")
async def approve_action(body: ApproveActionBody,
                          authorization: Optional[str] = Header(None)):
    # Re-checks the founder's bearer token on every approval — the
    # only "signed" gate this single-admin system has; there's no
    # separate step-up auth mechanism to layer on top of it.
    user = await require_admin(authorization)
    from cto_services.db import get_db as _get_db_v2
    from services.ora_chat_v2 import audit as _v2_audit, catalog as _v2_catalog
    db = _get_db_v2()

    proposal = await _v2_audit.get_proposal(db, body.proposal_id)
    if not proposal or proposal.get("admin_id") != user["user_id"]:
        raise HTTPException(404, "proposal not found")
    if proposal.get("event_type") != "proposed":
        return {"ok": False, "error": f"already_{proposal.get('event_type')}"}

    await _v2_audit.log_event(
        db, admin_id=user["user_id"], action_id=proposal["action_id"],
        params=proposal["params"], proposed_by=proposal["proposed_by"],
        event_type="approved", proposal_id=body.proposal_id,
        approved_ts=time.time())

    result = await _v2_catalog.execute_action(
        db, proposal["action_id"], proposal["params"])
    await _v2_audit.log_event(
        db, admin_id=user["user_id"], action_id=proposal["action_id"],
        params=proposal["params"], proposed_by=proposal["proposed_by"],
        event_type="executed" if result.get("ok") else "failed",
        proposal_id=body.proposal_id,
        result=result if result.get("ok") else None,
        error=None if result.get("ok") else str(result.get("error")))
    return {"ok": result.get("ok"), "result": result}


@router.post("/action/reject")
async def reject_action(body: RejectActionBody,
                         authorization: Optional[str] = Header(None)):
    user = await require_admin(authorization)
    from cto_services.db import get_db as _get_db_v2
    from services.ora_chat_v2 import audit as _v2_audit
    db = _get_db_v2()

    proposal = await _v2_audit.get_proposal(db, body.proposal_id)
    if not proposal or proposal.get("admin_id") != user["user_id"]:
        raise HTTPException(404, "proposal not found")
    if proposal.get("event_type") != "proposed":
        return {"ok": False, "error": f"already_{proposal.get('event_type')}"}

    await _v2_audit.log_event(
        db, admin_id=user["user_id"], action_id=proposal["action_id"],
        params=proposal["params"], proposed_by=proposal["proposed_by"],
        event_type="rejected", proposal_id=body.proposal_id)
    return {"ok": True}


@router.get("/actions/recent")
async def list_recent_actions(authorization: Optional[str] = Header(None)):
    await require_admin(authorization)
    from cto_services.db import get_db as _get_db_v2
    from services.ora_chat_v2 import audit as _v2_audit
    db = _get_db_v2()
    rows = await _v2_audit.recent_actions(db, limit=20)
    return {"ok": True, "actions": rows}


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
#      claims (services/ora_chat/grounding_check.py — fired from the
#      legacy /message pipeline; retained for the /slash + preview-scan
#      paths below). Positives → Mongo `ora_hallucination_log`.
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
    # Iter 386 · Session 2.7 · Fix F — credential redaction on every
    # extraction path. Vision LLM can OCR a screenshot that happens to
    # show `test_credentials.md` on a side monitor; MarkItDown can
    # extract inline secrets from a PDF onboarding doc. Both flows
    # now pass through `upload_redactor.redact` BEFORE the text is
    # returned / persisted / echoed to the LLM.
    from services.ora_chat.upload_redactor import redact as _redact_creds

    def _apply_redaction(raw_text: str, source: str) -> tuple[str, dict]:
        """Wrap redact() with observability. `source` = "vision"|"doc" —
        included in the Sentry breadcrumb so on-call can tell WHICH
        upload path was smuggling credentials."""
        redacted, hits = _redact_creds(raw_text or "")
        if hits:
            try:
                import sentry_sdk
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag("event", "upload_credential_redacted")
                    scope.set_tag("source", source)
                    scope.set_tag("user_id", user.get("user_id") or "?")
                    scope.set_context("redaction_hits", hits)
                    sentry_sdk.capture_message(
                        "Credentials redacted from user upload — "
                        f"source={source} hits={hits}",
                        level="warning",
                    )
            except Exception:
                pass
            logger.warning(
                "upload_redactor: credentials scrubbed from %s upload "
                "user=%s hits=%s", source, user.get("user_id"), hits)
        return redacted, hits

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
        # Redact BEFORE truncation — else we could truncate a partial
        # secret and still leak it.
        description, _hits = _apply_redaction(description, "vision")
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
    # Same redaction pass on the doc branch.
    text, _hits = _apply_redaction(text, "doc")
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

