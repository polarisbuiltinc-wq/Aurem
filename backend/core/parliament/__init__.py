"""
core/parliament/__init__.py — 2026-09-08 Phase 3 god-class split.

Re-exports everything that used to live in the single
core/parliament.py (1389 lines) so callers doing `core.parliament.X`
(package-attribute access) or a relative-style import of `X` from
this package keep working unchanged for every existing caller and
test — see `ROADMAP.md` / `CHANGELOG.md` Phase 3 entry.

  Parliament
    ├── TaskRouter   (routing.py)     — picks which Council based on task_type
    ├── CouncilA/B/C (councils.py)    — 3 members @ various temps
    ├── CEO          (ceo.py)         — final picker, output-type-aware temperature
    ├── SelfHeal     (self_heal.py)   — healer.heal() for Verify-phase recovery
    ├── ParliamentCircuitBreaker (breaker.py) — opens after 3 consecutive
    │                                  LLM failures; 45s cooldown
    ├── llm_call.py  — the shared protected LLM call (semaphore + breaker)
    └── scoring.py   — output scoring + output-type detection

Wired into ONLY two places (per founder spec):
  • services/loop_engine.py::_do_execute  — per-file LLM patch
  • services/loop_engine.py::_do_verify   — heal LLM call

All decisions logged to `parliament_log` Mongo collection.
"""
from __future__ import annotations

from core.task_type import infer_task_type  # noqa: F401 (compat re-export)

from .scoring import (
    _strip_fences, _score_output, _score_analysis, _score_writing,
    detect_output_type, CEO_TEMPS, OUTPUT_TYPE_SIGNALS,
)
from .breaker import ParliamentCircuitBreaker, _GLOBAL_BREAKER
from .routing import TaskRouter
from . import llm_call
from .llm_call import _llm_call_protected, MAX_CONCURRENT_LLM_CALLS, _GLOBAL_LLM_SEM
from .councils import (
    _CouncilMember, _Council, CouncilA, CouncilB, CouncilC,
    _COUNCIL_A_PERSONA, _COUNCIL_B_PERSONAS, _COUNCIL_C_PERSONAS,
)
from .ceo import CEO, _ceo_judge_call_with_rescue
from .self_heal import SelfHeal
from .parliament import Parliament

__all__ = [
    "Parliament", "TaskRouter", "CEO", "SelfHeal",
    "CouncilA", "CouncilB", "CouncilC",
    "ParliamentCircuitBreaker",
    "CEO_TEMPS", "OUTPUT_TYPE_SIGNALS", "detect_output_type",
    "MAX_CONCURRENT_LLM_CALLS",
]
