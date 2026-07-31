"""
Session 5 · LLM.py 3-Way Split · Phase 2 regression contract.

Locks in the Phase 2 extraction of `services/_llm_probes.py`:
  - `LONGCAT_LIVE` canonical mutable bool
  - `set_longcat_live(bool)` explicit setter
  - `probe_longcat_availability()` — OpenRouter ping
  - `periodic_longcat_reprobe(interval_seconds)` — background loop
  - `_deepseek_model()` — env-derived slug lookup

The tricky invariant is the `LONGCAT_LIVE` bool routing:
  - Reads via `services.llm.LONGCAT_LIVE` must resolve to
    `_llm_probes.LONGCAT_LIVE` (via module-level `__getattr__`).
  - Writes `llm_mod.LONGCAT_LIVE = X` must land on
    `_llm_probes.LONGCAT_LIVE` (via custom ModuleType.__setattr__).
  - Function-body imports (`from services.llm import LONGCAT_LIVE`
    INSIDE a function body — the pattern used by main.py health,
    routers/admin.py council_health, routers/feature_window.py
    feature_window_status) must see the CURRENT value on every call
    — because they re-import per call, not once at module load.

ZERO MOCKS. Real live routing exercised.
"""
from __future__ import annotations

import services._llm_probes as _probes
import services._llm_state   as _state
import services.llm          as llm


# ═══ Function identity ═════════════════════════════════════════
def test_probe_longcat_availability_is_shared():
    """The `services.llm.probe_longcat_availability` binding MUST be
    the SAME function object as `_llm_probes.probe_longcat_availability`.
    A stale wrapper stub in llm.py would silently shadow the canonical
    body and break every future refactor here."""
    assert llm.probe_longcat_availability is _probes.probe_longcat_availability


def test_periodic_longcat_reprobe_is_shared():
    assert llm.periodic_longcat_reprobe is _probes.periodic_longcat_reprobe


def test_deepseek_model_is_shared():
    assert llm._deepseek_model is _probes._deepseek_model


def test_set_longcat_live_helper_exists_and_is_callable():
    """The helper is what `_call_longcat` in llm.py uses to write
    the flag without needing `global` across modules. Locking it
    in so a future refactor doesn't quietly delete it."""
    assert hasattr(_probes, "set_longcat_live")
    assert callable(_probes.set_longcat_live)


# ═══ Read routing (module __getattr__) ═════════════════════════
def test_llm_longcat_live_read_routes_to_probes():
    """`llm.LONGCAT_LIVE` MUST return `_llm_probes.LONGCAT_LIVE`."""
    original = _probes.LONGCAT_LIVE
    try:
        _probes.LONGCAT_LIVE = True
        assert llm.LONGCAT_LIVE is True
        _probes.LONGCAT_LIVE = False
        assert llm.LONGCAT_LIVE is False
    finally:
        _probes.LONGCAT_LIVE = original


# ═══ Write routing (ModuleType.__setattr__) ════════════════════
def test_llm_longcat_live_write_routes_to_probes():
    """`services.llm.LONGCAT_LIVE = X` must LAND on
    `_llm_probes.LONGCAT_LIVE`. This is what makes the 5+ test
    files that do `llm_mod.LONGCAT_LIVE = False` keep working
    byte-for-byte after Phase 2."""
    original = _probes.LONGCAT_LIVE
    try:
        llm.LONGCAT_LIVE = False
        assert _probes.LONGCAT_LIVE is False, (
            "ModuleType.__setattr__ hook broken — write shadowed llm dict"
        )
        assert llm.LONGCAT_LIVE is False, (
            "READ after WRITE didn't reflect — hook wired wrong way"
        )
        llm.LONGCAT_LIVE = True
        assert _probes.LONGCAT_LIVE is True and llm.LONGCAT_LIVE is True
    finally:
        _probes.LONGCAT_LIVE = original


# ═══ Bool coercion invariant ═══════════════════════════════════
def test_write_coerces_to_bool():
    """`set_longcat_live(1)` must land as `True`, not `1`. This
    keeps the type stable so downstream `if LONGCAT_LIVE:` (which
    would pass either way) doesn't drift into `is True` failures."""
    original = _probes.LONGCAT_LIVE
    try:
        _probes.set_longcat_live(1)
        assert _probes.LONGCAT_LIVE is True
        _probes.set_longcat_live(0)
        assert _probes.LONGCAT_LIVE is False
        _probes.set_longcat_live("yes")
        assert _probes.LONGCAT_LIVE is True
    finally:
        _probes.LONGCAT_LIVE = original


# ═══ External-caller shape — the 3 bare-import sites ═══════════
def test_main_health_handler_shape_still_works():
    """`main.py::health` L1742 does a function-body import of
    `LONGCAT_LIVE`, `LONGCAT_ENABLED`, `council_a_primary_model`.
    Re-implement that exact shape and verify it still resolves."""
    def _like_main_health():
        from services.llm import (
            council_a_primary_model as _council_a_primary_model,
            LONGCAT_LIVE as _LL,
            LONGCAT_ENABLED as _LE,
        )
        return _council_a_primary_model(), bool(_LL), bool(_LE)

    model, live, enabled = _like_main_health()
    assert isinstance(model, str) and model
    assert isinstance(live, bool)
    assert isinstance(enabled, bool)


def test_admin_council_health_handler_shape_still_works():
    """`routers/admin.py::council_health` L403 imports six symbols
    (including two moved to _llm_probes) from services.llm.  Prove
    all six still resolve via the module."""
    from services.llm import (
        _LONGCAT_LAST_PROBE, LONGCAT_ENABLED, LONGCAT_LIVE,
        _LONGCAT_MODEL, _GLM_MODEL, council_a_primary_model,
    )
    assert _LONGCAT_LAST_PROBE is _state._LONGCAT_LAST_PROBE, (
        "Phase 0a state dict no longer shared"
    )
    assert isinstance(LONGCAT_ENABLED, bool)
    assert isinstance(LONGCAT_LIVE, bool)
    assert isinstance(_LONGCAT_MODEL, str) and _LONGCAT_MODEL
    assert isinstance(_GLM_MODEL, str) and _GLM_MODEL
    assert callable(council_a_primary_model)


def test_feature_window_handler_shape_still_works():
    """`routers/feature_window.py::feature_window_status` L92 pulls
    three symbols; verify they resolve + `council_a_primary_model()`
    returns a non-empty slug."""
    from services.llm import (
        council_a_primary_model,
        LONGCAT_LIVE,
        LONGCAT_ENABLED,
    )
    slug = council_a_primary_model()
    assert isinstance(slug, str) and slug
    assert isinstance(LONGCAT_LIVE, bool)
    assert isinstance(LONGCAT_ENABLED, bool)


# ═══ Function-body import propagation (the flap-fear case) ═════
def test_write_via_llm_visible_in_subsequent_function_body_import():
    """Writes to `llm.LONGCAT_LIVE` must be visible to callers that
    do a FRESH function-body import per call — because each such
    caller re-runs `from services.llm import LONGCAT_LIVE`, which
    triggers the module `__getattr__` and returns the CURRENT value.
    This is the real prod scenario for the 3 bare-import sites."""
    def _fresh_read():
        from services.llm import LONGCAT_LIVE as _LL
        return _LL

    original = _probes.LONGCAT_LIVE
    try:
        llm.LONGCAT_LIVE = True
        assert _fresh_read() is True
        llm.LONGCAT_LIVE = False
        assert _fresh_read() is False, (
            "function-body import didn't observe the write — "
            "the ModuleType.__setattr__ hook is not routing correctly"
        )
        llm.LONGCAT_LIVE = True
        assert _fresh_read() is True
    finally:
        _probes.LONGCAT_LIVE = original


# ═══ Council A routing sees LONGCAT_LIVE flips ═════════════════
def test_council_a_primary_reflects_longcat_live_flip(monkeypatch):
    """`council_a_primary_model()` reads `LONGCAT_LIVE` on every call
    via a deferred import (Phase 1). After Phase 2 the READ path is
    `_llm_routing → services.llm → __getattr__ → _llm_probes`.  Flip
    the flag and verify the returned slug flips too — but only when
    `LONGCAT_ENABLED=True`, otherwise the fallback always wins."""
    import importlib
    monkeypatch.setenv("LONGCAT_ENABLED", "true")
    from services import _llm_routing as _routing
    importlib.reload(_routing)
    importlib.reload(llm)

    original = _probes.LONGCAT_LIVE
    try:
        llm.LONGCAT_LIVE = True
        with_live = llm.council_a_primary_model()
        llm.LONGCAT_LIVE = False
        without_live = llm.council_a_primary_model()
        # With LONGCAT_ENABLED=true, the two must DIFFER.
        assert with_live != without_live, (
            f"expected different models when LONGCAT_LIVE flips, "
            f"got with={with_live!r} without={without_live!r}"
        )
        assert with_live  == llm._LONGCAT_MODEL
        assert without_live == llm._GLM_MODEL
    finally:
        _probes.LONGCAT_LIVE = original
