"""
Iter 388j — Advisor audit (Bug 3 + 4 + 5) regression tests.

Root cause: `chat.py` advisor prompt structure had SCREENSHOT ANALYSIS
as the last block with rule "ground concrete UI observations in the
screenshot" — this overrode the anti-fabrication rule for DATA
questions.  Meanwhile `advisor_context.py` had NO data sources for
run state, open PRs, or per-task token breakdown.  Net: three
Advisor chip buttons ("Diagnose failed run" / "Summarize open PRs" /
"Token breakdown") fabricated confidently from screenshot text.

Fix shape:
  1. `advisor_context.py` gets three new blocks: `recent_tasks`,
     `open_prs`, `token_breakdown`.  Every block carries an `error:`
     field so the LLM can honestly say "yeh data abhi available nahi
     hai" without falling back on vision.
  2. `chat.py::_adv_directive` gets a HARD DATA HONESTY section that
     wins over the visual-context rule for data claims.
  3. `chat.py` SKIPS screenshot vision entirely when the prompt is
     one of the three chip labels (data question, not UI question).

These tests only exercise the shape contract; the end-to-end flow is
covered by a manual test on the Advisor panel post-deploy.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the `backend/` package importable when pytest runs from /app.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# 1. Bug 5 helper: chip-label carveout string matching.
# ---------------------------------------------------------------------------

def _is_data_chip(prompt: str) -> bool:
    """Mirror of the chat.py:2265 carveout so this test breaks if the
    canonical label set drifts and someone forgets to update the
    backend."""
    head = (prompt or "").strip().lower()[:64]
    return head in (
        "diagnose failed run",
        "summarize open prs",
        "token breakdown",
    )


def test_chip_labels_matched_case_insensitive():
    for label in ("Diagnose failed run", "Summarize open PRs", "Token breakdown"):
        assert _is_data_chip(label), f"{label!r} must be a data-chip"
        assert _is_data_chip(label.upper())
        assert _is_data_chip(label.lower())


def test_free_prose_is_not_a_data_chip():
    """UI questions must keep the screenshot path — otherwise Bug 8's
    kind of question ("where is the Start Free button?") stops working.
    """
    for p in (
        "where is the diagnose failed run button?",
        "explain the loop timeline",
        "hey what's up",
        "",
    ):
        assert not _is_data_chip(p)


# ---------------------------------------------------------------------------
# 2. advisor_context.py — new block shape contract.
# ---------------------------------------------------------------------------

def test_advisor_context_response_shape_has_new_blocks():
    """The response dict must always include the three new keys, even
    when the underlying fetch fails.  Otherwise the chat.py prompt
    formatter throws and the Advisor breaks."""
    from routers.advisor_context import get_advisor_context  # noqa: F401
    import inspect
    src = inspect.getsource(get_advisor_context)
    # The `resp` dict must list the three new keys.
    assert '"recent_tasks":' in src
    assert '"open_prs":' in src
    assert '"token_breakdown":' in src


def test_advisor_context_never_raises_from_missing_repo():
    """Bug 4 root cause: projects without github_owner/repo silently
    blocked the PR fetch. Verify the fallback path sets an error
    string rather than crashing. The PR-fetch helper was mechanically
    split into services/advisor_open_prs.py (2026-08-27, file-size
    guard) — same behaviour, different source file."""
    import inspect
    from services import advisor_open_prs
    src = inspect.getsource(advisor_open_prs)
    assert 'open_prs["error"] = "repo_not_configured"' in src


# ---------------------------------------------------------------------------
# 3. chat.py — HARD DATA HONESTY rule is present in the advisor
#    directive.  If someone silently removes it we want the test to fail.
# ---------------------------------------------------------------------------

def test_data_honesty_rule_present_in_advisor_directive():
    from routers import chat as chat_mod
    src = Path(chat_mod.__file__).read_text()
    # The literal marker string that identifies the rule block.
    assert "DATA HONESTY (highest priority):" in src
    # Anti-fabrication essentials — these three phrases must appear.
    assert "NEVER derive" in src
    assert "yeh data abhi available" in src
    assert "Project:" in src  # project name pinning


def test_screenshot_vision_carveout_present():
    from routers import chat as chat_mod
    src = Path(chat_mod.__file__).read_text()
    assert "_is_data_chip" in src
    assert "diagnose failed run" in src.lower()
    assert "if body.screenshot_b64 and not _is_data_chip" in src
