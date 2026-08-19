#!/usr/bin/env python3
"""
scripts/verify_regression_patterns.py — 2026-08-19

Actually RUNS each regression pattern's `test_ref` (a real pytest node
ID or file) and records the live pass/fail into `ora_regression_patterns`
via record_pattern_verification — same convention as the other g*.py
scripts (g1_route_smoke_sweep.py, g15_dependency_scan.py): a script that
does the real work, persists the result, and the admin endpoint just
reads what was persisted (never runs pytest inline inside a request).

Run manually, from CI, or from predeploy_gate.sh:
    python scripts/verify_regression_patterns.py
"""
from __future__ import annotations
import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient
from services.ora_fix_learning import (
    list_regression_patterns, record_pattern_verification,
)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_test(test_ref: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", test_ref, "-q"],
            cwd=BACKEND_DIR, capture_output=True, text=True, timeout=120,
        )
        detail = (r.stdout[-400:] + r.stderr[-200:]).strip()
        return r.returncode == 0, detail
    except Exception as e:                                    # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


async def main() -> None:
    mongo_url = os.environ.get("MONGO_URL")
    if not mongo_url:
        print("ERROR: MONGO_URL not set.", file=sys.stderr)
        sys.exit(1)
    db = AsyncIOMotorClient(mongo_url)[os.environ.get("DB_NAME", "aurem_dev")]

    patterns = await list_regression_patterns(db)
    checked, passed = 0, 0
    for p in patterns:
        test_ref = p.get("test_ref")
        if not test_ref:
            print(f"SKIP  {p['pattern_id']} — no automated test (status={p['status']})")
            continue
        ok, detail = _run_test(test_ref)
        await record_pattern_verification(
            db, pattern_id=p["pattern_id"], passed=ok, detail=detail,
        )
        checked += 1
        passed += int(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {p['pattern_id']} — {test_ref}")

    print(f"\n{passed}/{checked} verified patterns pass "
          f"({len(patterns) - checked} have no automated test yet).")
    sys.exit(0 if passed == checked else 1)


if __name__ == "__main__":
    asyncio.run(main())
