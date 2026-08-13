"""
Iter 388k — Bug 12 regression tests

Bug 12: Query-tier `read_repo_file` reads landed in
`_synthesise_max_iters_summary` with the template
"Send the same prompt again — with the context I've already loaded,
the next response will land the concrete answer." — an INFINITE loop
because resending hit the same wall.  Confirmed root cause: query
tier had `max_iters=2`, so any 2nd exploratory tool call exhausted
the budget before the LLM produced a text answer.

Fix shape:
  1. `chat.py` query-tier `_max_iters_eff` bumped 2 → 3.
  2. `orchestrator.py` last-iter guard: system prompt is patched with
     a "FINAL ANSWER ROUND — no more tools" directive when the loop
     is about to exit.
  3. `_synthesise_max_iters_summary` rewrites the fallback text so it
     no longer tells the user to "send the same prompt again" — that
     phrase is banned by string-level assertion below.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# --------------------------------------------------------------------- #
#  Layer 3 — synthesiser message must not send the user into a loop.    #
# --------------------------------------------------------------------- #

def test_synthesiser_never_says_send_same_prompt_again():
    """The exact phrase the user reported in prod MUST NOT appear
    anywhere in the fallback message anymore.  This assertion locks
    the fix — anyone regressing it will fail this test."""
    from services.orchestrator import _synthesise_max_iters_summary
    msg = _synthesise_max_iters_summary(
        "Read backend/routers/health.py",
        [{"tool": "read_repo_file",
          "args": {"path": "backend/routers/health.py"}}],
    )
    banned = (
        "send the same prompt again",
        "Send the same prompt again",
        "need one more round",
        "next response will land the concrete answer",
    )
    for phrase in banned:
        assert phrase.lower() not in msg.lower(), (
            f"regression: fallback msg still contains {phrase!r}"
        )


def test_synthesiser_names_the_actual_paths_it_inspected():
    from services.orchestrator import _synthesise_max_iters_summary
    msg = _synthesise_max_iters_summary(
        "Read backend/routers/health.py and backend/main.py",
        [
            {"tool": "read_repo_file",
             "args": {"path": "backend/routers/health.py"}},
            {"tool": "read_repo_file",
             "args": {"path": "backend/main.py"}},
        ],
    )
    assert "backend/routers/health.py" in msg
    assert "backend/main.py" in msg


def test_synthesiser_empty_invocations_stays_actionable():
    from services.orchestrator import _synthesise_max_iters_summary
    msg = _synthesise_max_iters_summary("something", [])
    # Must still be helpful — mention rephrasing with specifics.
    assert "rephrase" in msg.lower() or "specific" in msg.lower()
    # And still no infinite-loop invitation.
    assert "send the same prompt again" not in msg.lower()


# --------------------------------------------------------------------- #
#  Layer 1 — chat.py query-tier max_iters bump.                         #
# --------------------------------------------------------------------- #

def test_query_tier_max_iters_bumped_to_three():
    """The exact value the router assigns to query-tier turns.  If
    someone reverts the fix to `_max_iters_eff = 2` this assertion
    fires.  We match on the source since the value is set inline in
    the SSE handler."""
    src = Path("/app/backend/routers/chat.py").read_text()
    assert 'if _tier == "query":' in src
    # The literal that has to be present.
    assert "_max_iters_eff = 3" in src
    # And the old value must be gone from the query branch (a bare
    # "_max_iters_eff = 2" would silently downgrade it again).
    idx = src.find('if _tier == "query":')
    branch = src[idx:idx + 1500]
    assert "_max_iters_eff = 2" not in branch, (
        "query branch reverted to max_iters=2 — Bug 12 will loop again"
    )


# --------------------------------------------------------------------- #
#  Layer 2 — orchestrator injects a final-answer directive on last iter #
# --------------------------------------------------------------------- #

def test_orchestrator_has_final_answer_round_directive():
    src = Path("/app/backend/services/orchestrator.py").read_text()
    assert "FINAL ANSWER ROUND" in src
    assert "Do NOT emit any more tool calls" in src
    # The guard must be tied to the terminal iter comparison so it
    # only fires when the loop is about to close.  Fail loudly if
    # someone lifts the check.
    assert "iters >= max_iters" in src
