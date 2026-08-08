"""
services/ora_chat/intent_router.py — Phase 3 · Feb 2026

Two-layer intent classification for /ora chat messages. Answers ONE
question: does the founder want a self-contained code SAMPLE they
can eyeball in the preview drawer, or an actual EDIT applied to the
repo (which should trigger the AUREM loop engine)?

Contract from the Phase 3 brief:
  Layer 1 — deterministic regex pre-filter (fast, zero LLM cost,
             high-precision patterns only).
  Layer 2 — LLM classifier fallback when regex returns UNKNOWN
             (constrained output: one of two label words).

Return shape everywhere: (intent, source, meta) where
  intent  ∈ {"PREVIEW_ONLY", "CODE_CHANGE", "UNKNOWN"}
  source  ∈ {"regex", "llm", "empty"}
  meta    = classifier-specific details (model, temperature, error, …).

Design rules:
  · CODE_CHANGE wins ties (imperative "commit / apply / update the
    repo" beats exploratory "show me / preview"). Founder review:
    it's cheaper to accidentally offer a loop CTA than to lose an
    intended code change to a preview-only reply.
  · LLM output MUST be one of two exact label strings — anything
    else collapses to UNKNOWN so downstream code never sees garbage.
  · The regex list is high-precision on purpose; low-recall is fine
    because Layer 2 catches the rest. Adding fuzzy verbs here would
    make the whole router unpredictable.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

INTENT_PREVIEW      = "PREVIEW_ONLY"
INTENT_CODE_CHANGE  = "CODE_CHANGE"
INTENT_CASUAL       = "CASUAL_CHAT"
INTENT_UNKNOWN      = "UNKNOWN"

ALL_INTENTS = (INTENT_PREVIEW, INTENT_CODE_CHANGE, INTENT_CASUAL, INTENT_UNKNOWN)


# ── Layer 1 · Deterministic regex pre-filter ────────────────────────
# High-precision patterns only.  If NOTHING matches → return UNKNOWN
# and let the LLM decide.  Do NOT add fuzzy verbs here.
_PREVIEW_STRONG_RE = [re.compile(p, re.I) for p in [
    r"\b(?:show|generate|draft|sketch|mock(?:up)?)\s+(?:me\s+)?(?:a|an|some|the)?\s*(?:sample|example|snippet|preview|mock(?:up)?|prototype|demo|widget|card|component|button|form|landing|page|hero|dashboard|design|layout|ui)\b",
    r"\bwhat\s+would\s+(?:that|it|this|a|an|the)\s+.*look\s+like\b",
    r"\bhow\s+would\s+(?:the|a|an|this|that)\s+.+\s+look\b",
    r"\b(?:can|could)\s+you\s+(?:show|preview|mock|sketch|draft)\b",
    r"\bpreview\s+only\b",
    r"\bjust\s+(?:the|a|an)\s+(?:html|jsx|tsx|component|snippet|example|sample|mock(?:up)?)\b",
    r"\b(?:no|don't|dont|do\s+not)\s+(?:commit|push|deploy|ship|change|modify|edit)\b",
]]

_CODE_CHANGE_STRONG_RE = [re.compile(p, re.I) for p in [
    r"\b(?:commit|push|merge|deploy|ship|open\s+a\s+pr|open\s+a\s+pull\s+request)\b",
    r"\b(?:apply|save)\s+(?:the|these|this|those)?\s*(?:change|edit|patch|fix|update)s?\b",
    r"\b(?:update|modify|edit|change|fix|refactor|rename|rewrite|patch)\s+(?:the|this|that|my|our|src/|app/|frontend/|backend/|/app)\b",
    r"\bin\s+(?:the|my|our)\s+(?:repo|codebase|code[\s-]?base|repository|source)\b",
    r"\bactually\s+(?:change|edit|update|modify|fix|patch|apply)\b",
    r"\bmake\s+it\s+(?:live|real|permanent|stick|so)\b",
    r"\b(?:start|run|kick\s+off)\s+(?:a|the)\s+loop\b",
    # File-path pattern — mentions of `path/to/file.py`, `frontend/x.jsx`,
    # etc. usually mean "touch this file".
    r"\b[a-z0-9_./-]+\.(?:py|jsx?|tsx?|css|html|md|json|ya?ml|toml|env)\b",
]]


def classify_intent_regex(text: str) -> tuple[str, list[str]]:
    """Layer 1 pre-filter.

    Returns (intent, matched_pattern_names_or_snippets).  When both
    families match, CODE_CHANGE wins (see module docstring).
    """
    if not text or not text.strip():
        return INTENT_UNKNOWN, []
    matches_change  = [r.pattern for r in _CODE_CHANGE_STRONG_RE if r.search(text)]
    matches_preview = [r.pattern for r in _PREVIEW_STRONG_RE     if r.search(text)]
    if matches_change:
        return INTENT_CODE_CHANGE, matches_change
    if matches_preview:
        return INTENT_PREVIEW, matches_preview
    return INTENT_UNKNOWN, []


# ── Layer 2 · Constrained LLM classifier ────────────────────────────
_LLM_SYSTEM_PROMPT = (
    "You are an intent classifier for the ORA chat used by AUREM's "
    "founder. Read the user's message and reply with EXACTLY ONE of "
    "these THREE label words, and nothing else:\n"
    "  PREVIEW_ONLY  — the user is exploring, asking to see, generate, "
    "                   sample, mock up, or preview a code snippet or "
    "                   UI element. No repository edit is being requested.\n"
    "  CODE_CHANGE   — the user is asking you to modify their real "
    "                   repository: commit, apply, push, update a file, "
    "                   fix a bug in the codebase, kick off a loop run, "
    "                   open a PR, ship, deploy, etc. Short imperative "
    "                   confirmations that clearly continue a prior "
    "                   edit request ('fix it', 'do it', 'ship it', "
    "                   'go ahead', 'make that change') ALSO count as "
    "                   CODE_CHANGE — they are the user telling you to "
    "                   proceed with a code action, not casual chat.\n"
    "  CASUAL_CHAT   — the user is greeting, thanking, small-talk, or "
    "                   their message contains NO concrete code/"
    "                   preview/edit request and NO short imperative "
    "                   confirmation of a code action. Includes: 'hi', "
    "                   'hello', 'thanks', 'ok', 'yo', 'what's up', "
    "                   'can you help' (unspecific), 'what can you "
    "                   do?', 'test' (single-word probe).\n"
    "When a message is ambiguous between CODE_CHANGE and CASUAL_CHAT "
    "(e.g. 'do it', 'go ahead' with no visible prior context), prefer "
    "CODE_CHANGE — false-CODE_CHANGE just offers an extra loop CTA "
    "the user can ignore, whereas false-CASUAL swallows a real edit "
    "intent silently. Same tie-break rule as the regex layer.\n"
    "Reply with the label ONLY. No punctuation, no explanation, no code."
)


def _sanitize_llm_label(raw: str) -> str:
    """Collapse loose LLM output to one of ALL_INTENTS."""
    if not raw:
        return INTENT_UNKNOWN
    stripped = raw.strip().upper()
    # Strip surrounding quotes/backticks/periods the LLM might add.
    stripped = stripped.strip("`\"'. \n\t")
    # Only accept exact matches — no substring / fuzzy match, so the
    # classifier can never accidentally promote a fabricated label.
    if stripped == INTENT_PREVIEW:
        return INTENT_PREVIEW
    if stripped == INTENT_CODE_CHANGE:
        return INTENT_CODE_CHANGE
    if stripped == INTENT_CASUAL:
        return INTENT_CASUAL
    return INTENT_UNKNOWN


async def classify_intent_llm(
    text: str,
    *,
    one_shot_fn: Callable[..., Awaitable[tuple[str, dict, Any]]],
    model: str = "google/gemini-2.5-flash",
    temperature: float = 0.0,
    max_tokens: int = 8,
) -> tuple[str, dict]:
    """Layer 2 · LLM fallback.  Uses whatever `one_shot` provider the
    caller injects — matches the shape used elsewhere in ora_chat so
    tests can mock it without spinning a real provider.

    Returns (intent, meta).  On error → UNKNOWN with `error` populated.
    """
    if not text or not text.strip():
        return INTENT_UNKNOWN, {"reason": "empty_text"}
    messages = [
        {"role": "system", "content": _LLM_SYSTEM_PROMPT},
        {"role": "user",   "content": text[:4000]},   # cap to control cost
    ]
    try:
        raw, usage, err = await one_shot_fn(
            model=model,
            messages=messages,
            temperature=temperature,
            top_p=1.0,
            presence_penalty=0.0,
            max_tokens=max_tokens,
        )
    except TypeError:
        # Test stubs may accept a smaller signature — retry without
        # the sampling knobs so pytest fixtures keep working without
        # having to mirror OpenRouter's schema.
        try:
            raw, usage, err = await one_shot_fn(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            logger.warning("intent_router: LLM classify raised (%r)", e)
            return INTENT_UNKNOWN, {"error": str(e)[:200]}
    except Exception as e:
        logger.warning("intent_router: LLM classify raised (%r)", e)
        return INTENT_UNKNOWN, {"error": str(e)[:200]}
    if err:
        return INTENT_UNKNOWN, {"error": str(err)[:200], "model": model}
    label = _sanitize_llm_label(raw or "")
    return label, {
        "model": model,
        "temperature": temperature,
        "raw": (raw or "")[:120],
        "usage": usage or {},
    }


# ── Public API · Two-layer orchestrator ────────────────────────────
async def classify_intent(
    text: str,
    *,
    one_shot_fn: Optional[Callable[..., Awaitable[tuple[str, dict, Any]]]] = None,
    llm_model: str = "google/gemini-2.5-flash",
) -> dict:
    """Full two-layer classify.

    Returns a dict:
        {
          "intent":  "PREVIEW_ONLY"|"CODE_CHANGE"|"UNKNOWN",
          "source":  "regex"|"llm"|"empty",
          "matches": [regex-pattern-strings]  (regex layer only),
          "meta":    { … }                    (llm layer only),
        }
    """
    if not text or not text.strip():
        return {"intent": INTENT_UNKNOWN, "source": "empty",
                "matches": [], "meta": {}}
    r_intent, r_matches = classify_intent_regex(text)
    if r_intent != INTENT_UNKNOWN:
        return {"intent": r_intent, "source": "regex",
                "matches": r_matches, "meta": {}}
    if one_shot_fn is None:
        # No LLM available (e.g. tests, or providers wired down) —
        # safe default: UNKNOWN.  Callers treat UNKNOWN as "don't
        # over-index on any behaviour switch".
        return {"intent": INTENT_UNKNOWN, "source": "regex",
                "matches": [], "meta": {"reason": "llm_unavailable"}}
    llm_intent, llm_meta = await classify_intent_llm(
        text, one_shot_fn=one_shot_fn, model=llm_model,
    )
    return {"intent": llm_intent, "source": "llm",
            "matches": [], "meta": llm_meta}
