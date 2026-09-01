"""
tests/test_copy_gate_no_via_cto.py — Round-2 PR (N3) copy-gate.

"Ship via CTO" was renamed to "Approve the fix" across every real
user-facing string (button label, modal title, disabled hint, chat
reply text, tooltip). This test blocks the banned phrase from ever
reappearing in the specific files that carry user-facing copy.

Deliberately NOT scanned here (scoping call, see PR body):
  - backend/tests/test_intent_gateway_*.py and similar guardrail
    suites that use "ship it via CTO" as example/fixture USER INPUT
    text (simulating colloquial phrasing), not as assistant/button
    copy under test.
  - Historical changelog comments (e.g. "Iter 212m-86 BUG 5 — Ship via
    CTO confirmation modal.") — these describe past work, not current
    copy, and are not user-facing.
"""
from __future__ import annotations

import os

import pytest

_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")

USER_FACING_FILES = [
    os.path.join(_ROOT, "backend", "services", "mode_d_debugger.py"),
    # 2026-09-08 — routers/chat.py (4184-line god-file) was split into
    # a package; scan every submodule instead of the old single file.
    os.path.join(_ROOT, "backend", "routers", "chat", "misc.py"),
    os.path.join(_ROOT, "backend", "routers", "chat", "turn.py"),
    os.path.join(_ROOT, "backend", "routers", "chat", "stream.py"),
    os.path.join(_ROOT, "backend", "routers", "chat", "history.py"),
    os.path.join(_ROOT, "backend", "services", "orchestrator.py"),
    os.path.join(_ROOT, "frontend", "src", "components", "ShipDialog.jsx"),
    os.path.join(_ROOT, "frontend", "src", "components", "ShipConfirmModal.jsx"),
    os.path.join(_ROOT, "frontend", "src", "components", "ChatPanel.jsx"),
    os.path.join(_ROOT, "frontend", "src", "pages", "DashboardPreviewV2.jsx"),
    os.path.join(_ROOT, "frontend", "src", "components", "demo", "demoSteps.jsx"),
]


@pytest.mark.parametrize("path", USER_FACING_FILES)
def test_copy_gate_no_via_cto(path):
    assert os.path.exists(path), f"expected file missing: {path}"
    src = open(path, encoding="utf-8").read()
    assert "via cto" not in src.lower(), (
        f"{path}: found banned copy 'via CTO' — user-facing strings must "
        "say 'Approve the fix' (Round-2 PR, N3). If this hit is a "
        "changelog comment, keep it out of this file or reword it."
    )
