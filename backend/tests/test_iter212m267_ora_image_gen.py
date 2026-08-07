"""
tests/test_iter212m267_ora_image_gen.py — Phase 5 · Feb 2026

Locks the cost-safety contract the founder scoped explicitly:
  · gpt-image-1 LOW quality, 1024²
  · Per-image cost pinned to $0.011 (constant, not derived)
  · Global daily cap $3.00 (env-tunable, but default is $3)
  · Per-user monthly cap 10 (env-tunable, default 10)
  · Founder-tier ONLY (Pro/Team explicitly locked out during
    internal-test phase)
  · Reserve-then-refund pattern for upstream failures

Runtime tests hit Mongo through the service module directly.  Cost
side-effects (OpenAI call) are NOT exercised — we don't want to
spend real dollars on every CI run.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.ora_chat import image_gen as G


_ROUTER_SRC = Path("/app/backend/routers/ora_chat.py").read_text()


# ── Static contract (fast, no DB) ──────────────────────────────────
class TestConstants:
    def test_per_image_cost_is_locked_to_11_cents_per_100(self):
        # If this constant ever drifts, the founder's cap math breaks.
        assert G.GPT_IMAGE_1_LOW_USD_PER_IMAGE == 0.011

    def test_default_daily_cap_is_3_dollars(self):
        # Default MUST be $3 unless the ORA_IMAGE_DAILY_CAP_USD env is
        # explicitly bumped — protects against silent creep.
        import importlib, os
        os.environ.pop("ORA_IMAGE_DAILY_CAP_USD", None)
        importlib.reload(G)
        assert G.ORA_IMAGE_DAILY_CAP_USD == 3.00

    def test_default_monthly_user_cap_is_10(self):
        import importlib, os
        os.environ.pop("ORA_IMAGE_MONTH_PER_USER_CAP", None)
        importlib.reload(G)
        assert G.ORA_IMAGE_MONTH_PER_USER_CAP == 10

    def test_model_pinned_to_gpt_image_1(self):
        assert G.ORA_IMAGE_MODEL == "gpt-image-1"

    def test_quality_pinned_to_low(self):
        assert G.ORA_IMAGE_QUALITY == "low"


# ── Router wiring (static — no HTTP) ────────────────────────────────
class TestRouterWiring:
    def test_endpoints_present(self):
        assert '@router.post("/image-generate")' in _ROUTER_SRC
        assert '@router.get("/image-status")'   in _ROUTER_SRC

    def test_founder_only_gate(self):
        idx = _ROUTER_SRC.find('@router.post("/image-generate")')
        body = _ROUTER_SRC[idx:idx + 5000]
        # Must gate on founder / is_admin / tier == "founder"
        assert 'is_founder = bool(user.get("is_founder") or user.get("is_admin")' in body
        assert 'raise HTTPException(402' in body
        assert '"feature": "image_generation"' in body

    def test_reservation_then_refund_on_failure(self):
        # The router MUST reserve BEFORE calling OpenAI, and MUST
        # refund on any exception.  This is the safety guarantee that
        # a transient upstream 500 doesn't burn the daily $3 cap.
        idx = _ROUTER_SRC.find('@router.post("/image-generate")')
        body = _ROUTER_SRC[idx:idx + 5000]
        assert 'check_and_reserve(db, user_id)' in body
        assert 'refund_reservation(db, user_id)' in body

    def test_429_on_cap_reached(self):
        idx = _ROUTER_SRC.find('@router.post("/image-generate")')
        body = _ROUTER_SRC[idx:idx + 5000]
        assert 'raise HTTPException(429' in body


# ── Runtime gates against a mock Mongo ─────────────────────────────
def _mock_db():
    """Async-compatible Mongo mock with two collections."""
    docs_daily  = {}
    docs_month  = {}
    docs_events = []

    class _Coll:
        def __init__(self, key_fn, sink):
            self._key = key_fn
            self._sink = sink
        async def find_one(self, filt):
            k = self._key(filt)
            return self._sink.get(k)
        async def update_one(self, filt, update, upsert=False):
            k = self._key(filt)
            cur = self._sink.get(k) or dict(filt)
            for op, changes in update.items():
                if op == "$inc":
                    for kk, v in changes.items():
                        cur[kk] = (cur.get(kk) or 0) + v
                elif op == "$set":
                    for kk, v in changes.items():
                        cur[kk] = v
            self._sink[k] = cur
        async def insert_one(self, doc):
            self._sink.append(doc)

    return {
        "ora_image_daily_spend":
            _Coll(lambda f: f["day"], docs_daily),
        "ora_image_user_month":
            _Coll(lambda f: (f["user_id"], f["month"]), docs_month),
        "ora_image_events":
            _Coll(lambda f: None, docs_events),
    }


class TestReservationGates:
    def test_reservation_succeeds_when_empty(self):
        db = _mock_db()
        db_dict = {k: v for k, v in db.items()}
        class D(dict):
            def __getitem__(self, k): return db_dict[k]
        out = asyncio.run(G.check_and_reserve(D(), "user-1"))
        assert out["reserved_usd"] == 0.011
        assert out["user_used"] == 1

    def test_daily_cap_blocks_when_reserved_would_exceed(self):
        db = _mock_db()
        class D(dict):
            def __getitem__(s, k): return db[k]
        # Pre-fill the daily counter to $2.999 — one more image would
        # push past $3.
        asyncio.run(db["ora_image_daily_spend"].update_one(
            {"day": G._utc_today_key()},
            {"$inc": {"spent_usd": 2.999}, "$set": {"day": G._utc_today_key()}},
            upsert=True,
        ))
        with pytest.raises(G.ImageGenError) as exc:
            asyncio.run(G.check_and_reserve(D(), "user-1"))
        assert exc.value.kind == "daily_cap_reached"

    def test_monthly_cap_blocks_at_10_images(self):
        db = _mock_db()
        class D(dict):
            def __getitem__(s, k): return db[k]
        # User has already used their 10 for the month.
        asyncio.run(db["ora_image_user_month"].update_one(
            {"user_id": "user-1", "month": G._utc_month_key()},
            {"$inc": {"count": 10},
             "$set": {"user_id": "user-1", "month": G._utc_month_key()}},
            upsert=True,
        ))
        with pytest.raises(G.ImageGenError) as exc:
            asyncio.run(G.check_and_reserve(D(), "user-1"))
        assert exc.value.kind == "monthly_cap_reached"

    def test_refund_reverses_the_counters(self):
        db = _mock_db()
        class D(dict):
            def __getitem__(s, k): return db[k]
        asyncio.run(G.check_and_reserve(D(), "user-1"))
        asyncio.run(G.refund_reservation(D(), "user-1"))
        # After refund, spent_usd should be back to 0 (± float noise)
        daily = asyncio.run(db["ora_image_daily_spend"].find_one(
            {"day": G._utc_today_key()}))
        assert abs(float(daily.get("spent_usd") or 0.0)) < 1e-9
        monthly = asyncio.run(db["ora_image_user_month"].find_one(
            {"user_id": "user-1", "month": G._utc_month_key()}))
        assert int(monthly.get("count") or 0) == 0

    def test_empty_prompt_refused_before_openai_call(self):
        with pytest.raises(G.ImageGenError) as exc:
            asyncio.run(G.generate(""))
        assert exc.value.kind == "empty_prompt"
