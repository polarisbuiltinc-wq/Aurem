"""
services/_llm_probes.py — LLM.py 3-Way Split · Phase 2

Owns the LongCat live-availability state + the two coroutines that
mutate it, plus the `_deepseek_model()` env-derived slug lookup that
was clustered with them in `llm.py`.

Scope (moved from `services/llm.py`):
  - `LONGCAT_LIVE` — canonical mutable bool (default True, "optimistic").
    Every runtime mutation the codebase performs on `LONGCAT_LIVE`
    now lands here. `services/llm.py` exposes it through a module
    `__getattr__` (reads) + a custom `ModuleType.__setattr__` (writes)
    so external callers still see `services.llm.LONGCAT_LIVE` and
    tests that do `llm_mod.LONGCAT_LIVE = False` still work — the
    write is transparently routed to this canonical location.
  - `set_longcat_live(bool)` — helper used by `_call_longcat` in
    llm.py (still there until Phase 4) so it doesn't need a
    `global` statement across modules.
  - `probe_longcat_availability()` — active OpenRouter ping,
    updates `LONGCAT_LIVE` + the shared `_LONGCAT_LAST_PROBE` dict
    + persists a compact record to Mongo.
  - `periodic_longcat_reprobe(interval_seconds=900)` — background
    coroutine re-probing forever; adaptive fast-retry on degradation.
  - `_deepseek_model()` — pure env-derived slug lookup.

Everything below is imported UNCHANGED at the top of
`services/llm.py` so external access via `services.llm.<name>` keeps
resolving byte-for-byte identically to pre-split behaviour.
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

from services._llm_state import _LONGCAT_LAST_PROBE
from services._llm_routing import LONGCAT_ENABLED

logger = logging.getLogger(__name__)


# ═══ Canonical live flag ════════════════════════════════════════
# Default True (optimistic) — flipped to False by
# `probe_longcat_availability()` on app boot when OpenRouter rejects
# the model slug. When False, `_call_longcat` in `services/llm.py`
# skips the wasted 400-round-trip and goes straight to GLM-5.2. A
# supervisor restart re-probes, so the moment LongCat goes live
# upstream the flag flips back True without a code change.
LONGCAT_LIVE = True


def set_longcat_live(value: bool) -> None:
    """Setter used by `_call_longcat` in `services/llm.py` (Phase 4
    will bring it here too). Explicit setter beats `global`-across-
    modules so the intent is clear and the mutation is
    single-sourced. Live external writes (tests, admin manual
    reprobe endpoint) still work via `services.llm.LONGCAT_LIVE = X`
    thanks to the ModuleType hook on llm.py."""
    global LONGCAT_LIVE
    LONGCAT_LIVE = bool(value)


# ═══ Env-derived model slug lookup ═════════════════════════════
def _deepseek_model() -> str:
    """Council B / C primary. Env override so prod can bump the
    slug without a redeploy (used during OpenRouter model migrations)."""
    return os.getenv("LLM_MODEL", "deepseek/deepseek-chat")


# ═══ Constants used by the probes ═══════════════════════════════
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _openrouter_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "")


def _longcat_model_slug() -> str:
    """Deferred lookup — `_LONGCAT_MODEL` still lives in `services/llm.py`
    (moves in Phase 4 alongside `_call_longcat`). We reach it through
    a module attribute so the probe always sees the current value even
    if a test / admin endpoint monkey-patched `llm._LONGCAT_MODEL`."""
    from services import llm as _llm
    return _llm._LONGCAT_MODEL


# ═══ probe_longcat_availability ═════════════════════════════════
async def probe_longcat_availability() -> bool:
    """Probe OpenRouter to see whether `_LONGCAT_MODEL` is a live slug.

    Sets the module-level `LONGCAT_LIVE` flag (here) and returns the
    resolved boolean. Logs a single WARNING when LongCat is
    unavailable so the on-call sees it once at boot (instead of a
    flood on every call).

    Iter 212m-192 — Also writes an in-memory snapshot into the shared
    `_LONGCAT_LAST_PROBE` dict (from `_llm_state.py`) and persists a
    compact record to the `council_health_probes` Mongo collection so
    the admin dashboard can surface a live "Council A degraded" badge
    without re-probing on each API call. Persistence is best-effort —
    if Mongo is unreachable we still update the in-memory flag so
    callers behave correctly.

    Safe to call from a background task — never raises.
    """
    global LONGCAT_LIVE
    import time as _time

    model_slug = _longcat_model_slug()

    def _snapshot(*, live: bool, http_code, error: str | None) -> None:
        _LONGCAT_LAST_PROBE.update({
            "live":       live,
            "checked_at": _time.time(),
            "http_code":  http_code,
            "error":      error,
            "model":      model_slug,
            "enabled":    LONGCAT_ENABLED,
        })

    async def _persist(*, live: bool, http_code, error: str | None) -> None:
        try:
            from cto_services.db import get_db as _get_db
            db = _get_db()
            if db is not None:
                await db.council_health_probes.insert_one({
                    "council":    "A",
                    "component":  "longcat_primary",
                    "model":      model_slug,
                    "enabled":    LONGCAT_ENABLED,
                    "live":       live,
                    "http_code":  http_code,
                    "error":      error,
                    "checked_at": _LONGCAT_LAST_PROBE["checked_at"],
                })
        except Exception as _e:
            # Persistence failure never masks the probe result — this
            # is intentional fail-open. Log at debug so ops can grep
            # for Mongo trouble without spamming prod.
            logger.debug(
                "[silent-catch] _llm_probes.probe_longcat_availability "
                "— council_health_probes persistence failed: %r", _e,
            )

    if not LONGCAT_ENABLED:
        _snapshot(live=LONGCAT_LIVE, http_code=None,
                  error="longcat_disabled_by_env")
        await _persist(live=LONGCAT_LIVE, http_code=None,
                       error="longcat_disabled_by_env")
        return LONGCAT_LIVE
    api_key = _openrouter_key()
    if not api_key:
        LONGCAT_LIVE = False
        logger.warning(
            "LongCat probe skipped — OPENROUTER_API_KEY missing. "
            "Council A will use GLM-5.2 fallback."
        )
        _snapshot(live=False, http_code=None,
                  error="openrouter_api_key_missing")
        await _persist(live=False, http_code=None,
                       error="openrouter_api_key_missing")
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       model_slug,
                    "messages":    [{"role": "user", "content": "ping"}],
                    "max_tokens":  1,
                    "temperature": 0,
                },
            )
    except Exception as e:
        LONGCAT_LIVE = False
        logger.warning(
            "LongCat probe network error (%r) — assuming unavailable. "
            "Council A will use GLM-5.2 fallback until next restart.", e,
        )
        _snapshot(live=False, http_code=None, error=f"network_error: {e!r}"[:200])
        await _persist(live=False, http_code=None, error=f"network_error: {e!r}"[:200])
        return False
    if r.status_code == 200:
        LONGCAT_LIVE = True
        logger.info("✅ LongCat probe OK — Council A primary = %s", model_slug)
        _snapshot(live=True, http_code=200, error=None)
        await _persist(live=True, http_code=200, error=None)
        return True
    # Iter 212m-221 — 429 rate-limit is NOT unavailability. The model
    # is alive on OpenRouter, we just hit the throttle. Keep the flag
    # green so Council A doesn't spend the next 15 min running on the
    # GLM-5.2 fallback (and showing "degraded" in the founder Advisor
    # brief) just because a health-check burnt a token quota tick.
    if r.status_code == 429:
        LONGCAT_LIVE = True
        logger.info(
            "LongCat probe rate-limited (429) — model reachable, "
            "keeping Council A on %s. Reprobe in 15 min.", model_slug,
        )
        _snapshot(live=True, http_code=429, error="rate_limited_but_reachable")
        await _persist(live=True, http_code=429, error="rate_limited_but_reachable")
        return True
    # 400 invalid-model / 404 no-endpoints / 5xx upstream → treat as unavailable
    try:
        err_msg = (r.json().get("error") or {}).get("message") or r.text[:120]
    except Exception:
        err_msg = r.text[:120]
    LONGCAT_LIVE = False
    logger.warning(
        "LongCat unavailable (HTTP %s: %s) — Council A on GLM-5.2 fallback "
        "until the next probe. Re-probe runs every 15 min in the "
        "background; a supervisor restart triggers an immediate re-probe. "
        "Live status is exposed at /api/aurem-dev/admin/council/health.",
        r.status_code, err_msg,
    )
    _snapshot(live=False, http_code=r.status_code, error=str(err_msg)[:200])
    await _persist(live=False, http_code=r.status_code, error=str(err_msg)[:200])
    return False


# ═══ periodic_longcat_reprobe ═══════════════════════════════════
async def periodic_longcat_reprobe(interval_seconds: int = 900) -> None:
    """Re-probe LongCat on a `interval_seconds` cadence forever.

    Iter 212m-192 — Startup-only probing meant a LongCat outage that
    resolved upstream stayed masked until the next supervisor restart.
    This coroutine keeps the flag fresh so the moment upstream comes
    back, Council A auto-recovers within the interval window.

    Iter 212m-221 — Adaptive backoff on failure. When the last probe
    said `live=False` we back off to 60 s (not 15 min) so a transient
    OpenRouter blip doesn't lock the Advisor brief into a "degraded"
    badge for 14 more minutes. A successful probe returns to the
    slow 15 min cadence.

    Runs quietly: only logs on a state transition (live ↔ degraded).
    """
    FAST_INTERVAL_S = 60
    if not LONGCAT_ENABLED:
        return
    model_slug = _longcat_model_slug()
    while True:
        previous = LONGCAT_LIVE
        try:
            current = await probe_longcat_availability()
        except Exception as e:  # pragma: no cover — defensive belt
            logger.warning("periodic_longcat_reprobe unexpected error: %r", e)
            current = LONGCAT_LIVE
        if current != previous:
            logger.warning(
                "🔁 Council A state transition: %s → %s (model=%s)",
                "LIVE" if previous else "DEGRADED",
                "LIVE" if current else "DEGRADED",
                model_slug,
            )
        # Fast retry when degraded; slow cadence when healthy.
        sleep_for = FAST_INTERVAL_S if not current else interval_seconds
        try:
            await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            return
