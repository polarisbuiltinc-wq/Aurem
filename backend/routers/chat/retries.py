"""
routers/chat/retries.py — confidence-mismatch retry gate, extracted
out of chat_stream()'s gen() post-processing (2026-09-08 StreamState
refactor). Mechanical move — see stream_state.py's field mapping.

2026-08-21 — cold-start / recall-mismatch mitigation. See
services/response_confidence.py. Must run BEFORE the token stream
loop in sse_stream.py so the user never sees the mismatched content
stream in. 2026-08-22 — hardened with a quiet auto-retry (layer d)
and verbose real-log observation (founder ask) before falling back
to the canned message.
"""
from __future__ import annotations

import logging

from .stream_state import StreamState

logger = logging.getLogger(__name__)


async def run_confidence_gate(state: StreamState, content: str, provider: str):
    """Returns (content, provider) — possibly swapped for a clean
    retry or a reason-carrying bail message. Side effects land on
    `state.low_confidence` / `state.ship_suppressed` /
    `state.bail_reason` / `state.prior_fix_signal`."""
    from cto_services.db import get_db
    from core.task_type import infer_task_type as _infer_task_type
    from services.chat_helpers import _regenerate_without_recall, _strip_council_block

    body = state.body
    user = state.user
    jwt_token = state.jwt_token
    bin_ctx = state.bin_ctx
    extra_sys = state.extra_sys
    _council_recalled = state.council_recalled
    _council_block = state.council_block

    try:
        from services.response_confidence import (
            response_seems_mismatched, has_ship_suggestion, FALLBACK_MESSAGE,
            prior_turn_had_fix_signal,
        )
        from services.bail_reason import classify_bail
        _prior_fix_signal = await prior_turn_had_fix_signal(
            get_db(), body.session_id, (user or {}).get("user_id")
        )
        state.prior_fix_signal = _prior_fix_signal
        _mismatch = response_seems_mismatched(body.prompt or "", content, _prior_fix_signal)
        logger.info(
            "chat.confidence_check surface=chat_stream turn=1 prompt=%r "
            "council_recalled=%s mismatch=%s content_preview=%r",
            (body.prompt or "")[:160], _council_recalled, _mismatch,
            (content or "")[:220],
        )
        from services.response_confidence import persist_confidence_check
        await persist_confidence_check(
            get_db(), surface="chat_stream", turn=1,
            prompt_preview=(body.prompt or "")[:160],
            content_preview=(content or "")[:220],
            council_recalled=_council_recalled, mismatch=_mismatch,
            user_id=(user or {}).get("user_id"), session_id=body.session_id,
            project_id=body.project_id,
        )
        if _mismatch:
            logger.warning(
                "chat_stream: mismatch detected on first response — "
                "retrying once without the ORA-Council recall block "
                "before showing anything to the user",
            )
            from services.subscription_tiers import allowed_modes_for_tier
            from services.usage import is_founder_email as _is_fnd_email_retry
            _allowed_retry = allowed_modes_for_tier((user or {}).get("tier") or "free")
            _req_mode_retry = body.mode if (body.mode in _allowed_retry) else _allowed_retry[-1]
            _is_fnd_retry = bool(
                user.get("is_admin") or user.get("is_unlimited")
                or (user.get("tier") == "founder")
                or _is_fnd_email_retry(user.get("email"))
            )
            _retry_content, _retry_provider = await _regenerate_without_recall(
                prompt=body.prompt, jwt_token=jwt_token,
                extra_sys_no_council=_strip_council_block(extra_sys, _council_block),
                max_iters=min(body.max_tool_iters or 2, 4),
                session_id=body.session_id, user_id=user.get("user_id"),
                project_id=body.project_id, mode=_req_mode_retry,
                task_type=body.task_type or _infer_task_type(body.prompt),
                is_founder=_is_fnd_retry, bin_ctx=bin_ctx,
            )
            _retry_mismatch = response_seems_mismatched(body.prompt or "", _retry_content, _prior_fix_signal)
            logger.info(
                "chat.confidence_check surface=chat_stream turn=2(retry) "
                "prompt=%r mismatch=%s content_preview=%r",
                (body.prompt or "")[:160], _retry_mismatch,
                (_retry_content or "")[:220],
            )
            await persist_confidence_check(
                get_db(), surface="chat_stream", turn=2,
                prompt_preview=(body.prompt or "")[:160],
                content_preview=(_retry_content or "")[:220],
                mismatch=_retry_mismatch,
                user_id=(user or {}).get("user_id"), session_id=body.session_id,
                project_id=body.project_id,
            )
            if _retry_content.strip() and not _retry_mismatch:
                content = _retry_content
                provider = _retry_provider or provider
                logger.info(
                    "chat_stream: retry resolved the mismatch — user "
                    "never saw the bad first draft",
                )
            else:
                state.ship_suppressed = (
                    has_ship_suggestion(content) or has_ship_suggestion(_retry_content)
                )
                # R2 (2026-08-31) — same reason-carrying bail as
                # chat_send: never the generic "try rephrasing"
                # fallback (see services/bail_reason.py).
                _bail = classify_bail(body.prompt or "")
                content = _bail["message"]
                state.bail_reason = _bail["reason"]
                state.low_confidence = True
                logger.warning(
                    "chat_stream: retry ALSO mismatched (or came back "
                    "empty) — showing reason-carrying bail (reason=%s), "
                    "never the generic 'try rephrasing' fallback", state.bail_reason,
                )
    except Exception as _rce:
        logger.debug("response_confidence gate skipped (chat_stream): %r", _rce)

    return content, provider
