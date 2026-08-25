"""
services/chat_helpers.py — safe mechanical extraction from routers/chat.py
(2026-08-26 coverage-floor extraction batch).

Pure/standalone helper functions used by chat_send/chat_stream, moved out
verbatim (zero logic changes) to shrink routers/chat.py. `routers/chat.py`
imports every name below at module level, so:
  - internal call sites inside chat_send/chat_stream keep resolving them
    as bare globals exactly as before;
  - `from routers.chat import X` and `patch("routers.chat.X", ...)` in the
    existing test suite keep working unchanged (re-export semantics).
"""
from __future__ import annotations
import logging
import re as _re_mode
import time
from typing import Optional

from cto_services.db import get_db
from services.llm import call_llm_with_meta, cap_for
from services.orchestrator import chat_with_tools

logger = logging.getLogger(__name__)

# Heuristic: prompt mentions build/create/fix/write code/etc → bump cap
_CODE_HINTS = ("```", "build", "create", "fix", "write", "implement",
               "function", "class", "refactor", "debug", "snippet", "code")


def _detect_mode(prompt: str) -> str:
    p = (prompt or "").lower()
    return "code" if any(h in p for h in _CODE_HINTS) else "chat"


async def _deduct_tokens(user_id: str, reply: str) -> int:
    """Deduct ~1 token per 3 words from the user's wallet. Returns new balance."""
    db = get_db()
    if db is None or not user_id:
        return 0
    used = max(1, len((reply or "").split()) // 3 + 1)
    try:
        # Iter 212m-106 — Token floor. Was `$inc -used` unconditional
        # which let the balance go negative (user saw -28,359 on the
        # health page). Now: atomic clamp via aggregation pipeline so
        # tokens_remaining never drops below 0 even if `used` exceeds
        # the current balance.
        await db.dev_users.update_one(
            {"user_id": user_id},
            [{
                "$set": {
                    "tokens_remaining": {
                        "$max": [
                            0,
                            {"$subtract": [
                                {"$ifNull": ["$tokens_remaining", 0]},
                                used,
                            ]},
                        ]
                    }
                }
            }],
        )
        u = await db.dev_users.find_one(
            {"user_id": user_id}, {"_id": 0, "tokens_remaining": 1}
        )
        return int((u or {}).get("tokens_remaining", 0))
    except Exception as e:
        # Iter 367 · Vanguard hardening — parameterised logging, not
        # f-strings, on paths that touch user-controlled ids.
        logger.warning("deduct_tokens failed: %r", e)
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Iter 42 — Mode classifier (A/B/C/D/E)
# Centralised so chat.py and the worker share the same logic.
# ─────────────────────────────────────────────────────────────────────────────

_FIX_CONFIRM = _re_mode.compile(
    r"\b(yes|yep|yeah|sure|ok|okay|fix\s+it|ship\s+it|do\s+it|go\s+ahead|apply\s+the\s+fix)\b",
    _re_mode.IGNORECASE,
)

# Iter 50 — short greetings should NEVER be classified as debug just
# because stale F12 errors are still in the browser's capture buffer.
_GREETING = _re_mode.compile(
    r"^\s*(hi|hello|hey|yo|sup|hola|namaste|good\s+(morning|afternoon|evening)|"
    r"thanks|thank\s+you|ok|okay|got\s+it|cool|nice|awesome)"
    r"(\s+\w{0,12}){0,3}\s*[!.?]?\s*$",
    _re_mode.IGNORECASE,
)


def is_fix_confirmation(message: str) -> bool:
    return bool(_FIX_CONFIRM.search(message or ""))


# Iter 212m-49 — read LLM provenance for the most recent hop in this
# request context. Wrapped so any import / future-shape error never
# breaks the SSE `done` frame.
def _safe_provenance() -> dict:
    try:
        from services.llm import get_last_provider
        return get_last_provider()
    except Exception:
        return {"provider": "openrouter", "model": "", "is_emergency": False}


# Iter 212m-48 — basic prompt-injection guard. We do NOT log the
# matched content (per security spec) — only the fact that a hit
# happened, with a short rule label. Patterns are case-insensitive
# and match anywhere in the message; this is a static deny-list,
# not a heuristic, so it's safe to expand without false-positive
# risk for normal user prose.
_PROMPT_INJECTION_PATTERNS = [
    ("ignore_previous_instructions",
     _re_mode.compile(r"ignore\s+previous\s+instructions", _re_mode.IGNORECASE)),
    ("ignore_all_previous",
     _re_mode.compile(r"ignore\s+all\s+previous", _re_mode.IGNORECASE)),
    ("im_start_marker",
     _re_mode.compile(r"<\|im_start\|>", _re_mode.IGNORECASE)),
    ("you_are_now",
     _re_mode.compile(r"\byou\s+are\s+now\b", _re_mode.IGNORECASE)),
    ("act_as_if_no_restrictions",
     _re_mode.compile(
         r"act\s+as\s+if\s+you\s+have\s+no\s+restrictions",
         _re_mode.IGNORECASE,
     )),
]


def detect_prompt_injection(message: str) -> str | None:
    """Returns the rule label of the FIRST matched injection pattern,
    or None when the message is clean. Caller is expected to refuse
    the request with HTTP 400 on a hit — DO NOT log the content."""
    if not message:
        return None
    for label, pat in _PROMPT_INJECTION_PATTERNS:
        if pat.search(message):
            return label
    return None


def _f12_has_real_signal(payload: dict) -> bool:
    """Iter 50 — guards against fishing-expedition Mode D triggers when the
    F12 buffer holds only noise (aborted/200 network entries, no stack
    traces, no real console.error messages).

    Iter 105 — also filters out transient proxy / gateway errors with an
    HTML body (Cloudflare 520, gateway 502/504, etc.). These fire on
    cold-start before the origin is ready and would otherwise trigger
    Mode D on the user's very first chat message, producing the spammy
    "Files to check: (unknown — error context too thin)" bailout.

    Returns True only when the payload contains something a debugger
    can actually use:
      * A console error with a non-trivial message (>5 chars)
      * A network error with HTTP status in 400-599 AND a real URL AND
        NOT a transient proxy/gateway code with an HTML body
      * Any stack trace
    """
    if not isinstance(payload, dict):
        return False
    for ce in (payload.get("console_errors") or []):
        msg = (ce.get("message") or ce.get("msg") or "").strip()
        if len(msg) > 5 and "aborted" not in msg.lower():
            return True
    for ne in (payload.get("network_errors") or []):
        st = ne.get("status", 0)
        if not (isinstance(st, int) and 400 <= st < 600 and ne.get("url")):
            continue
        if _is_transient_proxy_error(st, ne.get("response_body", "")):
            continue
        return True
    if payload.get("stack_traces"):
        return True
    return False


# Iter 105 — Cloudflare / proxy / gateway codes whose body is typically a
# generic HTML error page (NOT a real application error). These get
# dropped from F12 signal so a cold-start 520 doesn't poison ORA's
# first-chat response.
_TRANSIENT_PROXY_CODES = {
    408,                                                # Request Timeout
    499,                                                # Client Closed Request (Iter 212m-8)
    502, 503, 504,                                      # Bad Gateway / SU / GT
    520, 521, 522, 523, 524, 525, 526, 527, 530,        # Cloudflare-specific
}


def _is_transient_proxy_error(status: int, body) -> bool:
    """Return True when (status, body) looks like a Cloudflare / nginx /
    proxy-level error page rather than a real API 5xx from our backend.
    Defensive: only treat as transient when status IS in the proxy set
    AND body looks like HTML (or is empty — proxy edge cases)."""
    if status not in _TRANSIENT_PROXY_CODES:
        return False
    # Iter 212m-8 — HTTP 499 (Client Closed Request) is by definition
    # client-side: the browser cancelled the request. The response body
    # may be a JSON error from our own 499 handler — body shape does
    # NOT change the fact that it's a transient disconnect, not an app
    # bug. Drop it from F12 signal unconditionally so a stale 499 in
    # the browser's capture buffer can't hijack Mode D on the next
    # user prompt (root cause of "ORA ignores my read request").
    if status == 499:
        return True
    b = (body or "")
    if isinstance(b, bytes):
        try:
            b = b.decode("utf-8", errors="ignore")
        except Exception:
            b = ""
    if not isinstance(b, str):
        return False
    if not b.strip():
        return True  # empty body on a proxy code → almost certainly a proxy error
    bl = b.lower()
    return ("<!doctype html" in bl) or ("<html" in bl) or ("cloudflare" in bl)


def classify_intent(message: str, f12_payload: Optional[dict]) -> str:
    """Returns one of: 'A','B','C','D','E','F'. Order matters."""
    from services.mode_d_debugger import is_debug_request
    from services.mode_e_auditor  import is_audit_request
    from services.mode_f_engage   import is_engage_request

    # Iter 50 — greeting wins over stale F12 noise. We still SHOW the
    # captured errors to the user via the F12 badge; we just don't
    # fire a hallucination-prone Mode D LLM call on a casual hello.
    msg = (message or "").strip()
    if _GREETING.match(msg):
        return "A"

    if f12_payload and _f12_has_real_signal(f12_payload):
        return "D"
    if is_debug_request(message):
        return "D"
    if is_audit_request(message):
        return "E"
    # Iter 60 — Engage mode catches market / positioning / GTM /
    # competitor / copy questions BEFORE the C/B coding classifiers
    # so a "write me a launch tweet about X" doesn't burn the full
    # codegen orchestrator.
    if is_engage_request(message):
        return "F"

    c_patterns = [
        r"\b(add|create|build|implement|write|generate|make|ship|deploy|fix|update|refactor)\b.*\b(to|in|for)\b.*\b(my|the)\b.*\b(repo|project|app|code|file)\b",
        r"\bship (this|it|the)\b",
        r"\bcommit\b",
        r"\bpush to (github|main|prod)\b",
        # Iter 162 — explicit deploy-target verbs. Previously a bare
        # "deploy to vercel" fell through to Mode A because the C regex
        # required "my|the …repo|project|app|code|file" after the verb.
        r"\bdeploy to (vercel|netlify|render|fly|railway|heroku|aws|cloudflare|production|prod|staging)\b",
        # Iter 212f — "debug full repo", "investigate the login flow",
        # "review the auth module" → agentic mode that actually reads
        # code. Previously these fell into Mode D which then bailed
        # with the "insufficient signal" template. The {0,3} word gap
        # lets natural phrasing pass ("debug *the login* flow").
        r"\b(debug|diagnose|investigate|review|trace)\b(?:\s+\w+){0,3}\s+\b(repo|repository|codebase|project|app|backend|frontend|file|folder|module|flow|auth|chat|api|router|endpoint)\b",
    ]
    for p in c_patterns:
        if _re_mode.search(p, message or "", _re_mode.IGNORECASE):
            return "C"

    b_patterns = [
        r"\bshould i\b",
        r"\bwhich is better\b",
        r"\bwhat['']s the best way\b",
        r"\bgive me (ideas|suggestions|options)\b",
        r"\bcompare\b",
        r"\brecommend\b",
        r"\bhow should i\b",
        r"\bwhat do you think\b",
        # Iter 81 — stuck-decision phrases. These also fire the Mode B
        # auto-upgrade (Decision Council) downstream.
        r"\btorn between\b",
        r"\bstuck (on|between)\b",
        r"\bcan'?t decide\b",
        r"\bcannot decide\b",
        r"\bdebating between\b",
        r"\b(pivot or persevere|build or buy)\b",
        r"\b(decision )?council\b",
    ]
    for p in b_patterns:
        if _re_mode.search(p, message or "", _re_mode.IGNORECASE):
            return "B"

    return "A"


_TITLE_SYSTEM = "Generate ultra-short chat titles. 3-5 words, Title Case, no punctuation. Just the title."


async def _generate_title(first_user_msg: str) -> str:
    """Ask the LLM to summarize the first user message in 3-5 words.
    Returns "" on any failure so the caller can fall back to last_message."""
    try:
        prompt = f"3-5 word title, Title Case, no punctuation: {first_user_msg.strip()[:100]}"
        meta = await call_llm_with_meta(_TITLE_SYSTEM, prompt,
                                         max_tokens=cap_for("title"),
                                         mode="title")
        title = (meta.get("content") or "").strip()
        title = title.strip("\"'`").rstrip(".!?").strip()
        if not title:
            return ""
        if len(title) > 60:
            title = title[:57].rstrip() + "…"
        return title
    except Exception as e:
        logger.warning("title generation failed: %r", e)
        return ""


async def _maybe_set_title(user_id: str, session_id: str,
                            first_user_msg: str) -> None:
    """If this session has no title yet, generate one and store it.
    Safe to call as a background task (fire-and-forget)."""
    db = get_db()
    if db is None or not session_id:
        return
    try:
        doc = await db.chat_sessions.find_one(
            {"session_id": session_id, "user_id": user_id},
            {"_id": 0, "title": 1, "turns": 1},
        )
        if not doc:
            return
        if doc.get("title"):
            return
        if len(doc.get("turns") or []) < 2:
            return
        title = await _generate_title(first_user_msg)
        if not title:
            return
        await db.chat_sessions.update_one(
            {"session_id": session_id, "user_id": user_id},
            {"$set": {"title": title}},
        )
        logger.info("titled session %s…: %r", session_id[:8], title)
    except Exception as e:
        logger.warning("_maybe_set_title failed: %r", e)


async def _regenerate_without_recall(
    *, prompt: str, jwt_token: str, extra_sys_no_council: str,
    max_iters: int, session_id: Optional[str], user_id: str,
    project_id: Optional[str], mode: str, task_type: Optional[str],
    is_founder: bool, bin_ctx,
) -> tuple[str, str]:
    """2026-08-21 — layered defense (d): a quiet, single automatic
    retry BEFORE anything is shown to the user, once a mismatch is
    detected. Founder observed manually re-asking the SAME question
    always produces the correct answer — this automates exactly that,
    with the ORA-Council few-shot block (the leading recall-bleed
    suspect) stripped out of the system prompt for this attempt.

    Deliberately a PLAIN `chat_with_tools` call — no streaming hooks
    (step cards / live invocations) — so it's invisible to the user
    whichever specialized branch (mode D debugger, casual gateway,
    advisor, etc.) produced the original mismatched turn. Goal is a
    correct answer to the actual question, not replaying the exact
    original pipeline.

    Returns (content, provider); NEVER raises — caller treats a
    failure the same as "retry still mismatched"."""
    try:
        result = await chat_with_tools(
            prompt=prompt,
            jwt_token=jwt_token,
            system=(extra_sys_no_council + "\n\n" if extra_sys_no_council else None),
            max_iters=max_iters,
            session_id=session_id,
            mongo_client=None,
            user_id=user_id,
            project_id=project_id,
            mode=mode,
            task_type=task_type,
            is_founder=is_founder,
            bin_ctx=bin_ctx,
        )
        return (result.get("content", "") or ""), (result.get("provider", "") or "")
    except Exception as e:
        logger.warning("chat.confidence_retry: regeneration call itself failed: %r", e)
        return "", ""


def _strip_council_block(extra_sys: str, council_block: str) -> str:
    """Best-effort removal of the exact ORA-Council few-shot block
    from an already-assembled `extra_sys` string, for the retry
    attempt. Falls back to the original string unchanged if the exact
    substring isn't found (never raises, never blocks the retry)."""
    if not council_block:
        return extra_sys
    return extra_sys.replace(council_block + "\n\n", "", 1)


async def _persist_turn(user_id: str, session_id: str, user_prompt: str,
                        assistant_reply: str, provider: str,
                        watchdog: Optional[dict] = None,
                        project_id: Optional[str] = None,
                        shipped_task_id: Optional[str] = None,
                        steps: Optional[list] = None,
                        low_confidence: bool = False,
                        ship_suppressed: bool = False) -> None:
    """Append user+assistant turns to db.chat_sessions, capped at 40 turns.
    Tags the session with the project it belongs to (None == Home/global).
    Iter 51 — when `shipped_task_id` is set (e.g. Mode D→C auto-handoff),
    it's pinned on the assistant turn so a refresh keeps the live progress
    card rendered (same contract as /chat/turn/shipped).
    Chat UX #4 (Tier 1) — when `steps` is a non-empty list of the SSE
    {type:"step"} frames emitted during this turn, pin it on the
    assistant turn too so GET /chat/history returns it verbatim and a
    page refresh keeps the "📖 Reading repo… ✍️ Writing files…" progress
    trail visible instead of it vanishing (it previously lived only in
    the in-memory `messages` state)."""
    db = get_db()
    if db is None or not session_id:
        return
    now = time.time()
    preview = (assistant_reply or "").strip()[:120] or (user_prompt or "")[:120]
    assistant_turn = {
        "role": "assistant", "content": assistant_reply,
        "ts": now, "provider": provider,
    }
    if watchdog:
        assistant_turn["watchdog"] = watchdog
    if shipped_task_id:
        assistant_turn["shipped_task_id"] = shipped_task_id
    if steps:
        # Cap so a runaway tool-loop can't bloat the session doc.
        assistant_turn["steps"] = steps[-40:]
    # 2026-08-21 — Confidence Badge: pin the mismatch-mitigation flag
    # on the assistant turn so GET /chat/history round-trips it and a
    # page refresh still shows the "low confidence" badge (founder ask
    # — a way to spot recurrences of the cold-start mismatch at a glance).
    if low_confidence:
        assistant_turn["low_confidence"] = True
    # 2026-08-22 — Ship-suppressed note: pinned only when a REAL
    # ```aurem-handoff fence (the thing that renders "Ship via CTO")
    # was suppressed by the confidence gate — narrower than the
    # generic low_confidence flag, see services/response_confidence.py.
    if ship_suppressed:
        assistant_turn["ship_suppressed"] = True
    set_on_insert = {
        "session_id": session_id,
        "user_id": user_id,
        "created_at": now,
        "project_id": project_id,
    }
    set_fields = {
        "updated_at": now,
        "last_message": preview,
    }
    try:
        await db.chat_sessions.update_one(
            {"session_id": session_id, "user_id": user_id},
            {
                "$setOnInsert": set_on_insert,
                "$set": set_fields,
                "$push": {
                    "turns": {
                        "$each": [
                            {"role": "user", "content": user_prompt, "ts": now},
                            assistant_turn,
                        ],
                        # Iter 329 · Chat-history B3 fix — write cap
                        # bumped -40 → -200. GET /chat/history reads
                        # last 100 turns, but the old write cap held
                        # only 40, so any long-running project or
                        # loop-heavy session (loop emits 8-15 turns
                        # per run) hit the ceiling in 3-5 runs and
                        # older turns silently vanished. Real repro
                        # confirmed via founder's live inspection:
                        # messages.length was EXACTLY 40 (the cap
                        # value). Cap now comfortably exceeds the
                        # read window; storage impact at ~500B/turn
                        # × 200 = ~100KB/session doc, safe under the
                        # 16MB Mongo doc limit at any realistic
                        # usage. Read slice stays at 100 (chat.py
                        # line 2940) so we hold headroom without
                        # rendering everything.
                        "$slice": -200,
                    }
                },
            },
            upsert=True,
        )
    except Exception as e:
        logger.warning("persist_turn failed: %r", e)


def _build_failed_followup(
    original: str, err: str, files: list[str],
    sha: Optional[str] = None, push_failed: bool = False,
    verify_failed: bool = False,
) -> str:
    """Deterministic — no LLM. Fail-fast, fail-honest.

    2026-08-26 · Ship/Commit Robustness — Verification-honesty applied
    to the SHIP path itself: `sha` being present means a commit
    object genuinely exists, so "nothing was committed" is a LIE in
    that case. Three distinct, truthful outcomes:
      - push_failed  → a commit exists (by SHA) but never reached the
                        branch (ref-update/push rejected).
      - verify_failed→ the commit WAS pushed to the branch, but
                        post-push verification couldn't confirm the
                        content — tell the user to check manually,
                        don't call it a plain failure.
      - neither       → the original "nothing was committed" case is
                        still accurate and stays unchanged.
    """
    if sha and push_failed:
        bits = [f"⚠️ **Committed but push FAILED** — commit `{sha[:7]}` "
                f"was created but never reached the branch history.\n"]
    elif sha and verify_failed:
        bits = [f"⚠️ **Pushed `{sha[:7]}`, but I couldn't confirm the "
                f"content landed correctly.** Please double-check the "
                f"file(s) below manually.\n"]
    else:
        bits = ["❌ Task failed — nothing was committed.\n"]
    if err:
        snippet = err[:400] + ("…" if len(err) > 400 else "")
        bits.append(f"**Error:** `{snippet}`\n")
    if files:
        bits.append("**Files I tried to touch:** "
                    + ", ".join(f"`{f}`" for f in files[:6]) + "\n")
    bits.append(
        "Want me to retry with a smaller scope? Or paste the exact "
        "error / steps to reproduce and I'll diagnose it in Mode D first."
    )
    return "".join(bits)


def _build_blocked_followup(
    original: str, blocked_reason: str, blocked_paths: list[str],
) -> str:
    """Deterministic — no LLM. A guard firing correctly is NEVER a
    failure — this is the SUCCESS path for a guard doing its job.

    2026-08-26 · Ship/Commit Robustness — the test-file lock (Iter
    286) blocking a fix is exactly this: the fix is ready, it's just
    waiting on a human. Must never render red / "failed" / "nothing
    was committed" — that collapse is what made a correctly-working
    guard look like a crash to the user."""
    bits = ["✅ **Fix ready — awaiting your approval.**\n"]
    if blocked_reason == "test_file_lock":
        bits.append(
            "This edit touches a test file, so I'm holding it for "
            "review before shipping (test-file changes need a human "
            "look, by design).\n"
        )
    elif blocked_reason:
        bits.append(f"**Held for review:** {blocked_reason}\n")
    if blocked_paths:
        bits.append("**File(s):** "
                    + ", ".join(f"`{p}`" for p in blocked_paths[:6]) + "\n")
    bits.append(
        "**Approve in Loop mode** to ship it, or reply here if you'd "
        "like changes first."
    )
    return "".join(bits)


def _build_done_fallback(original: str, summary: str,
                         files: list[str], sha: Optional[str]) -> str:
    """Used when the follow-up LLM call itself fails — never block the UX."""
    file_list = ", ".join(f"`{f}`" for f in files[:8]) or "_no files reported_"
    return (
        f"✅ **Done — `{sha or 'commit'}` pushed.**\n\n"
        f"**Changed:** {file_list}\n\n"
        f"**Summary:** {summary or 'See diff for details.'}\n\n"
        "**Verify it:** pull the latest, restart, and re-trigger the "
        "original flow. Reply here if anything's still off — I'll "
        "diagnose without burning another quota."
    )


_FOLLOWUP_SYS = (
    "You are ORA, an AI engineering lead. A code task just completed. "
    "Write a SHORT closing message (max 6 short lines) to the user with "
    "EXACTLY this structure:\n\n"
    "Line 1: ✅ one-line summary of what was actually changed.\n"
    "Line 2: **Files:** `path1`, `path2` (max 5, real names only).\n"
    "Line 3: **Likely resolves original ask?** Yes / Partially / No "
    "— with a one-clause reason. Be honest. If the commit feels off-"
    "scope or generic vs. the user's ask, say 'Partially' or 'No'.\n"
    "Line 4: **Verify it:** one concrete step the user can take in "
    "<30 seconds to confirm (a curl, a button to click, a page to open, "
    "etc.). Be specific.\n"
    "Line 5 (optional): **Next:** one specific follow-up if needed.\n\n"
    "Rules: no fluff, no 'great question', no emoji except the leading "
    "✅. Plain English. No markdown headers. No code fences. Keep total "
    "under 90 words."
)


async def _generate_done_followup(original: str, summary: str,
                                  files: list[str],
                                  sha: Optional[str]) -> str:
    """Single ~320-token DeepSeek call. Strict format, low temperature.
    The system prompt does the heavy lifting — keep the user message
    tight so the model can't wander."""
    file_list = ", ".join(files[:8]) if files else "(none reported)"
    user_msg = (
        f"ORIGINAL USER ASK:\n{original or '(missing)'}\n\n"
        f"COMMIT SHA: {sha or '(none)'}\n"
        f"FILES CHANGED: {file_list}\n"
        f"COMMIT SUMMARY: {summary or '(none)'}\n\n"
        "Write the closing message now, following the structure exactly."
    )
    res = await call_llm_with_meta(
        system=_FOLLOWUP_SYS,
        user=user_msg,
        max_tokens=320,
        mode="chat",
    )
    text = (res.get("content") or "").strip()
    if not text:
        return _build_done_fallback(original, summary, files, sha)
    return text
