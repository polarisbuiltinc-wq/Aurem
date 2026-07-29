"""Iter 353 — audit round 3 locks.

1. Speed diagnostic subtracts PAUSED_FOR_USER wait from phase durations
   (ship "avg 120s" was mostly the human confirm-click wait).
2. Shared cleanErr sanitizer wired into Financials + API Keys pages.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _FakeDB:
    def __init__(self, docs):
        self._docs = docs

    @property
    def loop_events(self):
        return self

    def find(self, *_a, **_k):
        return _FakeCursor(self._docs)


@pytest.mark.asyncio
async def test_ship_duration_excludes_user_confirm_wait():
    from services.loop_speed_diagnostic import _phase_durations_from_events
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    def ev(offset_s, phase, state=""):
        return {"phase": phase, "state": state,
                "ts": (t0 + timedelta(seconds=offset_s)).isoformat()}

    docs = [
        ev(0,   "plan"),
        ev(10,  "execute"),
        ev(40,  "ship"),                                  # ship starts
        ev(42,  "ship", state="paused_for_user"),         # waits for click
        ev(162, "ship", state="shipping"),                # user confirmed +120s
        ev(170, "ship", state="completed"),
    ]
    d = await _phase_durations_from_events(_FakeDB(docs), "loop_test353")
    # Raw ship wall-clock = 130s, of which 120s was human wait.
    assert d["ship"] == pytest.approx(10.0, abs=0.5), (
        f"ship must exclude the 120s user-confirm pause, got {d['ship']}")
    assert d["plan"] == pytest.approx(10.0, abs=0.5)
    assert d["execute"] == pytest.approx(30.0, abs=0.5)


@pytest.mark.asyncio
async def test_durations_without_pauses_unchanged():
    from services.loop_speed_diagnostic import _phase_durations_from_events
    t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    docs = [
        {"phase": "plan", "state": "", "ts": t0.isoformat()},
        {"phase": "ship", "state": "",
         "ts": (t0 + timedelta(seconds=50)).isoformat()},
        {"phase": "ship", "state": "completed",
         "ts": (t0 + timedelta(seconds=60)).isoformat()},
    ]
    d = await _phase_durations_from_events(_FakeDB(docs), "loop_test353b")
    assert d["plan"] == pytest.approx(50.0, abs=0.5)
    assert d["ship"] == pytest.approx(10.0, abs=0.5)


def test_clean_err_wired_into_admin_pages():
    for path, needle in [
        ("/app/frontend/src/pages/AdminFinancials.jsx", "cleanErr(e"),
        ("/app/frontend/src/pages/AdminApiKeys.jsx", "cleanErr(e"),
        ("/app/frontend/src/components/AdminHouseRules.jsx", "cleanErr(e"),
    ]:
        src = open(path).read()
        assert 'from "../lib/cleanErr"' in src or "lib/cleanErr" in src, path
        assert needle in src, path


def test_financials_error_state_has_retry():
    src = open("/app/frontend/src/pages/AdminFinancials.jsx").read()
    assert 'data-testid="financials-retry-btn"' in src


# ── Iter 354 — round-4 audit locks ───────────────────────────────────
def test_dollars_preserves_negative_sign():
    src = open("/app/frontend/src/pages/AdminFinancials.jsx").read()
    assert "return `-$${v}`" in src, (
        "dollars() must render negative values with a minus sign "
        "(net LOSS was showing as positive $223.77)")
    assert '.replace("$-"' not in src


def test_api_keys_count_not_zero_on_fetch_error():
    src = open("/app/frontend/src/pages/AdminApiKeys.jsx").read()
    assert 'err ? "?" : keys.length' in src, (
        "fetch-error state must not claim 'Active keys (0)'")


def test_net_profit_formula_is_mrr_minus_burn():
    src = open("/app/backend/services/financials.py").read()
    assert "net_profit = mrr - total_burn" in src
