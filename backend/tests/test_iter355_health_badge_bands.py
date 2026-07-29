"""Iter 355 — "health badge showing wrong data" (founder suggestion,
2026-07-12) locks.

RCA: the Dashboard top-bar HealthRing used its own 80/50 color cutoffs
while the backend `_category_label` bands are <20 / <50 / <=80 / >80.
Result: score 44 → Health page said amber "NEEDS ATTENTION" but the
badge rang RED; score 80 → page "GOOD" but badge GREEN. The badge now
mirrors the backend bands exactly.
"""
import os

_RING_SRC = open(
    "/app/frontend/src/components/dashboard/v2/TopBar.jsx").read()
_BACKEND_SRC = open(os.path.join(
    os.path.dirname(__file__), "..", "routers", "codebase_health.py")).read()


def _backend_label(score: int) -> str:
    if score < 20:
        return "CRITICAL RISK"
    if score < 50:
        return "NEEDS ATTENTION"
    if score <= 80:
        return "GOOD"
    return "HEALTHY"


def _ring_label(score: int) -> str:
    # Mirrors the JSX ternary in HealthRing (Iter 355).
    if score > 80:
        return "HEALTHY"
    if score >= 50:
        return "GOOD"
    if score >= 20:
        return "NEEDS ATTENTION"
    return "CRITICAL RISK"


def test_backend_bands_unchanged():
    """If someone re-tunes _category_label, this test forces them to
    update the frontend ring in the same change."""
    assert "if score <  20:" in _BACKEND_SRC
    assert "if score <  50:" in _BACKEND_SRC
    assert "if score <= 80:" in _BACKEND_SRC


def test_ring_source_mirrors_backend_bands():
    assert 'score > 80 ? "#22c55e"' in _RING_SRC
    assert ': score >= 50 ? "#38bdf8"' in _RING_SRC
    assert ': score >= 20 ? "#f59e0b"' in _RING_SRC
    assert ': "#ef4444"' in _RING_SRC
    # Old divergent cutoff must stay gone.
    assert 'score >= 80 ? "#22c55e"' not in _RING_SRC


def test_every_score_agrees_between_badge_and_page():
    for s in range(0, 101):
        assert _ring_label(s) == _backend_label(s), (
            f"score {s}: badge={_ring_label(s)} page={_backend_label(s)}")


def test_ring_title_includes_label():
    assert "${ringLabel}" in _RING_SRC
