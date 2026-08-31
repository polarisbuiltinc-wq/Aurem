"""
tests/test_contrast_guard_2026_08_31.py

Named tests for the WCAG contrast guard (Item 2, 2026-08-31). Pure
math — no LLM, no browser, deterministic.
"""
from __future__ import annotations

from services.contrast_guard import (
    contrast_ratio, is_readable, nudge_to_readable, check_and_nudge_css,
    WCAG_AA_NORMAL_TEXT,
)


def test_t_contrast_known_reference_values():
    assert round(contrast_ratio("#000000", "#ffffff"), 1) == 21.0
    assert round(contrast_ratio("#ffffff", "#ffffff"), 1) == 1.0
    # #767676 on white is the textbook "just clears WCAG AA" gray.
    assert contrast_ratio("#767676", "#ffffff") >= WCAG_AA_NORMAL_TEXT
    assert contrast_ratio("#8a8a8a", "#ffffff") < WCAG_AA_NORMAL_TEXT


def test_t_contrast_rejects_unreadable():
    # a light-gray-on-white pair, ~1.6:1 — well below 4.5:1
    assert not is_readable("#cccccc", "#ffffff")


def test_t_contrast_passes_readable():
    assert is_readable("#1a1a1a", "#ffffff")  # ~16:1


def test_t_contrast_is_deterministic():
    a = contrast_ratio("#336699", "#f0f0f0")
    b = contrast_ratio("#336699", "#f0f0f0")
    assert a == b
    n1 = nudge_to_readable("#cccccc", "#ffffff")
    n2 = nudge_to_readable("#cccccc", "#ffffff")
    assert n1 == n2


def test_t_nudge_produces_readable_result():
    nudged = nudge_to_readable("#cccccc", "#ffffff")
    assert is_readable(nudged, "#ffffff")
    assert nudged != "#cccccc"


def test_t_nudge_noop_when_already_readable():
    assert nudge_to_readable("#000000", "#ffffff") == "#000000"


def test_t_check_and_nudge_css_catches_bad_pair():
    css = ":root { --text-color: #cccccc; --background: #ffffff; }"
    result = check_and_nudge_css(css)
    assert result["adjustments"]
    adj = result["adjustments"][0]
    assert adj["before_ratio"] < WCAG_AA_NORMAL_TEXT
    assert adj["after_ratio"] >= WCAG_AA_NORMAL_TEXT
    assert "#cccccc" not in result["content"]
    assert result["ok"] is True


def test_t_check_and_nudge_css_noop_when_readable():
    css = ":root { --text-color: #111111; --background: #ffffff; }"
    result = check_and_nudge_css(css)
    assert result["adjustments"] == []
    assert result["content"] == css


def test_t_check_and_nudge_css_noop_when_no_theme_vars():
    css = ".btn { padding: 8px; }"
    result = check_and_nudge_css(css)
    assert result["adjustments"] == []
    assert result["ok"] is True
