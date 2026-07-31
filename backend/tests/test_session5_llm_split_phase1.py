"""
Session 5 · LLM.py 3-Way Split · Phase 1 regression contract.

Locks in the Phase 1 extraction of `services/_llm_routing.py`:
  - `MAX_TOKENS`, `TEMPERATURE`, `_DEEPSEEK_HOSTS`, `_CLAUDE_MODES`
  - `LONGCAT_ENABLED`, `COUNCIL_B_GLM_ENABLED`, `CEO_RESCUE_ENABLED`
  - `CEO_PRIMARY_TIMEOUT_S`, `CEO_RESCUE_MODEL`
  - `cap_for`, `temperature_for`
  - `council_a_primary_model`, `council_b_primary_model`
    (both use DEFERRED imports of `services.llm` because
    `LONGCAT_LIVE`, `_LONGCAT_MODEL`, `_GLM_MODEL`, `_deepseek_model`
    still live in llm.py during Phase 0b/4.)

All symbols must remain re-exportable via `services.llm.<name>` so
the 45 external importers see byte-for-byte identical behavior.

ZERO MOCKS.
"""
from __future__ import annotations

import services._llm_routing as _routing
import services.llm as llm


# ═══ Container identity — must be SAME dict/set/list ═══════════
def test_max_tokens_is_shared_dict():
    assert llm.MAX_TOKENS is _routing.MAX_TOKENS


def test_temperature_is_shared_dict():
    assert llm.TEMPERATURE is _routing.TEMPERATURE


def test_deepseek_hosts_is_shared_list():
    assert llm._DEEPSEEK_HOSTS is _routing._DEEPSEEK_HOSTS


def test_claude_modes_is_shared_set():
    assert llm._CLAUDE_MODES is _routing._CLAUDE_MODES


# ═══ Env-flag values (immutable at runtime after import) ═══════
def test_v2_flags_value_equal_across_modules():
    assert llm.LONGCAT_ENABLED       == _routing.LONGCAT_ENABLED
    assert llm.COUNCIL_B_GLM_ENABLED == _routing.COUNCIL_B_GLM_ENABLED
    assert llm.CEO_RESCUE_ENABLED    == _routing.CEO_RESCUE_ENABLED
    assert llm.CEO_PRIMARY_TIMEOUT_S == _routing.CEO_PRIMARY_TIMEOUT_S
    assert llm.CEO_RESCUE_MODEL      == _routing.CEO_RESCUE_MODEL


# ═══ Function identity ═════════════════════════════════════════
def test_pure_helpers_are_same_function_object():
    assert llm.cap_for              is _routing.cap_for
    assert llm.temperature_for      is _routing.temperature_for
    assert llm.council_a_primary_model is _routing.council_a_primary_model
    assert llm.council_b_primary_model is _routing.council_b_primary_model


# ═══ Behavioural roundtrip ═════════════════════════════════════
def test_cap_for_known_modes():
    assert llm.cap_for("chat")     == 4000
    assert llm.cap_for("code")     == 3500
    assert llm.cap_for("review")   == 4096
    assert llm.cap_for("title")    == 30
    assert llm.cap_for("advisor")  == 2500
    assert llm.cap_for("write")    == 2500
    assert llm.cap_for("analysis") == 2000


def test_cap_for_unknown_mode_falls_to_default():
    assert llm.cap_for("nonexistent-mode") == llm.MAX_TOKENS["default"]


def test_temperature_for_known_modes():
    assert llm.temperature_for("code")     == 0.0
    assert llm.temperature_for("review")   == 0.0
    assert llm.temperature_for("title")    == 0.0
    assert llm.temperature_for("chat")     == 0.7
    assert llm.temperature_for("analysis") == 0.4
    assert llm.temperature_for("advisor")  == 0.2
    assert llm.temperature_for("write")    == 0.8


def test_temperature_for_unknown_mode_falls_to_default():
    assert llm.temperature_for("nonexistent-mode") == llm.TEMPERATURE["default"]


# ═══ Deferred-import (council_*_primary_model reaches into llm.py) ═══
def test_council_a_primary_model_resolves_to_str():
    """`council_a_primary_model` reads `LONGCAT_ENABLED` from
    routing AND `LONGCAT_LIVE` + `_LONGCAT_MODEL`/`_GLM_MODEL` from
    llm via a deferred import. Deferred import is the whole reason
    Phase 1 can land without waiting for Phase 0b."""
    m = llm.council_a_primary_model()
    assert isinstance(m, str) and m, f"empty/invalid model: {m!r}"
    # Depending on env — one of two known slugs.
    assert m in (llm._LONGCAT_MODEL, llm._GLM_MODEL), (
        f"council_a returned {m!r}; expected _LONGCAT_MODEL or _GLM_MODEL"
    )


def test_council_b_primary_model_resolves_to_str():
    m = llm.council_b_primary_model()
    assert isinstance(m, str) and m
    # Either GLM (when flag on) or deepseek slug.
    assert m == llm._GLM_MODEL or m.startswith("deepseek/"), (
        f"council_b returned {m!r}; expected GLM or deepseek/*"
    )


# ═══ External-caller compatibility — the 45-importer contract ══
def test_all_moved_symbols_still_reachable_via_services_llm():
    """Every symbol moved to `_llm_routing.py` MUST still resolve
    through `services.llm.<name>`. If any of these AttributeError,
    an importer somewhere in the 45-caller surface is about to break."""
    expected = [
        "MAX_TOKENS", "TEMPERATURE",
        "_DEEPSEEK_HOSTS", "_CLAUDE_MODES",
        "LONGCAT_ENABLED", "COUNCIL_B_GLM_ENABLED", "CEO_RESCUE_ENABLED",
        "CEO_PRIMARY_TIMEOUT_S", "CEO_RESCUE_MODEL",
        "cap_for", "temperature_for",
        "council_a_primary_model", "council_b_primary_model",
    ]
    for name in expected:
        assert hasattr(llm, name), (
            f"services.llm.{name} missing — external importer will break"
        )
