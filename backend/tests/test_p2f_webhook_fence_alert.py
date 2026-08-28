"""
tests/test_p2f_webhook_fence_alert.py — P2-F (2026-08-28), GitHub
Webhook Fence wired into the existing health_notifier alert pipeline.

Named tests:
  t_fence_check_registered        — "int_webhook_fence" is present in
                                     the health registry.
  t_fence_maps_ok_true_to_green   — webhook_fence_status(ok=True) maps
                                     to the 3-state "green".
  t_fence_maps_ok_false_to_red    — webhook_fence_status(ok=False,
                                     configured=True) maps to "red"
                                     (the real, currently-broken state).
  t_fence_maps_not_configured_to_gray — configured=False maps to
                                     "gray" (not-set-up, not a failure).
"""
from unittest.mock import AsyncMock, patch

import pytest

import services.health_checks as health_checks
from services.health_registry import all_checks


def test_fence_check_registered():
    ids = {c.id for c in all_checks()}
    assert "int_webhook_fence" in ids


@pytest.mark.asyncio
async def test_fence_maps_ok_true_to_green():
    with patch("services.github_app.webhook_fence_status", new=AsyncMock(return_value={
        "ok": True, "configured": True, "subscribed_events": ["pull_request"],
        "recent_deliveries": [{"success": True}], "missing_subscriptions": [],
        "failing_count": 0,
    })):
        res = await health_checks._check_github_webhook_fence()
    assert res["status"] == "green"


@pytest.mark.asyncio
async def test_fence_maps_ok_false_to_red():
    with patch("services.github_app.webhook_fence_status", new=AsyncMock(return_value={
        "ok": False, "configured": True, "subscribed_events": [],
        "recent_deliveries": [{"success": False}] * 15, "missing_subscriptions": ["pull_request"],
        "failing_count": 15,
    })):
        res = await health_checks._check_github_webhook_fence()
    assert res["status"] == "red"
    assert "pull_request" in res["detail"]


@pytest.mark.asyncio
async def test_fence_maps_not_configured_to_gray():
    with patch("services.github_app.webhook_fence_status", new=AsyncMock(return_value={
        "ok": False, "configured": False, "subscribed_events": [],
        "recent_deliveries": [], "missing_subscriptions": ["pull_request"],
        "failing_count": 0,
    })):
        res = await health_checks._check_github_webhook_fence()
    assert res["status"] == "gray"
