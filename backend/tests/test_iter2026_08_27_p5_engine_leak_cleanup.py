"""
tests/test_iter2026_08_27_p5_engine_leak_cleanup.py — P5 (Compactness +
Engine-Leak Cleanup), Journey/Intent-Grounding build round.

Confirmed leaks (grep'd from a live founder transcript) fixed here:
  - "Iter 286" iteration counters in user-facing copy
  - "Mode D" internal mode letters in ORA's own reply
  - raw Python exception class names / tracebacks
  - "verify-agent", "e2b" internal names
  - "injected Vanguard security skills" (mechanistic phrasing)
  - "N files passed truncation check" (pluralization + jargon)
  - a raw boolean rendered as "via Council true"
  - a hardcoded, not-derived-from-real-data "clean (25 patterns checked)"
Two-pronged fix: (a) authoring — the leaking strings themselves were
rewritten in plain English; (b) backstop — the runtime
strip_machinery_leak() net now runs for EVERY user (not just the
plain_english_contract_active allowlist) and has new deny patterns.
"""
import re

from services.output_guard import strip_machinery_leak, _MACHINERY_LEAK_PATTERNS


def test_iter_counter_stripped():
    text, stripped = strip_machinery_leak(
        "ship_code blocked — Loop-pipeline test-file lock is enforced "
        "on this path (Iter 286)."
    )
    assert stripped is True
    assert "Iter 286" not in text
    assert "286" not in text


def test_mode_letter_stripped():
    text, stripped = strip_machinery_leak(
        "Paste the error and I'll diagnose it in Mode D first."
    )
    assert stripped is True
    assert "Mode D" not in text


def test_mode_letter_does_not_over_match_normal_words():
    # "Mode" followed by a lowercase word must never be touched.
    text, stripped = strip_machinery_leak("Switch to dark mode anytime.")
    assert stripped is False
    assert text == "Switch to dark mode anytime."


def test_aurem_handoff_fence_never_touched_by_output_guard_at_all():
    """The aurem-handoff fence's job is bundling the exact file path
    for the Ship button — output_guard must NEVER run on it at all
    (not even to strip cosmetic jargon), so its content survives
    byte-for-byte for extractHandoffBrief to parse. The visible-fence
    leak is fixed at DISPLAY time instead (MessageBubble.jsx's
    stripHandoffFenceForDisplay), never server-side."""
    raw = "Here's what I found.\n```aurem-handoff\nFix X.\n```\nDone."
    text, stripped = strip_machinery_leak(raw)
    # strip_machinery_leak itself has no opinion on this — the actual
    # guarantee lives in routers/chat.py's `"aurem-handoff" not in
    # content` gate, which skips calling this function entirely.
    src = open("/app/backend/routers/chat.py").read()
    assert '"aurem-handoff" not in content' in src, (
        "chat.py must exempt any content carrying a real ship handoff "
        "fence from the output guard entirely"
    )


def test_verify_agent_and_e2b_stripped():
    text, stripped = strip_machinery_leak(
        "verify-agent + e2b disabled by admin for this task."
    )
    assert stripped is True
    assert "verify-agent" not in text
    assert "e2b" not in text


def test_raw_traceback_and_exception_stripped():
    text, stripped = strip_machinery_leak(
        "Something broke: AttributeError: 'str' object has no attribute 'get'"
    )
    assert stripped is True
    assert "AttributeError" not in text


def test_raw_boolean_status_stripped():
    text, stripped = strip_machinery_leak("via Council true · main")
    assert stripped is True
    assert "true" not in text.lower() or "Council" not in text


def test_council_field_is_a_real_label_not_a_boolean():
    """Root-cause regression: routers/chat.py used to send
    `"council": True` / `bool(result.get("council"))` — a bare
    boolean the frontend interpolated directly into visible copy."""
    src = open("/app/backend/routers/chat.py").read()
    assert '"council": True' not in src
    assert '"council": bool(result.get("council"))' not in src


def test_injected_vanguard_skills_phrasing_fixed():
    src = open("/app/backend/routers/cto_projects.py").read()
    assert "injected Vanguard security skills" not in src


def test_truncation_check_pluralization_fixed():
    src = open("/app/backend/routers/cto_projects.py").read()
    assert "files passed truncation check" not in src


def test_hardcoded_pattern_count_removed_from_live_task_popup():
    src = open("/app/frontend/src/components/LiveTaskPopup.jsx").read()
    assert "25 patterns checked" not in src


def test_test_file_lock_blocked_message_is_plain_english():
    src = open("/app/backend/routers/cto_projects.py").read()
    assert "Loop-pipeline test-file lock is enforced on this path (Iter 286)" not in src
    assert "Can't apply this change" in src


def test_engine_leak_stripping_is_universal_not_flag_gated():
    """P5 — founder explicitly asked these to apply to ALL users, not
    stay behind explain_plain_english_v1 (which is still allowlisted
    to test_admin_001 for the separate explain-mode enhancement)."""
    src = open("/app/backend/routers/chat.py").read()
    assert 'if _plain_english_active and content:' not in src, (
        "leak-stripping must no longer be gated behind "
        "_plain_english_active — only length-capping stays gated"
    )
