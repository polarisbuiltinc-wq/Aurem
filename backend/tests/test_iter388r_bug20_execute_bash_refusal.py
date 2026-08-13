"""
Iter 388r — Bug 20 regression test.

Bug 20: `execute_bash` tool was documented as available for founder
accounts and IS server-side wired (see local_tools.py:1900 founder
gate), but the LLM was refusing legit inspection prompts with prose
like "I work with your repository only.  I don't have access to my
own system files or credentials." — a 90+ second wait for a refusal.

Root cause hypothesis: LLM's safety training makes it refuse
"inspect server files" prompts, and the existing Rule 5 in the
orchestrator system prompt ("you MUST call execute_bash") wasn't
strong enough to override that bias.

Fix: added Rule 5b — explicit ANTI-REFUSAL directive that names the
exact refusal phrases the LLM was emitting, tells it those refusals
are FACTUALLY WRONG for the caller (because the tool is only in the
catalog for founder accounts — see the tier filter at
orchestrator.py:1471-1476), and reminds it to call the tool and let
the server-side gate decide.

This test verifies the rule text is present + names the specific
refusal phrases we want blocked.
"""

from __future__ import annotations

from pathlib import Path


def test_orchestrator_has_execute_bash_anti_refusal_rule():
    src = Path("/app/backend/services/orchestrator.py").read_text()
    # The marker string identifies the rule block.
    assert "5b. ANTI-REFUSAL FOR execute_bash" in src, (
        "Rule 5b anti-refusal for execute_bash is missing — Bug 20 will "
        "regress and LLM will start refusing legit founder inspection "
        "prompts again."
    )


def test_anti_refusal_names_the_exact_phrases_the_llm_emitted():
    """If the LLM ever changes the prose it uses to refuse, we want
    the anti-refusal rule to name AT LEAST the phrases we've seen in
    prod so this test file is the audit trail of known bad refusals."""
    src = Path("/app/backend/services/orchestrator.py").read_text()
    banned_phrases = [
        "'I only work with your repository'",
        "'I don't have access to",
        "'I can't inspect internal server paths'",
    ]
    for phrase in banned_phrases:
        assert phrase in src, f"anti-refusal rule missing phrase: {phrase!r}"


def test_anti_refusal_covers_the_expected_pod_paths():
    """The rule must enumerate the exact paths execute_bash is
    allowed to read on (`/app`, `/tmp`, `/var`, `/var/log`, `/etc`,
    `/usr`) so the LLM has a whitelist to check against."""
    src = Path("/app/backend/services/orchestrator.py").read_text()
    for path in ("/app", "/tmp", "/var", "/var/log", "/etc", "/usr"):
        assert path in src, f"anti-refusal path whitelist missing: {path}"


def test_anti_refusal_advises_call_tool_when_uncertain():
    """Fallback: when in doubt, call the tool.  The server-side gate
    already refuses if the caller isn't founder, so an LLM cautious-
    call is cheaper than a cautious-refusal that wastes the user's
    turn."""
    src = Path("/app/backend/services/orchestrator.py").read_text()
    assert "When in doubt, CALL THE TOOL" in src
