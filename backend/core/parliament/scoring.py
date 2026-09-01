"""
core/parliament/scoring.py — 2026-09-08 Phase 3 god-class split.

Output scoring + type-detection helpers. Moved verbatim out of the
single core/parliament.py (zero logic change) — used by councils.py,
ceo.py, self_heal.py, and parliament.py.
"""
from __future__ import annotations

import re

_REFUSAL_RX = re.compile(
    r"\b(i (?:cannot|can't|won't|will not|am unable to)|"
    r"as an ai|i'm just an ai|i'm sorry, (?:but )?i can(?:not|'t))\b",
    re.IGNORECASE,
)

_FENCE_RX = re.compile(r"^```(?:[a-zA-Z0-9_-]+)?\s*\n([\s\S]*?)\n```\s*$")


def _strip_fences(text: str) -> str:
    """Strip a single outer ```lang … ``` if present (LLMs love these)."""
    if not text:
        return ""
    s = text.strip()
    m = _FENCE_RX.match(s)
    return m.group(1) if m else s


def _score_output(output: str, *, task_type: str = "code_fix",
                  expected_min_chars: int = 50) -> float:
    """Heuristic 0.0-1.0 score for a candidate output."""
    if not output:
        return 0.0
    text = output.strip()
    if _REFUSAL_RX.search(text):
        return 0.0
    if len(text) < expected_min_chars:
        return 0.2
    # Iter 212m-155 — task-type-aware scoring.
    if task_type == "analysis":
        return _score_analysis(text)
    if task_type == "writing":
        return _score_writing(text)
    if task_type == "code_fix":
        markers = ("def ", "class ", "import ", "function ", "const ",
                   "let ", "return ", "{", "}", "from ", "export ")
        hits = sum(1 for m in markers if m in text)
        if hits >= 6:
            return 0.92
        if hits >= 3:
            return 0.78
        if hits >= 1:
            return 0.60
        return 0.30
    return 0.65


# ─────────────────────────────────────────────────────────────────────
#  Iter 212m-155 — structural scoring for Council B (analysis) and
#  Council C (writing).  Returns a 0.0-1.0 float on the same scale as
#  the code scorer so the CEO can compare apples-to-apples.
# ─────────────────────────────────────────────────────────────────────

_NUMBER_RX = re.compile(r"\d+\.?\d*%?")


def _score_analysis(text: str) -> float:
    """Score analysis output by structural quality, not correctness."""
    score = 0.60
    # Has numbers / data points → +0.15
    if _NUMBER_RX.search(text):
        score += 0.15
    # Has clear structure (markdown headers or numbered lists or bullets)
    if any(tok in text for tok in ("## ", "1.", "2.", "- ")):
        score += 0.10
    # Length sanity.
    words = len(text.split())
    if words < 50:
        score -= 0.20
    elif words > 500:
        score -= 0.10
    # Has actionable conclusion.
    action_signals = ("recommend", "suggest", "should",
                      "consider", "next step", "action")
    if any(s in text.lower() for s in action_signals):
        score += 0.15
    return max(0.0, min(1.0, score))


def _score_writing(text: str) -> float:
    """Score writing/copy output by structural quality."""
    score = 0.60
    words = len(text.split())
    if 30 <= words <= 200:
        score += 0.15
    elif words > 300:
        score -= 0.15
    elif words < 20:
        score -= 0.20
    cta_signals = ("reply", "click", "schedule", "book", "call",
                   "reach out", "let me know", "interested", "response")
    if any(s in text.lower() for s in cta_signals):
        score += 0.15
    # Weak opening: starting with "I " bleeds the reader-focus.
    if text.lstrip().startswith("I "):
        score -= 0.10
    personal_signals = ("you", "your", "you're")
    if any(s in text.lower() for s in personal_signals):
        score += 0.10
    return max(0.0, min(1.0, score))


# ─────────────────────────────────────────────────────────────────────
#  GAP 3 — Output-type detection + CEO temperature mapping
# ─────────────────────────────────────────────────────────────────────

CEO_TEMPS: dict[str, float] = {
    "code_output":     0.0,
    "json_output":     0.0,
    "tool_call":       0.0,
    "analysis_output": 0.3,
    "plan_output":     0.4,
    "writing_output":  0.65,
    "casual_output":   0.7,
}

OUTPUT_TYPE_SIGNALS: dict[str, tuple[str, ...]] = {
    "code_output":     ("fix", "patch", "implement", "refactor",
                        "write code", "function", "class", "bug",
                        "rewrite", "vulnerability", "exploit"),
    "json_output":     ("json", "schema", "structured", "format as"),
    "analysis_output": ("analyze", "report", "summary", "insights",
                        "what is", "how many", "show me", "summarize",
                        "summarise"),
    "writing_output":  ("email", "write", "draft", "message",
                        "outreach", "copy"),
}


def detect_output_type(task: str, *, council: str | None = None) -> str:
    """Explicit output-type classifier driving the CEO's temperature.

    Counts keyword matches across each registered output type and
    picks the highest-scoring class.  Ties resolve to `code_output`
    when the council is A (safer to default to t=0.0 for code
    contexts), otherwise to `analysis_output`."""
    task_lower = (task or "").lower()
    if not task_lower:
        return "code_output" if council == "A" else "analysis_output"
    scores = {k: 0 for k in OUTPUT_TYPE_SIGNALS}
    for output_type, signals in OUTPUT_TYPE_SIGNALS.items():
        for signal in signals:
            if signal in task_lower:
                scores[output_type] += 1
    best, best_score = max(scores.items(), key=lambda kv: kv[1])
    if best_score == 0:
        return "code_output" if council == "A" else "analysis_output"
    return best
