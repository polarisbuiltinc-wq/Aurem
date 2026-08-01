"""services/_llm_probes.py — Backward-compat shim (Session C, Sub-step 2).

The real module now lives at `services/llm/_probes.py`. This shim
keeps `from services import _llm_probes` and
`from services._llm_probes import ...` call sites working without
an atomic mass-edit.

DO NOT add new code here.
TODO(session-Z): remove once all imports migrate to `services.llm._probes`.
"""
# ruff: noqa: F401 — re-export module only.
from services.llm._probes import (  # noqa: F401
    probe_longcat_availability,
    periodic_longcat_reprobe,
    _deepseek_model,
    LONGCAT_LIVE,
    set_longcat_live,
)
# Bind the real module so any `services._llm_probes` attribute access
# (e.g. `_probes.LONGCAT_LIVE`) resolves against the canonical module.
# Callers that do `from services import _llm_probes as _probes` still
# get the shim namespace — hence the individual re-exports above.
from services.llm import _probes as _real_module  # noqa: F401
