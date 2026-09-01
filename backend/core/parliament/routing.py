"""
core/parliament/routing.py — 2026-09-08 Phase 3 god-class split.

`TaskRouter`. Moved verbatim out of the single core/parliament.py
(zero logic change).
"""
from __future__ import annotations

from core.task_type import infer_task_type  # noqa: E402,F401 (compat re-export)


class TaskRouter:
    """Picks which Council should handle a given task.

    Iter 212m-160 — task_type routing:
      • analysis / report / insight / summarize → Council B (GLM-5.2 + DeepSeek rescue)
      • email / copy / write / draft            → Council C (DeepSeek, creative voice)
      • code_fix / code_review / security / lint_heal → Council A (LongCat → Claude)
      • everything else                          → Council A (safe default)

    `context["council"]` still wins when explicitly set so unit tests
    and any future caller that wants to pin a specific council can do
    so without losing the keyword path.
    """

    _COUNCIL_A_KEYWORDS = (
        "code", "patch", "fix", "refactor", "implement",
        "security", "vuln", "scan", "lint", "syntax",
        "rewrite", "test", "validate",
    )

    # Iter 212m-160 — explicit task_type → council map. Keys mirror the
    # strings already used by callers (`code_fix`, `code_review`,
    # `security`, `lint_heal`); the new B/C entries unlock Council B's
    # GLM-5.2 V2 swap + Council C's creative DeepSeek path for analysis
    # and writing tasks respectively.
    _TASK_TYPE_TO_COUNCIL = {
        # Council A — code surgery
        "code_fix":     "A",
        "code_review":  "A",
        "security":     "A",
        "lint_heal":    "A",
        # Council B — analysis / advisory (V2 GLM-5.2 + DeepSeek rescue)
        "analysis":     "B",
        "report":       "B",
        "insight":      "B",
        "summarize":    "B",
        # Council C — creative / writing
        "email":        "C",
        "copy":         "C",
        "write":        "C",
        "draft":        "C",
    }

    def route(self, task: str, context: dict | None = None) -> str:
        ctx = context or {}
        forced = ctx.get("council")
        if forced in ("A", "B", "C"):
            return forced
        ttype = (ctx.get("task_type") or "").lower()
        if ttype in self._TASK_TYPE_TO_COUNCIL:
            return self._TASK_TYPE_TO_COUNCIL[ttype]
        text = (task or "").lower()
        if any(k in text for k in self._COUNCIL_A_KEYWORDS):
            return "A"
        return "A"
