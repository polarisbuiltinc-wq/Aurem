"""
services/visibility/schema.py — JSON-LD + meta/OG gap-fill (spec §5/§6.4).

Renders Organization, WebSite(+SearchAction), FAQPage (from detected
FAQs), and a Person entity for the author (with sameAs) — merged into
the target HTML's <head>, never clobbering anything already there.
Delimited by the §7.1 AUREM comment so re-apply (R6) replaces only our
own block.
"""
from __future__ import annotations

import json

_START = "<!-- AUREM Visibility Kit — auremcto.com (managed block) -->"
_END = "<!-- end AUREM Visibility Kit -->"


def render_json_ld(site: dict) -> str:
    """`site` = {name, url, logo_url, sameAs, faqs, author}."""
    blocks = [{
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": site["name"], "url": site["url"],
        **({"logo": site["logo_url"]} if site.get("logo_url") else {}),
        **({"sameAs": site["sameAs"]} if site.get("sameAs") else {}),
    }, {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": site["name"], "url": site["url"],
        "potentialAction": {
            "@type": "SearchAction",
            "target": f"{site['url'].rstrip('/')}/?q={{search_term_string}}",
            "query-input": "required name=search_term_string",
        },
    }]
    faqs = site.get("faqs") or []
    if faqs:
        blocks.append({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [{
                "@type": "Question", "name": f["question"],
                "acceptedAnswer": {"@type": "Answer", "text": f["answer"]},
            } for f in faqs],
        })
    author = site.get("author")
    if author and author.get("name"):
        # t_author_schema — Person+sameAs emitted ONLY when author data present.
        person = {"@context": "https://schema.org", "@type": "Person", "name": author["name"]}
        if author.get("sameAs"):
            person["sameAs"] = author["sameAs"]
        blocks.append(person)

    scripts = "\n".join(
        f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in blocks
    )
    return f"{_START}\n{scripts}\n{_END}\n"


def apply_managed_block(existing_head: str, site: dict) -> str:
    """R6 idempotent re-apply — replaces only our own delimited block."""
    block = render_json_ld(site)
    if _START in existing_head and _END in existing_head:
        pre, rest = existing_head.split(_START, 1)
        _, post = rest.split(_END, 1)
        return pre + block + post
    return existing_head.rstrip("\n") + "\n" + block
