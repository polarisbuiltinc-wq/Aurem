"""services/seo/finding_translator.py — Onboarding Step 4 · S-B (2026-08-26).

`run_seo_fixes(dry_run=True)` returns a PATCH shape (see
orchestrator.py:246-256): `{path, action, reason, before_len, after_len}`,
where `reason` is a short technical label, sometimes several actions
bundled in one string ("injected missing meta/og tags; injected
JSON-LD schema; added alt= on 1 image(s)").

Users don't want a diff. This is the thin, deterministic (no LLM)
translator that turns that patch list into plain-language finding
cards, one card per file, one bullet per action.
"""
from __future__ import annotations

import re

_FILE_LABELS = {
    "index.html": "your homepage", "index.htm": "your homepage",
    "about.html": "your about page", "about/index.html": "your about page",
    "contact.html": "your contact page", "contact/index.html": "your contact page",
}

# Order matters — first match wins. Each entry: (regex on a single
# semicolon-split reason fragment, plain-language template).
_REASON_RULES = [
    (re.compile(r"missing meta.?/?og tags", re.I),
     "{file} is missing some search-preview tags (title/description) — that affects how Google and social previews show your site."),
    (re.compile(r"json-?ld schema", re.I),
     "{file} is missing structured data (schema.org) — that prevents Google from understanding what the page is about."),
    (re.compile(r"alt= on (\d+) image", re.I),
     "{count} image(s) on {file} are missing alt text — screen readers and Google Images can't see what they show."),
    (re.compile(r"created robots\.txt", re.I),
     "Your site is missing a robots.txt file — that controls how search engines crawl your site."),
    (re.compile(r"(generated|created) sitemap\.xml", re.I),
     "Your site is missing a sitemap.xml file — that helps search engines find all your pages."),
    (re.compile(r"canonical", re.I),
     "{file} is missing a canonical link — that can cause duplicate-content confusion in search results."),
]


def _file_label(path: str) -> str:
    key = path.rsplit("/", 1)[-1].lower() if "/" in path else path.lower()
    if path.lower() in _FILE_LABELS:
        return _FILE_LABELS[path.lower()]
    if key in _FILE_LABELS:
        return _FILE_LABELS[key]
    return path


def _translate_fragment(fragment: str, file_label: str) -> str:
    fragment = fragment.strip()
    for rx, template in _REASON_RULES:
        m = rx.search(fragment)
        if m:
            count = m.group(1) if m.groups() else None
            return template.format(file=file_label, count=count)
    return f"{file_label}: {fragment}"


def translate_patches(patches: list[dict]) -> list[dict]:
    """[{path, action, reason, ...}] -> [{path, file_label, bullets: [str]}]

    One card per file (per `path`), one bullet per ';'-split action —
    the raw semicolon-joined `reason` string is never shown to the
    user."""
    cards: list[dict] = []
    for p in patches:
        path = p.get("path", "")
        file_label = _file_label(path)
        reason = p.get("reason", "") or ""
        fragments = [f for f in (x.strip() for x in reason.split(";")) if f]
        bullets = [_translate_fragment(f, file_label) for f in fragments] or [
            f"{file_label} was updated."
        ]
        cards.append({"path": path, "file_label": file_label, "bullets": bullets})
    return cards
