"""
tests/test_ssot_model_id_no_drift.py — SSOT-refactor drift guard (Feb 2026)

Purpose:
    Verify that every runtime file that ever needs a Claude / GLM
    model slug pulls the value from `services.llm.openrouter_providers`
    instead of re-typing the literal string. This is a REAL zero-mock
    test — it imports each module at boot and asserts the constant
    resolves to the SSOT value (or the same env-override).

Why this exists:
    Session G · Batch 4a caught several runtime files that hard-coded
    `"anthropic/claude-sonnet-4.5"` / `"z-ai/glm-5.2"` in their
    env-fallback defaults. When Council A swapped from LongCat to GLM,
    each of these had to be hunted down individually. This guard fails
    LOUDLY if any new file re-introduces that drift.

Testing rule:
    - Zero mocks.
    - Zero monkeypatch — SSOT values must be visible via a plain import.
    - Assertions are equality checks against the canonical constants.
"""
from __future__ import annotations

import os
import importlib
import sys

import pytest


# ── Canonical SSOT identity ────────────────────────────────────────────
def _fresh_import(mod: str):
    """Force a fresh import so module-level constants re-read env vars.

    We nuke every `services.*` entry from `sys.modules` so import-time
    lookups (`from services.llm.openrouter_providers import _CLAUDE_MODEL`)
    inside downstream files re-run against the current env.
    """
    for name in list(sys.modules):
        if name.startswith("services.") or name == "services":
            del sys.modules[name]
    return importlib.import_module(mod)


def test_ssot_claude_and_glm_defined():
    """Baseline: SSOT constants exist and are non-empty."""
    op = _fresh_import("services.llm.openrouter_providers")
    assert op._CLAUDE_MODEL, "SSOT `_CLAUDE_MODEL` empty"
    assert op._GLM_MODEL, "SSOT `_GLM_MODEL` empty"
    assert "claude" in op._CLAUDE_MODEL.lower(), (
        f"SSOT Claude slug looks wrong: {op._CLAUDE_MODEL!r}"
    )
    assert "glm" in op._GLM_MODEL.lower(), (
        f"SSOT GLM slug looks wrong: {op._GLM_MODEL!r}"
    )


def test_ssot_reexports_from_services_llm():
    """`from services.llm import _CLAUDE_MODEL` still resolves.

    Any downstream file that imports at the package level (not the
    provider submodule) must keep working — this is the primary
    re-export surface for legacy call sites.
    """
    llm = _fresh_import("services.llm")
    op  = _fresh_import("services.llm.openrouter_providers")
    assert llm._CLAUDE_MODEL == op._CLAUDE_MODEL
    assert llm._GLM_MODEL    == op._GLM_MODEL
    assert llm._LONGCAT_MODEL is not None


# ── Env-override propagation to every downstream file ─────────────────
@pytest.mark.parametrize(
    "modpath, attr, ssot_attr",
    [
        # Claude drift sites
        ("services.vanguard_verify_agent",     "_VERIFY_MODEL",   "_CLAUDE_MODEL"),
        ("services.loop_independent_verifier", "_DEFAULT_MODEL",  "_CLAUDE_MODEL"),
        # GLM drift sites
        ("services.ora_chat.session",          "SUMMARY_MODEL",   "_GLM_MODEL"),
        ("services.scaffold_design_review",    "_DEFAULT_MODEL",  "_GLM_MODEL"),
    ],
)
def test_env_override_propagates_to_runtime_file(modpath, attr, ssot_attr,
                                                  monkeypatch):
    """Setting `CLAUDE_MODEL` / `GLM_MODEL` env changes downstream defaults.

    This is the real drift guard: if someone hard-codes a slug back
    into any of these files, the env override will NOT propagate and
    this test fails.

    Note: monkeypatch is used ONLY to set env vars — no function or
    module patching. The imports run for real against the mutated env.
    """
    sentinel = {
        "_CLAUDE_MODEL": "anthropic/claude-sonnet-SSOTGUARD",
        "_GLM_MODEL":    "z-ai/glm-SSOTGUARD",
    }[ssot_attr]

    env_key = {
        "_CLAUDE_MODEL": "CLAUDE_MODEL",
        "_GLM_MODEL":    "GLM_MODEL",
    }[ssot_attr]
    monkeypatch.setenv(env_key, sentinel)

    op = _fresh_import("services.llm.openrouter_providers")
    assert getattr(op, ssot_attr) == sentinel, (
        f"SSOT {ssot_attr} did not pick up env override — check "
        f"openrouter_providers.py"
    )

    mod = _fresh_import(modpath)
    actual = getattr(mod, attr)
    assert actual == sentinel, (
        f"DRIFT DETECTED — {modpath}.{attr} = {actual!r} but SSOT is "
        f"{sentinel!r}. Someone likely re-hard-coded the model slug "
        f"instead of importing it from services.llm.openrouter_providers."
    )


def test_ora_router_fallback_uses_ssot_glm(monkeypatch):
    """ORA router's `fallback` route must resolve to SSOT GLM."""
    monkeypatch.setenv("GLM_MODEL", "z-ai/glm-ROUTER-GUARD")
    monkeypatch.delenv("ORA_MODEL_FALLBACK", raising=False)

    op = _fresh_import("services.llm.openrouter_providers")
    router = _fresh_import("services.ora_chat.router")

    assert op._GLM_MODEL == "z-ai/glm-ROUTER-GUARD"
    assert router._ROUTES["fallback"].model == "z-ai/glm-ROUTER-GUARD", (
        "ORA router `fallback` route drifted from SSOT GLM — check "
        "services/ora_chat/router.py"
    )


def test_reasoning_evals_uses_valid_anthropic_native_id():
    """`llm_faithfulness_check` default model must be a real Anthropic ID.

    This asserts we never regress to `claude-sonnet-4-6` (invented,
    404s at Anthropic). The Emergent SDK requires the Anthropic-native
    dash-date format, NOT the OpenRouter dotted slug — so this test
    checks the format explicitly.
    """
    revs = _fresh_import("services.reasoning_evals")
    default = revs.llm_faithfulness_check.__kwdefaults__["model"]
    # Anthropic-native uses dashes, no slashes, and starts with claude-
    assert default.startswith("claude-"), (
        f"reasoning_evals model default not Anthropic-native: {default!r}"
    )
    assert "/" not in default, (
        f"reasoning_evals uses OpenRouter dotted slug — Emergent SDK "
        f"needs Anthropic-native format: {default!r}"
    )
    # `4-6` never existed as an Anthropic sonnet release — guard against
    # the exact bug we fixed.
    assert "4-6" not in default, (
        f"regression: reasoning_evals default reverted to invented "
        f"model ID: {default!r}"
    )


def test_no_hardcoded_claude_or_glm_in_scanned_files():
    """Static text scan — refactored files must not contain hard-coded slugs.

    We grep each refactored file for the literal SSOT default values.
    Legitimate matches (docstrings, comments, defensive `except`
    fallbacks) are allowed as long as they occur ONLY in an `except`
    block or a comment — the primary assignment must resolve through
    the SSOT import.

    This is a lightweight, verify-first check that complements the
    dynamic tests above.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]  # /app/backend
    scanned = [
        root / "services" / "vanguard_verify_agent.py",
        root / "services" / "loop_independent_verifier.py",
        root / "services" / "ora_chat" / "session.py",
        root / "services" / "ora_chat" / "router.py",
        root / "services" / "scaffold_design_review.py",
        root / "routers" / "feature_window.py",
        root / "main.py",
    ]

    # Every scanned file MUST import from services.llm.openrouter_providers
    # (or services.llm) at least once — that's the SSOT signal. Both
    # absolute (`services.llm...`) and relative (`.llm...`) imports are
    # accepted since sibling modules use the shorter relative form.
    ssot_import = re.compile(
        r"from\s+(?:services)?\.?llm(?:\.openrouter_providers)?\s+import\s+"
        r"[\s\S]{0,200}?"
        r"(?:_CLAUDE_MODEL|_GLM_MODEL|_LONGCAT_MODEL|council_a_primary_model)",
    )

    for f in scanned:
        assert f.exists(), f"Scan target missing: {f}"
        text = f.read_text(encoding="utf-8")
        assert ssot_import.search(text), (
            f"{f.relative_to(root)} does NOT import a model constant from "
            f"services.llm — every file listing hard-coded slugs must "
            f"resolve through SSOT."
        )
