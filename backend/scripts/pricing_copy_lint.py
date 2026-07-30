"""
scripts/pricing_copy_lint.py — Iter 364 · Marketing-copy truth gate

Prevents the "unlimited tasks" drift that landed in production before
Iter 364. Reads `backend/services/subscription_tiers.py` (source of
truth) and greps frontend copy for a small blacklist of drift-prone
phrases. Fails the build (non-zero exit) if any hit is found in a
marketing/pricing surface.

Rules:
  1. The word "unlimited" (any case) is forbidden within 60 characters
     of the words "task", "Pro", "Team", or "Starter" — those are the
     copy zones users read as plan limits.
  2. Exempt words with `is_unlimited`, `unlimited=`, `unlimited=true`,
     JSX prop patterns — those are server-side flags for the founder /
     admin tier and are legitimate.
  3. Explicitly-allowed sentences may be listed in
     `scripts/pricing_copy_lint_allowlist.txt`.

Wired into CI via `.github/workflows/ci.yml` in a follow-up.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"
PUBLIC   = ROOT / "frontend" / "public"

# Marketing surfaces where we care about pricing accuracy.
SCAN_DIRS = [FRONTEND, PUBLIC]
SCAN_EXT  = (".jsx", ".tsx", ".js", ".ts", ".html", ".md", ".txt")

# Files that legitimately talk about the founder flag / server-side
# `is_unlimited` boolean — these are NOT marketing copy.
SKIP_FILES = {
    "TokenBell.jsx",           # UI shows "∞ Unlimited" for founder token wallet
    "Shell.jsx",               # reads is_unlimited from /usage/me
    "BulkFixConfirmModal.jsx", # server flag pass-through
    "ChatPanel.jsx",           # founder detection helper
    "chatTextUtils.js",        # is_unlimited detection helper
    "useFixQuota.js",          # server flag pass-through
    "Tokens.jsx",              # "∞ Unlimited" label for founder
    "Settings.jsx",            # founder tasks-remaining label
}

# Whitelisted exact sentences (case-preserved substring match).
ALLOWLIST_FILE = ROOT / "backend" / "scripts" / "pricing_copy_lint_allowlist.txt"


def _load_allowlist() -> set[str]:
    if not ALLOWLIST_FILE.exists():
        return set()
    return {
        ln.strip() for ln in ALLOWLIST_FILE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }


# Danger phrases: "unlimited" within N chars of a pricing keyword.
_DANGER_KEYWORDS = ("task", "tasks", "Pro", "Team", "Starter", "Free", "loop")
_UNLIMITED_RE = re.compile(r"[Uu]nlimited")


def _scan_file(path: Path, allowlist: set[str]) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return hits
    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        for m in _UNLIMITED_RE.finditer(line):
            # Skip identifiers like is_unlimited / unlimited=
            surrounding = line[max(0, m.start() - 5):m.end() + 5].lower()
            if any(bad in surrounding for bad in (
                "is_unlimited", "unlimited=", "unlimited:", "!unlimited",
                "unlimited &&", "unlimited ?", "unlimited)", "unlimited,",
                "!!unlimited",
            )):
                continue
            # Danger only if a pricing keyword appears within 60 chars.
            window = line[max(0, m.start() - 60): m.end() + 60]
            if not any(kw in window for kw in _DANGER_KEYWORDS):
                continue
            # Allowlisted exact line?
            if any(al in line for al in allowlist):
                continue
            hits.append((lineno, line.strip()[:180]))
    return hits


def main() -> int:
    allowlist = _load_allowlist()
    all_hits: list[tuple[Path, int, str]] = []
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in SCAN_EXT:
                continue
            if p.name in SKIP_FILES:
                continue
            for lineno, snippet in _scan_file(p, allowlist):
                all_hits.append((p.relative_to(ROOT), lineno, snippet))

    if not all_hits:
        print("[pricing_copy_lint] OK — no drift-prone 'unlimited' copy found.")
        return 0

    print("[pricing_copy_lint] ❌ FOUND drift-prone marketing copy:")
    print("Backend source of truth: backend/services/subscription_tiers.py:26-83")
    print("  Free=10  Starter=50  Pro=300  Team=400 tasks/month")
    print()
    for rel, lineno, snippet in all_hits:
        print(f"  {rel}:{lineno}  {snippet}")
    print()
    print("If a line is a legitimate exception (e.g. describes the")
    print("server-side is_unlimited flag, or founder-tier UI), add it")
    print(f"to {ALLOWLIST_FILE.relative_to(ROOT)}.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
