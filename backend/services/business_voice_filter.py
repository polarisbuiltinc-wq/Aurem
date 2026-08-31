"""
services/business_voice_filter.py — R1 (2026-08-31)

DETERMINISTIC user-facing reply filter, same discipline as
hallucination_guard.py / citation_guard.py / response_confidence.py:
NO LLM in this layer. The model proposes a reply; this filter
guarantees a non-technical business owner never sees a raw filename
(with extension) or a developer term in what they read.

Scope: ONLY the string returned/streamed to the USER. Tool-calls (e.g.
`read_repo_file`, `write_repo_file`) keep the REAL file path exactly as
the model chose it — this filter never touches `args`/tool-call
payloads, only final user-facing `content`. Breaking that separation
would break ORA's ability to target the right file.

Callers: routers/chat.py — applied at every point `content` is about
to be returned to the user, gated on `not body.ora_panel` (the ORA
Admin/technical view intentionally keeps real filenames — see
ORA_PANEL_TONE in chat.py).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from functools import lru_cache

_MAP_PATH = Path(__file__).resolve().parent.parent / "config" / "business_voice_map.json"

# Same file-path signal response_confidence.py uses for its own
# _FILE_PATH_RE, kept in sync deliberately (both must recognize the
# same universe of "this looks like a filename").
_FILENAME_RE = re.compile(
    r"\b[\w\-./]+\.(?:py|jsx?|tsx?|json|md|ya?ml|css|s?css|html?|txt|sql|env|sh)\b",
    re.IGNORECASE,
)

_PAGE_NAME_HINTS = (
    (re.compile(r"index|home|landing", re.IGNORECASE), "your homepage"),
    (re.compile(r"about", re.IGNORECASE), "your About page"),
    (re.compile(r"contact", re.IGNORECASE), "your Contact page"),
    (re.compile(r"footer", re.IGNORECASE), "the bottom of your page"),
    (re.compile(r"header|nav", re.IGNORECASE), "the top of your page"),
    (re.compile(r"pricing|price", re.IGNORECASE), "your pricing page"),
)
_DEFAULT_PAGE_TERM = "your page"


@lru_cache(maxsize=1)
def _load_map() -> list[tuple[re.Pattern, str]]:
    raw = json.loads(_MAP_PATH.read_text())
    pairs = [(k, v) for k, v in raw.items() if not k.startswith("_")]
    # Longest phrase first so "pull request" wins over a lone "pull".
    pairs.sort(key=lambda kv: len(kv[0]), reverse=True)
    return [(re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE), repl)
            for term, repl in pairs]


def _humanize_filename(match: re.Match) -> str:
    stem = match.group(0)
    for pattern, label in _PAGE_NAME_HINTS:
        if pattern.search(stem):
            return label
    return _DEFAULT_PAGE_TERM


def _preserve_case(original: str, replacement: str) -> str:
    if original[:1].isupper() and not replacement[:1].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


_AN_FIX_RE = re.compile(r"\b(a)\s+(?=[aeiouAEIOU])", re.IGNORECASE)

# 2026-08-31 (Gate 1 T4 testing_agent finding) — "I don't have access
# to X" is banned dev/system framing regardless of whether the reply
# is a design ask (design_refusal_guard.py only fires on design asks;
# this phrasing shows up on ANY "your site isn't connected yet" reply,
# so it needs to be caught here, universally).
_ACCESS_TO_RE = re.compile(
    r"i (don'?t|do not|didn'?t) (currently )?have access to\b",
    re.IGNORECASE,
)


def _fix_a_an(text: str) -> str:
    """Cosmetic-only pass after term substitution: 'a update' -> 'an
    update'. Never touches correctness, only grammar of the rewritten
    business-owner-facing sentence."""
    def _repl(m: re.Match) -> str:
        return ("An " if m.group(1)[0].isupper() else "an ")
    return _AN_FIX_RE.sub(_repl, text)


def filter_for_business_owner(text: str) -> str:
    """Deterministic — same input always produces the same output, no
    LLM call. Strips filenames-with-extension (replaced with a plain
    page reference) and rewrites dev terms to business-owner terms."""
    if not text:
        return text
    out = _FILENAME_RE.sub(_humanize_filename, text)
    out = _ACCESS_TO_RE.sub(lambda m: _preserve_case(m.group(0), "I can't see"), out)
    for pattern, replacement in _load_map():
        out = pattern.sub(lambda m: _preserve_case(m.group(0), replacement), out)
    return _fix_a_an(out)


def apply_business_voice(ora_panel: bool, content: str) -> str:
    """Convenience wrapper for routers/chat.py's call sites — the ORA
    Admin/technical view (`ora_panel=True`) intentionally keeps real
    filenames and dev terms; the regular (business-owner) chat does
    not. Kept as one function so all sites gate identically (same
    single-surface-drift discipline as apply_no_false_success_guard)."""
    if ora_panel:
        return content
    return filter_for_business_owner(content)


async def apply_business_owner_guards(
    ora_panel: bool, content: str, user_prompt: str = "",
    *, session_id: str = "", user_id: str = "",
) -> str:
    """R1+R2 (2026-08-31) — the SINGLE guard chain every user-facing
    reply passes through, replacing the 4 duplicated call sites this
    used to be spread across in routers/chat.py (code-review finding:
    duplication is a real regression risk for this exact feature — a
    site that drifts out of sync silently re-opens the jargon/dead-end
    bug this whole rework exists to close).

    Order: voice filter -> banned-fallback-phrase strip -> dead-end
    guard -> completeness guard. A real ```aurem-handoff fence (the
    Approve-the-fix button's source) is NEVER touched — R5a regression
    guard, checked FIRST so no caller can forget it.

    Each guard that actually rewrites something also logs a
    self_bug_event (P7) — the model was about to hand the owner a
    jargon leak / dead end / cut-off reply, which is ORA's OWN bug,
    not the owner's website's.
    """
    if not content or "aurem-handoff" in content:
        return content
    content = apply_business_voice(ora_panel, content)
    if ora_panel:
        return content
    from services.bail_reason import strip_banned_fallback_phrases
    from services.no_dead_end_guard import ensure_alternative, has_dead_end
    from services.incomplete_reply_guard import ensure_complete, is_incomplete
    from services import self_bug

    ctx = {"session_id": session_id, "user_id": user_id}
    before = content
    content = strip_banned_fallback_phrases(content, user_prompt)
    if content != before:
        await self_bug.emit("dead_end_leak", before[:300], ctx, source="bail_reason")
    if is_incomplete(content):
        await self_bug.emit("truncated_reply", content[-300:], ctx, source="incomplete_reply_guard")
    content = ensure_complete(content)
    if has_dead_end(content):
        await self_bug.emit("dead_end_leak", content[:300], ctx, source="no_dead_end_guard")
    content = ensure_alternative(content)

    # P8-B/E (2026-08-31) — only checked on an actual design/brand/
    # visual ask (see design_ask_detector.py); a normal fix reply is
    # never touched by this branch, so it can't misfire on unrelated
    # "can't" phrasing.
    from services.design_ask_detector import is_design_ask
    if is_design_ask(user_prompt):
        from services.design_refusal_guard import is_compliant_design_reply, strip_design_refusal
        if not is_compliant_design_reply(content):
            await self_bug.emit("dead_end_leak", content[:300], ctx, source="design_refusal_guard")
            content = strip_design_refusal(content)
    return content
