"""
services/stripe_client.py — Iter 212m-230

Canonical Stripe key resolver + client bootstrap.  Extracted from
`routers/payments.py` to break the circular dependency
`services/billing_cron → routers/payments → services/billing_cron`
flagged by `architecture_health`.

Both the payments router AND the billing cron service now import
their Stripe key from HERE, restoring the router → service
dependency direction.

Public API
==========
    stripe_key() -> str
        Returns the effective Stripe secret key.  Runtime admin
        override (set via set_runtime_stripe_key) wins over env,
        which in turn wins over the dotenv fallback.  Filters out
        the platform's `sk_test_emergent...` placeholder that
        supervisor injects into every process.

    set_runtime_stripe_key(key: str) -> None
        Hot-swap the key at runtime — used by
        POST /admin/stripe-config so a founder can rotate keys
        without a redeploy.

    stripe_client()
        Returns the `stripe` module with `.api_key` set to
        `stripe_key()`.  Convenience wrapper used by
        billing_cron._stripe_client() and payments'
        webhook_handler().
"""
from __future__ import annotations

import os
from typing import Optional


# ── Runtime override (populated at boot or via /admin/stripe-config) ─
_RUNTIME_STRIPE_KEY: str = ""

# ── Runtime override for Stripe PRICE IDs (Session-fork · 2026-02-09) ─
# Multi-worker split-brain fix. Previously price IDs were sourced from
# `os.environ` per-request AND `routers/payments.py::_RESOLVED_PRICES`
# was a module-level (per-process) cache — so each of the 2 prod uvicorn
# workers could diverge on which plan-id it served (checkerboard failures
# across identical back-to-back checkout tests). This dict mirrors the
# secret-key pattern: single Mongo doc `admin_settings._id="stripe_price_ids"`
# → hydrated at boot into all workers, hot-swapped via admin endpoint.
# Any per-process caching is now forbidden — always read from here.
_RUNTIME_STRIPE_PRICE_IDS: dict[str, str] = {}

# Canonical plan → env-var mapping (single source of truth for the
# env-fallback path). Kept in sync with routers/payments.py::STRIPE_PRICES.
_PLAN_ENV = {
    "starter":        "STRIPE_STARTER_PRICE_ID",
    "pro":            "STRIPE_PRO_PRICE_ID",
    "team":           "STRIPE_TEAM_PRICE_ID",
    "starter_annual": "STRIPE_STARTER_ANNUAL_PRICE_ID",
    "pro_annual":     "STRIPE_PRO_ANNUAL_PRICE_ID",
    "team_annual":    "STRIPE_TEAM_ANNUAL_PRICE_ID",
}
PLAN_IDS = tuple(_PLAN_ENV.keys())


def stripe_key() -> str:
    """Return the effective Stripe secret key.

    Resolution order:
      1. Runtime admin override (highest priority; set via
         `set_runtime_stripe_key()` — e.g. from
         POST /admin/stripe-config).
      2. `STRIPE_SECRET_KEY` env var.
      3. `STRIPE_API_KEY` env var (legacy name).
      4. Values read directly from `.env` via `dotenv_values`
         (defends against supervisor injecting a stale placeholder
         `sk_test_emergent...` that shadows the real key).

    Returns an empty string if none of the sources yield a usable key.
    """
    if (_RUNTIME_STRIPE_KEY
            and not _RUNTIME_STRIPE_KEY.startswith("sk_test_emergent")):
        return _RUNTIME_STRIPE_KEY

    for candidate in (os.environ.get("STRIPE_SECRET_KEY"),
                      os.environ.get("STRIPE_API_KEY")):
        if candidate and not candidate.startswith("sk_test_emergent"):
            return candidate

    # dotenv fallback — bypasses the stale supervisor-exported placeholder.
    try:
        from dotenv import dotenv_values
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".env",
        )
        vals = dotenv_values(env_path)
        for k in ("STRIPE_SECRET_KEY", "STRIPE_API_KEY"):
            v = (vals.get(k) or "").strip().strip('"').strip("'")
            if v and not v.startswith("sk_test_emergent"):
                return v
    except Exception:
        pass
    return ""


def set_runtime_stripe_key(key: str) -> None:
    """Hot-swap the Stripe secret key for this process.

    Used by POST /admin/stripe-config so a founder can rotate the key
    from the admin panel without redeploying.  Also writes to the
    underlying `stripe` SDK so already-imported call-sites pick up
    the new key on their next call.
    """
    global _RUNTIME_STRIPE_KEY
    _RUNTIME_STRIPE_KEY = (key or "").strip()
    if _RUNTIME_STRIPE_KEY:
        try:
            import stripe as _stripe
            _stripe.api_key = _RUNTIME_STRIPE_KEY
        except Exception:
            pass


def stripe_client():
    """Return the `stripe` module with `.api_key` set to `stripe_key()`.
    Callers use this to get a ready-to-go client without repeating the
    2-line key-then-import dance."""
    import stripe as _stripe
    _stripe.api_key = stripe_key()
    return _stripe


# ── Price-ID resolution (single source of truth) ────────────────────
def price_id_for(plan: str) -> str:
    """Return the effective Stripe price ID for a plan.

    Resolution order (matches the secret-key ladder above):
      1. Runtime override — populated at boot from
         `admin_settings._id="stripe_price_ids"` OR live-swapped via
         POST /admin/stripe-prices. Wins over env because it's the
         Mongo-persisted "single truth" across workers/pods.
      2. `STRIPE_<PLAN>_PRICE_ID` env var — legacy per-pod fallback.

    Returns `""` when neither yields a usable price id.
    """
    plan = (plan or "").strip().lower()
    if plan not in _PLAN_ENV:
        return ""
    v = (_RUNTIME_STRIPE_PRICE_IDS.get(plan) or "").strip()
    if v:
        return v
    env_name = _PLAN_ENV[plan]
    return (os.environ.get(env_name) or "").strip()


def set_runtime_stripe_price_ids(mapping: dict) -> None:
    """Hot-swap the Stripe price-ID runtime overrides for this process.

    Accepts a dict `{plan: price_id, ...}` — any keys outside the 6
    canonical plans are silently ignored. Empty/missing values are
    treated as "unset the override for that plan", falling back to env
    on the next `price_id_for()` call.

    Used by:
      * `main.py` lifespan — boot-time hydration from Mongo.
      * `POST /admin/stripe-prices` — founder rotates without restart.
    """
    global _RUNTIME_STRIPE_PRICE_IDS
    m = mapping or {}
    _RUNTIME_STRIPE_PRICE_IDS = {
        plan: (m.get(plan) or "").strip()
        for plan in _PLAN_ENV
        if (m.get(plan) or "").strip()
    }


def get_runtime_stripe_price_ids() -> dict:
    """Return a copy of the current runtime price-ID override dict.
    Empty dict if none set. Used by the admin UI to show what's live."""
    return dict(_RUNTIME_STRIPE_PRICE_IDS)


__all__ = [
    "stripe_key",
    "set_runtime_stripe_key",
    "stripe_client",
    "price_id_for",
    "set_runtime_stripe_price_ids",
    "get_runtime_stripe_price_ids",
    "PLAN_IDS",
]
