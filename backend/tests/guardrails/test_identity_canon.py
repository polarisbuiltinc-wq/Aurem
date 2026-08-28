"""
tests/guardrails/test_identity_canon.py — NAMING & IDENTITY CANON PR
(2026-08). Locks the root-fix: both system prompts consume the shared
services/identity.py::OR_IDENTITY constant, and the literal string
"You are AUREM" never reappears in any prompt source file.
"""
from __future__ import annotations

import os

import pytest

from services.identity import OR_IDENTITY

ORCHESTRATOR_PY = os.path.join(os.path.dirname(__file__), "..", "..", "services", "orchestrator.py")
LOOP_ENGINE_PY  = os.path.join(os.path.dirname(__file__), "..", "..", "services", "loop_engine.py")


def test_identity_constant_used_by_both():
    """Both prompt builders must consume the shared OR_IDENTITY constant
    — not a hand-rolled "You are ..." line of their own."""
    orch_src = open(ORCHESTRATOR_PY, encoding="utf-8").read()
    loop_src = open(LOOP_ENGINE_PY, encoding="utf-8").read()
    assert "identity import OR_IDENTITY" in orch_src, (
        "orchestrator.py must import the shared OR_IDENTITY constant"
    )
    assert "identity import OR_IDENTITY" in loop_src, (
        "loop_engine.py must import the shared OR_IDENTITY constant"
    )
    assert "OR_IDENTITY" in orch_src
    assert "OR_IDENTITY" in loop_src
    assert OR_IDENTITY, "OR_IDENTITY must not be empty"


@pytest.mark.parametrize("path", [ORCHESTRATOR_PY, LOOP_ENGINE_PY])
def test_no_you_are_aurem(path):
    """Grep-guard: the literal drift string "You are AUREM" must never
    reappear in any prompt source file again."""
    src = open(path, encoding="utf-8").read()
    assert "You are AUREM" not in src, (
        f"{path}: found banned self-identification string 'You are AUREM' "
        "— ORA must never call itself AUREM in a system prompt."
    )
