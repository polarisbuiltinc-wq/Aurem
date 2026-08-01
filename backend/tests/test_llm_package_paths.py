"""Regression guard — `__file__`-relative paths after LLM package split.

Session C · Sub-step 1 (rename `services/llm.py` → `services/llm/__init__.py`)
introduced a subtle silent regression: any code inside the moved file
that computed a path via `os.path.dirname(os.path.dirname(__file__))`
now landed on `services/` instead of `backend/`, so files like
`prompts/groq_house_rules.md` were silently NOT loaded — Groq fallback
lost its identity prompt.

This test locks the paths so any future refactor that touches the
package layout must re-verify these resolve correctly.
"""
from __future__ import annotations

import os
import sys
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _ensure_backend_on_path():
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


def test_groq_house_rules_path_resolves_to_backend_prompts():
    """`_GROQ_HOUSE_RULES_PATH` must land on the real file, not a
    `services/prompts/` non-existent directory."""
    _ensure_backend_on_path()
    import services.llm as m

    p = pathlib.Path(m._GROQ_HOUSE_RULES_PATH)
    assert p == BACKEND / "prompts" / "groq_house_rules.md", (
        f"path drift after llm-package split: {p}"
    )
    assert p.exists(), f"resolved path does not exist: {p}"


def test_groq_house_rules_load_returns_nonempty():
    """Downstream contract — `_call_groq` prepends these rules as a
    system prompt. Empty content silently degrades identity/tone."""
    _ensure_backend_on_path()
    import services.llm as m

    # Reset any cached value from a prior import to force a fresh read.
    if hasattr(m._load_groq_house_rules, "_cached"):
        delattr(m._load_groq_house_rules, "_cached")
    rules = m._load_groq_house_rules()
    assert rules, "_load_groq_house_rules() returned empty — path regression?"
    assert "Groq Model House Rules" in rules, (
        "loaded content does not look like the house rules file"
    )
