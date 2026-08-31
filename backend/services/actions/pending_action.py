"""
services/actions/pending_action.py — 2026-09-05 Commit-Boundary class fix.

Founder's explicit architectural ruling (after 4 rounds of prose/guard
patches survived and re-broke on real non-technical repro): a
CONFIRMATION IS A DETERMINISTIC SERVER-SIDE STATE TRANSITION, NOT A
MODEL TURN. A confirmation resolving a real pending action must never
reach the LLM for re-generation.

This module is the single source of truth for the PendingAction
lifecycle:

    PROPOSED -> validate concrete payload -> AWAITING_CONFIRM
    AWAITING_CONFIRM -> confirm -> EXECUTING
    EXECUTING -> apply -> EXECUTED -> read-back verify -> VERIFIED
                                                        -> APPLIED_FAILED
    (any non-terminal state) -> cancel / TTL expiry -> CANCELLED

Stored in its OWN dedicated Mongo collection (`pending_actions`) —
deliberately NOT piggy-backed onto `chat_sessions` (the prior
2026-09-04 `confirm_execution.py` approach) so the state machine has
its own atomic, race-safe transitions independent of the chat
transcript.

Invariants enforced here (CBR-1..8 from the founder's spec):
  CBR-1 — prose alone can never create an AWAITING_CONFIRM action;
          only a payload that passes `validate_payload()` against
          real, live data gets there. An unvalidated proposal is
          persisted as PROPOSED then immediately CANCELLED
          (traceable, but never actionable).
  CBR-2/3 — `resolve_confirm()` is deterministic Python, not an LLM
          call. A real confirm ALWAYS executes the stored payload
          verbatim (literal substitution for `edit`, the same
          checkout call for `upgrade`) — never a re-proposal.
  CBR-4 — zero active pending action on confirm returns
          `NO_PENDING_ACTIONABLE_MESSAGE` (honest + actionable),
          never a dead "nothing pending".
  CBR-5/8 — `execute_action()` always performs a real read-back
          verification before a success ("Done") framing is used;
          a failed verification is APPLIED_FAILED, never silently
          shown as success.
  CBR-6 — `resolve_confirm()` is wrapped so it always returns a
          terminal dict for a real confirm/cancel intent, even if an
          executor raises.
  CBR-7 — more than one AWAITING_CONFIRM action in a session
          disambiguates (numbered list + numeric-reply selection)
          rather than silently picking one or stacking forever.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# ── status constants ─────────────────────────────────────────────────
STATUS_PROPOSED = "PROPOSED"
STATUS_AWAITING_CONFIRM = "AWAITING_CONFIRM"
STATUS_EXECUTING = "EXECUTING"
STATUS_EXECUTED = "EXECUTED"
STATUS_VERIFIED = "VERIFIED"
STATUS_APPLIED_FAILED = "APPLIED_FAILED"
STATUS_CANCELLED = "CANCELLED"

_TERMINAL_STATUSES = (STATUS_VERIFIED, STATUS_APPLIED_FAILED, STATUS_EXECUTED)

PENDING_ACTION_TTL_S = 20 * 60          # 20 min — matches prior confirm_execution.py
IDEMPOTENCY_ECHO_WINDOW_S = 5 * 60      # re-confirming a just-finished action echoes, never re-executes

NO_PENDING_ACTIONABLE_MESSAGE = (
    "I don't have a change waiting right now — tell me what you'd like "
    "changed and I'll set it up."
)

PROVIDER_EXECUTOR = "commit-boundary-executor"
PROVIDER_EXECUTING = "commit-boundary-executing"
PROVIDER_NO_PENDING = "commit-boundary-no-pending"
PROVIDER_DISAMBIGUATE = "commit-boundary-disambiguate"
PROVIDER_CANCELLED = "commit-boundary-cancelled"
PROVIDER_CANCEL_NOOP = "commit-boundary-cancel-noop"
PROVIDER_ERROR = "commit-boundary-error"


# ── deterministic pre-LLM intent classifier ─────────────────────────
# Broader than a strict "bare word" matcher: allows a short trailing
# continuation phrase ("yes please update it") — the exact founder
# repro phrase — while staying whole-message-anchored so a genuinely
# NEW request ("yes but also change the color") is never mistaken for
# a confirmation of an OLD action.
_CONFIRM_RE = re.compile(
    r"^\s*(?:yes[,]?\s*)?(?:please\s*)?"
    r"(?:yes|yeah|yep|yup|sure|ok|okay|approve|approved|confirm|confirmed|"
    r"go ahead|go for it|do it|ship it|ship that|proceed|sounds good|go)?"
    r"[\s,.!]*(?:please\s+)?"
    r"(?:update it|update that|update the change|apply it|apply that|"
    r"make the change|make that change|do that|do it|go ahead)?"
    r"\s*[.!]?\s*$",
    re.IGNORECASE,
)
_CONFIRM_WORD_RE = re.compile(
    r"\b(?:yes|yeah|yep|yup|sure|ok|okay|approve|approved|confirm|"
    r"confirmed|go ahead|go for it|do it|ship it|ship that|proceed|"
    r"sounds good|go)\b",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(
    r"^\s*(?:no[,]?\s*)?(?:please\s*)?"
    r"(?:no|nope|nah|cancel|stop|never\s*mind|nevermind|don'?t|"
    r"skip\s*it|not\s*now|actually\s*no)"
    r"[\s,.!]*(?:that|this|it)?\s*[.!]?\s*$",
    re.IGNORECASE,
)
_NUMBER_ONLY_RE = re.compile(r"^\s*(\d{1,2})\s*[.)]?\s*$")


def is_confirm_intent(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    if not _CONFIRM_WORD_RE.search(text):
        return False
    return bool(_CONFIRM_RE.match(text))


def is_cancel_intent(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    return bool(_CANCEL_RE.match(text))


def classify_confirm_intent(message: str) -> tuple[str, Optional[int]]:
    """Returns (intent, selected_index) where intent is one of
    "confirm" / "cancel" / "new_request". `selected_index` is a
    1-based int when the WHOLE message is a bare number (used to
    resolve a disambiguation reply), else None."""
    text = (message or "").strip()
    if not text:
        return "new_request", None
    num_m = _NUMBER_ONLY_RE.match(text)
    if num_m:
        return "confirm", int(num_m.group(1))
    if is_cancel_intent(text):
        return "cancel", None
    if is_confirm_intent(text):
        return "confirm", None
    return "new_request", None


# ── deterministic content-edit extraction ───────────────────────────
_FILE_PATH_RE = re.compile(r"`?([\w./-]+\.(?:jsx?|tsx?|html?|css|md|json))`?")
_FROM_TO_RE = re.compile(
    r"from\s+[\"'`]([^\"'`]{1,200})[\"'`]\s+to\s+[\"'`]([^\"'`]{1,200})[\"'`]",
    re.IGNORECASE,
)


def extract_deterministic_edit(proposal_text: str) -> Optional[dict]:
    """Best-effort extraction of a concrete {path, old_value,
    new_value} triple from a proposal's prose. Returns None when it
    can't find all three cleanly — the caller (`propose_from_turn`)
    then creates NO pending action at all (CBR-1: prose that can't be
    concretized never becomes actionable)."""
    if not proposal_text:
        return None
    path_m = _FILE_PATH_RE.search(proposal_text)
    if not path_m:
        return None
    from_to = _FROM_TO_RE.search(proposal_text)
    if not from_to:
        return None
    return {
        "path": path_m.group(1),
        "old_value": from_to.group(1),
        "new_value": from_to.group(2),
    }


# ── payload validation (the CBR-1 enforcement point) ────────────────
async def _validate_edit_payload(payload: dict, *, ctx: Optional[dict]):
    path = (payload or {}).get("path")
    old_value = (payload or {}).get("old_value")
    new_value = (payload or {}).get("new_value")
    if not path or old_value is None or new_value is None:
        return False, None, "incomplete_payload"
    if not ctx or not ctx.get("bin_ctx"):
        return False, None, "no_repo_context"
    from services.local_tools import read_repo_file
    read_ctx = {
        "user_id": ctx.get("user_id"), "project_id": ctx.get("project_id"),
        "bin_ctx": ctx.get("bin_ctx"),
    }
    try:
        res = await read_repo_file(read_ctx, {"path": path})
    except Exception as e:                                # noqa: BLE001
        return False, None, f"read_failed:{e}"
    if not res or not res.get("ok"):
        return False, None, "file_unreadable"
    content = res.get("content") or ""
    if content.count(old_value) != 1:
        return False, None, "old_value_not_unique"
    return True, {
        "path": path, "old_value": old_value, "new_value": new_value,
        "commit_message": (payload or {}).get("commit_message")
        or f"chore: update {path} (via chat approval)",
    }, None


async def validate_payload(type_: str, payload: dict, *, ctx: Optional[dict] = None):
    """Returns (ok, concretized_payload_or_None, reason_or_None)."""
    if type_ == "edit":
        return await _validate_edit_payload(payload, ctx=ctx)
    if type_ == "upgrade":
        plan = (payload or {}).get("plan")
        if plan not in ("starter", "pro", "team"):
            return False, None, "invalid_plan"
        return True, {"plan": plan}, None
    return False, None, "unsupported_type"


# ── persistence / lifecycle ─────────────────────────────────────────
async def propose_action(
    db, *, session_id: str, user_id: str, project_id: Optional[str],
    type_: str, raw_payload: dict, ctx: Optional[dict] = None,
    ttl_s: int = PENDING_ACTION_TTL_S,
) -> dict:
    """PROPOSED -> validate -> AWAITING_CONFIRM, or CANCELLED
    (validation_failed) if the payload can't be concretized against
    real, live data right now. Always persists a doc (traceable
    audit trail) but ONLY an AWAITING_CONFIRM doc is ever actionable
    — see `get_active_actions`."""
    now = time.time()
    doc = {
        "id": uuid.uuid4().hex, "session_id": session_id, "user_id": user_id,
        "project_id": project_id, "type": type_, "payload": raw_payload,
        "status": STATUS_PROPOSED, "confirmation_token": uuid.uuid4().hex,
        "idempotency_key": uuid.uuid4().hex, "created_at": now, "updated_at": now,
        "expires_at": now + ttl_s, "verification_result": None,
    }
    ok, validated_payload, reason = await validate_payload(type_, raw_payload, ctx=ctx)
    if ok:
        doc["status"] = STATUS_AWAITING_CONFIRM
        doc["payload"] = validated_payload
    else:
        doc["status"] = STATUS_CANCELLED
        doc["cancel_reason"] = f"validation_failed:{reason}"
    await db.pending_actions.insert_one(doc)
    return doc


async def get_active_actions(db, *, session_id: str, user_id: str) -> list[dict]:
    """AWAITING_CONFIRM actions for this session, auto-cancelling any
    that have crossed their TTL (CBR: TTL expiry cancels, never
    silently executes a stale action)."""
    if db is None or not session_id:
        return []
    now = time.time()
    docs = await db.pending_actions.find(
        {"session_id": session_id, "user_id": user_id, "status": STATUS_AWAITING_CONFIRM},
    ).to_list(length=50)
    active = []
    for d in docs:
        if now > float(d.get("expires_at") or 0):
            await db.pending_actions.update_one(
                {"id": d["id"], "status": STATUS_AWAITING_CONFIRM},
                {"$set": {"status": STATUS_CANCELLED, "cancel_reason": "ttl_expired",
                          "updated_at": now}},
            )
            continue
        active.append(d)
    return active


async def get_recent_terminal_action(
    db, *, session_id: str, user_id: str, window_s: int = IDEMPOTENCY_ECHO_WINDOW_S,
) -> Optional[dict]:
    if db is None or not session_id:
        return None
    now = time.time()
    return await db.pending_actions.find_one(
        {"session_id": session_id, "user_id": user_id,
         "status": {"$in": list(_TERMINAL_STATUSES)},
         "executed_at": {"$gte": now - window_s}},
        sort=[("executed_at", -1)],
    )


async def _cancel_actions(db, actions: list[dict], *, reason: str) -> None:
    if not actions:
        return
    ids = [a["id"] for a in actions]
    await db.pending_actions.update_many(
        {"id": {"$in": ids}},
        {"$set": {"status": STATUS_CANCELLED, "cancel_reason": reason, "updated_at": time.time()}},
    )


async def propose_from_turn(
    db, *, session_id: str, user_id: str, project_id: Optional[str],
    provider: str, assistant_reply: str, bin_ctx=None,
) -> None:
    """Called exactly once per real chat turn (persist-time). This is
    the ONLY place a PendingAction can be created — never from a raw
    model turn, always from this deterministic, server-side check
    against the FINAL assistant reply. See module docstring, CBR-1."""
    if db is None or not session_id:
        return
    try:
        if "```aurem-handoff" in (assistant_reply or ""):
            edit = extract_deterministic_edit(assistant_reply)
            if edit:
                await propose_action(
                    db, session_id=session_id, user_id=user_id, project_id=project_id,
                    type_="edit", raw_payload=edit,
                    ctx={"user_id": user_id, "project_id": project_id, "bin_ctx": bin_ctx},
                )
            # else: no clean (path, old, new) triple could be
            # extracted from the prose — CBR-1, no action created at
            # all. The multi-file/complex case stays button-only
            # (the existing, separately-tested task-queue pipeline).
        elif provider == "edit-tier-upgrade-offer":
            await propose_action(
                db, session_id=session_id, user_id=user_id, project_id=project_id,
                type_="upgrade", raw_payload={"plan": "pro"},
            )
        elif provider not in (PROVIDER_EXECUTOR, PROVIDER_EXECUTING):
            active = await get_active_actions(db, session_id=session_id, user_id=user_id)
            if active:
                await _cancel_actions(db, active, reason="new_unrelated_turn")
    except Exception as e:                                 # noqa: BLE001
        logger.warning("propose_from_turn failed: %r", e)


# ── result shaping ───────────────────────────────────────────────────
def _shape_result(content: str, provider: str, extra: Optional[dict] = None) -> dict:
    return {
        "ok": True,
        "content": content,
        "provider": provider,
        "iterations": 0,
        "tool_calls_run": 1 if provider == PROVIDER_EXECUTOR else 0,
        "meta": extra or {},
        "council": None,
        "task_type": None,
        "findings_saved_this_turn": [],
        "_skip_output_guards": True,
    }


def _result_from_doc(doc: dict) -> dict:
    extra = dict(doc.get("result_extra") or {})
    extra["idempotent_echo"] = True
    extra["verification_result"] = doc.get("verification_result")
    return _shape_result(doc.get("result_content") or "", PROVIDER_EXECUTOR, extra)


def _describe_action(action: dict) -> str:
    t = action.get("type")
    p = action.get("payload") or {}
    if t == "edit":
        return f"update `{p.get('path')}`: \"{p.get('old_value')}\" -> \"{p.get('new_value')}\""
    if t == "upgrade":
        return f"upgrade to the {str(p.get('plan', 'pro')).title()} plan"
    return f"a {t} action"


def _disambiguation_text(active: list[dict]) -> str:
    lines = [f"{i + 1}. {_describe_action(a)}" for i, a in enumerate(active)]
    return (
        "I have more than one change waiting for your approval — which one?\n"
        + "\n".join(lines)
        + "\nReply with the number."
    )


class _Outcome:
    def __init__(self, ok: bool, content: str,
                 verification_result: Optional[dict] = None, extra: Optional[dict] = None):
        self.ok = ok
        self.content = content
        self.verification_result = verification_result or {}
        self.extra = extra or {}


# ── executors ─────────────────────────────────────────────────────────
async def _execute_edit(action: dict, *, user: dict, project_id: Optional[str], bin_ctx) -> _Outcome:
    from services.local_tools import read_repo_file, write_repo_file

    p = action.get("payload") or {}
    ctx = {"user_id": user.get("user_id"), "project_id": project_id, "bin_ctx": bin_ctx}

    read_res = await read_repo_file(ctx, {"path": p.get("path")})
    if not read_res.get("ok"):
        return _Outcome(False, (
            f"I had the change ready but couldn't reload `{p.get('path')}` to "
            f"apply it: {read_res.get('error', 'unknown error')}. Ask me to try again."
        ))
    current = read_res.get("content") or ""
    if current.count(p.get("old_value")) != 1:
        return _Outcome(False, (
            "The file changed since I proposed this edit, so I can't safely "
            "auto-apply it anymore. Ask me again and I'll re-check the current content."
        ))
    new_content = current.replace(p["old_value"], p["new_value"], 1)
    write_res = await write_repo_file(ctx, {
        "path": p["path"], "content": new_content,
        "commit_message": p.get("commit_message") or f"chore: update {p['path']} (via chat approval)",
    })
    if not write_res.get("ok"):
        return _Outcome(False, (
            f"I had the change ready but the write failed: "
            f"{write_res.get('error', 'unknown error')}. Ask me to try again."
        ))

    # Read-back verification — CBR-5/8: never claim "Done" without
    # independently re-reading the file, not just trusting the write call.
    verify_res = await read_repo_file(ctx, {"path": p["path"]})
    verified = bool(verify_res.get("ok")) and p["new_value"] in (verify_res.get("content") or "")
    if not verified:
        return _Outcome(
            False,
            (
                f"I committed the change (commit {write_res.get('sha', '?')}) but "
                f"couldn't confirm it landed when I read the file back — please "
                f"check `{p['path']}` manually or ask me to look again."
            ),
            verification_result={"verified": False, "path": p["path"]},
            extra={"commit_sha": write_res.get("sha")},
        )
    return _Outcome(
        True,
        (
            f"Done — updated `{p['path']}` (changed \"{p['old_value']}\" to "
            f"\"{p['new_value']}\"). Commit: {write_res.get('html_url', write_res.get('sha', ''))}"
        ),
        verification_result={"verified": True, "path": p["path"]},
        extra={"commit_sha": write_res.get("sha"), "html_url": write_res.get("html_url")},
    )


async def _execute_upgrade(db, action: dict, *, user: dict) -> _Outcome:
    from routers.payments import create_checkout_session

    plan = (action.get("payload") or {}).get("plan") or "pro"
    try:
        checkout = await create_checkout_session(user, plan)
    except Exception as e:                                 # noqa: BLE001
        logger.warning("commit_boundary upgrade checkout failed: %r", e)
        return _Outcome(False, (
            f"I wasn't able to start the {plan} upgrade checkout just now — "
            f"please try again in a moment, or visit /settings#pricing to "
            f"upgrade directly."
        ))
    # Read-back verification — confirm the checkout session was really
    # persisted before telling the user checkout started.
    row = None
    try:
        row = await db.cto_payments.find_one({"session_id": checkout["session_id"]})
    except Exception as e:                                 # noqa: BLE001
        logger.debug("upgrade read-back check skipped: %r", e)
    if not row:
        return _Outcome(
            False,
            (
                "I started checkout but couldn't confirm it was recorded — "
                f"try this link if it still works: {checkout.get('checkout_url')}, "
                "otherwise visit /settings#pricing to upgrade directly."
            ),
            verification_result={"verified": False},
            extra={"checkout_url": checkout.get("checkout_url")},
        )
    return _Outcome(
        True,
        (
            f"Great — I've started your upgrade to the {plan.title()} plan. "
            f"Complete checkout here: {checkout['checkout_url']}"
        ),
        verification_result={"verified": True, "session_id": checkout["session_id"]},
        extra={"checkout_url": checkout["checkout_url"], "stripe_session_id": checkout["session_id"]},
    )


async def _atomic_transition(db, action_id: str, *, from_status: str, to_status: str) -> Optional[dict]:
    from pymongo import ReturnDocument
    return await db.pending_actions.find_one_and_update(
        {"id": action_id, "status": from_status},
        {"$set": {"status": to_status, "updated_at": time.time()}},
        return_document=ReturnDocument.AFTER,
    )


async def execute_action(db, action: dict, *, user: dict, project_id: Optional[str], bin_ctx) -> dict:
    """AWAITING_CONFIRM -> EXECUTING (atomic, race-safe) -> real
    side effect -> read-back verify -> VERIFIED / APPLIED_FAILED.
    Never re-enters the LLM. Idempotent: a second confirm racing the
    same action_id gets the SAME stored terminal result, never a
    second execution."""
    action_id = action["id"]
    claimed = await _atomic_transition(
        db, action_id, from_status=STATUS_AWAITING_CONFIRM, to_status=STATUS_EXECUTING,
    )
    if not claimed:
        latest = await db.pending_actions.find_one({"id": action_id})
        if latest and latest.get("status") in _TERMINAL_STATUSES:
            return _result_from_doc(latest)
        return _shape_result("I'm already applying that change — one moment.", PROVIDER_EXECUTING)

    try:
        t = claimed.get("type")
        if t == "edit":
            outcome = await _execute_edit(claimed, user=user, project_id=project_id, bin_ctx=bin_ctx)
        elif t == "upgrade":
            outcome = await _execute_upgrade(db, claimed, user=user)
        else:
            outcome = _Outcome(False, f"`{t}` actions aren't supported yet — please try a different request.")
    except Exception as e:                                 # noqa: BLE001
        logger.exception("commit_boundary execute_action failed: %r", e)
        outcome = _Outcome(False, "Something went wrong while applying this change — please ask me to try again.")

    final_status = STATUS_VERIFIED if outcome.ok else STATUS_APPLIED_FAILED
    now = time.time()
    await db.pending_actions.update_one(
        {"id": action_id},
        {"$set": {
            "status": final_status, "executed_at": now, "updated_at": now,
            "verification_result": outcome.verification_result,
            "result_content": outcome.content, "result_extra": outcome.extra,
        }},
    )
    return _shape_result(outcome.content, PROVIDER_EXECUTOR, outcome.extra)


# ── the single deterministic entry point ────────────────────────────
async def _resolve_confirm_inner(
    db, *, session_id: str, user_id: str, project_id: Optional[str],
    prompt: str, user: dict, bin_ctx,
) -> Optional[dict]:
    if db is None or not session_id:
        return None
    intent, selected_idx = classify_confirm_intent(prompt)
    if intent == "new_request":
        return None

    active = await get_active_actions(db, session_id=session_id, user_id=user_id)

    if intent == "cancel":
        if not active:
            return _shape_result(
                "There's nothing pending to cancel — what would you like to do?",
                PROVIDER_CANCEL_NOOP,
            )
        await _cancel_actions(db, active, reason="user_cancelled")
        return _shape_result("Cancelled — nothing was changed.", PROVIDER_CANCELLED)

    # intent == "confirm"
    if not active:
        recent = await get_recent_terminal_action(db, session_id=session_id, user_id=user_id)
        if recent:
            return _result_from_doc(recent)
        return _shape_result(NO_PENDING_ACTIONABLE_MESSAGE, PROVIDER_NO_PENDING)

    if len(active) > 1:
        if selected_idx and 1 <= selected_idx <= len(active):
            return await execute_action(
                db, active[selected_idx - 1], user=user, project_id=project_id, bin_ctx=bin_ctx,
            )
        return _shape_result(
            _disambiguation_text(active), PROVIDER_DISAMBIGUATE, {"pending_count": len(active)},
        )

    return await execute_action(db, active[0], user=user, project_id=project_id, bin_ctx=bin_ctx)


async def resolve_confirm(
    db, *, session_id: str, user_id: str, project_id: Optional[str],
    prompt: str, user: dict, bin_ctx,
) -> Optional[dict]:
    """THE shared deterministic entry point — call this from both
    /chat/send and /chat/stream BEFORE any tier/LLM routing. Always
    returns a terminal dict for a real confirm/cancel intent (CBR-6),
    even if an executor raises internally; returns None for a genuine
    new request so the caller falls through to normal LLM routing."""
    try:
        return await _resolve_confirm_inner(
            db, session_id=session_id, user_id=user_id, project_id=project_id,
            prompt=prompt, user=user, bin_ctx=bin_ctx,
        )
    except Exception as e:                                 # noqa: BLE001
        logger.exception("resolve_confirm failed unexpectedly: %r", e)
        intent, _ = classify_confirm_intent(prompt)
        if intent == "new_request":
            return None
        return _shape_result(
            "Something went wrong while checking on that — please try again in a moment.",
            PROVIDER_ERROR,
        )
