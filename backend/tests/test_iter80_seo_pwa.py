"""
test_iter80_seo_pwa.py — SEO / GEO / PWA wiring lock.

Iter 80 closed three gaps (kept as reference):
  1. PWA — added /sw.js service worker with offline shell + cache strategies,
     enriched site.webmanifest (id, shortcuts, screenshots, display_override),
     registered the SW in src/main.jsx.
  2. SEO — sitemap.xml lists /wall + /wrapped, JSON-LD structured data,
     canonical brand name + pricing.
  3. GEO — robots.txt opens for AI crawlers (YouBot, Meta-ExternalAgent,
     Amazonbot etc.), llms.txt carries current pricing + capability list.

Feb 2026 refresh (Session G · Batch 4d, founder-confirmed):
  - Brand: **"Aurem CTO"** (Title Case), NOT "AUREM CTO".
  - Pricing: Free $0 / Starter $9/mo / Pro $19/mo / Team $49/mo per user
    (Team was previously $35 — corrected). JSON-LD's SoftwareApplication
    `offers` block is now a SINGLE Offer for the Founder Plan at $9,
    not a four-tier array. Full four-tier pricing lives in `llms.txt`
    for LLM crawlers.
  - JSON-LD structure: 4 SEPARATE `<script>` blocks (Organization,
    WebSite, SoftwareApplication, FAQPage) — the `@graph` wrapper was
    NOT re-introduced.

This test locks all of that so a refactor can't quietly regress it.
"""
from __future__ import annotations

import json
import os
import re

BASE = os.path.join(os.path.dirname(__file__), "..", "..")
PUBLIC = os.path.join(BASE, "frontend", "public")


def _read(rel: str) -> str:
    with open(os.path.join(BASE, rel), encoding="utf-8") as fh:
        return fh.read()


# ── PWA ────────────────────────────────────────────────────────────────

def test_service_worker_exists_and_has_versioned_cache():
    src = _read("frontend/public/sw.js")
    assert "CACHE_VERSION" in src
    # Three lifecycle handlers are required.
    for evt in ("install", "activate", "fetch"):
        assert f'addEventListener("{evt}"' in src, f"sw.js missing {evt}"
    # API calls must NEVER be cached (chat is live).
    assert "/api/" in src and "return;" in src
    # SSE must bypass cache.
    assert "text/event-stream" in src


def test_main_jsx_registers_service_worker():
    src = _read("frontend/src/main.jsx")
    assert "serviceWorker" in src
    assert 'navigator.serviceWorker' in src
    assert '/sw.js' in src


def test_manifest_is_valid_pwa_with_shortcuts():
    src = _read("frontend/public/site.webmanifest")
    m = json.loads(src)
    # Required PWA fields
    assert m["start_url"]
    assert m["display"] in ("standalone", "fullscreen", "minimal-ui")
    assert m["theme_color"].startswith("#")
    assert m["background_color"].startswith("#")
    # PWA installability essentials
    assert m.get("id"), "manifest missing `id` (required for new Chrome PWAs)"
    icons = m["icons"]
    sizes = {icon["sizes"] for icon in icons}
    assert "192x192" in sizes
    assert "512x512" in sizes
    has_maskable = any("maskable" in icon.get("purpose", "") for icon in icons)
    assert has_maskable, "manifest missing maskable icon"
    # Shortcuts hook ORA's main surfaces.
    shortcuts = m.get("shortcuts") or []
    short_urls = {s["url"].split("?")[0] for s in shortcuts}
    assert "/dashboard" in short_urls
    assert "/projects" in short_urls


def test_manifest_linked_in_index_html():
    src = _read("frontend/index.html")
    assert 'rel="manifest"' in src
    assert '/site.webmanifest' in src


# ── SEO — JSON-LD ──────────────────────────────────────────────────────

def _parse_all_json_ld_blocks(src: str) -> list[dict]:
    """Extract every <script type='application/ld+json'>{…}</script>."""
    blobs = []
    for m in re.finditer(
        r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>',
        src, re.DOTALL,
    ):
        blobs.append(json.loads(m.group(1)))
    return blobs


def test_json_ld_pricing_is_current():
    """The SoftwareApplication JSON-LD block must expose the current
    2-tier offer model: Free ($0, 10 tasks/mo) + Founder ($9/mo flat,
    first 500 users) — not the old single-Offer or 4-tier structures."""
    src = _read("frontend/index.html")
    blobs = _parse_all_json_ld_blocks(src)
    assert blobs, "no JSON-LD blocks in index.html"
    app = next(
        (b for b in blobs if b.get("@type") == "SoftwareApplication"),
        None,
    )
    assert app, "no SoftwareApplication JSON-LD block"
    offers = app["offers"]
    assert isinstance(offers, list) and len(offers) == 2, \
        f"offers must be the 2-tier Free+Founder array, got: {offers}"
    by_name = {o["name"]: o for o in offers}
    assert by_name["Free"]["price"] == "0"
    assert by_name["Founder"]["price"] == "9"
    for o in offers:
        assert o["priceCurrency"] == "USD"


def test_json_ld_feature_list_covers_current_capabilities():
    src = _read("frontend/index.html")
    # Case-sensitive substrings from the actual live featureList (2026-09):
    for must_mention in (
        "Verified 5-phase Loop",
        "Vanguard pre-commit security scanner",
        "Self-heal on verify failure",
        "One-click rollback",
        "MCP 2.4 server",
        "Flat monthly pricing",
    ):
        assert must_mention in src, f"JSON-LD missing feature: {must_mention}"


def test_index_title_and_description_reflect_aurem_cto():
    """2026-08-13 P0 brand alignment (CHANGELOG) superseded the old
    'Aurem CTO' product label with the current hierarchy: Legal name
    Polaris Built Inc., trade name AUREM, product 'ORA by Aurem'.
    The product tag on Twitter + OG must lead with 'ORA'."""
    src = _read("frontend/index.html")
    # Canonical current brand phrase:
    assert "ORA by Aurem" in src, "brand 'ORA by Aurem' missing"
    # No leftover deprecated product label from the pre-P0-brand era:
    assert "AUREM CTO —" not in src, \
        "deprecated 'AUREM CTO —' product label still present"
    # Product tag on Twitter + OG — both must lead with the ORA
    # product name (2026-02 refresh preferred the short-tag).
    assert 'name="twitter:title" content="ORA' in src
    assert 'property="og:title" content="ORA' in src
    # Explicit sanity — neither should embed the deprecated all-caps
    # product label.
    assert 'name="twitter:title" content="AUREM' not in src
    assert 'property="og:title" content="AUREM' not in src


# ── GEO (LLM-engine optimization) ──────────────────────────────────────

def test_robots_allows_modern_ai_crawlers():
    src = _read("frontend/public/robots.txt")
    for bot in ("GPTBot", "PerplexityBot", "ClaudeBot", "Google-Extended",
                "OAI-SearchBot", "Bytespider", "CCBot",
                "YouBot", "Meta-ExternalAgent", "Amazonbot"):
        assert re.search(rf"User-agent:\s*{re.escape(bot)}\s*\nAllow:\s*/", src), \
            f"robots.txt missing Allow for {bot}"
    # Public surfaces opened up
    assert "Allow: /wall" in src
    assert "Allow: /wrapped" in src


def test_llms_txt_has_current_pricing_and_capabilities():
    """llms.txt is the AI-crawler-facing canonical pricing + capability
    reference. 2026-08-30 rewrite: single $9/mo flat Founder Plan
    (first 500 users) + a $0/10-tasks free tier — the old 4-tier
    Free/Starter/Pro/Team grid was retired."""
    src = _read("frontend/public/llms.txt")
    for fragment in ("$0 free tier", "10 tasks/month", "$9 founder flat",
                     "$9/month flat"):
        assert fragment in src, f"llms.txt missing pricing fragment: {fragment}"
    # Capabilities the model must be able to cite
    for cap in ("Vanguard", "MCP 2.4", "GitHub", "5-phase Loop"):
        assert cap in src, f"llms.txt missing capability: {cap}"
    # Stale tier names / prices from the retired 4-tier grid must be gone.
    for stale in ("$19/month", "$49/month"):
        assert stale not in src, f"stale retired-tier price still in llms.txt: {stale}"


# ── SEO ────────────────────────────────────────────────────────────────

def test_sitemap_lists_all_public_pages():
    src = _read("frontend/public/sitemap.xml")
    for path in ("/", "/signup", "/login", "/wall", "/wrapped"):
        assert f"<loc>https://auremcto.com{path}</loc>" in src, \
            f"sitemap missing public route {path}"
    # Admin-gated surfaces must NOT be in the sitemap.
    for path in ("/admin", "/dashboard", "/settings", "/automations"):
        assert f"<loc>https://auremcto.com{path}</loc>" not in src, \
            f"sitemap leaks gated route {path}"


