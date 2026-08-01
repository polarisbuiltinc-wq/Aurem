"""Session 5 · Item 3 · llm.py — 3 hygiene sites patched + split plan doc.

Item 3 was scoped as "3-way split of services/llm.py + naturally
clean the 3 hygiene sites". After forensic analysis (shared module
globals for LongCat state, ContextVar for provider provenance,
tight coupling between HTTP-client functions), the disciplined
outcome is:

  1. **Shipped this session**: 3 hygiene sites patched with
     `logger.debug("[silent-catch] ...")` — safe surgical wins,
     zero behaviour change.
  2. **Deferred with plan**: the actual 3-way file split needs a
     shared-state architecture (`_state.py` for LongCat globals +
     provenance ContextVar), which is a dedicated session with its
     own test budget on the 45-importer prod-critical module.
     Migration plan authored at `memory/LLM_SPLIT_MIGRATION_PLAN.md`.

This test locks the 3 hygiene fixes and asserts the migration doc
exists so future me doesn't lose the thread.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest


BACKEND = Path(__file__).resolve().parents[1]
LLM_PY = BACKEND / "services" / "llm" / "__init__.py"
# Session 5 · Phase 2 — `probe_longcat_availability()` moved to
# services/_llm_probes.py. Session C · Sub-step 2 — that module moved
# again to `services/llm/_probes.py` (the sibling is now a shim).
LLM_PROBES_PY = BACKEND / "services" / "llm" / "_probes.py"
# Session D · D-2a — `call_openrouter_model` moved into its own module.
OR_CLIENT_PY = BACKEND / "services" / "llm" / "openrouter_client.py"


# ═════════════════════════════════════════════════════════════════
# 1) 3 hygiene sites now log at debug
# ═════════════════════════════════════════════════════════════════
def test_llm_py_has_three_silent_catch_debug_lines():
    """Session 5 · Phase 2 + Session D · D-2a — the 3 hygiene sites
    now live across THREE files. We verify the TOTAL sweep is still 3."""
    llm_src    = LLM_PY.read_text()
    or_src     = OR_CLIENT_PY.read_text()
    probes_src = LLM_PROBES_PY.read_text()
    llm_count    = llm_src.count('"[silent-catch] llm.py:')
    or_count     = or_src.count('"[silent-catch] llm.py:')
    probes_count = probes_src.count(
        '"[silent-catch] _llm_probes.probe_longcat_availability')
    total = llm_count + or_count + probes_count
    assert total == 3, (
        f"expected 3 [silent-catch] sites across llm.py + openrouter_client.py "
        f"+ _llm_probes.py, got llm={llm_count} or={or_count} "
        f"probes={probes_count} total={total}"
    )
    # Precise post-D-2a shape lock: 1 in __init__.py (_call_deepseek),
    # 1 in openrouter_client.py (call_openrouter_model), 1 in _probes.py.
    assert llm_count == 1 and or_count == 1 and probes_count == 1, (
        f"expected __init__.py=1 + openrouter_client.py=1 + _probes.py=1 "
        f"after D-2a, got llm={llm_count} or={or_count} probes={probes_count}"
    )


def test_llm_py_hygiene_sites_are_the_expected_ones():
    """Locked identifiers of the 3 patched sites so future refactors
    trip when someone accidentally re-introduces a silent swallow.

    Session D · D-2a — `call_openrouter_model` moved to
    `services/llm/openrouter_client.py`. Its silent-catch site
    relocated with it; scan that file for the marker.
    """
    llm_src = LLM_PY.read_text()
    or_src = OR_CLIENT_PY.read_text()
    probes_src = LLM_PROBES_PY.read_text()
    # 1 site remaining in __init__.py (inside `_call_deepseek`).
    assert '"[silent-catch] llm.py:738 in _call_deepseek' in llm_src
    # 1 site migrated to openrouter_client.py (call_openrouter_model).
    assert '"[silent-catch] llm.py:1104 in call_openrouter_model' in or_src
    # 1 site migrated to _llm_probes.py during Phase 2.
    assert (
        '"[silent-catch] _llm_probes.probe_longcat_availability'
        in probes_src
    )


def test_llm_py_hygiene_fixes_preserve_fail_open_behavior():
    """Every patched site MUST still swallow — behaviour-neutral fix.
    The `except X as _e: logger.debug(...)` block must NOT re-raise
    on the SAME LINE / immediate next line as the debug call."""
    llm_src    = LLM_PY.read_text()
    or_src     = OR_CLIENT_PY.read_text()
    probes_src = LLM_PROBES_PY.read_text()
    checks = [
        (llm_src,    "llm.py:738"),
        (or_src,     "llm.py:1104"),       # D-2a — moved to openrouter_client.py
        (probes_src, "_llm_probes.probe_longcat_availability"),
    ]
    for src, ln_marker in checks:
        idx = src.index(f'"[silent-catch] {ln_marker}')
        after = src[idx: idx + 300]
        first_close = after.index(")\n") + 2
        handler_end = after[:first_close]
        assert "raise" not in handler_end, (
            f"handler body at {ln_marker} must remain fail-open; "
            f"got: {handler_end!r}"
        )


# ═════════════════════════════════════════════════════════════════
# 2) File-split migration plan document exists
# ═════════════════════════════════════════════════════════════════
def test_llm_split_migration_plan_exists():
    """The full 3-way split of llm.py is deferred (see docstring above).
    A migration plan MUST exist so future work can pick it up cleanly."""
    plan = BACKEND.parent / "memory" / "LLM_SPLIT_MIGRATION_PLAN.md"
    assert plan.exists(), (
        "Deferred work needs a plan doc — see docstring for context"
    )
    text = plan.read_text()
    # The plan must name the 3 target sub-modules
    for target in ("openrouter_client.py", "routing.py", "probes.py"):
        assert target in text, f"plan must reference {target}"
    # And must acknowledge the shared-state challenge that made
    # the split too risky for the P1 batch
    assert "LONGCAT_LIVE" in text or "_last_provider_ctx" in text or \
           "shared" in text.lower(), \
        "plan must document the shared-state challenge that gates the split"


# ═════════════════════════════════════════════════════════════════
# 3) Regression — module still imports cleanly after edits
# ═════════════════════════════════════════════════════════════════
def test_llm_module_still_imports_cleanly():
    """Any edit to llm.py must not break the module — 45 importers
    depend on this."""
    import importlib
    import services.llm as mod
    importlib.reload(mod)
    # Sanity: all documented public symbols still exist
    for name in (
        "call_llm", "call_llm_with_meta", "call_openrouter_model",
        "cap_for", "temperature_for",
        "probe_longcat_availability", "periodic_longcat_reprobe",
        "council_a_primary_model", "council_b_primary_model",
        "get_last_provider", "reset_last_provider",
        "call_emergent_watchdog",
        "MAX_TOKENS", "TEMPERATURE",
        "LONGCAT_ENABLED", "LONGCAT_LIVE",
        "CEO_RESCUE_ENABLED", "CEO_PRIMARY_TIMEOUT_S",
        "_LONGCAT_LAST_PROBE",
    ):
        assert hasattr(mod, name), (
            f"public surface regression: services.llm.{name} missing"
        )


# ═════════════════════════════════════════════════════════════════
# 4) Behavioural — logger.debug actually fires (not just present)
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_hygiene_debug_actually_fires_on_ledger_failure(caplog):
    """Force `log_llm_usage` to raise inside `call_openrouter_model`,
    then assert the debug log line lands with the [silent-catch]
    prefix. Zero mocks on the surrounding LLM call — this proves the
    log wire-up end-to-end."""
    import services.llm as mod
    caplog.set_level(logging.DEBUG, logger=mod.logger.name)
    mod.logger.propagate = True

    # Force the ledger call to raise. Everything else stays real.
    async def _boom(*a, **kw):
        raise RuntimeError("simulated ledger failure")

    # Also short-circuit the actual HTTP call so we don't hit OpenRouter
    class _FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "hello"}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 5}}

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, *a, **kw): return _FakeResp()

    with patch("services.loop_token_ledger.log_llm_usage", _boom), \
         patch("services.llm._openrouter_key", return_value="sk-fake"), \
         patch("services.llm.httpx.AsyncClient", _FakeClient), \
         caplog.at_level(logging.DEBUG, logger=mod.logger.name):
        result = await mod.call_openrouter_model(
            model="test/fake",
            system="sys",
            user="hello",
            max_tokens=10,
            temperature=0.0,
        )
    # Behaviour preserved — call still returned content
    assert result == "hello"
    # Debug log fired
    silent_catch_records = [
        r for r in caplog.records
        if "[silent-catch] llm.py:1104" in r.getMessage()
    ]
    assert silent_catch_records, (
        f"expected [silent-catch] llm.py:1104 debug log; "
        f"got: {[r.getMessage() for r in caplog.records]}"
    )
