"""
services/confirm_execution.py — 2026-09-04 (confirm-execution round)

Holds a PENDING ACTION server-side across chat turns and, on a real
confirmation reply, EXECUTES it deterministically instead of asking
the orchestrator to regenerate a fresh (and possibly differently-
worded) proposal.

Founder's explicit call (2026-09-04, reversing the 2026-08-28
Iter 212m-26 "no auto-ship-via-chat-text" decision after a live 3x
repro showed the alternative — a second, reworded proposal with a
DIFFERENT CSS class name, then "yes please" → "nothing pending" — is
a worse, more confusing bug than the thing 212m-26 was guarding
against): "yes please" / "approve" / "go ahead" to a held proposal
now EXECUTES it for real — ships the code change or starts the
Stripe checkout — not just re-affirms it.

ONE shared entry point, `maybe_execute_pending`, is called from both
/chat/send and /chat/stream so there is a single place that
recognizes a confirmation intent, looks up the pending action, and
executes it — not N copies (the DRY half of this round's request).

Pending-action lifecycle:
  register  — `register_code_fence_pending` / `register_upgrade_pending`,
              called right after a turn's content is finalized
              (persist-time), whenever that turn produced a real
              `aurem-handoff` fence or a Root 4 upgrade offer.
  look up   — `get_pending_action` (with a soft TTL — a VERY old
              pending action from a long-dead session shouldn't
              suddenly execute if the user resumes it weeks later).
  execute   — `maybe_execute_pending`, dispatches by `type`:
                "code_fence" → `_execute_code_fence`
                "upgrade"    → `_execute_upgrade`
  clear     — always cleared after an execute attempt (success or
              failure) so a repeated "yes" can't double-execute.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

_PENDING_TTL_S = 20 * 60  # 20 minutes — stale pending actions don't fire

# 2026-09-04 — deliberately more permissive than
# response_confidence.is_confirmation_reply (which is used elsewhere
# for a stricter "bare confirmation, nothing else" contract). This
# one additionally allows a short trailing continuation phrase like
# "update it" / "do it" / "make the change" — the exact repro phrase
# was "yes please update it", which the strict matcher does NOT
# match (it requires the message to END right after the confirm
# word). Still whole-message-anchored so a genuinely NEW request
# ("yes but also change the color") is never mistaken for a
# confirmation of the OLD pending action.
_CONFIRM_EXECUTE_RE = re.compile(
    r"^\s*(?:yes[,]?\s*)?(?:please\s*)?"
    r"(?:yes|yeah|yep|yup|sure|ok|okay|approve|approved|confirm|confirmed|"
    r"go ahead|go for it|do it|ship it|ship that|proceed|sounds good|go)?"
    r"[\s,.!]*(?:please\s+)?"
    r"(?:update it|update that|update the change|apply it|apply that|"
    r"make the change|make that change|do that|do it|go ahead)?"
    r"\s*[.!]?\s*$",
    re.IGNORECASE,
)


def is_confirm_execute_intent(message: str) -> bool:
    """True iff the whole message is a confirmation (optionally with
    a short trailing continuation like 'update it') — see module
    docstring for why this is broader than
    response_confidence.is_confirmation_reply. A message with no
    confirmation word at all (e.g. "update it" alone, or "") must
    NOT match — the regex above allows every individual piece to be
    optional, so a bare emptiness/whitespace-only string is rejected
    explicitly, and the caller only ever passes real chat prompts.
    """
    text = (message or "").strip()
    if not text:
        return False
    if not re.search(
        r"\b(?:yes|yeah|yep|yup|sure|ok|okay|approve|approved|confirm|"
        r"confirmed|go ahead|go for it|do it|ship it|ship that|proceed|"
        r"sounds good|go)\b",
        text, re.IGNORECASE,
    ):
        return False
    return bool(_CONFIRM_EXECUTE_RE.match(text))


async def get_pending_action(db, session_id: str) -> Optional[dict]:
    if db is None or not session_id:
        return None
    sess = await db.chat_sessions.find_one(
        {"session_id": session_id}, {"pending_action": 1, "_id": 0},
    )
    action = (sess or {}).get("pending_action")
    if not action:
        return None
    if time.time() - float(action.get("created_at") or 0) > _PENDING_TTL_S:
        return None
    return action


async def clear_pending_action(db, session_id: str) -> None:
    if db is None or not session_id:
        return
    await db.chat_sessions.update_one(
        {"session_id": session_id}, {"$unset": {"pending_action": ""}},
    )


async def register_code_fence_pending(
    db, *, session_id: str, project_id: str, proposal_text: str, brief: str,
) -> None:
    if db is None or not session_id or not proposal_text:
        return
    await db.chat_sessions.update_one(
        {"session_id": session_id},
        {"$set": {"pending_action": {
            "type":          "code_fence",
            "created_at":    time.time(),
            "project_id":    project_id,
            "proposal_text": proposal_text[:4000],
            "brief":         (brief or "")[:1500],
        }}},
        upsert=True,
    )


async def register_upgrade_pending(
    db, *, session_id: str, plan: str = "pro",
) -> None:
    if db is None or not session_id:
        return
    await db.chat_sessions.update_one(
        {"session_id": session_id},
        {"$set": {"pending_action": {
            "type":       "upgrade",
            "created_at": time.time(),
            "plan":       plan,
        }}},
        upsert=True,
    )


# ── Deterministic content-edit extraction ───────────────────────────
# Business-content proposals ("I'll change the opening hours from
# '9am-5pm' to '9am-6pm' in `src/components/Hours.jsx`") usually state
# the concrete old/new value and file path in prose. When we can
# extract all three cleanly, execution is a literal, deterministic
# string substitution on the file's CURRENT real content — no LLM
# involved in the apply step at all, so the CSS class / surrounding
# markup is byte-for-byte preserved (the exact smoking-gun the live
# repro caught: a regenerated proposal had a DIFFERENT class name).
_FILE_PATH_RE = re.compile(r"`?([\w./-]+\.(?:jsx?|tsx?|html?|css|md|json))`?")
_FROM_TO_RE = re.compile(
    r"from\s+[\"'`]([^\"'`]{1,200})[\"'`]\s+to\s+[\"'`]([^\"'`]{1,200})[\"'`]",
    re.IGNORECASE,
)
_TO_ONLY_RE = re.compile(r"\bto\s+[\"'`]([^\"'`]{1,200})[\"'`]", re.IGNORECASE)


def extract_deterministic_edit(proposal_text: str) -> Optional[dict]:
    """Best-effort extraction of {path, old_value, new_value} from a
    proposal's prose. Returns None when it can't find all three
    cleanly — callers should fall back to a constrained-replay
    execution in that case (see `_execute_code_fence`)."""
    if not proposal_text:
        return None
    path_m = _FILE_PATH_RE.search(proposal_text)
    if not path_m:
        return None
    from_to = _FROM_TO_RE.search(proposal_text)
    if from_to:
        return {"path": path_m.group(1), "old_value": from_to.group(1),
                "new_value": from_to.group(2)}
    return None


async def _execute_code_fence(action: dict, *, user: dict, project_id: str,
                               bin_ctx) -> dict:
    from services.local_tools import read_repo_file, write_repo_file

    ctx = {"user_id": user.get("user_id"), "project_id": project_id, "bin_ctx": bin_ctx}
    proposal_text = action.get("proposal_text") or ""
    edit = extract_deterministic_edit(proposal_text)

    if edit:
        read_res = await read_repo_file(ctx, {"path": edit["path"]})
        if not read_res.get("ok"):
            return {
                "content": (
                    f"I had the change ready (`{edit['old_value']}` -> "
                    f"`{edit['new_value']}` in `{edit['path']}`) but "
                    f"couldn't reload the file to apply it: "
                    f"{read_res.get('error', 'unknown error')}. Ask me "
                    f"to try again and I'll take another look."
                ),
                "executed": False,
            }
        current = read_res.get("content") or ""
        if current.count(edit["old_value"]) != 1:
            # Not safely unique in the current file — don't guess which
            # occurrence, and don't silently skip either. Honest fallback.
            return await _execute_code_fence_replay(action, ctx=ctx)
        new_content = current.replace(edit["old_value"], edit["new_value"], 1)
        write_res = await write_repo_file(ctx, {
            "path": edit["path"], "content": new_content,
            "commit_message": f"chore: update {edit['path']} (via chat approval)",
        })
        if write_res.get("ok"):
            return {
                "content": (
                    f"Done — updated `{edit['path']}` (changed "
                    f"\"{edit['old_value']}\" to \"{edit['new_value']}\"). "
                    f"Commit: {write_res.get('html_url', write_res.get('sha', ''))}"
                ),
                "executed": True,
                "commit_sha": write_res.get("sha"),
                "html_url": write_res.get("html_url"),
                "file_path": edit["path"],
            }
        return {
            "content": (
                f"I had the change ready but the write failed: "
                f"{write_res.get('error', 'unknown error')}. Ask me to "
                f"try again."
            ),
            "executed": False,
        }

    return await _execute_code_fence_replay(action, ctx=ctx)


async def _execute_code_fence_replay(action: dict, *, ctx: dict) -> dict:
    """Fallback when the proposal couldn't be reduced to a clean,
    literal (path, old, new) substitution: replay the EXACT approved
    proposal text as a direct, non-negotiable instruction to the real
    tool-having pipeline — told explicitly to apply what was already
    approved, not invent a new version of it."""
    from services.orchestrator import chat_with_tools

    proposal_text = action.get("proposal_text") or action.get("brief") or ""
    instruction = (
        "The user already reviewed and approved this exact proposal in "
        "the previous turn — apply it EXACTLY as written, do not "
        "reinterpret, restructure, or rename anything not explicitly "
        "called for. Do not ask for confirmation again. Use the write "
        "tool to commit the change, then report what changed.\n\n"
        f"APPROVED PROPOSAL:\n{proposal_text}"
    )
    result = await chat_with_tools(
        prompt=instruction,
        jwt_token="",
        system=None,
        max_iters=4,
        session_id=None,
        mongo_client=None,
        user_id=ctx.get("user_id"),
        project_id=ctx.get("project_id"),
        mode="pro",
        task_type=None,
        is_founder=False,
        bin_ctx=ctx.get("bin_ctx"),
    )
    return {
        "content": result.get("content") or "Applied the approved change.",
        "executed": bool(result.get("tool_calls_run")),
    }


async def _execute_upgrade(action: dict, *, user: dict) -> dict:
    from routers.payments import create_checkout_session

    plan = action.get("plan") or "pro"
    try:
        checkout = await create_checkout_session(user, plan)
    except Exception as e:                                # noqa: BLE001
        logger.warning("confirm-execution upgrade checkout failed: %r", e)
        return {
            "content": (
                "I wasn't able to start the upgrade checkout just now "
                "— please try again in a moment, or visit "
                "/settings#pricing to upgrade directly."
            ),
            "executed": False,
        }
    return {
        "content": (
            f"Great — I've started your upgrade to the {plan.title()} "
            f"plan. Complete checkout here: {checkout['checkout_url']}"
        ),
        "executed": True,
        "checkout_url": checkout["checkout_url"],
        "session_id": checkout["session_id"],
    }


async def maybe_execute_pending(
    db, *, user: dict, session_id: str, project_id: str, prompt: str, bin_ctx,
) -> Optional[dict]:
    """THE shared entry point — call this from both /chat/send and
    /chat/stream BEFORE normal tier routing. Returns a chat-result-
    shaped dict (with `_skip_output_guards: True` so the deterministic,
    already-honest completion message isn't rewritten by the false-
    success guard) when a confirmation intent matched a real pending
    action; returns None otherwise (let the caller fall through to
    normal routing, including the existing NO_PENDING_FIX_MESSAGE path
    when there's genuinely nothing pending)."""
    if not is_confirm_execute_intent(prompt):
        return None
    action = await get_pending_action(db, session_id)
    if not action:
        return None

    try:
        if action.get("type") == "upgrade":
            outcome = await _execute_upgrade(action, user=user)
        elif action.get("type") == "code_fence":
            outcome = await _execute_code_fence(
                action, user=user, project_id=project_id, bin_ctx=bin_ctx,
            )
        else:
            return None
    finally:
        await clear_pending_action(db, session_id)

    return {
        "ok":                       True,
        "content":                  outcome["content"],
        "provider":                 "confirm-executor",
        "iterations":               0,
        "tool_calls_run":           1 if outcome.get("executed") else 0,
        "meta":                     {"executed": outcome.get("executed", False),
                                      "action_type": action.get("type")},
        "council":                  None,
        "task_type":                None,
        "findings_saved_this_turn": [],
        "_skip_output_guards":      True,
    }
