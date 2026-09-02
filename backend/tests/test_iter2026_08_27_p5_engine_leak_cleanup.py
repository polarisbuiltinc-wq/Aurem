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
from tests._cto_projects_src import cto_projects_src


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
    src = "".join(open(f"/app/backend/routers/chat/{_f}.py", encoding="utf-8").read() for _f in ("__init__","misc","turn","stream","history"))
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
    src = "".join(open(f"/app/backend/routers/chat/{_f}.py", encoding="utf-8").read() for _f in ("__init__","misc","turn","stream","history"))
    assert '"council": True' not in src
    assert '"council": bool(result.get("council"))' not in src


def test_injected_vanguard_skills_phrasing_fixed():
    src = cto_projects_src()
    assert "injected Vanguard security skills" not in src


def test_truncation_check_pluralization_fixed():
    src = cto_projects_src()
    assert "files passed truncation check" not in src


def test_hardcoded_pattern_count_removed_from_live_task_popup():
    src = open("/app/frontend/src/components/LiveTaskPopup.jsx").read()
    assert "25 patterns checked" not in src


def test_test_file_lock_blocked_message_is_plain_english():
    src = cto_projects_src()
    assert "Loop-pipeline test-file lock is enforced on this path (Iter 286)" not in src
    assert "Can't apply this change" in src


def test_engine_leak_stripping_is_universal_not_flag_gated():
    """P5 — founder explicitly asked these to apply to ALL users, not
    stay behind explain_plain_english_v1 (which is still allowlisted
    to test_admin_001 for the separate explain-mode enhancement)."""
    src = "".join(open(f"/app/backend/routers/chat/{_f}.py", encoding="utf-8").read() for _f in ("__init__","misc","turn","stream","history"))
    assert 'if _plain_english_active and content:' not in src, (
        "leak-stripping must no longer be gated behind "
        "_plain_english_active — only length-capping stays gated"
    )



# ---------------------------------------------------------------------------
# 2026-08-27 · P6 live-run regression, NAMED BEFORE/AFTER.
#
# BEFORE (bug): routers/chat.py called `strip_machinery_leak(content)`
# with no `universal_only` kwarg -> defaulted to False -> the
# explain-mode-only tier (bare file paths -> "a project file", DB
# collection names -> "the database", framework jargon) ran for EVERY
# user, including a live repo scan reply. This is the exact failure a
# P6 live drive-through surfaced: a real scan mentioning
# "backend/services/orchestrator.py" or "cto_projects" collection came
# back as "a project file" / "the database", destroying the scan's
# useful structured content.
#
# AFTER (fix): chat.py now calls
# `strip_machinery_leak(content, universal_only=not _plain_english_active)`
# — regular users (the common case) get ONLY the always-a-bug
# universal tier (Iter NNN, Mode X, raw tracebacks); the explain-only
# tier only runs when the founder's explain-mode contract is active
# for that turn.
# ---------------------------------------------------------------------------

def test_before_fix_default_call_over_strips_real_scan_content():
    """Reproduces the exact bug: the OLD call signature (no kwarg,
    universal_only defaults to False) strips legitimate file paths and
    DB collection names out of a real scan reply — this is what P6
    caught live."""
    scan_reply = (
        "I scanned the repo. The bug is in backend/services/orchestrator.py "
        "and the affected records live in the cto_projects collection."
    )
    text, stripped = strip_machinery_leak(scan_reply)  # old default behavior
    assert stripped is True
    assert "backend/services/orchestrator.py" not in text
    assert "a project file" in text
    assert "cto_projects" not in text


def test_after_fix_universal_only_preserves_real_scan_content():
    """The FIX: regular users go through universal_only=True, which
    must leave real file paths and DB collection names untouched while
    still catching genuine leaks."""
    scan_reply = (
        "I scanned the repo. The bug is in backend/services/orchestrator.py "
        "and the affected records live in the cto_projects collection."
    )
    text, stripped = strip_machinery_leak(scan_reply, universal_only=True)
    assert stripped is False
    assert "backend/services/orchestrator.py" in text
    assert "cto_projects" in text


def test_after_fix_universal_only_still_catches_real_leaks():
    """universal_only=True must still strip the always-a-bug tier
    (Iter counters, Mode letters, raw tracebacks) for regular users —
    the fix must not throw out the real leak protection."""
    text, stripped = strip_machinery_leak(
        "Blocked (Iter 286, Mode D): AttributeError: boom", universal_only=True
    )
    assert stripped is True
    assert "Iter 286" not in text
    assert "Mode D" not in text
    assert "AttributeError" not in text


def test_chat_py_call_sites_pass_universal_only_flag():
    """Both chat/send and chat/stream call sites must gate the
    explain-only tier behind _plain_english_active, never apply it
    unconditionally to every user."""
    src = "".join(open(f"/app/backend/routers/chat/{_f}.py", encoding="utf-8").read() for _f in ("__init__","misc","turn","stream","history"))
    assert src.count("universal_only=not _plain_english_active") == 2
