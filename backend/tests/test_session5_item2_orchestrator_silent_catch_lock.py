"""Session 5 · Item 2 · orchestrator.py silent-catch classification lock.

Item 2 was originally scoped as "patch 8 silent-catch sites in
orchestrator.py". After running a properly-fixed AST classifier
that inspects the try-body for hook/label patterns (not just the
preamble), all 7 remaining sites (one was `return False`, not a
bare-return — my Session 4 heuristic mistook it) turn out to be
LEGITIMATE UI-hook `activity_hook`/`step_hook` fail-opens. Adding
`logger.debug()` would spam prod logs every time a client closes
the tab mid-stream.

This test locks the current classification so:
  1. Any FUTURE `except: pass` added in orchestrator.py that is
     NOT inside an `activity_hook` / `step_hook` try-body fails
     the test (a real hygiene site sneaking in).
  2. If someone removes the `activity_hook` guards, this test
     catches that too (the classification map would drift).

Zero fixes shipped in Item 2 — this is the disciplined outcome.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1]
ORCHESTRATOR = BACKEND / "services" / "orchestrator.py"


# The 7 currently-legitimate UI-hook wrapper sites (locked as of
# 2026-07-31 post-Session-5-Item-2 audit, re-snapshotted 2026-08-23
# after the P0 security-scan fix added lines to chat_with_tools —
# all 7 sites verified still-hooks, only line numbers shifted). If a
# real hygiene target ever slips into orchestrator.py, it will NOT be
# in this set and the test below will fail.
LEGIT_UI_HOOK_LINES = {2067, 2072, 2174, 2439, 2490, 2521, 2530}
# 2026-08-23 audit fix — the last 3 of these 7 line numbers drifted
# (+4) after this session's findings-to-fix-bridge edits added lines
# earlier in orchestrator.py (capturing `findings_saved_this_turn`),
# shifting everything below that point. Re-derived from the actual
# current file: each of these 7 `pass` lines (inside `except
# Exception:` blocks that call activity_hook/step_hook) is unchanged
# in behavior — same 7 legit silent-catches, 3 of them just moved.


def _is_truly_empty_return(s: ast.stmt) -> bool:
    """Strict emptiness check — treats `return False` and `return True`
    as REAL return values, NOT bare returns. (Python's `0 == False`
    quirk means a naive `value in (None, "", 0)` check misclassifies
    boolean returns.)"""
    if not isinstance(s, ast.Return):
        return False
    if s.value is None:
        return True
    v = s.value
    if isinstance(v, ast.Constant):
        return v.value is None or v.value == "" or (
            isinstance(v.value, int)
            and not isinstance(v.value, bool)
            and v.value == 0
        )
    if isinstance(v, ast.Dict) and not v.keys:
        return True
    if isinstance(v, ast.List) and not v.elts:
        return True
    return False


def _try_body_is_ui_hook(try_node: ast.Try) -> bool:
    """Search entire try body for hook/label call patterns."""
    body_src = "".join(ast.unparse(stmt) + "\n" for stmt in try_node.body)
    patterns = (
        "activity_hook", "step_hook", "_STEP_LABELS",
        "progress_hook", "on_progress", "on_activity",
        "_step_label_for_tool",
    )
    return any(p in body_src for p in patterns)


def _collect_silent_catches():
    """Return list of (line_no, exc_type_str, kind, is_ui_hook) for
    every silent-catch site in orchestrator.py."""
    src = ORCHESTRATOR.read_text()
    tree = ast.parse(src)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        is_hook = _try_body_is_ui_hook(node)
        for h in node.handlers:
            if len(h.body) != 1:
                continue
            s = h.body[0]
            is_pass  = isinstance(s, ast.Pass)
            is_empty = _is_truly_empty_return(s)
            if not (is_pass or is_empty):
                continue
            exc = ast.unparse(h.type) if h.type else "bare"
            hits.append((s.lineno, exc, "pass" if is_pass else "empty-return", is_hook))
    return hits


# ═════════════════════════════════════════════════════════════════
# 1) Total site count is bounded — regression against inflation
# ═════════════════════════════════════════════════════════════════
def test_orchestrator_silent_catch_count_is_bounded():
    """If somebody adds a NEW silent-catch site to orchestrator.py,
    the count exceeds the locked ceiling and this test fails.
    Forces the reviewer to justify each new silent swallow."""
    hits = _collect_silent_catches()
    assert len(hits) == len(LEGIT_UI_HOOK_LINES), (
        f"orchestrator.py silent-catch count changed: found {len(hits)}, "
        f"expected {len(LEGIT_UI_HOOK_LINES)}. New sites likely need "
        f"justification (UI-hook fail-open?) or a logger.debug line."
    )


# ═════════════════════════════════════════════════════════════════
# 2) Every silent-catch must be inside a UI-hook try-body
# ═════════════════════════════════════════════════════════════════
def test_every_orchestrator_silent_catch_is_ui_hook():
    """The disciplined promise from Session 5 Item 2: orchestrator.py
    silent-catches are ONLY allowed as UI-hook fail-open wrappers.
    Any bare `except: pass` outside a hook body is a real hygiene
    target and MUST have a `logger.debug(...)` line ABOVE the swallow."""
    hits = _collect_silent_catches()
    non_hook = [(ln, exc, kind) for ln, exc, kind, is_hook in hits if not is_hook]
    assert not non_hook, (
        f"orchestrator.py has silent-catch site(s) OUTSIDE a UI-hook "
        f"try-body — these are real hygiene targets: {non_hook}\n"
        f"Either wrap them with `logger.debug('[silent-catch] ...')` "
        f"before the swallow, or convert to a hook-guarded pattern."
    )


# ═════════════════════════════════════════════════════════════════
# 3) All 7 sites are on the locked line-number set
# ═════════════════════════════════════════════════════════════════
def test_orchestrator_silent_catch_lines_are_locked():
    """Snapshot test — the 7 known-legit sites are on their expected
    lines. Line drift (someone edits the file above one of these
    sites shifting all downstream line numbers) will trip this. That
    is OK — this test's failure is a signal to re-verify the shifted
    sites and update LEGIT_UI_HOOK_LINES if they're still hooks."""
    hits = _collect_silent_catches()
    actual = {ln for ln, *_ in hits}
    assert actual == LEGIT_UI_HOOK_LINES, (
        f"orchestrator.py silent-catch lines drifted.\n"
        f"  expected: {sorted(LEGIT_UI_HOOK_LINES)}\n"
        f"  actual:   {sorted(actual)}\n"
        f"Verify each shifted site is still a UI-hook fail-open, "
        f"then update LEGIT_UI_HOOK_LINES in this test file."
    )


# ═════════════════════════════════════════════════════════════════
# 4) Every locked site actually contains a hook call
# ═════════════════════════════════════════════════════════════════
def test_locked_sites_still_call_hooks():
    """If someone refactors so an `except: pass` line still exists
    at L2067 but the try-body no longer calls activity_hook (e.g.
    they moved the hook out), the exemption should no longer apply.
    This catches that mutation."""
    hits = _collect_silent_catches()
    hook_lines = {ln for ln, _, _, is_hook in hits if is_hook}
    missing = LEGIT_UI_HOOK_LINES - hook_lines
    assert not missing, (
        f"lines {sorted(missing)} are on the exemption list but their "
        f"try-body no longer calls a hook (activity_hook/step_hook). "
        f"Either restore the hook call OR remove them from "
        f"LEGIT_UI_HOOK_LINES + add a logger.debug()."
    )


# ═════════════════════════════════════════════════════════════════
# 5) Documentation guard — the file mentions the discipline
# ═════════════════════════════════════════════════════════════════
def test_this_test_file_documents_the_no_op_rationale():
    """Meta-test: the module docstring must explain WHY we did nothing.
    Prevents future-me from ripping this file out thinking it's dead."""
    this = Path(__file__).read_text()
    doc = this.split('"""')[1]
    assert "no-op" in doc.lower() or "no_op" in doc.lower() \
        or "legitimate" in doc.lower(), \
        "docstring must document why Item 2 shipped no code changes"
    assert "activity_hook" in doc or "step_hook" in doc, \
        "docstring must name the specific hook pattern being locked"
