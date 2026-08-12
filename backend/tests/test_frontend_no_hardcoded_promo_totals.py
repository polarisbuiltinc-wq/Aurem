"""
Guard against re-introducing hardcoded promo/offer counter TOTALS in
the frontend UI (the "498/500", "496 of 500" class of bug).

The rule: any component that renders a promo/offer counter MUST use
`total` from the live API response, not a numeric literal. The API is
the single source of truth so env-driven changes to
`PROMO_FIRST50_TOTAL` / `PROMO_FOUNDER_OFFER_TOTAL` never silently
drift the UI.

Precedent:
  • 2026-01: hardcoded "498/500" on landing hero chip → replaced with
    live poll of /promo/first50/status (Track 3 item #31)
  • 2026-02-12: hardcoded "of 500" in FounderOfferPill.jsx surfaced
    on landing page as "🇨🇦 496 of 500 founder spots remaining" while
    the API's total was still 500 — coincidentally matching but
    guaranteed to drift if the env changes. Fixed by binding to
    `s.total`.

This test walks the frontend components + pages and flags any file
that renders `founder spots` next to a numeric literal instead of a
prop/state variable. Keep the exempt list minimal and reviewed —
any addition needs a comment justifying why the literal is safe.
"""
from pathlib import Path
import re

FRONTEND_ROOT = Path("/app/frontend/src")
GLOBS = ("**/*.jsx", "**/*.js", "**/*.tsx", "**/*.ts")

# Regex: matches "of 500", "of 50", "of 100", etc. inside a template
# literal or JSX text — where 500/50/100 is a numeric literal (not
# an interpolation `${…}` or `{…}`).
#
# Positive matches (what we WANT to catch):
#   of 500 founder spots remaining          ← 498/500-class bug
#   {s.remaining} of 500 founder spots      ← FounderOfferPill.jsx pre-fix
#
# Negative matches (what we DON'T want to catch — legit patterns):
#   of {s.total} founder spots              ← live-bound (correct)
#   {promo.remaining}/{promo.total} founder ← live-bound (correct)
#   "First-50" in comments                  ← doc / brand names
HARDCODED_TOTAL_RE = re.compile(
    r"of\s+\d+\s+founder\s+spots", re.IGNORECASE
)

# Files that are ALLOWED to contain the pattern for a legitimate
# reason. Each entry MUST include a comment explaining why.
EXEMPT_FILES = {
    # No exemptions today. If adding: keep to files that explain
    # the bug pattern in a JSDoc comment (not the actual render).
}


def _all_frontend_files() -> list[Path]:
    files: list[Path] = []
    for pattern in GLOBS:
        files.extend(FRONTEND_ROOT.glob(pattern))
    # Exclude node_modules just in case
    return [f for f in files if "node_modules" not in f.parts]


def test_no_hardcoded_founder_spot_totals_in_frontend():
    offenders: list[tuple[Path, int, str]] = []
    for path in _all_frontend_files():
        rel = path.relative_to(FRONTEND_ROOT)
        if str(rel) in EXEMPT_FILES:
            continue
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for lineno, line in enumerate(src.splitlines(), 1):
            # Skip JSDoc / comment lines that describe the bug — we
            # only care about actual JSX render lines.
            stripped = line.strip()
            if stripped.startswith("*") or stripped.startswith("//"):
                continue
            m = HARDCODED_TOTAL_RE.search(line)
            if m:
                offenders.append((rel, lineno, line.strip()))

    if offenders:
        details = "\n".join(
            f"  {p}:{ln}  {snippet}"
            for p, ln, snippet in offenders
        )
        raise AssertionError(
            "Hardcoded promo/offer counter total detected. Bind to the "
            "live API `total` field instead of a numeric literal — see "
            "FounderOfferPill.jsx / Landing.jsx hero chip for the correct "
            "pattern.\n\n" + details
        )
