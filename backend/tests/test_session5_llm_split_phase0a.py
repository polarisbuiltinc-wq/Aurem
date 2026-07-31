"""
Session 5 · LLM.py 3-Way Split · Phase 0a regression contract.

Locks in the invariant that the state moved into
`services/_llm_state.py` is the SAME OBJECT that `services.llm`
exposes at module scope. Any future refactor that accidentally
creates a shadow copy (e.g. by re-defining `_last_provider_ctx`
in llm.py, or by `from _llm_state import *` in a way that
re-binds) will fail these tests.

ZERO MOCKS. Pure identity + roundtrip assertions.
"""
from __future__ import annotations

import asyncio

import services._llm_state as _state
import services.llm as llm


# ═══ Identity — the tricky "shared mutable" invariants ═════════
def test_last_provider_context_var_is_shared():
    """The ContextVar MUST be the same instance across both modules.
    A shadow copy would silently break child-task provenance."""
    assert llm._last_provider_ctx is _state._last_provider_ctx


def test_longcat_last_probe_dict_is_shared():
    """The probe snapshot dict MUST be shared — `_call_longcat`
    inside llm.py writes to it, and `routers/admin.py` reads
    `from services.llm import _LONGCAT_LAST_PROBE`. Diverge and
    the admin badge lies."""
    assert llm._LONGCAT_LAST_PROBE is _state._LONGCAT_LAST_PROBE


def test_provenance_helpers_are_shared():
    """The 5 provenance functions are re-exported by identity so
    tests that patch `llm.get_last_provider` and code that calls
    `_state._set_last_provider` still see the same behaviour."""
    assert llm._new_provenance_slot is _state._new_provenance_slot
    assert llm._set_last_provider   is _state._set_last_provider
    assert llm.get_last_provider    is _state.get_last_provider
    assert llm.reset_last_provider  is _state.reset_last_provider


# ═══ Behavioural roundtrip ═════════════════════════════════════
def test_provenance_roundtrip_via_llm_module_names():
    """Every call goes through llm.<name>; state observed via the
    canonical `_state` module — proves the shared-mutable dict
    story works end-to-end."""
    llm.reset_last_provider()
    assert llm.get_last_provider() == {
        "provider": "openrouter", "model": "", "is_emergency": False,
    }
    llm._set_last_provider("groq", "llama-3.3-70b-versatile")
    p = llm.get_last_provider()
    assert p["provider"]      == "groq"
    assert p["model"]         == "llama-3.3-70b-versatile"
    assert p["is_emergency"]  is True
    # Same view from the shared dict via _state.
    slot = _state._last_provider_ctx.get()
    assert slot is not p, "get_last_provider must return a COPY"
    assert slot["provider"] == "groq"


def test_longcat_last_probe_mutation_is_visible_from_both_modules():
    """Mutation via llm-side reference must show up on state-side
    read (and vice versa) — the whole point of Phase 0a."""
    original = dict(_state._LONGCAT_LAST_PROBE)
    try:
        llm._LONGCAT_LAST_PROBE["error"] = "phase0a-marker"
        assert _state._LONGCAT_LAST_PROBE["error"] == "phase0a-marker"
        _state._LONGCAT_LAST_PROBE["http_code"] = 429
        assert llm._LONGCAT_LAST_PROBE["http_code"] == 429
    finally:
        _state._LONGCAT_LAST_PROBE.clear()
        _state._LONGCAT_LAST_PROBE.update(original)


# ═══ Phase 0a scope guard ═══════════════════════════════════════
def test_longcat_live_still_owned_by_llm_module_this_phase():
    """Phase 0a INTENTIONALLY leaves `LONGCAT_LIVE` (mutable bool)
    in `llm.py`. Phase 0b will move it with a ModuleType.__setattr__
    hook so external `llm.LONGCAT_LIVE = X` writes route to state.
    This test flags if someone attempts the naive move without the
    hook — that would silently break 5+ tests that do
    `llm_mod.LONGCAT_LIVE = False` writes."""
    assert hasattr(llm, "LONGCAT_LIVE")
    assert isinstance(llm.LONGCAT_LIVE, bool)
    assert not hasattr(_state, "LONGCAT_LIVE"), (
        "Phase 0b not landed yet — `LONGCAT_LIVE` must NOT appear in "
        "_llm_state until the ModuleType.__setattr__ hook on services.llm "
        "is in place, or external writes will silently diverge."
    )


# ═══ ChildTask propagation (the real motivation for the shared dict) ═══
def test_child_task_writes_visible_to_parent_via_shared_dict():
    """The whole reason the provenance stash is a mutable DICT
    (not a raw ContextVar value) is: child asyncio tasks each get
    their own context copy, so `ContextVar.set()` in a child is
    invisible to the parent. Mutating the DICT the ContextVar
    already points to IS visible. Phase 0a must preserve this."""
    async def run():
        llm.reset_last_provider()

        async def child():
            llm._set_last_provider("groq", "child-model")

        await asyncio.create_task(child())
        return llm.get_last_provider()

    p = asyncio.run(run())
    assert p["provider"] == "groq"
    assert p["model"]    == "child-model"
    assert p["is_emergency"] is True
