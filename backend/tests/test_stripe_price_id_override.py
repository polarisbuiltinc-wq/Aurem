"""Session-fork · 2026-02-09 — Stripe Price ID DB override.

Regression + behavioural coverage for the multi-worker split-brain fix.

Root-cause context: on prod (2 uvicorn workers per pod, N pods), the
old code path read price IDs from `os.environ` per-request AND kept a
module-level `_RESOLVED_PRICES` cache in `routers/payments.py`. Result
was a checkerboard where identical back-to-back checkout attempts
succeeded for one plan and failed for another depending on which
worker/pod received the request. Fix mirrors the `stripe_api_key`
pattern: single Mongo doc `admin_settings._id="stripe_price_ids"`
hydrated at boot into every worker via a runtime dict in
`services.stripe_client`. `routers/payments.py` no longer caches
anything in-process; every checkout goes through `price_id_for()`.
"""
from __future__ import annotations

import os

import pytest

from services import stripe_client as sc
from routers import payments as pay


class TestPriceIdForResolution:
    """`price_id_for()` resolution ladder: runtime override → env → ""."""

    def setup_method(self):
        # Reset runtime override + env for a clean slate every test.
        sc.set_runtime_stripe_price_ids({})
        for env_name in sc._PLAN_ENV.values():
            os.environ.pop(env_name, None)

    def teardown_method(self):
        sc.set_runtime_stripe_price_ids({})
        for env_name in sc._PLAN_ENV.values():
            os.environ.pop(env_name, None)

    def test_returns_empty_when_nothing_configured(self):
        assert sc.price_id_for("starter") == ""
        assert sc.price_id_for("pro") == ""
        assert sc.price_id_for("team_annual") == ""

    def test_unknown_plan_returns_empty(self):
        assert sc.price_id_for("enterprise") == ""
        assert sc.price_id_for("") == ""

    def test_env_used_when_no_runtime_override(self):
        os.environ["STRIPE_PRO_PRICE_ID"] = "price_env_pro"
        assert sc.price_id_for("pro") == "price_env_pro"

    def test_runtime_override_wins_over_env(self):
        os.environ["STRIPE_PRO_PRICE_ID"] = "price_env_pro"
        sc.set_runtime_stripe_price_ids({"pro": "price_db_pro"})
        assert sc.price_id_for("pro") == "price_db_pro"

    def test_env_fallback_still_works_for_plans_not_in_override(self):
        os.environ["STRIPE_TEAM_PRICE_ID"] = "price_env_team"
        # Runtime dict has starter/pro but not team → team falls to env.
        sc.set_runtime_stripe_price_ids({
            "starter": "price_db_starter",
            "pro":     "price_db_pro",
        })
        assert sc.price_id_for("starter") == "price_db_starter"
        assert sc.price_id_for("pro") == "price_db_pro"
        assert sc.price_id_for("team") == "price_env_team"

    def test_empty_value_in_override_falls_through_to_env(self):
        os.environ["STRIPE_STARTER_PRICE_ID"] = "price_env_starter"
        sc.set_runtime_stripe_price_ids({"starter": "  "})
        # Empty/whitespace values are filtered out on set → env wins.
        assert sc.price_id_for("starter") == "price_env_starter"

    def test_whitespace_trimmed_on_read(self):
        os.environ["STRIPE_PRO_PRICE_ID"] = "  price_env_pro  "
        assert sc.price_id_for("pro") == "price_env_pro"

    def test_all_six_plans_addressable(self):
        mapping = {
            "starter":        "price_a",
            "pro":            "price_b",
            "team":           "price_c",
            "starter_annual": "price_d",
            "pro_annual":     "price_e",
            "team_annual":    "price_f",
        }
        sc.set_runtime_stripe_price_ids(mapping)
        for plan, pid in mapping.items():
            assert sc.price_id_for(plan) == pid


class TestSetRuntime:
    def setup_method(self):
        sc.set_runtime_stripe_price_ids({})

    def test_replaces_entire_dict_not_merges(self):
        sc.set_runtime_stripe_price_ids({"starter": "a"})
        sc.set_runtime_stripe_price_ids({"pro": "b"})
        # `starter` must be gone — set is a full replacement.
        assert sc.get_runtime_stripe_price_ids() == {"pro": "b"}

    def test_extra_keys_are_ignored(self):
        sc.set_runtime_stripe_price_ids({
            "starter":    "a",
            "enterprise": "should_be_dropped",
        })
        assert sc.get_runtime_stripe_price_ids() == {"starter": "a"}

    def test_none_mapping_clears_runtime(self):
        sc.set_runtime_stripe_price_ids({"starter": "a"})
        sc.set_runtime_stripe_price_ids(None)
        assert sc.get_runtime_stripe_price_ids() == {}

    def test_empty_dict_clears_runtime(self):
        sc.set_runtime_stripe_price_ids({"pro": "x"})
        sc.set_runtime_stripe_price_ids({})
        assert sc.get_runtime_stripe_price_ids() == {}

    def test_get_returns_copy_not_reference(self):
        sc.set_runtime_stripe_price_ids({"pro": "x"})
        snap = sc.get_runtime_stripe_price_ids()
        snap["pro"] = "MUTATED"
        assert sc.get_runtime_stripe_price_ids() == {"pro": "x"}


class TestPaymentsRouterUsesResolver:
    """`routers/payments.STRIPE_PRICES[plan]()` must go through
    `services.stripe_client.price_id_for()` — kills the direct
    `os.environ.get()` per-request path that caused the split-brain."""

    def setup_method(self):
        sc.set_runtime_stripe_price_ids({})
        for env_name in sc._PLAN_ENV.values():
            os.environ.pop(env_name, None)

    def teardown_method(self):
        sc.set_runtime_stripe_price_ids({})
        for env_name in sc._PLAN_ENV.values():
            os.environ.pop(env_name, None)

    def test_router_reads_from_runtime_override(self):
        sc.set_runtime_stripe_price_ids({"starter": "price_from_db"})
        assert pay.STRIPE_PRICES["starter"]() == "price_from_db"

    def test_router_falls_back_to_env(self):
        os.environ["STRIPE_TEAM_PRICE_ID"] = "price_from_env"
        assert pay.STRIPE_PRICES["team"]() == "price_from_env"

    def test_router_returns_empty_when_neither_configured(self):
        assert pay.STRIPE_PRICES["pro_annual"]() == ""

    def test_no_module_level_resolved_prices_cache_exists(self):
        """Guard against regressions that re-add `_RESOLVED_PRICES`
        module-level dict — the exact structure that caused the
        multi-worker checkerboard on prod."""
        assert not hasattr(pay, "_RESOLVED_PRICES"), (
            "_RESOLVED_PRICES was re-added — this is the per-process "
            "cache that caused split-brain across the 2 prod uvicorn "
            "workers. Use the DB override via services.stripe_client "
            "instead."
        )


class TestConcurrentWorkerSimulation:
    """Simulates the exact prod condition: two independent 'workers'
    (represented as sequential calls that both go through the SAME
    module-level runtime dict). With the fix in place they must
    converge on the same output for the same input."""

    def setup_method(self):
        sc.set_runtime_stripe_price_ids({})
        for env_name in sc._PLAN_ENV.values():
            os.environ.pop(env_name, None)

    def teardown_method(self):
        sc.set_runtime_stripe_price_ids({})
        for env_name in sc._PLAN_ENV.values():
            os.environ.pop(env_name, None)

    def test_hot_swap_converges_all_readers(self):
        # Both "workers" (call sites) share the same module-level
        # runtime dict. A single set_runtime call makes both see the
        # new value — no per-process cache to diverge.
        sc.set_runtime_stripe_price_ids({
            "starter":        "price_v1_starter",
            "pro":            "price_v1_pro",
            "team":           "price_v1_team",
            "starter_annual": "price_v1_starter_annual",
            "pro_annual":     "price_v1_pro_annual",
            "team_annual":    "price_v1_team_annual",
        })
        # Worker A reads for 6 plans
        wa = {p: sc.price_id_for(p) for p in sc.PLAN_IDS}
        # Founder rotates via /admin/stripe-prices
        sc.set_runtime_stripe_price_ids({
            p: f"price_v2_{p}" for p in sc.PLAN_IDS
        })
        # Worker B reads immediately — must see v2 for ALL 6 plans
        wb = {p: sc.price_id_for(p) for p in sc.PLAN_IDS}
        assert all(v.startswith("price_v2_") for v in wb.values())
        # Worker A on its next read also sees v2 — no split-brain.
        wa2 = {p: sc.price_id_for(p) for p in sc.PLAN_IDS}
        assert wa2 == wb
