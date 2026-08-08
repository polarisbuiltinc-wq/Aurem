"""
migrations/cli.py
=================
Command-line interface for the AUREM migration framework.

Usage:
    python -m backend.migrations status
    python -m backend.migrations up [--target 003] [--dry-run]
    python -m backend.migrations down [--target 001] [--force]
    python -m backend.migrations new <slug>
    python -m backend.migrations verify
    python -m backend.migrations mark-applied 001
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient

# Load backend/.env into the process before touching Mongo — the CLI
# is invoked outside supervisor so it doesn't inherit env by default.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_env_file = _BACKEND_ROOT / ".env"
if _env_file.is_file():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        _v = _v.strip().strip('"').strip("'")
        os.environ.setdefault(_k.strip(), _v)

# Ensure the `backend/` root is on sys.path so `services.*` imports
# inside migration files resolve when invoked from repo root.
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from . import framework as fw  # noqa: E402


def _get_db():
    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(
        mongo_url,
        maxPoolSize=10, minPoolSize=1, maxIdleTimeMS=30_000,
        connectTimeoutMS=10_000,
    )
    return client, client[db_name]


async def _cmd_status(_args) -> int:
    client, db = _get_db()
    try:
        report = await fw.status(db)
        env = fw._current_env()
        print(f"AUREM migration status  (env={env})")
        print(f"  applied: {len(report.applied)}")
        for r in report.applied:
            print(f"    ✓ {r.version}  {r.name}  ({r.duration_ms}ms, "
                  f"env={r.env}, at={r.applied_at:%Y-%m-%d %H:%M:%S})")
        print(f"  pending: {len(report.pending)}")
        for p in report.pending:
            tag = " [dev-only]" if p.dev_only else ""
            print(f"    ○ {p.version}  {p.name}{tag}")
        if report.drift:
            print(f"  DRIFT DETECTED ({len(report.drift)}):")
            for d in report.drift:
                print(f"    ! {d.version}  {d.name}")
                print(f"       recorded: {d.recorded_checksum[:16]}...")
                print(f"       current:  {d.current_checksum[:16]}...")
        if report.orphans:
            print(f"  ORPHAN HISTORY ROWS ({len(report.orphans)}):")
            for o in report.orphans:
                print(f"    ? {o.version}  {o.name}  (file missing)")
        return 0 if report.is_clean else (2 if report.drift or report.orphans else 0)
    finally:
        client.close()


async def _cmd_up(args) -> int:
    client, db = _get_db()
    try:
        results = await fw.apply_pending(
            db, target=args.target, dry_run=args.dry_run,
        )
        if not results:
            print("Nothing to do — all migrations already applied.")
            return 0
        exit_code = 0
        for r in results:
            marker = "✓" if r.ok else "✗"
            note = "" if not args.dry_run else "  [DRY RUN]"
            print(f"  {marker} {r.version}  {r.name}  ({r.duration_ms}ms){note}")
            if not r.ok:
                print(f"       error: {r.error}")
                exit_code = 1
        return exit_code
    finally:
        client.close()


async def _cmd_down(args) -> int:
    client, db = _get_db()
    try:
        results = await fw.rollback_last(
            db, target=args.target, force=args.force,
        )
        if not results:
            print("Nothing to rollback.")
            return 0
        exit_code = 0
        for r in results:
            marker = "✓" if r.ok else "✗"
            print(f"  {marker} rollback {r.version}  ({r.duration_ms}ms)")
            if not r.ok:
                print(f"       error: {r.error}")
                exit_code = 1
        return exit_code
    finally:
        client.close()


async def _cmd_new(args) -> int:
    path = fw.scaffold_new_migration(args.slug)
    print(f"Created: {path}")
    return 0


async def _cmd_verify(_args) -> int:
    client, db = _get_db()
    try:
        drift = await fw.verify_checksums(db)
        if not drift:
            print("✓ No checksum drift — every applied migration matches its file.")
            return 0
        print(f"✗ Drift on {len(drift)} migration(s):")
        for d in drift:
            print(f"    {d.version}  {d.name}")
            print(f"       recorded: {d.recorded_checksum[:16]}...")
            print(f"       current:  {d.current_checksum[:16]}...")
        return 2
    finally:
        client.close()


async def _cmd_mark_applied(args) -> int:
    client, db = _get_db()
    try:
        await fw.mark_applied(db, args.version)
        print(f"✓ Recorded {args.version} as applied (imported existing state).")
        return 0
    finally:
        client.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m backend.migrations",
        description="AUREM database migration runner.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("status", help="Show applied/pending migrations.")
    sp.set_defaults(handler=_cmd_status)

    sp = sub.add_parser("up", help="Apply pending migrations in order.")
    sp.add_argument("--target", help="Apply up to this version (inclusive).")
    sp.add_argument("--dry-run", action="store_true",
                    help="List what would run without executing.")
    sp.set_defaults(handler=_cmd_up)

    sp = sub.add_parser("down", help="Rollback the most recent migration.")
    sp.add_argument("--target",
                    help="Rollback everything above this version.")
    sp.add_argument("--force", action="store_true",
                    help="Allow rollback of migrations marked irreversible.")
    sp.set_defaults(handler=_cmd_down)

    sp = sub.add_parser("new", help="Scaffold a new migration file.")
    sp.add_argument("slug", help="Short slug, e.g. 'add_founder_metrics'.")
    sp.set_defaults(handler=_cmd_new)

    sp = sub.add_parser("verify", help="Check applied migrations haven't drifted.")
    sp.set_defaults(handler=_cmd_verify)

    sp = sub.add_parser(
        "mark-applied",
        help="Record an already-applied migration in history without running it.",
    )
    sp.add_argument("version", help="Version string, e.g. '001'.")
    sp.set_defaults(handler=_cmd_mark_applied)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
