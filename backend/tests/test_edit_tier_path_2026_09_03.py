"""
tests/test_edit_tier_path_2026_09_03.py

Root 4 (2026-09-03 core-flow round) — t_tier_edit_path. TWO
config-gated tier-edit paths, both fully implemented, flip via the
`EDIT_TIER_MODE` env var:

  Path A ("transparent") — a real edit on Swift silently escalates to
    the reliable model for EVERY account, regardless of plan.
  Path B ("gated", DEFAULT) — a free/starter account (no Pro access)
    attempting a real edit gets an HONEST upgrade offer instead of a
    silent escalation OR a "nothing pending" dead end. An account
    that already has Pro access is NEVER gated in either path.
"""
from __future__ import annotations

import os

import pytest

from services.mode_routing import (
    resolve_model_mode,
    needs_edit_upgrade_offer,
    edit_tier_mode,
    UPGRADE_OFFER_MESSAGE,
    EDIT_TIER_MODE_TRANSPARENT,
    EDIT_TIER_MODE_GATED,
)


@pytest.fixture(autouse=True)
def _clean_env():
    """Isolate EDIT_TIER_MODE across tests."""
    original = os.environ.get("EDIT_TIER_MODE")
    yield
    if original is None:
        os.environ.pop("EDIT_TIER_MODE", None)
    else:
        os.environ["EDIT_TIER_MODE"] = original


def test_default_env_unset_is_gated():
    os.environ.pop("EDIT_TIER_MODE", None)
    assert edit_tier_mode() == EDIT_TIER_MODE_GATED


def test_default_backend_env_file_sets_gated():
    """The real backend/.env explicitly documents the default so the
    founder can find + flip it without a code change."""
    import pathlib
    env_path = pathlib.Path(__file__).resolve().parents[1] / ".env"
    text = env_path.read_text()
    assert "EDIT_TIER_MODE=gated" in text


# ── Path B (gated, default) — free/starter accounts ────────────────
def test_gated_default_free_account_gets_upgrade_offer_not_silent_escalation():
    os.environ["EDIT_TIER_MODE"] = EDIT_TIER_MODE_GATED
    assert needs_edit_upgrade_offer("agentic", "swift", account_has_pro=False) is True
    # mode stays on swift -- caller shows the upgrade offer instead of running it
    assert resolve_model_mode("agentic", "swift", account_has_pro=False) == "swift"


def test_gated_never_dead_ends_real_upgrade_message_not_nothing_pending():
    """The upgrade offer is a REAL, concrete next step -- never the
    generic 'nothing pending' dead end."""
    assert "upgrade" in UPGRADE_OFFER_MESSAGE.lower()
    assert "nothing pending" not in UPGRADE_OFFER_MESSAGE.lower()


def test_gated_account_with_pro_is_never_gated():
    """An account that already has Pro access owns the capability --
    never shown an upgrade offer, silently escalated like Path A."""
    os.environ["EDIT_TIER_MODE"] = EDIT_TIER_MODE_GATED
    assert needs_edit_upgrade_offer("agentic", "swift", account_has_pro=True) is False
    assert resolve_model_mode("agentic", "swift", account_has_pro=True) == "pro"


def test_gated_only_fires_on_real_edit_swift_combo():
    os.environ["EDIT_TIER_MODE"] = EDIT_TIER_MODE_GATED
    assert needs_edit_upgrade_offer("casual", "swift", account_has_pro=False) is False
    assert needs_edit_upgrade_offer("query", "swift", account_has_pro=False) is False
    assert needs_edit_upgrade_offer("agentic", "pro", account_has_pro=False) is False


# ── Path A (transparent) ────────────────────────────────────────────
def test_transparent_path_silently_escalates_free_account_too():
    os.environ["EDIT_TIER_MODE"] = EDIT_TIER_MODE_TRANSPARENT
    assert needs_edit_upgrade_offer("agentic", "swift", account_has_pro=False) is False
    assert resolve_model_mode("agentic", "swift", account_has_pro=False) == "pro"


def test_transparent_path_still_untouched_for_non_edit_turns():
    os.environ["EDIT_TIER_MODE"] = EDIT_TIER_MODE_TRANSPARENT
    assert resolve_model_mode("casual", "swift", account_has_pro=False) == "swift"
    assert resolve_model_mode("query", "swift", account_has_pro=False) == "swift"


# ── Backward-compat: default kwarg preserves the 2026-09-02 baseline ─
def test_default_account_has_pro_true_preserves_original_behavior():
    """Existing callers that don't pass `account_has_pro` (the
    original 2026-09-02 signature) keep the exact original
    unconditional escalation behavior -- baseline
    test_mode_auto_escalation_2026_09_02.py must stay green unchanged."""
    assert resolve_model_mode("agentic", "swift") == "pro"
    assert resolve_model_mode("casual", "swift") == "swift"
    assert resolve_model_mode("agentic", "pro") == "pro"
    assert resolve_model_mode("agentic", "maxx") == "maxx"


def test_invalid_env_value_falls_back_to_gated():
    os.environ["EDIT_TIER_MODE"] = "nonsense"
    assert edit_tier_mode() == EDIT_TIER_MODE_GATED
