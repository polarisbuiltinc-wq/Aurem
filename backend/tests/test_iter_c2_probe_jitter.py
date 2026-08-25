"""C2 hardening — per-worker startup jitter for integration_health_cron.

Deploy-loop closure (Step 2): a >1-worker prod pod could otherwise run
`schedule_integration_health_cron()`'s probe cycle in perfect lockstep
across both workers forever (same boot instant -> same 150s sleep ->
same cadence), doubling the CPU burst each cycle competes for.
`_startup_jitter_s()` adds a bounded (0-60s) per-worker offset, seeded
by PID (not boot wall-clock time, which would be identical for two
workers booting at the same instant and defeat the whole point).
"""
from __future__ import annotations

import asyncio

from services.integration_health_cron import _startup_jitter_s


def test_jitter_is_bounded():
    for pid in range(1, 200):
        j = _startup_jitter_s(pid=pid)
        assert 0.0 <= j < 60.0


def test_jitter_is_deterministic_for_a_given_pid():
    """Seedable/injectable — same PID always yields the same offset,
    which is what makes proof (a) below deterministic instead of
    flaky."""
    assert _startup_jitter_s(pid=4242) == _startup_jitter_s(pid=4242)


# ── Proof (a): two workers with the SAME boot instant -> DIFFERENT
#    first probe-cycle start times (non-aligned) ──────────────────────

def test_proof_a_same_boot_instant_different_pids_desync():
    boot_instant = 1_000_000.0  # identical for both simulated workers

    worker_1_pid, worker_2_pid = 501, 502
    jitter_1 = _startup_jitter_s(pid=worker_1_pid)
    jitter_2 = _startup_jitter_s(pid=worker_2_pid)

    first_probe_cycle_start_1 = boot_instant + 150 + jitter_1
    first_probe_cycle_start_2 = boot_instant + 150 + jitter_2

    assert jitter_1 != jitter_2, (
        "two different worker PIDs must not collide on the jitter "
        "value in this scenario — that would silently re-align them"
    )
    assert first_probe_cycle_start_1 != first_probe_cycle_start_2, (
        "two workers booting at the IDENTICAL instant must still get "
        "DIFFERENT first probe-cycle start times — this is the exact "
        "C2 residual-risk case (same boot time used to defeat "
        "boot-time-seeded jitter)"
    )
    # Both offsets are within the documented bound regardless.
    assert 0.0 <= jitter_1 < 60.0
    assert 0.0 <= jitter_2 < 60.0


# ── Proof (b): a single worker's normal schedule is unaffected — the
#    jitter only adds a bounded ONE-TIME startup offset, the recurring
#    interval and the per-probe serial gap are untouched ─────────────

def test_proof_b_single_worker_schedule_only_gets_bounded_startup_offset(monkeypatch):
    from services import integration_health_cron as cron_mod

    sleeps: list[float] = []
    calls = {"n": 0}

    async def _fake_sleep(seconds):
        sleeps.append(seconds)
        calls["n"] += 1
        if calls["n"] >= 2:
            raise SystemExit  # stop the infinite `while True:` loop

    monkeypatch.setattr(cron_mod.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(cron_mod, "_is_enabled", lambda: True)
    monkeypatch.setattr(cron_mod, "_interval_seconds", lambda: 600)
    monkeypatch.setattr(cron_mod, "_startup_jitter_s", lambda pid=None: 12.5)

    async def _fake_paused():
        return True  # skip the real probe body — only the sleep schedule matters here

    monkeypatch.setattr(cron_mod, "_is_paused_by_flag", _fake_paused)

    async def go():
        try:
            await cron_mod.schedule_integration_health_cron()
        except SystemExit:
            pass

    asyncio.run(go())

    # First sleep = the boot stagger (150s) + the bounded jitter — a
    # ONE-TIME offset, not a change to the recurring cadence.
    assert sleeps[0] == 150 + 12.5
    # Second sleep = the normal recurring interval, untouched by jitter.
    assert sleeps[1] == 600
