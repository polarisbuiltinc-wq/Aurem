"""Iter 358 locks — SEO/GEO/AEO refresh (Guard 2 truth extension).

1. Public AI files (llms.txt / llms-full.txt) carry NO fabricated
   adoption stats — live numbers come from /usage/public/stats.
2. Marketing copy matches the pricing SSOT (subscription_tiers.py).
3. Build-time SEO snapshots wired: `yarn build` runs seo-prerender.mjs
   which writes real static HTML for /vs/* + /compare (AEO bots without
   JS see actual content, not an empty SPA shell).
"""
from __future__ import annotations
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
COMPETITORS = FRONTEND / "src" / "data" / "competitors.mjs"
LLMS = FRONTEND / "public" / "llms.txt"
LLMS_FULL = FRONTEND / "public" / "llms-full.txt"

FAKE_STAT = re.compile(
    r"(500\+\s*developers|12,?0?00\+\s*(production\s*)?commits|"
    r"4\.9\s*/?\s*5|4\.9★|498\s*(of|/)\s*500)", re.I)


def test_llms_files_have_no_fabricated_stats():
    for p in (LLMS, LLMS_FULL):
        src = p.read_text()
        m = FAKE_STAT.search(src)
        assert not m, f"{p.name} still contains fabricated stat: {m.group(0)!r}"


def test_llms_points_to_live_stats_endpoint():
    for p in (LLMS, LLMS_FULL):
        assert "/usage/public/stats" in p.read_text(), \
            f"{p.name} must direct AI systems to the live stats endpoint"


def test_pricing_matches_subscription_tiers_ssot():
    """Marketing copy must mirror services/subscription_tiers.py."""
    tiers_src = (ROOT / "backend" / "services" / "subscription_tiers.py").read_text()
    # sanity on the SSOT itself
    assert '"price_monthly":       19' in tiers_src.replace("  ", "  ")
    comp = COMPETITORS.read_text()
    llms = LLMS.read_text()
    for claim in ("$9", "$19", "$49", "300 tasks", "50 tasks"):
        assert claim in comp, f"competitors.js missing SSOT pricing claim {claim}"
        assert claim in llms, f"llms.txt missing SSOT pricing claim {claim}"
    for stale in ("$99 / seat", "600 tasks", "unlimited tasks"):
        assert stale.lower() not in comp.lower()
        assert stale.lower() not in llms.lower()


def test_build_runs_seo_prerender():
    pkg = json.loads((FRONTEND / "package.json").read_text())
    assert "seo-prerender.mjs" in pkg["scripts"]["build"], \
        "yarn build must produce the SEO snapshots"


def test_prerender_script_uses_single_content_source():
    src = (FRONTEND / "scripts" / "seo-prerender.mjs").read_text()
    assert "src/data/competitors.mjs" in src
    assert "FAQPage" in src
    assert 'id="root"' in src  # content injected INSIDE the SPA root


def test_prerender_snapshots_exist_after_build():
    """Only enforced when dist/ exists (i.e. a build has run)."""
    dist = FRONTEND / "dist"
    if not (dist / "index.html").exists():
        return
    for rel in ("vs/devin/index.html", "vs/cursor/index.html",
                "vs/github-copilot/index.html", "vs/replit-agent/index.html",
                "vs/windsurf/index.html", "compare/index.html"):
        p = dist / rel
        assert p.exists(), f"missing snapshot {rel} — did seo-prerender run?"
        html = p.read_text()
        assert "FAQPage" in html or "ItemList" in html
        assert "<h1>" in html, f"{rel} snapshot has no real content"


def test_landing_last_verified_is_current():
    comp = COMPETITORS.read_text()
    assert 'LAST_VERIFIED = "June 30, 2026"' in comp
