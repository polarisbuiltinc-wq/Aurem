"""predeploy_integration_health.py — Iter 388-aa (2026-02-14).

Pre-deploy integration-health gate. Reads the last cached
`integration_health.latest` snapshot from Mongo and surfaces any probe
that is `broken` or `warn` — so the agent (and founder) don't ship a
deploy while an external dependency is silently melting (e.g. OpenRouter
balance at $0.20 → Council-A LLM routing failure risk).

Why this exists
---------------
Session 388 root-cause of the "kitni der se critical hai" complaint:
integration_health probes already emit `critical` signals, but nothing
surfaces them into the pre-deploy path. Every previous deploy ran
`predeploy_gate.sh` and shipped happily while OpenRouter drained.

Behaviour
---------
- Reads Mongo via MONGO_URL / DB_NAME from backend/.env (same as the
  app itself). No secrets in this script.
- Prints a compact status table.
- Exit codes:
    0 = all probes ok or only 'disabled'/'missing' warnings
    2 = at least one 'warn'  (soft signal — agent surfaces + continues)
    3 = at least one 'broken' (hard signal — agent surfaces + asks founder)
  This script NEVER hard-blocks — the founder decides whether to ship.

Run
---
    python3 scripts/predeploy_integration_health.py

Wired into `scripts/predeploy_gate.sh` as Lane 6.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _load_env() -> None:
    env_path = Path(__file__).resolve().parent.parent / "backend" / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def _snapshot() -> dict | None:
    from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore
    mongo_url = os.environ.get("MONGO_URL")
    db_name   = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print("!! predeploy_integration_health: MONGO_URL / DB_NAME missing",
              file=sys.stderr)
        return None
    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=3000)
    try:
        snap = await client[db_name].integration_health.find_one(
            {"_id": "latest"}, {"_id": 0},
        )
    finally:
        client.close()
    return snap


def _age_seconds(generated_at: float | None) -> float | None:
    if not generated_at:
        return None
    try:
        return max(0.0, datetime.now(timezone.utc).timestamp() - float(generated_at))
    except Exception:
        return None


def _fmt_age(sec: float | None) -> str:
    if sec is None:
        return "unknown"
    if sec < 60:
        return f"{int(sec)}s ago"
    if sec < 3600:
        return f"{int(sec/60)}m ago"
    return f"{sec/3600:.1f}h ago"


def _summarise(results: list[dict]) -> tuple[int, list[dict], list[dict]]:
    """Return (exit_code, broken_list, warn_list)."""
    broken, warn = [], []
    for r in results or []:
        st = (r.get("status") or "").lower()
        if st == "broken":
            broken.append(r)
        elif st == "warn":
            warn.append(r)
    if broken:
        return 3, broken, warn
    if warn:
        return 2, broken, warn
    return 0, broken, warn


def _print_row(r: dict) -> None:
    name    = r.get("name") or r.get("id") or "?"
    status  = r.get("status") or "?"
    summary = (r.get("summary") or "").replace("\n", " ")[:100]
    hint    = (r.get("fix_hint") or "").replace("\n", " ")[:120]
    print(f"  [{status:<7}] {name:<26} — {summary}")
    if hint:
        print(f"           fix: {hint}")


def main() -> int:
    _load_env()
    print("══════════ INTEGRATION-HEALTH PRE-DEPLOY LANE ══════════")
    try:
        snap = asyncio.run(_snapshot())
    except Exception as e:  # noqa: BLE001
        print(f"!! Could not read integration_health snapshot: {e!r}",
              file=sys.stderr)
        print("   (non-blocking — proceed with caution)")
        return 0

    if not snap:
        print("!! No integration_health snapshot found in Mongo.")
        print("   Trigger one via Admin → Integrations → Refresh, or wait for the cron.")
        print("   (non-blocking — proceed with caution)")
        return 0

    results = snap.get("results") or []
    summary = snap.get("summary") or {}
    age_s   = _age_seconds(snap.get("generated_at"))

    print(f"Snapshot age: {_fmt_age(age_s)}   ({len(results)} probes)")
    print(f"Summary:      {json.dumps(summary, sort_keys=True)}")

    code, broken, warn = _summarise(results)

    if broken:
        print()
        print(f"🔴 BROKEN probes ({len(broken)}):")
        for r in broken:
            _print_row(r)
    if warn:
        print()
        print(f"🟡 WARN probes ({len(warn)}):")
        for r in warn:
            _print_row(r)

    if code == 0:
        print()
        print("✅ All probes ok — integration-health lane clean.")
    else:
        print()
        print("──────────────────────────────────────────────────────────")
        print("⚠️  Predeploy signal (NON-BLOCKING):")
        if code == 3:
            print("    At least one integration is BROKEN. Founder must decide")
            print("    whether the deploy proceeds despite the outage.")
        elif code == 2:
            print("    At least one integration is degraded (WARN).")
            print("    Common causes: low balance, no verified domains, quota.")
        print("──────────────────────────────────────────────────────────")

    return code


if __name__ == "__main__":
    sys.exit(main())
