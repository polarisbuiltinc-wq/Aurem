"""
tests/test_iter_hardening_e2b_loud_off.py — Production Hardening
Fix 2 (2026-08).

Vanguard's e2b sandbox layer (3rd of 3 verify layers) has always
been silently skippable via the admin master switch
(services/vanguard_config.py). This locks in the LOUD version:
services.health_checks._check_vanguard_e2b_sandbox() must report
red (not gray, not silently green) when that switch is off and at
least one mode is active — and it must NEVER touch the setting
itself (the re-enable is a founder click, not a code action).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import health_checks  # noqa: E402


def _shape_ok(res: dict, expected_status: str) -> None:
    assert isinstance(res, dict)
    assert res["status"] == expected_status, (
        f"expected status={expected_status!r}, got {res['status']!r} · "
        f"detail={res.get('detail')!r}"
    )
    assert res.get("detail")
    assert res.get("checked_at")


@pytest.fixture(autouse=True)
def _e2b_key_present(monkeypatch):
    monkeypatch.setenv("E2B_API_KEY", "test-key-present")


# ─────────────────────────────────────────────────────────────
# T-F2a — sandbox ON → no alert, status OK (green)
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_f2a_sandbox_on_is_green(monkeypatch):
    async def _fake_get_config():
        return {"enabled": True, "levels": {"swift": "CRITICAL", "pro": "CRITICAL", "maxx": "HIGH"}}
    monkeypatch.setattr("services.vanguard_config.get_config", _fake_get_config)

    res = await health_checks._check_vanguard_e2b_sandbox()
    _shape_ok(res, "green")
    assert "on" in res["detail"].lower()


# ─────────────────────────────────────────────────────────────
# T-F2b — sandbox OFF for an active mode → red, decision-framed message
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_f2b_sandbox_off_for_active_mode_is_red(monkeypatch):
    async def _fake_get_config():
        return {"enabled": False, "levels": {"swift": "CRITICAL", "pro": "OFF", "maxx": "HIGH"}}
    monkeypatch.setattr("services.vanguard_config.get_config", _fake_get_config)

    res = await health_checks._check_vanguard_e2b_sandbox()
    _shape_ok(res, "red")
    detail_lower = res["detail"].lower()
    assert "e2b" in detail_lower and "off" in detail_lower
    assert "swift" in res["detail"] and "maxx" in res["detail"]
    # This must read as a DECISION, not a silent/broken state.
    assert "confirm" in detail_lower and "re-enable" in detail_lower
    assert "/admin/vanguard" in res["detail"]


@pytest.mark.asyncio
async def test_f2b_sandbox_off_but_no_active_mode_is_gray_not_red(monkeypatch):
    """If every mode is already OFF, there's nothing being degraded —
    must not cry wolf."""
    async def _fake_get_config():
        return {"enabled": False, "levels": {"swift": "OFF", "pro": "OFF", "maxx": "OFF"}}
    monkeypatch.setattr("services.vanguard_config.get_config", _fake_get_config)

    res = await health_checks._check_vanguard_e2b_sandbox()
    _shape_ok(res, "gray")


@pytest.mark.asyncio
async def test_e2b_key_missing_is_gray(monkeypatch):
    monkeypatch.delenv("E2B_API_KEY", raising=False)

    async def _fake_get_config():
        return {"enabled": False, "levels": {"swift": "CRITICAL"}}
    monkeypatch.setattr("services.vanguard_config.get_config", _fake_get_config)

    res = await health_checks._check_vanguard_e2b_sandbox()
    _shape_ok(res, "gray")


# ─────────────────────────────────────────────────────────────
# T-F2c — re-enable clears the alert, AND the check never writes
# anything itself (no auto re-enable — founder's choice stays theirs)
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_f2c_reenable_clears_and_check_never_writes_the_setting(monkeypatch):
    save_calls = []

    async def _fake_save_config(**kwargs):
        save_calls.append(kwargs)
        return kwargs

    async def _fake_get_config_off():
        return {"enabled": False, "levels": {"swift": "CRITICAL"}}

    monkeypatch.setattr("services.vanguard_config.save_config", _fake_save_config)
    monkeypatch.setattr("services.vanguard_config.get_config", _fake_get_config_off)

    red = await health_checks._check_vanguard_e2b_sandbox()
    _shape_ok(red, "red")
    assert save_calls == [], (
        "the health check must NEVER call save_config — re-enabling "
        "is a founder click at /admin/vanguard, not a code action"
    )

    # Founder flips it back on themselves — simulate the new read.
    async def _fake_get_config_on():
        return {"enabled": True, "levels": {"swift": "CRITICAL"}}
    monkeypatch.setattr("services.vanguard_config.get_config", _fake_get_config_on)

    green = await health_checks._check_vanguard_e2b_sandbox()
    _shape_ok(green, "green")
    assert save_calls == [], "still never called save_config"
