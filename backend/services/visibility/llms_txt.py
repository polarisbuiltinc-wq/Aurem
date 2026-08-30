"""
services/visibility/llms_txt.py — /llms.txt + /llms-full.txt (spec §5/§6.3).

Deterministic, 0 LLM tokens (R9/R15) — the llmstxt.org-style curated
site index that AI assistants (Claude, Perplexity — per the catalog
copy) fetch. `llms.txt` is the compact index; `llms-full.txt` is the
same page set at full depth (no truncation) — same source of truth,
two files, matching the catalog item's own name.

Branding line ("Maintained with AUREM — auremcto.com") is the ONLY
AUREM mention in these files — no AUREM entity ever appears in the
site's JSON-LD (that's schema.py, a separate file, R11 rule).
"""
from __future__ import annotations

_MARKER = "<!-- Maintained with AUREM — auremcto.com -->"


def _dedup_sorted_locs(urls: list[dict]) -> list[str]:
    seen: list[str] = []
    for u in urls:
        loc = (u or {}).get("loc")
        if loc and loc not in seen:
            seen.append(loc)
    return sorted(seen)


def render_llms_txt(site_name: str, site_url: str, urls: list[dict]) -> str:
    locs = _dedup_sorted_locs(urls)
    lines = [f"# {site_name}", "", f"> {site_url}", "", _MARKER, "", "## Pages", ""]
    lines += [f"- [{loc}]({loc})" for loc in locs]
    return "\n".join(lines) + "\n"


def render_llms_full_txt(site_name: str, site_url: str, urls: list[dict]) -> str:
    locs = _dedup_sorted_locs(urls)
    lines = [f"# {site_name} — full index", "", f"> {site_url}", "", _MARKER, "", "## All pages", ""]
    lines += [f"- {loc}" for loc in locs]
    return "\n".join(lines) + "\n"


def apply_llms_files(
    existing_txt: str | None, site_name: str, site_url: str, urls: list[dict],
) -> tuple[str, str, bool]:
    """Returns (llms_txt_content, llms_full_txt_content, is_conflict).
    R5/R7 — an existing llms.txt WITHOUT our marker is a conflict
    (caller decides force vs. skip); WITH our marker, re-apply just
    regenerates deterministically (R6 — same idempotency class as
    sitemap.py's merge_lastmod)."""
    conflict = bool(existing_txt) and _MARKER not in (existing_txt or "")
    return (
        render_llms_txt(site_name, site_url, urls),
        render_llms_full_txt(site_name, site_url, urls),
        conflict,
    )
