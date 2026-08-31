"""
tests/test_narration_leak_strip_2026_09_02.py

Item #3 (founder's 2026-09-02 decision): strip parenthetical/asterisk-
wrapped AI self-narration ("(Silently checks the page first)",
"*checking the file now*") from user-facing output -- on-theme with
the business-owner-voice / no-machinery-leak guard already shipped.
"""
from __future__ import annotations

from services.output_guard import strip_machinery_leak


def test_t_narration_stripped_parenthetical():
    text = "Sure! (Silently checks the page first) Your hours are already listed."
    clean, stripped = strip_machinery_leak(text, universal_only=True)
    assert stripped is True
    assert "silently checks" not in clean.lower()
    assert "Your hours are already listed." in clean


def test_t_narration_stripped_asterisk():
    text = "*checking the file now* Looks good, no changes needed."
    clean, stripped = strip_machinery_leak(text, universal_only=True)
    assert stripped is True
    assert "checking the file" not in clean.lower()
    assert "Looks good" in clean


def test_narration_strip_does_not_eat_normal_prose_about_the_page():
    """False-positive guard -- ordinary sentences that happen to
    mention 'the page' must survive untouched."""
    text = "The page loads fine and the hours section is at the top."
    clean, stripped = strip_machinery_leak(text, universal_only=True)
    assert stripped is False
    assert clean == text
