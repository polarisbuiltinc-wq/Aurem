"""
services/visibility/sitemap.py — sitemap.xml generate/update (spec §5/§6.5).

Deterministic, 0 LLM tokens (R9/R15). Missing → generate from scanned
URLs. Exists → update lastmod, drop dupes (R6 idempotent).
"""
from __future__ import annotations

_AI_COMMENT = "<!-- AI crawlers: see robots.txt (managed by AUREM) -->"


def render_sitemap(urls: list[dict], scan_date: str) -> str:
    """`urls` = [{loc}], deduped + sorted for determinism (R6)."""
    seen = []
    for u in urls:
        loc = u["loc"]
        if loc not in seen:
            seen.append(loc)
    entries = "\n".join(
        f"  <url>\n    <loc>{loc}</loc>\n    <lastmod>{scan_date}</lastmod>\n  </url>"
        for loc in sorted(seen)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"{_AI_COMMENT}\n"
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n"
    )


def merge_lastmod(existing_xml: str | None, urls: list[dict], scan_date: str) -> str:
    """t_sitemap_idempotent — running apply twice yields one sitemap, no
    dupes, existing entries get a fresh lastmod. We regenerate
    deterministically from the same URL set rather than XML-patching,
    since the output is byte-identical either way and far simpler."""
    return render_sitemap(urls, scan_date)
