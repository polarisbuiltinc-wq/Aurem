"""
services/page_resolver.py — R4 (2026-08-31)

Deterministic resolver: business-owner page phrases ("my homepage",
"the about page", "the footer") -> ranked candidate file paths from
a REAL repo file listing. Pure function, no LLM, no network call of
its own — the caller (list_repo_files' tool result) supplies the
paths it already fetched from GitHub.

Guarantee (closes L-02 — the "named a file, got a different file"
recurrence): this function NEVER invents or silently substitutes an
unrelated path. If nothing matches, or two+ candidates tie at the
same confidence, the result is `ambiguous=True` / `best=None` and the
caller must ask ONE plain question instead of guessing.
"""
from __future__ import annotations

import re

# (category, phrase-in-user-message regex, filename/dirname regex)
_PAGE_CATEGORIES: list[tuple[str, "re.Pattern", "re.Pattern"]] = [
    ("home", re.compile(r"\b(home\s?page|main page|landing page|first page)\b", re.I),
     re.compile(r"(^|/)(index|home|landing|app)\.(jsx?|tsx?|html?)$", re.I)),
    ("about", re.compile(r"\babout\b", re.I),
     re.compile(r"(^|/)about[\w-]*\.(jsx?|tsx?|html?)$", re.I)),
    ("contact", re.compile(r"\bcontact\b", re.I),
     re.compile(r"(^|/)contact[\w-]*\.(jsx?|tsx?|html?)$", re.I)),
    ("pricing", re.compile(r"\bpric(e|ing)\b", re.I),
     re.compile(r"(^|/)pric(e|ing)[\w-]*\.(jsx?|tsx?|html?)$", re.I)),
    ("footer", re.compile(r"\bfooter|bottom of (my|our|the) page\b", re.I),
     re.compile(r"(^|/)footer[\w-]*\.(jsx?|tsx?)$", re.I)),
    ("header", re.compile(r"\bheader|nav ?bar|top of (my|our|the) page\b", re.I),
     re.compile(r"(^|/)(header|nav ?bar)[\w-]*\.(jsx?|tsx?)$", re.I)),
]


def detect_category(user_text: str) -> str | None:
    """Which business-owner page phrase (if any) is in this message."""
    for category, phrase_re, _ in _PAGE_CATEGORIES:
        if phrase_re.search(user_text or ""):
            return category
    return None


def resolve(paths: list[str], user_text: str) -> dict:
    """Deterministic — same (paths, user_text) always returns the same
    result. Returns:
      {category, candidates: [path,...], best: path|None, ambiguous: bool}
    `paths` MUST be a real repo file listing; this never fabricates one.
    """
    category = detect_category(user_text)
    if not category or not paths:
        return {"category": category, "candidates": [], "best": None, "ambiguous": False}
    file_re = next(c[2] for c in _PAGE_CATEGORIES if c[0] == category)
    matches = [p for p in paths if file_re.search(p)]
    if not matches:
        return {"category": category, "candidates": [], "best": None, "ambiguous": False}
    # Deterministic tie-break: shallowest path (closest to repo root),
    # then shortest string — never an LLM guess.
    matches.sort(key=lambda p: (p.count("/"), len(p)))
    shallowest = matches[0].count("/")
    tied = [p for p in matches if p.count("/") == shallowest]
    ambiguous = len(tied) > 1
    return {
        "category": category,
        "candidates": matches[:5],
        "best": None if ambiguous else matches[0],
        "ambiguous": ambiguous,
    }


def hint_text(result: dict) -> str | None:
    """Render `resolve()`'s output as a short tool-result hint string
    for the model — or None when there's nothing to say."""
    if not result.get("category"):
        return None
    if result.get("best"):
        return (
            f"📍 page-resolver hint: the user's \"{result['category']}\" page "
            f"reference most likely maps to `{result['best']}`. Use this file "
            "unless you have concrete evidence it's wrong — if so, ask ONE "
            "plain question naming the page in business terms rather than "
            "silently picking a different file."
        )
    if result.get("ambiguous"):
        cands = ", ".join(f"`{c}`" for c in result["candidates"][:3])
        return (
            f"📍 page-resolver hint: {len(result['candidates'])} files could "
            f"be the user's \"{result['category']}\" page ({cands}). Do NOT "
            "silently pick one — ask ONE plain question naming the pages in "
            "business terms (e.g. \"the page with your products, or the "
            "page that comes first when someone visits?\")."
        )
    return (
        f"📍 page-resolver hint: no file obviously matches the user's "
        f"\"{result['category']}\" page reference. Do not guess an unrelated "
        "file (e.g. README.md) — say you're not sure which page they mean "
        "and ask ONE plain question."
    )
