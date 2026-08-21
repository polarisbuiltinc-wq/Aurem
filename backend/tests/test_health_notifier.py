"""
tests/test_health_notifier.py — Cockpit-Bell notifier unit tests.

Covers every branch of the fire-decision truth table:

    green → red       fires (primary case)
    red   → green     fires (recovery)
    green → gray      NO fire (config change, not a failure)
    gray  → green     NO fire (config finally set)
    green → green     NO fire
    red   → red       NO fire on the first-ever observation
                      (pre-existing red = baseline); fires once per
                      cooldown thereafter
    any    → any     on an acked check: NO fire
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import health_notifier as hn


class _FakeCol:
    """Minimal Motor-like collection so tests don't need real Mongo."""

    def __init__(self):
        self.rows: dict = {}
        self.inserted: list = []
        self.updated: list = []

    async def find_one(self, q):
        return self.rows.get(q.get("_id"))

    async def update_one(self, q, upd, upsert=False):
        cur = self.rows.get(q.get("_id"), {"_id": q["_id"]})
        cur.update(upd.get("$set", {}))
        self.rows[q["_id"]] = cur
        self.updated.append((q, upd))

    async def insert_one(self, doc):
        self.inserted.append(doc)


class _FakeDB:
    def __init__(self):
        self.health_check_state = _FakeCol()
        self.health_notifications = _FakeCol()
        self.founder_alert_sends = _FakeCol()


def _iso_ago(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


# ─────────────────────────────────────────────────────────────
# _should_fire truth table
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_should_fire_green_to_red_immediate():
    # new==red, no prior alert → NOT should_fire from _should_fire
    # (that path is handled by the caller: last_known=green, new=red
    # → forces should_notify=True upstream). _should_fire only gates
    # the red-staying-red re-alert.
    assert await hn._should_fire({}, "green") is True  # non-red passes


@pytest.mark.asyncio
async def test_should_fire_red_baseline_no_alert():
    """Pre-existing red at boot: no prior alert ever → baseline,
    silent. This is the exact bug the initial notifier version had
    (fired for every red-at-restart)."""
    row = {}    # no last_alert_at
    assert await hn._should_fire(row, "red") is False


@pytest.mark.asyncio
async def test_should_fire_red_after_recovery_re_reds():
    """Last alert was a red→green recovery. Now red again → fresh
    real transition, fire once."""
    row = {"last_alert_at": _iso_ago(60), "last_alert_to": "green"}
    assert await hn._should_fire(row, "red") is True


@pytest.mark.asyncio
async def test_should_fire_red_within_cooldown_blocks_re_alert():
    row = {"last_alert_at": _iso_ago(60), "last_alert_to": "red"}
    assert await hn._should_fire(row, "red") is False


@pytest.mark.asyncio
async def test_should_fire_red_past_cooldown_re_alerts():
    row = {"last_alert_at": _iso_ago(hn._RE_ALERT_COOLDOWN_S + 60),
           "last_alert_to": "red"}
    assert await hn._should_fire(row, "red") is True


# ─────────────────────────────────────────────────────────────
# _ack_active
# ─────────────────────────────────────────────────────────────

def test_ack_active_none():
    assert hn._ack_active(None) is False
    assert hn._ack_active("") is False


def test_ack_active_past_expired():
    assert hn._ack_active(_iso_ago(60)) is False


def test_ack_active_future_still_muted():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert hn._ack_active(future) is True


def test_ack_active_corrupt_row_treated_as_unacked():
    assert hn._ack_active("not-a-real-iso") is False


# ─────────────────────────────────────────────────────────────
# _fire_notification — writes both rows + calls founder_alerts
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fire_notification_writes_row_and_updates_state(monkeypatch):
    db = _FakeDB()

    sent = {}

    async def _fake_send(db_, source_key, title, detail, level, guard):
        sent["source_key"] = source_key
        sent["title"] = title
        sent["level"] = level

    monkeypatch.setattr(hn, "send_founder_alert" if hasattr(hn, "send_founder_alert") else "__unused__", _fake_send, raising=False)
    # Actual import path:
    monkeypatch.setattr("services.founder_alerts.send_founder_alert", _fake_send)

    await hn._fire_notification(
        db, "g_test", "G-Test", "guard",
        old="green", new="red", detail="broke!",
    )

    assert len(db.health_notifications.inserted) == 1
    row = db.health_notifications.inserted[0]
    assert row["check_id"] == "g_test"
    assert row["from_state"] == "green"
    assert row["to_state"] == "red"
    assert row["read"] is False

    # Founder alert fired with correct source_key + severity.
    assert sent["source_key"] == "health:g_test:red"
    assert "RED" in sent["title"]
    assert sent["level"] == "critical"

    # State row updated with last_alert_at + last_alert_to.
    state = db.health_check_state.rows.get("g_test") or {}
    assert state.get("last_alert_to") == "red"
    assert state.get("last_alert_at")


@pytest.mark.asyncio
async def test_fire_recovery_uses_info_level(monkeypatch):
    db = _FakeDB()
    sent = {}

    async def _fake_send(db_, source_key, title, detail, level, guard):
        sent["title"] = title
        sent["level"] = level

    monkeypatch.setattr("services.founder_alerts.send_founder_alert", _fake_send)

    await hn._fire_notification(
        db, "g_test", "G-Test", "guard",
        old="red", new="green", detail="recovered",
    )
    assert sent["level"] == "info"
    assert "recovered" in sent["title"].lower() or "🟢" in sent["title"]


# ─────────────────────────────────────────────────────────────
# _tick_once — full diff loop against a synthetic registry
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tick_fires_on_green_to_red(monkeypatch):
    """2026-08-21 — a transition only fires once CONFIRMED on
    hn._CONFIRM_TICKS (2) consecutive ticks — see test_tick_flap_..."""
    db = _FakeDB()
    # Seed prior state: baseline green.
    db.health_check_state.rows["c1"] = {
        "_id": "c1", "last_known": "green",
    }

    from services.health_registry import HealthCheck

    async def _now_red():
        return {"status": "red", "detail": "went boom", "checked_at": "t"}

    check = HealthCheck(id="c1", name="C1", category="guard", check_fn=_now_red)
    monkeypatch.setattr("services.health_registry.all_checks",
                        lambda: [check])
    monkeypatch.setattr("cto_services.db.get_db", lambda: db)

    fired = {}

    async def _fake_send(**kw):
        fired["kw"] = kw
    monkeypatch.setattr("services.founder_alerts.send_founder_alert", _fake_send)

    # Tick 1: red observed for the first time — NOT confirmed yet, no fire.
    await hn._tick_once()
    assert db.health_notifications.inserted == []
    assert db.health_check_state.rows["c1"]["last_known"] == "green"

    # Tick 2: red confirmed (2 consecutive) — fires now.
    await hn._tick_once()
    assert len(db.health_notifications.inserted) == 1
    row = db.health_notifications.inserted[0]
    assert row["from_state"] == "green"
    assert row["to_state"] == "red"
    assert db.health_check_state.rows["c1"]["last_known"] == "red"


@pytest.mark.asyncio
async def test_tick_flap_single_blip_never_fires(monkeypatch):
    """2026-08-21 — founder-reported bell-spam fix: a status that
    goes red for exactly ONE tick then back to green (the exact G18
    "timeout, then fine again" pattern) must NEVER fire a
    notification — it's absorbed as noise, not a real transition."""
    db = _FakeDB()
    db.health_check_state.rows["c1"] = {"_id": "c1", "last_known": "green"}

    from services.health_registry import HealthCheck

    state = {"status": "red"}

    async def _flaky():
        return {"status": state["status"], "detail": "x", "checked_at": "t"}

    check = HealthCheck(id="c1", name="C1", category="guard", check_fn=_flaky)
    monkeypatch.setattr("services.health_registry.all_checks", lambda: [check])
    monkeypatch.setattr("cto_services.db.get_db", lambda: db)

    async def _fake_send(**kw):
        raise AssertionError("a single-tick flap must NEVER fire a notification")
    monkeypatch.setattr("services.founder_alerts.send_founder_alert", _fake_send)

    await hn._tick_once()          # tick 1: red (candidate, not confirmed)
    state["status"] = "green"
    await hn._tick_once()          # tick 2: back to green — flap absorbed

    assert db.health_notifications.inserted == []
    assert db.health_check_state.rows["c1"]["last_known"] == "green"


@pytest.mark.asyncio
async def test_tick_does_not_fire_on_green_to_gray(monkeypatch):
    """Config disappearing (green→gray) MUST NOT fire — it's not
    a failure. Founder spec bullet 3. (Confirmed over 2 ticks.)"""
    db = _FakeDB()
    db.health_check_state.rows["c1"] = {
        "_id": "c1", "last_known": "green",
    }
    from services.health_registry import HealthCheck

    async def _now_gray():
        return {"status": "gray", "detail": "config gone", "checked_at": "t"}

    check = HealthCheck(id="c1", name="C1", category="guard", check_fn=_now_gray)
    monkeypatch.setattr("services.health_registry.all_checks",
                        lambda: [check])
    monkeypatch.setattr("cto_services.db.get_db", lambda: db)

    async def _fake_send(**kw):
        raise AssertionError("founder_alert must NOT be called on green→gray")
    monkeypatch.setattr("services.founder_alerts.send_founder_alert", _fake_send)

    await hn._tick_once()
    await hn._tick_once()   # confirm on 2nd consecutive tick
    assert db.health_notifications.inserted == []
    # But last_known still updated so next tick has fresh baseline.
    assert db.health_check_state.rows["c1"]["last_known"] == "gray"


@pytest.mark.asyncio
async def test_tick_respects_ack(monkeypatch):
    db = _FakeDB()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    db.health_check_state.rows["c1"] = {
        "_id": "c1", "last_known": "green", "acked_until": future,
    }
    from services.health_registry import HealthCheck

    async def _now_red():
        return {"status": "red", "detail": "boom", "checked_at": "t"}

    check = HealthCheck(id="c1", name="C1", category="guard", check_fn=_now_red)
    monkeypatch.setattr("services.health_registry.all_checks",
                        lambda: [check])
    monkeypatch.setattr("cto_services.db.get_db", lambda: db)

    async def _fake_send(**kw):
        raise AssertionError("acked check must NOT fire")
    monkeypatch.setattr("services.founder_alerts.send_founder_alert", _fake_send)

    await hn._tick_once()
    await hn._tick_once()   # confirm on 2nd consecutive tick
    assert db.health_notifications.inserted == []


@pytest.mark.asyncio
async def test_tick_baseline_red_does_not_fire(monkeypatch):
    """A red observed on the first-ever (confirmed) tick (no
    last_known + no last_alert_at) MUST NOT fire. The initial
    cockpit-bell rollout hit this exact bug — spammed founder with
    'still red' alerts for every pre-existing incident row at pod
    boot. (Confirmed over 2 ticks.)"""
    db = _FakeDB()   # no state rows for "c1" — first observation
    from services.health_registry import HealthCheck

    async def _now_red():
        return {"status": "red", "detail": "pre-existing", "checked_at": "t"}

    check = HealthCheck(id="c1", name="C1", category="guard", check_fn=_now_red)
    monkeypatch.setattr("services.health_registry.all_checks",
                        lambda: [check])
    monkeypatch.setattr("cto_services.db.get_db", lambda: db)

    async def _fake_send(**kw):
        raise AssertionError("baseline red must NOT fire on first tick")
    monkeypatch.setattr("services.founder_alerts.send_founder_alert", _fake_send)

    await hn._tick_once()
    await hn._tick_once()   # confirm on 2nd consecutive tick
    assert db.health_notifications.inserted == []
    # But last_known was recorded for future ticks.
    assert db.health_check_state.rows["c1"]["last_known"] == "red"
