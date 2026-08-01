#!/usr/bin/env python3
"""
scripts/persona_diet_report.py — Persona LOC breakdown by section.

When `test_persona_loc_guardrail` fires (persona ≥ 20,000 chars),
the founder needs a targeted "trim these 3 rules first" list, not
a full-file re-read. This script parses `AUREM_CTO_PERSONA` into
sections (each `# HEADING` marker becomes a section) and prints a
sorted table of chars-per-section — heaviest first.

Usage (from repo root):
    python3 scripts/persona_diet_report.py           # human table
    python3 scripts/persona_diet_report.py --top 10  # only heaviest 10
    python3 scripts/persona_diet_report.py --json    # machine-readable

Output columns:
    CHARS  %TOTAL  BUDGET-SHARE  SECTION-HEADING
where BUDGET-SHARE = section-chars ÷ 22,000 (the hard budget).

Cheap by design — zero deps, ~50 LOC, one-file runnable.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BUDGET = 22_000                # hard budget (test_iter129_chat_latency_budget)
WARN   = 20_000                # early-warning (test_persona_loc_guardrail)


def _load_persona() -> str:
    """Import the live persona without booting FastAPI."""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "backend"))
    from services.orchestrator import AUREM_CTO_PERSONA
    return AUREM_CTO_PERSONA


def _split_by_heading(text: str) -> list[tuple[str, str]]:
    """Split at every `# HEADING` marker. Preamble before first
    heading is grouped as `(PROLOGUE)`."""
    sections: list[tuple[str, str]] = []
    current_head = "(PROLOGUE)"
    current_body: list[str] = []
    for line in text.splitlines(keepends=True):
        # Heading = line starts with "# " and next non-space is a capital
        stripped = line.lstrip()
        if stripped.startswith("# ") and stripped[2:3].isupper():
            if current_body:
                sections.append((current_head, "".join(current_body)))
            current_head = stripped[2:].rstrip()
            current_body = [line]
        else:
            current_body.append(line)
    if current_body:
        sections.append((current_head, "".join(current_body)))
    return sections


def _report(top: int | None, as_json: bool) -> int:
    persona = _load_persona()
    total = len(persona)
    sections = _split_by_heading(persona)
    ranked = sorted(sections, key=lambda p: -len(p[1]))
    if top is not None:
        ranked = ranked[:top]

    rows = [
        {
            "chars": len(body),
            "pct_total": round(100 * len(body) / total, 2),
            "budget_share": round(100 * len(body) / BUDGET, 2),
            "heading": head,
        }
        for head, body in ranked
    ]

    if as_json:
        print(json.dumps({
            "total_chars": total,
            "budget":       BUDGET,
            "warn_threshold": WARN,
            "over_warn":    total >= WARN,
            "over_budget":  total >= BUDGET,
            "sections":     rows,
        }, indent=2))
        return 0

    # Human table
    print(f"AUREM_CTO_PERSONA — {total:,} chars  "
          f"(warn ≥ {WARN:,}, hard ≥ {BUDGET:,})")
    if total >= BUDGET:
        print(f"  ⛔ OVER HARD BUDGET by {total - BUDGET:,} chars")
    elif total >= WARN:
        print(f"  ⚠  OVER WARN THRESHOLD by {total - WARN:,} chars "
              f"({BUDGET - total:,} char headroom to hard budget)")
    else:
        print(f"  ✓ under warn threshold ({WARN - total:,} char headroom)")
    print()
    print(f"  {'CHARS':>7}  {'%TOT':>5}  {'BUDGET':>6}  SECTION")
    print(f"  {'─'*7}  {'─'*5}  {'─'*6}  {'─'*60}")
    for r in rows:
        print(f"  {r['chars']:>7,}  {r['pct_total']:>4.1f}%  "
              f"{r['budget_share']:>5.1f}%  {r['heading'][:60]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=None,
                    help="only show the heaviest N sections")
    ap.add_argument("--json", action="store_true",
                    help="emit JSON instead of human table")
    args = ap.parse_args()
    return _report(args.top, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
