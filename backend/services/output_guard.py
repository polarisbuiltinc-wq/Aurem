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
_MACHINERY_LEAK_PATTERNS = [
    # internal review-process jargon
    (re.compile(r"\b\d+[- ]adviser council\b", re.IGNORECASE), "internal review process"),
    (re.compile(r"\bchairman\b", re.IGNORECASE), "lead reviewer"),
    (re.compile(r"\bvia\s+[\w.\-]+\s+(?:true|false)\b", re.IGNORECASE), ""),
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
    # bare file paths / module paths (e.g. backend/services/x.py, x.py:42)
    (re.compile(r"\b[\w\-]+(?:/[\w\-]+)+\.\w{1,5}\b(?::\d+)?"
                r"|\b[\w\-]+\.(?:py|jsx?|tsx?)\b(?::\d+)?"), "a project file"),
]


def strip_machinery_leak(text: str) -> tuple[str, bool]:
    """Returns (clean_text, was_stripped)."""
    original = text
    for pattern, replacement in _MACHINERY_LEAK_PATTERNS:
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
