#!/usr/bin/env python3
"""Iter 348b — classify ALL open PRs for the founder's eyeball review.

Produces a markdown table: PR #, created, category (from title), files
changed, +/-, and POISON flag (any changed file is in the expanded
scanner-pipeline exclusion list → 100% self-scan artifact, close
without opening).

Usage:
    GITHUB_PAT=ghp_xxx REPO=polarisbuiltinc-wq/auremdev \
        python3 scripts/classify_open_prs.py > /tmp/pr_table.md
"""
import os
import re
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from services.scanner_utils import is_scanner_rule_file  # noqa: E402

PAT  = os.environ.get("GITHUB_PAT")
REPO = os.environ.get("REPO")
API  = "https://api.github.com"
if not PAT or not REPO:
    sys.exit("Set GITHUB_PAT and REPO env vars.")

H = {"Authorization": f"Bearer {PAT}",
     "Accept": "application/vnd.github+json"}


def all_open_prs():
    page = 1
    while True:
        r = requests.get(f"{API}/repos/{REPO}/pulls", headers=H,
                         params={"state": "open", "per_page": 100,
                                 "page": page}, timeout=30)
        r.raise_for_status()
        prs = r.json()
        yield from prs
        if len(prs) < 100:
            return
        page += 1


def pr_files(number):
    out, page = [], 1
    while True:
        r = requests.get(f"{API}/repos/{REPO}/pulls/{number}/files",
                         headers=H,
                         params={"per_page": 100, "page": page}, timeout=30)
        r.raise_for_status()
        fs = r.json()
        out.extend(fs)
        if len(fs) < 100:
            return out
        page += 1


def category(title):
    m = re.search(r"fix\(([^)]+)\)", title or "")
    return m.group(1) if m else (title or "")[:40]


def main():
    rows, poison_count = [], 0
    for pr in all_open_prs():
        n = pr["number"]
        files = pr_files(n)
        names = [f["filename"] for f in files]
        adds = sum(f.get("additions", 0) for f in files)
        dels = sum(f.get("deletions", 0) for f in files)
        marker_only = bool(names) and all(
            f.startswith(".vanguard/") for f in names)
        poison = any(is_scanner_rule_file(f) for f in names)
        if poison:
            poison_count += 1
        flag = ("POISON" if poison
                else "marker-only" if marker_only else "REVIEW")
        rows.append((n, pr["created_at"][:10], category(pr["title"]),
                     "; ".join(names[:4]) + ("…" if len(names) > 4 else ""),
                     f"+{adds}/-{dels}", pr.get("draft"), flag))

    print("| PR | Created | Category | Files changed | +/- | Draft | Verdict |")
    print("|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: x[0]):
        print("| #{} | {} | {} | {} | {} | {} | **{}** |".format(*r))
    print(f"\nTotals: {len(rows)} open · {poison_count} POISON "
          f"(touch scanner-pipeline files) · "
          f"{sum(1 for r in rows if r[6] == 'marker-only')} marker-only · "
          f"{sum(1 for r in rows if r[6] == 'REVIEW')} need eyeball review")


if __name__ == "__main__":
    main()
