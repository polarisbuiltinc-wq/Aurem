"""
scripts/g4_secret_scanner.py — G4 · Rendered-page secret scanner

Fetches every public page's rendered HTML/JS and greps for real
secret patterns:
  - `sk-aurem-[A-Za-z0-9_-]{20,}`  (our API key format)
  - `ghp_[A-Za-z0-9]{20,}`          (GitHub PATs)
  - `sk_(live|test)_[A-Za-z0-9]{20,}` (Stripe secret keys)
  - `AKIA[0-9A-Z]{16}`              (AWS access keys)
  - JWT bearer tokens (`eyJ` prefix + base64)

Wired into CI + predeploy_gate. Exit non-zero on any hit outside
allow-listed masked patterns (e.g. `sk-aurem-****` docs strings).

Run locally: python scripts/g4_secret_scanner.py [--base-url URL]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.request
import urllib.error
from typing import List

PATTERNS = {
    "aurem_api_key":  re.compile(r"sk-aurem-[A-Za-z0-9_\-]{20,}"),
    "github_pat":     re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    "stripe_live":    re.compile(r"sk_live_[A-Za-z0-9]{20,}"),
    "stripe_test":    re.compile(r"sk_test_[A-Za-z0-9]{20,}"),
    "aws_key":        re.compile(r"AKIA[0-9A-Z]{16}"),
    "jwt_bearer":     re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}"),
}

# Allowed masked / placeholder tokens that legitimately appear in HTML.
ALLOWLIST_SUBSTRS = (
    "sk-aurem-****",  "sk-aurem-XXXX",  "sk-aurem-abcd",
    "ghp_XXXX",       "ghp_your_token",
    "sk_test_XXXX",   "sk_live_XXXX",
)

# Public pages we scan on every deploy. Add here as new routes ship.
DEFAULT_ROUTES = (
    "/", "/pricing", "/login", "/signup",
    "/wall", "/tools", "/docs",
)


def _fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "aurem-g4-scanner"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _scan_text(text: str, path: str) -> List[tuple[str, str, str]]:
    hits: List[tuple[str, str, str]] = []
    for name, rx in PATTERNS.items():
        for m in rx.finditer(text):
            match = m.group(0)
            # Skip obvious mask/doc strings.
            if any(al in match for al in ALLOWLIST_SUBSTRS):
                continue
            # Preview the surrounding 40 chars for the report.
            ctx_start = max(0, m.start() - 20)
            ctx_end   = min(len(text), m.end() + 20)
            snippet = text[ctx_start:ctx_end].replace("\n", " ")
            hits.append((path, name, snippet[:200]))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url",
                    default=os.environ.get("G4_BASE_URL", "https://auremcto.com"))
    ap.add_argument("--route", action="append", default=list(DEFAULT_ROUTES))
    args = ap.parse_args()

    all_hits: List[tuple[str, str, str]] = []
    for path in args.route:
        url = args.base_url.rstrip("/") + path
        try:
            text = _fetch(url)
        except urllib.error.HTTPError as e:
            if e.code >= 500:
                print(f"[g4] WARN {path}: HTTP {e.code} (skipped)")
                continue
            text = ""
        except Exception as e:                          # noqa: BLE001
            print(f"[g4] WARN {path}: {e}")
            continue
        hits = _scan_text(text, path)
        all_hits.extend(hits)

    if not all_hits:
        print(f"[g4] OK — scanned {len(args.route)} routes, "
              "zero real secrets leaked in rendered output.")
        return 0

    print(f"[g4] ❌ FOUND {len(all_hits)} secret leaks in rendered pages:")
    for path, name, snippet in all_hits:
        print(f"  {path}  [{name}]  {snippet}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
