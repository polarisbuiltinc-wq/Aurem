"""
services/visibility/preferred_sources.py — Preferred Sources badge (spec §5/§6.1).

Google's official Preferred Sources button (confirmed via Google's
current docs, 2026-08-30 — developers.google.com/search/docs/appearance/
preferred-sources; the feature is ~4 months old, verified live not
guessed):
  <script async src="https://news.google.com/swg/js/v1/publisher.js"></script>
  <div google-add-preferred-source-btn data-theme="light" data-lang="en"></div>

Note for the PR body (§14): if the target site sends a
Content-Security-Policy header, `news.google.com` must be allowed in
`script-src` or the SDK script will be blocked — this is a real,
documented Google requirement, not an AUREM caveat.

R2 (no-silent-fail): the SDK can be ad-blocked or CSP-blocked, so a
plain deeplink (https://www.google.com/preferences/source?q={domain})
ALWAYS renders beside the SDK element too — never SDK-only, and no
JS-based feature-detection needed (the link just always works).

Delimited by the AUREM managed-block comment (R6 idempotent re-apply,
R5 read-modify-write — same convention as robots.py/schema.py).
"""
from __future__ import annotations

_START = "<!-- AUREM Visibility Kit — auremcto.com (managed block: preferred-sources) -->"
_END = "<!-- end AUREM Visibility Kit: preferred-sources -->"
SDK_SCRIPT_SRC = "https://news.google.com/swg/js/v1/publisher.js"


def render_badge_block(domain: str, site_name: str | None = None) -> str:
    """`domain` — bare domain (e.g. "example.com"), used verbatim in the
    deeplink's `?q=` param per Google's docs."""
    label = site_name or domain
    deeplink = f"https://www.google.com/preferences/source?q={domain}"
    return (
        f"{_START}\n"
        f'<script async src="{SDK_SCRIPT_SRC}"></script>\n'
        f'<div google-add-preferred-source-btn data-theme="light" data-lang="en"></div>\n'
        f'<a href="{deeplink}" target="_blank" rel="noopener">'
        f"Prefer {label} in AI answers \u2197</a>\n"
        f"{_END}\n"
    )


def apply_managed_block(existing_html: str, domain: str, site_name: str | None = None) -> str:
    """R6 idempotent re-apply — replace only our own delimited block;
    inject before `</body>` on a first apply. Caller (apply.py) is
    responsible for the R5/R7 conflict check (no `</body>` found)."""
    block = render_badge_block(domain, site_name)
    if _START in existing_html and _END in existing_html:
        pre, rest = existing_html.split(_START, 1)
        _, post = rest.split(_END, 1)
        return pre + block + post
    return existing_html.replace("</body>", block + "</body>", 1)
