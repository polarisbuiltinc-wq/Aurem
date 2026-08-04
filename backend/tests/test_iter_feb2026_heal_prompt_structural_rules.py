"""
test_iter_feb2026_heal_prompt_structural_rules.py — Feb 2026 · Iter 362

Founder P1 (Part A · root cause investigation for the reproducible
verify failure on `uptime_webhook_router.py`):

  The failing runs looked like: LLM writes an initial diff, verify
  catches a structural error (e.g. `import` placed inside a function
  body → ruff E402), self-heal round 1 rewrites but the healer picks
  a fresh (still-wrong) way to preserve the request, self-heal round
  2 does the same → terminal fail.

  Root cause class: the heal prompt didn't tell the healer WHERE to
  put things or that certain classes of edit are constrained. It
  just handed over the lint errors and said "fix". Without explicit
  structural rules, the healer's per-round temperature bump
  (0.1 → 0.2 → 0.3) explored different-but-equally-broken diffs.

Fix: inject explicit structural + import-placement rules into
`heal_task` so the healer has hard constraints to obey on every
round.

This test locks in the presence and content of those rules so a
future refactor of `_do_verify` can't silently drop them.
"""
from __future__ import annotations

from pathlib import Path


def _read_heal_task_construction():
    """Extract the ~60-line window around the `heal_task = (...)`
    assignment so assertions read against the actual runtime string
    construction, not just the whole 4000-line file."""
    src = (Path("/app/backend/services/loop_engine.py")
           .read_text(encoding="utf-8"))
    # Anchor on the assignment inside _do_verify's self-heal loop.
    idx = src.find("heal_task = (")
    assert idx > 0, "heal_task construction site missing"
    return src[idx:idx + 5000]


def test_heal_prompt_has_import_placement_rule():
    """Ruff E402 (import inside function body) is the exact class of
    error that broke the founder's `uptime_webhook_router.py` run.
    The heal prompt must explicitly tell the healer:
      (a) imports go at the top of the file
      (b) never place an import inside a function body
    so round 2 can't repeat round 1's mistake."""
    block = _read_heal_task_construction()
    assert "E402" in block, (
        "heal prompt must reference E402 by name so the healer "
        "recognises the constraint class."
    )
    # Both halves of the rule must be present.
    assert "TOP of the file" in block, (
        "heal prompt must state imports live at the top of the file."
    )
    assert "inside a function body" in block, (
        "heal prompt must forbid imports inside function bodies."
    )


def test_heal_prompt_has_structural_preservation_rule():
    """The healer must not rename/reorder/drop existing symbols
    when fixing lint errors — that produced the 'diff is malformed
    for this file's existing structure' failure class."""
    block = _read_heal_task_construction()
    # Rule 1 keywords.
    assert "Preserve" in block
    assert "signature" in block
    assert "rename" in block or "reorder" in block


def test_heal_prompt_has_targeted_insertion_rule():
    """When the user asks to add validation / error handling, the
    healer must insert it at the first logical position inside the
    target function — not rewrap the whole body. This is the exact
    guidance the founder's Part A step 3 asked for."""
    block = _read_heal_task_construction()
    # NOTE: the source-code phrase spans a Python string concat
    # boundary ("FIRST "\n"logical position …"), so grep both parts.
    assert "FIRST " in block and "logical position" in block, (
        "heal prompt must tell the healer where to insert new "
        "validation lines (Part A step 3 guidance)."
    )
    assert "BEFORE the main side-effect call" in block, (
        "heal prompt must specify the position boundary "
        "(after preamble, before side-effects)."
    )


def test_heal_prompt_has_unused_symbol_rule():
    """F841 (unused local) and F401 (unused import) are the other
    common ways a heal round trips the linter. Rule 5 forbids them."""
    block = _read_heal_task_construction()
    assert "F841" in block and "F401" in block, (
        "heal prompt must forbid leaving unused variables / imports."
    )


def test_heal_prompt_forbids_elision_markers():
    """Rule 6 pins the invariant that the entire final file body is
    returned — no fences, no `... unchanged` placeholders. The
    post-emission integrity guard rejects those, but the healer
    should not emit them in the first place."""
    block = _read_heal_task_construction()
    assert "COMPLETE final file body" in block
    assert "no elision" in block.lower()
