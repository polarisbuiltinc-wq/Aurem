"""
tests/test_q2_kit_day14_reminder.py — Q2 (2026-08-28), day-14 recheck
reminder reusing the existing health_notifier cron (no new scheduler).
"""
from unittest.mock import patch

import pytest

import services.health_checks as health_checks
from services.health_registry import all_checks


def test_day14_check_registered():
    ids = {c.id for c in all_checks()}
    assert "kit_day14_reminder" in ids


@pytest.mark.asyncio
async def test_day14_gray_before_target_date():
    with patch("time.time", return_value=1755000000):  # ~2025-08 — before target
        res = await health_checks._check_kit_day14_reminder()
    assert res["status"] == "gray"


@pytest.mark.asyncio
async def test_day14_red_after_target_no_file_update():
    import time as _t
    future = _t.mktime(_t.strptime("2026-09-12", "%Y-%m-%d"))
    with patch("time.time", return_value=future), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value="# Day-0 baseline only, no day-14 section yet"):
        res = await health_checks._check_kit_day14_reminder()
    assert res["status"] == "red"


@pytest.mark.asyncio
async def test_day14_green_once_file_updated():
    import time as _t
    future = _t.mktime(_t.strptime("2026-09-12", "%Y-%m-%d"))
    with patch("time.time", return_value=future), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value="## Day-14 results\nreal numbers here"):
        res = await health_checks._check_kit_day14_reminder()
    assert res["status"] == "green"
