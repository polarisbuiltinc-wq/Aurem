"""
routers/chat/__init__.py — AUREM Dev
AI chat endpoints: send (sync), stream (SSE), history, sessions.
All messages persisted to db.chat_sessions per user.
First assistant reply triggers a background title-summarization.

Split from the former routers/chat.py god-file (4184 lines) into a
package on 2026-09-08 (chat.py -> chat/{misc,turn,stream,history}.py).
Behavior byte-identical — pure mechanical move, no logic change.

Each submodule imports `router` from here and attaches its own
routes to it directly (`from . import router`, then `@router.post(...)`
in that submodule). This __init__ then re-exports every endpoint/
class so `from routers.chat import X` and direct `chat_mod.X` access
(both used throughout the test suite) keep working unchanged.

IMPORTANT — mock.patch("routers.chat.X") does NOT survive this move
for names used INSIDE a moved function (e.g. `current_dev` called
inside `chat_send`, now in turn.py) — patching a name on this
__init__ module does not change turn.py's own separate import
binding of that same name. Every such test-suite patch target was
updated to the new submodule path (routers.chat.turn.X /
routers.chat.stream.X / routers.chat.misc.X) in the same commit as
this split. See CHANGELOG.md 2026-09-08 entry for the full list.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/chat", tags=["Chat"])

from . import misc, turn, stream, history  # noqa: F401,E402 — registers routes on `router`

# Re-export so `from routers.chat import X` / `chat_mod.X` keep working.
from .misc import (  # noqa: F401,E402
    ChatOpenedBody, chat_opened, list_agents, available_modes,
    IntentClassifyBody, classify_intent_endpoint,
    TurnShippedBody, chat_turn_shipped, FeedbackBody, chat_feedback,
    TaskFollowupBody, chat_task_followup, draft_support_email,
    PLAIN_ENGLISH_EXPLAIN_CONTRACT, BUSINESS_OWNER_VOICE_CONTRACT,
    ORA_PANEL_TONE, _HANDOFF_FENCE_RE, _SHELL_COMMAND_TOKENS,
)
from .turn import ChatBody, chat_send  # noqa: F401,E402
from .stream import (  # noqa: F401,E402
    _handoff_brief_is_shell_command, _maybe_guard_shell_handoff_followup,
    chat_stream,
)
from .history import (  # noqa: F401,E402
    chat_history, chat_sessions_list, chat_session_delete,
    chat_session_clear_messages,
)

# services.chat_helpers re-exports (2026-08-26 extraction, predates this
# split) — kept here too since `from routers.chat import X` for these
# (classify_intent, _persist_turn, _f12_has_real_signal, etc.) must
# keep working unchanged.
from services.chat_helpers import (  # noqa: F401,E402
    _detect_mode, _deduct_tokens,
    is_fix_confirmation, _safe_provenance, detect_prompt_injection,
    _f12_has_real_signal, _is_transient_proxy_error, _TRANSIENT_PROXY_CODES,
    classify_intent,
    _TITLE_SYSTEM, _generate_title, _maybe_set_title,
    _regenerate_without_recall, _strip_council_block,
    _persist_turn,
    _build_failed_followup, _build_blocked_followup, _build_done_fallback,
    _FOLLOWUP_SYS, _generate_done_followup,
    retrieved_context_for_grounding, apply_output_guards,
)
