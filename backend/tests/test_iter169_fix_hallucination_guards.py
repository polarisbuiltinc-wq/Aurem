"""Iter 169 — guardrails against 'fix' hallucinations (PARTIAL).

  1. ~~Short 'fix'/'ship'/'do it' messages with NO prior `aurem-handoff`~~
     ~~fence in the recent history must trigger a clarification reply~~
     REMOVED in Iter 212m-26 along with the entire `_maybe_clarify_short_fix`
     and ship-shortcut auto-trigger path. Short replies now fall into
     the normal orchestrator and get a conversational answer.
  2. The CTO persona must contain Rule 8 — ANALYSIS → SPEC CONTRACT —
     forbidding analysis turns that end without a concrete spec.
  3. The orchestrator budget-hit message must be USER-FIRST — never
     blame system limits and never push work back onto the user with
     'narrow your ask'. Founder directive Iter 212m-208 explicitly
     reversed the earlier 'I ran out of time' / 'narrow to one file'
     wording. The current contract, enforced below:
       - MUST summarise the surfaces already inspected
         (via `seen_paths` + `seen_tools`).
       - MUST offer 'send the same prompt again' continuation so the
         next round lands the concrete answer.
       - MUST NOT lead with 'I ran out of time'.
       - MUST NOT tell the user to 'narrow the ask'.
       - MUST NOT re-introduce the old '4-pillar sweep' language.
"""
import re
from pathlib import Path


CHAT_PATH = Path(__file__).parent.parent / "routers" / "chat.py"
ORCH_PATH = Path(__file__).parent.parent / "services" / "orchestrator.py"


# ── Fix 2 ─────────────────────────────────────────────────────────────

def test_persona_has_rule_8_analysis_spec_contract():
    src = ORCH_PATH.read_text()
    assert "ANALYSIS → SPEC CONTRACT" in src, (
        "Rule 8 (ANALYSIS → SPEC CONTRACT) missing from CTO persona"
    )
    # Must reference the canonical mechanism — the aurem-handoff fence.
    rule_block = src[src.index("ANALYSIS → SPEC CONTRACT"):]
    assert "aurem-handoff" in rule_block[:1200]
    assert "Which file" in rule_block[:1200] or "Which file" in src
    # Must explicitly forbid speculating about unread files.
    assert "not actually called read_repo_file" in rule_block[:1200] \
        or "speculating about file contents" in rule_block.lower()[:1200]


def test_rule_8_lives_in_core_layer():
    """Rule 8 sits inside the TOP-OF-MIND HARD RULES block which is
    pinned to L1 core — so it's always loaded, even in execute path."""
    src = ORCH_PATH.read_text()
    rules_start = src.index("TOP-OF-MIND HARD RULES")
    # Next section heading after the rules block:
    next_heading = src.index("# MODE DETECTION", rules_start)
    rules_block = src[rules_start:next_heading]
    assert "ANALYSIS → SPEC CONTRACT" in rules_block, (
        "Rule 8 must be inside the TOP-OF-MIND HARD RULES block (L1 core)"
    )


# ── Fix 3 (post Iter 212m-208 founder directive) ──────────────────────

def test_budget_hit_message_is_user_first_not_blame():
    """The synthesised max-iters summary must NEVER blame system limits
    ('I ran out of time') or tell the user to 'narrow the ask' — those
    were the Iter 169 wording that Iter 212m-208 explicitly reversed
    per founder ruling."""
    src = ORCH_PATH.read_text()
    # Old vague / dead phrases must NOT be present:
    assert "4-pillar sweep" not in src, (
        "'4-pillar sweep' — dead Iter 169 wording, must stay removed"
    )
    assert "the scope of your question is broader" not in src
    # Blame-tone that Iter 212m-208 reversed:
    synth_start = src.index("_synthesise_max_iters_summary")
    # Walk forward to the next top-level def (or 4000 chars, safety).
    synth_end = src.find("\ndef ", synth_start + 40)
    synth_body = src[synth_start:synth_end if synth_end != -1 else synth_start + 4000]
    # The literal "I ran out of time" appears in the DOCSTRING as a
    # negative example ("NOT 'I ran out of time...'") — we must
    # therefore assert that NO STRING LITERAL passed to the user
    # contains that phrase. Simple heuristic: it must not appear
    # inside a `lines.append("..."|"""...""")` block.
    #
    # We look at every string literal appended to `lines` and verify
    # none of the banned phrases appear.
    lines_appended = re.findall(
        r'lines\.append\(\s*[fr]?["\'](.*?)["\']\s*\)',
        synth_body,
        re.DOTALL,
    )
    banned_phrases = [
        "I ran out of time",
        "narrow the ask to one file",
        "narrow your ask",
        "please reformulate",
    ]
    for phrase in banned_phrases:
        for text in lines_appended:
            assert phrase.lower() not in text.lower(), (
                f"blame-tone phrase {phrase!r} leaked into user-facing "
                f"summary string: {text[:120]!r}. Iter 212m-208 founder "
                f"directive forbids this."
            )


def test_budget_hit_summary_uses_real_seen_paths():
    """The user-facing summary MUST cite files the model actually read
    this turn (via `seen_paths`) — not a generic prompt example."""
    src = ORCH_PATH.read_text()
    synth_start = src.index("_synthesise_max_iters_summary")
    synth_end = src.find("\ndef ", synth_start + 40)
    synth_body = src[synth_start:synth_end if synth_end != -1 else synth_start + 4000]
    # Must derive the summary from real invocations, not a canned string.
    assert "seen_paths" in synth_body, (
        "budget-hit summary must reference `seen_paths` (files actually "
        "read this turn) so the follow-up prompt lands in real context"
    )
    # Must include a "send the same prompt again" continuation so the
    # user isn't pushed back but gets a natural retry path.
    assert (
        "send the same prompt" in synth_body.lower()
        or "same prompt again" in synth_body.lower()
    ), (
        "budget-hit summary must invite the user to re-send the same "
        "prompt (context is already loaded on the next round)"
    )
    # Must record tools used so the reply is auditable.
    assert "seen_tools" in synth_body
