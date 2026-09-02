"""test_fake_tool_call_narration_guard_2026_09_09.py

Founder-caught PRODUCTION bug: the model wrote a bracketed
"[Tool note: Checking production status via
site_ops.check_production_publishing_status()...]" followed by a
"[Tool result: ... update ID: a1b2c3d ...]" block — a fabricated tool
call AND its fabricated result (the SAME generic fake hex ID repeated
across unrelated requests proves it's a template, not a real ID).

Two-part fix, per founder's own root-cause hypothesis + fix principle:
  (a)/(b) PRIMARY — system-prompt guard in services/ora_context.py's
          ORA ABSOLUTE BOUNDARY block (all 3 variants: repo-scoped,
          no-repo, founder-pod-debug) telling the model it may only
          report a tool's output if it actually called that tool, and
          must never invent a fake tool-call/tool-result narration.
  (c) best-effort — services/output_guard.py's `_UNIVERSAL_LEAK_PATTERNS`
          mechanically strips a `[Tool note: ...]` / `[Tool result: ...]`
          bracket block that slips through anyway, replacing it with an
          honest "I have not actually run that check yet" line. This is
          explicitly NOT a perfect detector (founder: don't over-engineer
          one) — just a narrow, low-false-positive net.
"""
from services.output_guard import strip_machinery_leak
from services import ora_context


def test_t_fake_tool_note_and_result_combo_is_stripped():
    text = (
        "Sure, let me check.\n"
        "[Tool note: Checking production status via "
        "site_ops.check_production_publishing_status()...]\n"
        "[Tool result: ... Last published update was 2 hours ago "
        "(update ID: a1b2c3d) ... footer shows old hours ...]\n"
        "Looks good!"
    )
    clean, stripped = strip_machinery_leak(text, universal_only=True)
    assert stripped is True
    assert "Tool note" not in clean
    assert "Tool result" not in clean
    assert "a1b2c3d" not in clean
    assert "I have not actually run that check yet" in clean
    # only ONE disclaimer for the paired note+result, not two
    assert clean.count("I have not actually run that check yet") == 1


def test_t_lone_fake_tool_result_block_is_stripped():
    text = "[Tool result: status=ok, id=deadbeef] All set."
    clean, stripped = strip_machinery_leak(text, universal_only=True)
    assert stripped is True
    assert "deadbeef" not in clean
    assert "I have not actually run that check yet" in clean


def test_t_normal_prose_mentioning_tool_or_result_is_untouched():
    text = ("I used the search_repo tool to find the file, and the "
            "result was helpful for fixing the bug.")
    clean, stripped = strip_machinery_leak(text, universal_only=True)
    assert stripped is False
    assert clean == text


def test_t_boundary_prompt_forbids_fake_tool_narration_repo_scoped():
    rendered = ora_context.ORA_BOUNDARY_SYSTEM_RULE_TEMPLATE.format(
        repo_slug="acme/widgets", branch="main",
    )
    assert "report a tool's output if you actually called" in rendered.lower()
    assert "Tool note" in rendered and "Tool result" in rendered


def test_t_boundary_prompt_forbids_fake_tool_narration_no_repo():
    assert "report a tool's output if you actually called" in ora_context.ORA_BOUNDARY_NO_REPO_RULE.lower()


def test_t_boundary_prompt_forbids_fake_tool_narration_founder_pod_debug():
    assert "report a tool's output if you actually called" in ora_context.ORA_FOUNDER_POD_DEBUG_RULE.lower()
