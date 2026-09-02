"""services/output_guard.py — "Show the Outcome, Never the Engine" P1.

The mechanical NET under the plain-English prompt instruction (see
`routers/chat.py::PLAIN_ENGLISH_EXPLAIN_CONTRACT`). The instruction is
the SHAPE layer; this is the GUARANTEE layer — it runs on the model's
actual output so a leak or an over-long answer can't slip through even
when the model doesn't fully follow the prompt.

Scope: explain/advisory answers ONLY (callers gate this on the same
`classify_intent()=="A"` + flag check already used for the prompt
instruction). Never applied to ship/confirm content — those keep
full file:line detail by construction (this module is never imported
by the ship/confirm card data path).
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

LENGTH_CAP_TOKENS = 500

# Deliberately narrow, named tokens — not a broad heuristic that could
# eat legitimate prose. Each pattern targets one specific machinery
# leak class the founder identified.
#
# 2026-08-27 · P5 split this into TWO tiers (Journey/Intent-Grounding
# build round). The ORIGINAL list below (adviser-council jargon, DB
# collection names, framework names) is INTENTIONALLY still
# explain-mode-only (`plain_english_contract_active`) — for a
# developer-facing reply, "backend/services/orchestrator.py" is
# exactly the useful information, not a leak. Redacting it universally
# was tried mid-round and immediately regressed real chat replies
# (e.g. "let me fetch the full `a project file`" instead of a real
# path) — caught via live E2E testing, not a unit test, which is why
# it's called out here explicitly. `_UNIVERSAL_LEAK_PATTERNS` below is
# the genuinely-always-a-bug tier (raw iteration counters, mode
# letters, raw tracebacks, raw booleans-as-status) that applies to
# EVERY user regardless of the explain-mode flag.
#
# 2026-08-30 · M3 — the bare-file-path entry that used to live in this
# list moved to its own `_FILE_PATH_PATTERN` below (still applied on
# every path through `strip_machinery_leak`, unconditionally — the
# ONLY thing that changed is it now has a per-match, user-named-file
# exemption instead of being a blanket strip).

# 2026-08-27 · P5 — genuinely-always-a-bug leaks, confirmed from a live
# transcript, that apply to EVERY user (not gated behind
# plain_english_contract_active). The aurem-handoff fence itself is
# deliberately NOT included here — this guard never even runs on
# ship/mutation content at all (see the `"aurem-handoff" not in
# content` gate in routers/chat.py); that specific leak is fixed at
# DISPLAY time instead (MessageBubble.jsx's stripHandoffFenceForDisplay),
# since the raw fence must survive server-side for the Ship button.
_UNIVERSAL_LEAK_PATTERNS = [
    (re.compile(r"\bvia\s+[\w.\-]+\s+(?:true|false)\b", re.IGNORECASE), ""),
    (re.compile(r"\bIter\s*\d+[a-z]?\b", re.IGNORECASE), "internally"),
    (re.compile(r"\bMode\s+[A-Z]\b(?!\w)"), "that flow"),
    (re.compile(r"\bverify-agent\b", re.IGNORECASE), "the review step"),
    (re.compile(r"\be2b\b", re.IGNORECASE), "the sandbox"),
    (re.compile(r"\bTraceback \(most recent call last\)[\s\S]*", re.IGNORECASE), ""),
    (re.compile(r"\b[A-Z][a-zA-Z0-9]*(?:Error|Exception)\b:\s*.*"), "an internal error"),
    # M3 (2026-08-30) — secret/token-shaped strings, ALWAYS redacted
    # regardless of the M3 user-named-file relaxation below (a value
    # that merely LOOKS like a token/secret/credential is never safe
    # to echo just because a filename elsewhere in the same reply was
    # user-named — these are two independent checks). Same shapes the
    # persona (AUREM_CTO_PERSONA rule 4) already tells the model to
    # self-censor; this is the mechanical net behind that instruction,
    # which previously didn't exist in this module at all.
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted credential]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"), "[redacted credential]"),
    (re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b"), "[redacted credential]"),
    (re.compile(r"\bmongodb(?:\+srv)?://[^\s\"'<>]+"), "[redacted connection string]"),
    # 2026-09-02 — leaked AI stage-direction / self-narration (e.g.
    # "(Silently checks the page first)", "*checking the file now*")
    # — internal planning narration that should never reach the
    # business-owner-facing reply. Scoped to parenthetical/asterisk-
    # wrapped clauses naming the AI's own silent action on a page/
    # file/code/repo, so normal prose about "the page" is untouched.
    (re.compile(
        r"[\(\*]\s*(?:silently\s+|quietly\s+|first\s+)*"
        r"(?:checks?|checking|looks?|looking|glances?|glancing|"
        r"takes?\s+a\s+look|reads?|reading|reviewing|reviews?)\s+"
        r"(?:at\s+)?(?:the\s+)?(?:page|file|code|repo|codebase|site)"
        r"[^)\*\n]*[\)\*]",
        re.IGNORECASE,
    ), ""),
    # 2026-09-09 — fabricated tool-call/tool-result narration (founder
    # live-caught in production: the model wrote a bracketed
    # "[Tool note: Checking ... via <invented tool>()...]" followed by
    # a "[Tool result: ... update ID: a1b2c3d ...]" block — the SAME
    # generic fake hex ID repeated across unrelated requests, i.e. an
    # invented tool call AND an invented result, never actually run.
    # This is a best-effort mechanical net (system-prompt guard in
    # `services/ora_context.py`'s ORA ABSOLUTE BOUNDARY rule 7/5/6 is
    # the PRIMARY fix) — narrow bracket pattern only, so it can't eat
    # legitimate prose that merely mentions "tool" or "result". The
    # note+result combo is matched FIRST (single replacement, not two)
    # so a paired fabrication doesn't leave a duplicated disclaimer.
    (re.compile(
        r"\[\s*Tool\s+note\s*:.*?\]\s*\[\s*Tool\s+result\s*:.*?\]"
        r"|\[\s*Tool\s+(?:note|result)\s*:.*?\]",
        re.IGNORECASE | re.DOTALL,
    ), "(I have not actually run that check yet — I can check now if you'd like.)"),
]

# M3 fix (2026-08-30, founder-directed) — the bare-file-path pattern
# used to be a blanket strip (see the 2026-08-27 comment above this
# module documenting the P5 regression it already caused once). Split
# out to its own named pattern so `strip_machinery_leak` can apply a
# context-aware exemption: a file the USER explicitly named earlier in
# THIS conversation is not a leak — ORA is allowed to call it by that
# exact name back. Any OTHER path (one the model surfaces on its own,
# never mentioned by the user) is still redacted, unchanged.
_FILE_PATH_PATTERN = (
    re.compile(r"\b[\w\-]+(?:/[\w\-]+)+\.\w{1,5}\b(?::\d+)?"
               r"|\b[\w\-]+\.(?:py|jsx?|tsx?|md|txt|json|ya?ml|toml|"
               r"cfg|ini|env|lock)\b(?::\d+)?"
               r"|\B\.[a-zA-Z][\w\-]*\b"),
    "a project file",
)

_MACHINERY_LEAK_PATTERNS = [
    # internal review-process jargon
    (re.compile(r"\b\d+[- ]adviser council\b", re.IGNORECASE), "internal review process"),
    (re.compile(r"\bchairman\b", re.IGNORECASE), "lead reviewer"),
    (re.compile(r"\btool[_ ]calls?\b|\bfunction[_ ]call\b", re.IGNORECASE), ""),
    # known DB collection names
    (re.compile(r"\b(?:ora_council_logs|loop_sessions|loop_locks|"
                r"loop_backups|loop_plans|loop_failures|cto_projects|"
                r"dev_users|feature_flags|ora_hallucination_log)\b"),
     "the database"),
    # framework/technical jargon
    (re.compile(r"\bpydantic\b|\bFastAPI\b|\basyncio\b|\bJWT\b|\bOODA\b|"
                r"\bsupervisorctl\b|\bMongoDB\b|\bmotor\b|\buvicorn\b",
                re.IGNORECASE), "the system"),
]


def extract_named_files(text: str) -> set[str]:
    """M3 fix — pull every file-path-looking token out of the USER'S
    OWN message this turn. Used to build the exemption set so ORA's
    reply can call a user-named file by its real name instead of the
    generic 'a project file' placeholder. Same shape as
    `_FILE_PATH_PATTERN` (reused, not a second regex to drift)."""
    if not text:
        return set()
    return {m.group(0).split(":")[0] for m in _FILE_PATH_PATTERN[0].finditer(text)}


def strip_machinery_leak(
    text: str, *, universal_only: bool = False,
    user_named_files: set[str] | None = None,
) -> tuple[str, bool]:
    """Returns (clean_text, was_stripped).

    `universal_only=True` (routers/chat.py's default, all-users path)
    applies ONLY `_UNIVERSAL_LEAK_PATTERNS` — the always-a-bug tier.
    `universal_only=False` (explain-mode path) applies BOTH tiers —
    the original "hide internal machinery from a non-technical
    explain-mode reply" behavior, unchanged.

    `user_named_files` (M3, 2026-08-30) — a set of exact file-path
    strings the USER already named this turn. Only relevant when
    `universal_only=False` (the bare-file-path redaction was, and
    still is, explain-mode-only — unchanged scope, M3 only adds the
    exemption within that same existing scope). The redaction is
    skipped for any match equal to one of these (case-insensitive) —
    ORA may reference a file the user themselves already brought up
    by its real name. Every OTHER path (anything NOT in this set) is
    still redacted, unchanged."""
    original = text
    user_named_lower = {f.lower() for f in (user_named_files or set())}

    def _file_path_sub(m: re.Match) -> str:
        matched = m.group(0)
        base = matched.split(":")[0]
        if base.lower() in user_named_lower:
            return matched  # user already named this file — keep it real
        return _FILE_PATH_PATTERN[1]

    if not universal_only:
        text = _FILE_PATH_PATTERN[0].sub(_file_path_sub, text)
    patterns = _UNIVERSAL_LEAK_PATTERNS if universal_only else (
        _MACHINERY_LEAK_PATTERNS + _UNIVERSAL_LEAK_PATTERNS
    )
    for pattern, replacement in patterns:
        text = pattern.sub(replacement, text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text, (text != original)


async def enforce_length_cap(text: str, *, max_tokens: int = LENGTH_CAP_TOKENS) -> tuple[str, bool]:
    """If `text` is over the cap, run ONE capped re-summarize pass.

    Lossy-safe: compresses to ~300 words, keeps the closing opt-in
    "want the technical detail" line if present, never cuts
    mid-sentence (the model is instructed to finish sentences; this
    call is capped so it cannot loop or run away).
    Returns (final_text, was_capped).
    """
    approx_tokens = len(text.split()) * 1.33
    if approx_tokens <= max_tokens:
        return text, False
    try:
        from services.llm._meta import call_llm_with_meta
        result = await call_llm_with_meta(
            "Compress the following founder-facing explanation to under "
            "300 words. Keep it plain-English (no jargon, no file paths, "
            "no code). Keep the final opt-in line about technical detail "
            "if one exists. Never cut a sentence off mid-way.",
            text, max_tokens=450, mode="chat",
        )
        compressed = (result.get("content") or "").strip()
        if compressed:
            return compressed, True
    except Exception as e:                                    # noqa: BLE001
        logger.warning("output_guard: length-cap re-summarize failed: %r", e)
    return text, False


async def apply_output_guard(text: str) -> dict:
    """Runs both nets. Returns {text, leak_stripped, length_capped, ref_id}."""
    from core.errors import new_ref_id
    clean_text, leak_stripped = strip_machinery_leak(text)
    final_text, length_capped = await enforce_length_cap(clean_text)
    ref_id = new_ref_id() if (leak_stripped or length_capped) else None
    if leak_stripped or length_capped:
        logger.info(
            "output_guard: leak_stripped=%s length_capped=%s ref_id=%s",
            leak_stripped, length_capped, ref_id,
        )
    return {
        "text": final_text,
        "leak_stripped": leak_stripped,
        "length_capped": length_capped,
        "ref_id": ref_id,
    }
