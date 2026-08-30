"""
services/response_confidence.py — 2026-08-21, hardened 2026-08-22

Layered defense for the "cold-start / council-recall mismatch" bug:
a weakly-matched past example (score just above the retriever's
_MIN_SCORE=0.25 weak-match gate — see ora_council_retriever.py) can
still get echoed by the model almost verbatim, INCLUDING an unrelated
`aurem-handoff` fix proposal, for a completely unrelated trivial
question (e.g. "what is 5+5?" answered with a GitHub-auth "root
cause" + a Ship via CTO button).

Root cause is still NOT identified (could not be reproduced under
verbose logging in preview as of 2026-08-22 — see
routers/chat.py's `chat.confidence_check` log lines and
services/ora_council_retriever.py's recall log line for the
instrumentation added to keep hunting it). This module is the
GUARANTEE layer: even if the cause is never found, the user must
never see a mismatched response.

Two checks, from strictest to broadest:
  1. `is_definitional_mismatch` — a HARD rule (founder-specified):
     a short (<=10 words), plain user message with no code/error/
     fix intent of its own can NEVER legitimately produce a response
     containing a file path, a "Root cause:" diagnosis, or an
     `aurem-handoff` fence. If it does, it's a mismatch, full stop.
  2. `response_seems_mismatched` — the broader fallback used for
     longer/ambiguous messages: an unsolicited code-ship
     (`aurem-handoff`) or diagnosis is only legitimate when the
     user's OWN message actually carries fix/bug/code intent.
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9_]+")

_HANDOFF_FENCE_RE = re.compile(r"```aurem-handoff\b")
_ROOT_CAUSE_RE = re.compile(r"root cause[:\s]", re.IGNORECASE)
# 2026-08-25 — widened (real live-reproduced miss): a response can
# describe the SAME "ship a fix" action in plain prose without ever
# emitting the literal fence or the exact "root cause:" phrase (e.g.
# "you'd click Ship via CTO to commit that fix") and slip straight
# past the old narrow check. This catches that class of prose without
# touching `has_ship_suggestion()`, which stays fence-only on purpose
# (it gates the actual Ship button, a different, narrower concern).
_TASK_ACTION_PROSE_RE = re.compile(
    r"ship\s+(it\s+)?via\s+cto"
    r"|click[^.\n]{0,25}\bship\b"
    r"|\bcommit\b[^.\n]{0,20}\bfix\b"
    r"|\bdeploy\b[^.\n]{0,20}\bfix\b"
    r"|\bship\b[^.\n]{0,20}\bfix\b",
    re.IGNORECASE,
)
_FILE_PATH_RE = re.compile(
    r"\b[\w\-./]+\.(?:py|js|jsx|ts|tsx|json|md|yml|yaml|css|html|txt|sql|env|sh)\b"
)
# Literal code/error SIGNALS in the user's OWN message — fenced code,
# a real file path, a stack trace, a URL, or an HTTP status code.
# Deliberately NOT keyword-based (bug/error/fix are handled by
# `is_fix_intent` below) — this only catches actual code artifacts.
_CODE_SIGNAL_RE = re.compile(
    r"```|" + _FILE_PATH_RE.pattern + r"|traceback|stack trace|https?://|\b[45]\d\d\b"
)

# Any of these present in the user's own message means they DO want
# a code/bug-fix answer — a diagnosis + Ship button is legitimate.
# 2026-08-22 — MORE AGGRESSIVE (founder-directed, after an intermittent
# recurrence of the cold-start mismatch on a re-test): deliberately
# dropped the purely DESCRIPTIVE nouns that used to live here (file,
# files, code, api, route, component, function, endpoint, config,
# repo, repository, test, tests) — a plain "what does the payment api
# do?" or "where's the config file?" contains one of those words but
# is NOT a fix request, and having them here meant an unrelated
# diagnosis+Ship response could slip past this gate completely
# untouched just because the user's question happened to mention a
# file/api/route. Keeping only unambiguous problem-report or
# change-request signals (verbs/states, not descriptive nouns) closes
# that gap — accepting that a genuinely good response occasionally
# gets the fallback treatment is the intended tradeoff here.
#
# 2026-08-22 (later same day) — real prod bug: that tightening had a
# blind spot. "Check my code for any security problems" (a completely
# legitimate audit request, no explicit "fix"/"bug" word) would get a
# real, on-topic audit reply back that naturally says "Root cause:"
# or proposes an aurem-handoff fix — and the gate wrongly nuked it as
# a "mismatch" since none of its tokens were in this set. Added
# AUDIT-specific signals (deliberately still not generic descriptive
# nouns like "code"/"file" — "audit"/"vulnerability"/"security" etc.
# are unambiguous code-review intent, unlike "code" or "file" alone).
_FIX_INTENT_TOKENS = {
    "fix", "fixes", "fixed", "bug", "bugs", "error", "errors", "broken",
    "crash", "crashes", "fail", "fails", "failing", "failed", "issue",
    "issues", "debug", "add", "implement", "build", "create", "update",
    "change", "refactor", "feature", "ship", "deploy", "commit",
    "write", "install", "upgrade", "migrate", "optimize",
    "improve", "remove", "delete", "revert",
    "rollback",
    "audit", "audits", "scan", "scans", "review", "reviews",
    "vulnerability", "vulnerabilities", "vulnerable", "insecure",
    "security", "secure", "harden", "hardening", "exploit", "exploits",
}

# 2026-08-22 — raised from 10: widens the coverage of the STRICT,
# deterministic `is_definitional_mismatch` hard rule (below), which
# doesn't rely on the token heuristic at all — a longer plain
# question with no genuine fix intent still can't legitimately come
# back with a diagnosis + Ship proposal.
_SIMPLE_WORD_LIMIT = 20

FALLBACK_MESSAGE = (
    "I couldn't find a confident answer to that — try rephrasing, or ask again."
)


async def prior_turn_had_fix_signal(db, session_id: str, user_id: str) -> bool:
    """Fetch the last stored assistant turn for this session (BEFORE
    the current turn is appended) and check whether IT already
    carried a fix/ship signal. Used to exempt a short confirmation
    reply ("yes"/"ship it"/"approve") from being treated as a fresh,
    out-of-context mismatch — see `response_seems_mismatched` above.
    Fail-open (returns False) on any DB hiccup — same posture as
    every other passive-audit read in this codebase."""
    if db is None or not session_id:
        return False
    try:
        doc = await db.chat_sessions.find_one(
            {"session_id": session_id, "user_id": user_id},
            {"_id": 0, "turns": {"$slice": -1}},
        )
        turns = (doc or {}).get("turns") or []
        if not turns:
            return False
        last = turns[-1]
        if not isinstance(last, dict) or last.get("role") != "assistant":
            return False
        return response_has_fix_signal(last.get("content") or "")
    except Exception:
        return False


async def prior_turn_context_text(db, session_id: str, user_id: str) -> str | None:
    """2026-08-30 — Issue B fix. Fetch the last stored assistant turn's
    raw text (same cheap single-doc `$slice: -1` query shape as
    `prior_turn_had_fix_signal` above) so a short follow-up
    ("i didnt find any ?") can be anchored to what the assistant was
    just doing, instead of the casual-tier reply path having ZERO
    turns of history by construction (confirmed root cause — not a
    model/context-length cap). Fail-open (returns None) on any DB
    hiccup or if there's no prior assistant turn — same posture as
    every other passive-audit read in this codebase."""
    if db is None or not session_id:
        return None
    try:
        doc = await db.chat_sessions.find_one(
            {"session_id": session_id, "user_id": user_id},
            {"_id": 0, "turns": {"$slice": -1}},
        )
        turns = (doc or {}).get("turns") or []
        if not turns:
            return None
        last = turns[-1]
        if not isinstance(last, dict) or last.get("role") != "assistant":
            return None
        return last.get("content") or None
    except Exception:
        return None


async def get_session_summary(db, session_id: str, user_id: str) -> str | None:
    """2026-08-30 — Issue C fix. The casual-tier reply path
    (`casual_direct_reply`) never sees `orchestrator.py`'s dynamic
    history window at all — this is its equivalent memory anchor for
    sessions long enough to have a rolling summary (see
    `services/session_summary.py`). Same fail-open, single-doc read
    posture as `prior_turn_context_text` above."""
    if db is None or not session_id:
        return None
    try:
        doc = await db.chat_sessions.find_one(
            {"session_id": session_id, "user_id": user_id},
            {"_id": 0, "summary": 1},
        )
        return (doc or {}).get("summary") or None
    except Exception:
        return None


async def persist_confidence_check(db, **fields) -> None:
    """2026-08-25 — passive audit trail for `chat.confidence_check`.
    Founder has no raw log access to Preview/Production; this makes
    every mismatch-check outcome queryable via
    GET /admin/insights/confidence-checks instead of requiring a
    support ticket for log access each time. Fire-and-forget: never
    lets a Mongo hiccup affect the actual chat response — same
    fail-open posture as every other passive audit write in this
    codebase (intent_classifications, cost_revenue_alert_log, etc.)."""
    if db is None:
        return
    try:
        import time as _time
        await db.response_confidence_log.insert_one({**fields, "ts": _time.time()})
    except Exception:
        pass


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def is_fix_intent(user_message: str) -> bool:
    """True iff the user's OWN message signals they actually want a
    bug-fix / code-change answer."""
    return bool(_tokens(user_message) & _FIX_INTENT_TOKENS)


def _looks_like_code(user_message: str) -> bool:
    """True iff the user's OWN message contains an actual code/error
    artifact (fenced code, file path, traceback, URL, HTTP status)."""
    return bool(_CODE_SIGNAL_RE.search(user_message or ""))


def _response_has_fix_signal(final_output: str) -> bool:
    return bool(
        _HANDOFF_FENCE_RE.search(final_output)
        or _ROOT_CAUSE_RE.search(final_output)
        or _TASK_ACTION_PROSE_RE.search(final_output)
    )


def has_ship_suggestion(final_output: str) -> bool:
    """True iff `final_output` carries an actual ```aurem-handoff fence
    — i.e. the ONE thing that renders a "Ship via CTO" button on the
    frontend (see MessageBubble.jsx). Deliberately narrower than
    `_response_has_fix_signal`: a bare "Root cause:" sentence with no
    fence never shows a Ship button, so suppressing it isn't a
    suppressed *ship suggestion* — just a suppressed diagnosis."""
    return bool(_HANDOFF_FENCE_RE.search(final_output or ""))


def is_definitional_mismatch(user_message: str, final_output: str) -> bool:
    """HARD rule — a short/plain question can never legitimately get a
    "Root cause:" diagnosis or an aurem-handoff ship-proposal back.
    NOTE: deliberately does NOT trigger on a bare file-path mention —
    "who handles billing?" → "that's in services/billing.py" is a
    perfectly legitimate short codebase answer, not a mismatch (see
    tests/test_citation_guard_persist_ordering.py, which already
    covers unverified-file-path correction via CitationGuard)."""
    if not final_output:
        return False
    words = (user_message or "").split()
    if len(words) > _SIMPLE_WORD_LIMIT:
        return False
    if is_fix_intent(user_message) or _looks_like_code(user_message):
        return False
    return _response_has_fix_signal(final_output)


def response_has_fix_signal(final_output: str) -> bool:
    """Public alias of `_response_has_fix_signal` — lets callers check
    whether a PRIOR turn's content already carried fix intent, so a
    short confirmatory reply to it isn't wrongly treated as an
    out-of-context question (see `is_confirmation_reply` below,
    2026-08-28 First-Experience Wave NEW-P0 fix)."""
    return _response_has_fix_signal(final_output)


# 2026-08-28 · NEW P0 (Ship-Approve false-success + no-button) —
# `response_seems_mismatched` only ever saw the CURRENT user message
# in isolation. A real, live repro: turn 1 ("ship a trivial README
# edit") legitimately gets a fence back. Turn 2, the user just
# confirms — "yes" / "go ahead" / "approve" / "ship it" — with no
# `_FIX_INTENT_TOKENS` word of its own. The model, seeing full
# conversation history, correctly re-describes/re-emits the SAME fix
# with a fresh fence. But this gate, seeing only "yes" (no fix
# intent, no code signal) + a response with a fix signal, flagged it
# as a fresh unsolicited mismatch and swapped the REAL fence for
# FALLBACK_MESSAGE — the fence never reached the user, so no Approve
# button ever rendered, and the P0-1 fallback banner ("approve button
# didn't load") fired on every retry because retrying hits the exact
# same gate again. This is the root cause of the "no button" bug.
_CONFIRMATION_RE = re.compile(
    r"^\s*(?:yes[,]?\s+)?(?:please\s+)?"
    r"(yes|yeah|yep|yup|sure|ok|okay|please|go ahead|go for it|"
    r"do it|ship it|ship that|approve|approved|approve it|confirm|"
    r"confirmed|proceed|sounds good|do that|go)\s*[.!]?\s*$",
    re.IGNORECASE,
)


def is_confirmation_reply(user_message: str) -> bool:
    """True iff the ENTIRE message is a short affirmative continuation
    ("yes", "go ahead", "ship it", "approve", ...) with nothing else —
    deliberately whole-message-anchored so a real, longer question
    that happens to contain "ok" or "confirm" is never matched."""
    return bool(_CONFIRMATION_RE.match(user_message or ""))


def response_seems_mismatched(
    user_message: str,
    final_output: str,
    prior_turn_had_fix_signal: bool = False,
) -> bool:
    """True iff this turn should NEVER be shown to the user as-is —
    combines the hard short-message rule with the broader fix-intent
    heuristic for longer messages.

    `prior_turn_had_fix_signal` — pass True when the immediately
    preceding assistant turn in this session already carried a fix/
    ship signal (fence, "Root cause:", or ship-action prose). A short
    confirmation reply to THAT turn is legitimate continuation, not a
    fresh out-of-context mismatch, even though it has no
    `_FIX_INTENT_TOKENS` word of its own."""
    if not final_output:
        return False
    if prior_turn_had_fix_signal and is_confirmation_reply(user_message):
        return False
    if is_definitional_mismatch(user_message, final_output):
        return True
    if not _response_has_fix_signal(final_output):
        return False
    return not is_fix_intent(user_message)


# 2026-08-28 · NEW P0 Task 2 — "false success" close-out. A bare
# confirmation reply ("approve"/"yes"/"ship it") can NEVER
# legitimately be followed by a reply that CLAIMS a ship/approve
# action already happened: real execution is a separate, explicit,
# button-triggered async flow (POST /cto/tasks/submit, polled to
# completion — see MessageBubble.jsx `shipViaCTO`/`TaskProgressCard`).
# A chat TEXT reply is generated and returned before the user has
# clicked anything, so any "Approved!"/"Shipped!"/"Done!" claim in
# THAT reply is false by construction. Live-reproduced by the
# founder: typing "approve" got "Approved! Let me know what you
# need" (free-form LLM prose from `casual_direct_reply`, which has
# no such guard) while GitHub stayed at the pre-turn SHA — no commit
# ever landed.
NO_PENDING_FIX_MESSAGE = (
    "There's nothing pending for me to approve right now — describe "
    "the fix you'd like (e.g. \"fix the README typo\") and I'll take "
    "a look."
)
RETRY_FIX_MESSAGE = (
    "I wasn't able to re-confirm that fix cleanly. Please restate what "
    "you'd like fixed in one sentence (e.g. \"fix the README typo\") "
    "and I'll set it up again."
)

_FALSE_SUCCESS_TOKENS_RE = re.compile(
    r"\b(approved|shipped|committed|merged|deployed)\b|"
    r"\ball set\b|\ball done\b|\bi'?ve done (it|that)\b|"
    # 2026-08-28 · testing_agent finding (iteration_p0_ship_approve_
    # fix_verify) — fresh-session "yes please ship it" got back a
    # present-tense promise ("On it—shipping now!") from the SAME
    # unguarded casual LLM call. No commit lands, but it still reads
    # as work-in-progress on a request that has nothing pending.
    r"\bon it\b|\bshipping now\b|\bkicking off\b|\bworking on it\b",
    re.IGNORECASE,
)


def contains_false_success_claim(content: str) -> bool:
    """True iff `content` uses past-tense completion language
    (approved/shipped/committed/merged/deployed/all set/all done) or a
    present-tense in-progress promise (on it/shipping now/kicking off/
    working on it) — used ONLY to guard bare-confirmation replies (see
    `is_confirmation_reply`), where a chat TEXT reply can never
    legitimately claim a ship/approve action already happened OR is
    happening right now (real execution only starts once the user
    clicks the real Approve button)."""
    return bool(_FALSE_SUCCESS_TOKENS_RE.search(content or ""))


def apply_no_false_success_guard(
    user_message: str,
    content: str,
    prior_turn_had_fix_signal: bool = False,
) -> str:
    """Final defense-in-depth safety net, applied to the FINAL content
    right before it reaches the user on both chat_send and
    chat_stream. Only two honest outcomes for a bare confirmation
    reply:
      - a real ```aurem-handoff fence is present → left untouched,
        the Approve button renders from it (the one real path).
      - no fence + a false completion claim → swapped for an honest,
        actionable message — never a silent "looks fine" pass-through
        of a fabricated success claim."""
    if not is_confirmation_reply(user_message):
        return content
    if has_ship_suggestion(content or ""):
        return content
    if not contains_false_success_claim(content or ""):
        return content
    return RETRY_FIX_MESSAGE if prior_turn_had_fix_signal else NO_PENDING_FIX_MESSAGE
