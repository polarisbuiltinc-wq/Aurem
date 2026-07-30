"""
Iter 124h (modernised in Iter 358) — /vs/* SEO/GEO page integrity guard.

Iter 358 moved all comparison content to src/data/competitors.js with a
generic VsPage.jsx shell (5 competitors: devin, cursor, github-copilot,
replit-agent, windsurf) + /compare hub + build-time SEO snapshots.
Pure file-content checks; no LLM, no browser, no network.
"""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPETITORS = ROOT / "frontend" / "src" / "data" / "competitors.js"
VS_PAGE = ROOT / "frontend" / "src" / "pages" / "VsPage.jsx"
VS_DEVIN = ROOT / "frontend" / "src" / "pages" / "VsDevin.jsx"
APP_JSX = ROOT / "frontend" / "src" / "App.jsx"
LANDING = ROOT / "frontend" / "src" / "pages" / "Landing.jsx"
SITEMAP = ROOT / "frontend" / "public" / "sitemap.xml"
LLMS = ROOT / "frontend" / "public" / "llms.txt"
LLMS_FULL = ROOT / "frontend" / "public" / "llms-full.txt"


def _src(p: Path) -> str:
    assert p.exists(), f"missing required file: {p}"
    return p.read_text(encoding="utf-8")


# ── Page content ─────────────────────────────────────────────────────

def test_vs_devin_mentions_both_delivery_modes():
    src = _src(COMPETITORS)
    assert "Pull Request" in src
    assert "Direct commit" in src or "direct commit" in src


def test_vs_devin_has_current_devin_acu_rate():
    """Devin Core is $20/mo + $2.25/ACU per devin.ai/pricing as of June 2026."""
    src = _src(COMPETITORS)
    assert "$2.25" in src, "Devin per-ACU rate ($2.25) missing — fact-check needed"
    assert "$20" in src, "Devin Core base ($20/mo) missing"


def test_vs_devin_has_no_stale_prices():
    src = _src(COMPETITORS)
    assert '"price": "35"' not in src
    assert '"$35/mo"' not in src
    # Iter 358 — Pro is $19/300 tasks per subscription_tiers.py SSOT;
    # the old "unlimited tasks" marketing claim must not return.
    assert "unlimited tasks" not in src.lower()
    assert "300 tasks" in src


def test_vs_pages_have_faq_jsonld():
    src = _src(VS_PAGE)
    assert "application/ld+json" in src
    assert "FAQPage" in src
    assert "BreadcrumbList" in src
    # JSON-LD built from the SAME c.faq array the page renders → 1:1
    assert "c.faq.map" in src


def test_vs_devin_credits_where_devin_wins():
    """Honesty policy — every page must acknowledge competitor strengths."""
    src = _src(COMPETITORS)
    low = src.lower()
    assert "devin is a strong fit" in low or "when is devin the better choice" in low
    for name in ("cursor", "github copilot", "replit agent", "windsurf"):
        assert f"when is {name} the better choice?".lower() in low, \
            f"missing 'where {name} wins' FAQ"


# ── Route wiring ─────────────────────────────────────────────────────

def test_app_jsx_has_vs_devin_route():
    src = _src(APP_JSX)
    assert 'path="/vs/devin"' in src
    assert 'lazy(() => import("./pages/VsDevin"))' in src


def test_app_jsx_has_generic_vs_and_compare_routes():
    """Iter 358 — /vs/cursor redirect stub replaced by real pages."""
    src = _src(APP_JSX)
    assert 'path="/vs/:slug"' in src
    assert 'path="/compare"' in src
    assert re.search(r'path="/vs/cursor".*Navigate', src, re.DOTALL) is None, \
        "old /vs/cursor redirect must be gone (real page exists now)"


def test_vs_devin_is_thin_wrapper():
    src = _src(VS_DEVIN)
    assert 'forcedSlug="devin"' in src


def test_all_five_competitors_defined():
    src = _src(COMPETITORS)
    for slug in ("devin", "cursor", "github-copilot", "replit-agent", "windsurf"):
        assert f'"{slug}"' in src or f"{slug}:" in src, f"missing competitor {slug}"


# ── Landing internal links ───────────────────────────────────────────

def test_landing_footer_compare_link():
    src = _src(LANDING)
    assert 'to="/compare"' in src
    assert 'data-testid="footer-vs"' in src


# ── SEO files ────────────────────────────────────────────────────────

def test_sitemap_has_all_vs_entries():
    src = _src(SITEMAP)
    for path in ("/vs/devin", "/vs/cursor", "/vs/github-copilot",
                 "/vs/replit-agent", "/vs/windsurf", "/compare"):
        assert f"https://auremcto.com{path}</loc>" in src, f"sitemap missing {path}"


def test_llms_txt_links_vs_devin():
    src = _src(LLMS)
    assert "/vs/devin" in src
    assert "/vs/cursor" in src
    assert "/compare" in src


def test_llms_full_links_vs_devin():
    src = _src(LLMS_FULL)
    assert "/vs/devin" in src
