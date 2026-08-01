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
    """The SoftwareApplication JSON-LD block's Offer must match the live
    Founder Plan at $9/mo. Full four-tier pricing is not in JSON-LD
    (only in llms.txt) — the LLM crawlers get the full grid, the
    schema.org validator gets a single canonical Offer."""
    src = _read("frontend/index.html")
    blobs = _parse_all_json_ld_blocks(src)
    assert blobs, "no JSON-LD blocks in index.html"
    app = next(
        (b for b in blobs if b.get("@type") == "SoftwareApplication"),
        None,
    )
    assert app, "no SoftwareApplication JSON-LD block"
    # 2026 refresh: single Offer for the Founder Plan, not a 4-tier array
    offer = app["offers"]
    assert isinstance(offer, dict), \
        f"offers must be a single Offer object, got {type(offer).__name__}"
    assert offer["@type"] == "Offer"
    assert offer["price"] == "9"
    assert offer["priceCurrency"] == "USD"


def test_json_ld_feature_list_covers_current_capabilities():
    src = _read("frontend/index.html")
    # Case-sensitive substrings from the actual live featureList (2026-02):
    for must_mention in (
        "direct GitHub commit",   # actual: "…direct GitHub commits via REST API"
        "Project Brain",
        "Vanguard",
        "Maxx mode",
        "Automations",
        "Live Preview",            # actual: "Live Preview Panel — …"
        "F12",
    ):
        assert must_mention in src, f"JSON-LD missing feature: {must_mention}"


def test_index_title_and_description_reflect_aurem_cto():
    """Brand must be Title-Case 'Aurem CTO', not all-caps 'AUREM CTO'.
    The product tag on Twitter + OG is 'ORA' (the flagship product
    name) — 'Aurem CTO' is the parent brand referenced elsewhere in
    the head + JSON-LD."""
    src = _read("frontend/index.html")
    # Canonical brand casing:
    assert "Aurem CTO" in src, "brand 'Aurem CTO' (Title Case) missing"
    # No leftover all-caps brand from the pre-refresh era:
    assert "AUREM CTO —" not in src, \
        "old all-caps 'AUREM CTO —' brand still present"
    # Product tag on Twitter + OG — both must lead with the ORA
    # product name (2026-02 refresh preferred the short-tag).
    assert 'name="twitter:title" content="ORA' in src
    assert 'property="og:title" content="ORA' in src
    # Explicit sanity — neither should embed the old all-caps brand.
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
    reference. Team price is $49/mo per user (was $35 pre-refresh)."""
    src = _read("frontend/public/llms.txt")
    # Full four-tier pricing — current values only.
    for tier in ("Free", "Starter", "$9/month", "Pro", "$19/month",
                 "Team", "$49/month"):
        assert tier in src, f"llms.txt missing pricing fragment: {tier}"
    # Capabilities the model must be able to cite
    for cap in ("Project Brain", "Vanguard", "Maxx", "MCP 2.4",
                "GitHub", "OpenRouter"):
        assert cap in src, f"llms.txt missing capability: {cap}"
    # No stale wording from the previous llms.txt.
    assert "1,000 tokens" not in src
    assert "50k tokens" not in src
    assert "100k tokens" not in src
    # Old $35 Team-plan number must be gone.
    assert "$35" not in src, "stale $35 Team-plan price still in llms.txt"


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


