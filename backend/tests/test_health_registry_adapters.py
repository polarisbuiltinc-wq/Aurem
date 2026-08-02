"""
tests/test_health_registry_adapters.py — Adapter unit tests (Feb 2026)

Per founder directive: every guard adapter needs a small unit test
that confirms the raw-payload → 3-state translation is correct. Two
kinds of assertions per adapter:

  1. Semantic — the RIGHT status for a given underlying state
     (e.g. G7 last_run=None → gray, NOT red).
  2. Shape — the return matches services.health_registry contract
     (`status` ∈ {green, red, gray}, `detail` non-empty, `checked_at`
     is ISO-8601-ish).

Adapters call REAL underlying functions in the app. We stub only
the deepest data source (Mongo find_one, snapshot_all) — never
the adapter itself. If a guard's raw shape changes upstream and
the adapter starts collapsing gray→red or misclassifies, these
tests fail LOUDLY.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import health_checks  # noqa: E402  — registers checks
from services.health_registry import (   # noqa: E402
    all_checks, get_check, run_check_safely,
)


def _shape_ok(res: dict, expected_status: str) -> None:
    assert isinstance(res, dict)
    assert res["status"] == expected_status, (
        f"expected status={expected_status!r}, got {res['status']!r} · "
        f"detail={res.get('detail')!r}"
    )
    assert res.get("detail"), "detail must be non-empty for cockpit rendering"
    assert res.get("checked_at"), "checked_at must be set"
    assert isinstance(res["checked_at"], str)


# ─────────────────────────────────────────────────────────────
# G1 · Route Sweep
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g1_gray_when_no_runs(monkeypatch):
    class _DB:
        class synthetic_checks:
            @staticmethod
            async def find_one(*a, **kw):
                return None
    monkeypatch.setattr("cto_services.db.get_db", lambda: _DB())
    res = await health_checks._check_g1_route_sweep()
    _shape_ok(res, "gray")
    assert "no g1 runs" in res["detail"].lower()


@pytest.mark.asyncio
async def test_g1_green_when_zero_failures(monkeypatch):
    class _DB:
        class synthetic_checks:
            @staticmethod
            async def find_one(*a, **kw):
                return {"failed": 0, "total": 42, "finished_at": "..."}
    monkeypatch.setattr("cto_services.db.get_db", lambda: _DB())
    res = await health_checks._check_g1_route_sweep()
    _shape_ok(res, "green")
    assert "42" in res["detail"]


@pytest.mark.asyncio
async def test_g1_red_when_failures_present(monkeypatch):
    class _DB:
        class synthetic_checks:
            @staticmethod
            async def find_one(*a, **kw):
                return {"failed": 3, "total": 42, "finished_at": "..."}
    monkeypatch.setattr("cto_services.db.get_db", lambda: _DB())
    res = await health_checks._check_g1_route_sweep()
    _shape_ok(res, "red")
    assert "3" in res["detail"] and "42" in res["detail"]


# ─────────────────────────────────────────────────────────────
# G7 · Payment Recon — THE edge case founder called out
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g7_gray_when_last_run_is_null(monkeypatch):
    """last_run=None MUST be gray (Stripe not configured / never ran),
    NOT red. Collapsing this to red is the exact bug the 3-state
    discipline is designed to prevent."""
    monkeypatch.setattr("cto_services.db.get_db", lambda: object())

    async def _fake_summary(db):
        return {"last_run": None, "drift_events": 0}
    monkeypatch.setattr(
        "services.payment_reconciliation.get_recon_summary", _fake_summary
    )

    res = await health_checks._check_g7_payment_recon()
    _shape_ok(res, "gray")
    assert "no recon runs" in res["detail"].lower()


@pytest.mark.asyncio
async def test_g7_green_when_clean(monkeypatch):
    monkeypatch.setattr("cto_services.db.get_db", lambda: object())

    async def _fake_summary(db):
        return {"last_run": "2026-02-01T00:00:00Z", "drift_events": 0}
    monkeypatch.setattr(
        "services.payment_reconciliation.get_recon_summary", _fake_summary
    )

    res = await health_checks._check_g7_payment_recon()
    _shape_ok(res, "green")


@pytest.mark.asyncio
async def test_g7_red_when_drift_detected(monkeypatch):
    monkeypatch.setattr("cto_services.db.get_db", lambda: object())

    async def _fake_summary(db):
        return {"last_run": "2026-02-01T00:00:00Z", "drift_events": 4}
    monkeypatch.setattr(
        "services.payment_reconciliation.get_recon_summary", _fake_summary
    )

    res = await health_checks._check_g7_payment_recon()
    _shape_ok(res, "red")
    assert "4" in res["detail"]


# ─────────────────────────────────────────────────────────────
# G10 · Founder Alerts
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g10_gray_when_channel_disabled(monkeypatch):
    monkeypatch.setattr(
        "services.founder_alerts._resend_conf",
        lambda: {"enabled": False},
    )
    res = await health_checks._check_g10_founder_alerts()
    _shape_ok(res, "gray")


@pytest.mark.asyncio
async def test_g10_green_when_channel_enabled_and_last_delivered(monkeypatch):
    monkeypatch.setattr(
        "services.founder_alerts._resend_conf",
        lambda: {"enabled": True},
    )

    class _DB:
        class founder_alert_sends:
            @staticmethod
            async def find_one(*a, **kw):
                return {"sent_at": "2026-02-01T00:00:00Z", "delivered": True}
    monkeypatch.setattr("cto_services.db.get_db", lambda: _DB())
    res = await health_checks._check_g10_founder_alerts()
    _shape_ok(res, "green")


@pytest.mark.asyncio
async def test_g10_red_when_last_send_failed(monkeypatch):
    monkeypatch.setattr(
        "services.founder_alerts._resend_conf",
        lambda: {"enabled": True},
    )

    class _DB:
        class founder_alert_sends:
            @staticmethod
            async def find_one(*a, **kw):
                return {"sent_at": "2026-02-01T00:00:00Z", "delivered": False}
    monkeypatch.setattr("cto_services.db.get_db", lambda: _DB())
    res = await health_checks._check_g10_founder_alerts()
    _shape_ok(res, "red")


# ─────────────────────────────────────────────────────────────
# G17 · Circuit Breakers — the other edge case founder flagged
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_g17_gray_when_no_breakers_registered(monkeypatch):
    monkeypatch.setattr(
        "services.retry_guard.snapshot_all", lambda: {},
    )
    res = await health_checks._check_g17_breakers()
    _shape_ok(res, "gray")


@pytest.mark.asyncio
async def test_g17_green_when_all_closed(monkeypatch):
    monkeypatch.setattr(
        "services.retry_guard.snapshot_all",
        lambda: {
            "openrouter": {"state": "closed"},
            "stripe":     {"state": "closed"},
        },
    )
    res = await health_checks._check_g17_breakers()
    _shape_ok(res, "green")
    assert "2" in res["detail"]


@pytest.mark.asyncio
async def test_g17_red_when_one_or_more_open(monkeypatch):
    """The specific edge case founder called out: open_deps.length
    check must translate correctly. One `open` breaker → red."""
    monkeypatch.setattr(
        "services.retry_guard.snapshot_all",
        lambda: {
            "openrouter": {"state": "open"},
            "stripe":     {"state": "closed"},
        },
    )
    res = await health_checks._check_g17_breakers()
    _shape_ok(res, "red")
    assert "openrouter" in res["detail"]


# ─────────────────────────────────────────────────────────────
# Registry integrity — all four proof-of-pattern adapters are
# registered under their expected ids and category.
# ─────────────────────────────────────────────────────────────
def test_registry_has_proof_of_pattern_batch():
    ids = {c.id for c in all_checks()}
    expected = {"g1_route_sweep", "g7_payment_recon",
                "g10_founder_alerts", "g17_breakers"}
    missing = expected - ids
    assert not missing, f"missing registrations: {missing}"
    for cid in expected:
        c = get_check(cid)
        assert c is not None
        assert c.category == "guard"
        assert c.name.startswith("G")


# ─────────────────────────────────────────────────────────────
# run_check_safely — crashing adapter returns RED (not gray)
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_check_safely_catches_exceptions_as_red():
    from services.health_registry import HealthCheck

    async def _boom():
        raise RuntimeError("something exploded")

    check = HealthCheck(id="boom", name="Boom", category="guard", check_fn=_boom)
    res = await run_check_safely(check)
    assert res["status"] == "red"    # crashing check is a REAL problem
    assert "RuntimeError" in res["detail"]
    assert "something exploded" in res["detail"]
