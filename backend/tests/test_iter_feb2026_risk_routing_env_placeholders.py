"""
test_iter_feb2026_risk_routing_env_placeholders.py — Feb 2026

Founder-reported false positive: the loop live-feed showed
    risk WARN on .env.example (score=0.4013)
on every commit that touched a `.env.example` file. Root cause:
`services/risk_routing.py` _PATH_WEIGHTS matched the broad
`^\.env(\..+)?$` regex on `.env.example` with weight 0.90, which
through the sigmoid clip landed at 0.4013 — just 0.0013 above the
WARN_THRESHOLD of 0.40. But `.env.example` (and its siblings
`.env.sample`, `.env.template`, `.env.dist`) are conventionally
committed placeholder files containing dummy values, not real
secrets. Vanguard scanner already allowlists them.

Fix: added an explicit low-weight `env_placeholder` rule and a
negative-lookahead on the broader `env_secrets` rule so placeholders
never inherit the 0.90 weight. This test locks in both parts.
"""
from __future__ import annotations

from services.risk_routing import (
    score_change, TIER_AUTO_SHIP, TIER_WARN_SHIP,
)


def test_env_example_is_auto_ship_not_warn():
    """`.env.example` must land in AUTO_SHIP — the broad env rule
    must NOT apply the 0.90 weight to it."""
    r = score_change(
        path=".env.example",
        before_bytes=b"API_KEY=your_key_here\n",
        after_bytes=b"API_KEY=your_key_here\nDB_URL=your_db_here\n",
    )
    assert r.tier == TIER_AUTO_SHIP, (
        f"Expected AUTO_SHIP for placeholder env file, got {r.tier} "
        f"(score={r.score}, signals={r.signals})"
    )
    assert r.score < 0.40, (
        f"Score must be below WARN_THRESHOLD (0.40) so no WARN "
        f"narration fires — got {r.score}"
    )
    # And the signal should record it as a placeholder, not env_secrets.
    path_tag = (r.signals.get("path") or {}).get("tag")
    assert path_tag == "env_placeholder", (
        f"Expected path signal tag='env_placeholder', got {path_tag!r}"
    )


def test_all_placeholder_variants_covered():
    """All conventional placeholder suffixes must be treated the same."""
    for suffix in ("example", "sample", "template", "dist", "defaults"):
        path = f".env.{suffix}"
        r = score_change(
            path=path,
            before_bytes=b"",
            after_bytes=b"FOO=bar\n",
        )
        assert r.tier == TIER_AUTO_SHIP, (
            f"{path} should be AUTO_SHIP, got {r.tier} (score={r.score})"
        )


def test_real_env_still_high_risk():
    """Regression guard — plain `.env` (real secrets) must STILL score
    high enough to hit WARN_SHIP or PAUSE_FOR_FOUNDER. Otherwise the
    fix would silently disable env-secret protection entirely."""
    r = score_change(
        path=".env",
        before_bytes=b"API_KEY=sk-real\n",
        after_bytes=b"API_KEY=sk-real2\nOPENAI_KEY=sk-something\n",
    )
    assert r.tier in (TIER_WARN_SHIP, "PAUSE_FOR_FOUNDER"), (
        f"Real .env must NOT be AUTO_SHIP — got {r.tier} (score={r.score})"
    )
    path_tag = (r.signals.get("path") or {}).get("tag")
    assert path_tag == "env_secrets", (
        f"Real .env must be tagged env_secrets, got {path_tag!r}"
    )


def test_env_production_still_high_risk():
    """`.env.production`, `.env.local` etc. must still trip WARN — only
    the placeholder suffixes are allowlisted."""
    for suffix in ("production", "local", "prod", "staging"):
        r = score_change(
            path=f".env.{suffix}",
            before_bytes=b"",
            after_bytes=b"API_KEY=sk-real\n",
        )
        path_tag = (r.signals.get("path") or {}).get("tag")
        assert path_tag == "env_secrets", (
            f".env.{suffix} must be tagged env_secrets, got {path_tag!r}"
        )
