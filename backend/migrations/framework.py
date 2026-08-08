"""
migrations/framework.py
=======================
Core migration runner for AUREM.

Public API (used by cli.py and by tests):

    await status(db)               → StatusReport
    await apply_pending(db, ...)   → list[AppliedResult]
    await rollback_last(db, ...)   → RollbackResult
    await mark_applied(db, ver)    → None            (import existing state)
    await verify_checksums(db)     → list[Drift]
    discover_migrations()          → list[Migration]

The framework stores every apply/rollback event in the
`migration_history` collection so we always know exactly which
migrations have run on the current DB.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import inspect
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .base import Migration

logger = logging.getLogger("aurem.migrations")

MIGRATIONS_DIR = Path(__file__).resolve().parent
HISTORY_COLLECTION = "migration_history"
VERSION_RE = re.compile(r"^(\d{3})_[a-zA-Z0-9_]+\.py$")


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class MigrationInfo:
    version: str
    name: str
    description: str
    dev_only: bool
    irreversible: bool
    file_path: Path
    checksum: str
    migration: Migration = field(repr=False)


@dataclass
class HistoryRow:
    version: str
    name: str
    applied_at: datetime
    duration_ms: int
    checksum: str
    env: str
    status: str  # "applied" | "rolled_back"
    rolled_back_at: datetime | None = None


@dataclass
class StatusReport:
    applied: list[HistoryRow]
    pending: list[MigrationInfo]
    drift: list["Drift"]         # applied migrations whose file checksum changed
    orphans: list[HistoryRow]    # history rows whose file no longer exists

    @property
    def is_clean(self) -> bool:
        return not self.pending and not self.drift and not self.orphans


@dataclass
class AppliedResult:
    version: str
    name: str
    duration_ms: int
    ok: bool
    error: str | None = None


@dataclass
class RollbackResult:
    version: str | None
    ok: bool
    duration_ms: int
    error: str | None = None


@dataclass
class Drift:
    version: str
    name: str
    recorded_checksum: str
    current_checksum: str


# ── Discovery ────────────────────────────────────────────────────────

def _file_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _current_env() -> str:
    """Detect current environment. Prefer explicit AUREM_ENV, else
    derive from MONGO_URL host (localhost → dev, else → prod)."""
    v = (os.getenv("AUREM_ENV") or "").strip().lower()
    if v in ("dev", "prod", "test"):
        return v
    mongo = (os.getenv("MONGO_URL") or "").lower()
    if "localhost" in mongo or "127.0.0.1" in mongo:
        return "dev"
    return "prod"


def _load_migration_class(file_path: Path) -> type[Migration] | None:
    """Dynamically import a migration file and return the Migration
    subclass defined inside. Returns None if none found.

    We import the file as a submodule of THIS package so that
    `from .base import Migration` (relative import) inside the
    migration file resolves correctly. `__package__` is set at import
    time to whichever name the framework was imported under
    (``backend.migrations`` when run as CLI, or ``migrations`` in
    tests that put ``backend/`` on sys.path).
    """
    parent_pkg = __package__ or "migrations"
    module_name = f"{parent_pkg}._loaded_{file_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec so relative imports resolve.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        logger.warning("failed to import migration %s: %r", file_path, e)
        sys.modules.pop(module_name, None)
        return None
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if (
            issubclass(obj, Migration)
            and obj is not Migration
            and obj.__module__ == module_name
        ):
            return obj
    return None


def discover_migrations(directory: Path | None = None) -> list[MigrationInfo]:
    """Scan `directory` for NNN_slug.py migration files and return them
    sorted by version ascending."""
    d = directory or MIGRATIONS_DIR
    out: list[MigrationInfo] = []
    for f in sorted(d.iterdir()):
        m = VERSION_RE.match(f.name)
        if not m:
            continue
        version = m.group(1)
        cls = _load_migration_class(f)
        if cls is None:
            logger.warning(
                "migration file %s has no Migration subclass, skipping",
                f.name,
            )
            continue
        if getattr(cls, "version", None) != version:
            raise RuntimeError(
                f"migration file {f.name} declares version="
                f"{getattr(cls, 'version', None)!r} but filename prefix "
                f"is {version!r} — they must match"
            )
        inst = cls()
        out.append(MigrationInfo(
            version=version,
            name=cls.name,
            description=getattr(cls, "description", ""),
            dev_only=bool(getattr(cls, "dev_only", False)),
            irreversible=bool(getattr(cls, "irreversible", False)),
            file_path=f,
            checksum=_file_checksum(f),
            migration=inst,
        ))
    # Verify strictly increasing, no gaps or duplicates.
    seen: set[str] = set()
    for info in out:
        if info.version in seen:
            raise RuntimeError(
                f"duplicate migration version {info.version} — refuse to run"
            )
        seen.add(info.version)
    return out


# ── History collection helpers ───────────────────────────────────────

async def _ensure_history_indexes(db) -> None:
    await db[HISTORY_COLLECTION].create_index("version", unique=True)


async def _load_history(db) -> dict[str, HistoryRow]:
    await _ensure_history_indexes(db)
    rows: dict[str, HistoryRow] = {}
    cursor = db[HISTORY_COLLECTION].find({}, {"_id": 0})
    async for doc in cursor:
        rows[doc["version"]] = HistoryRow(
            version=doc["version"],
            name=doc.get("name", ""),
            applied_at=doc.get("applied_at"),
            duration_ms=int(doc.get("duration_ms", 0)),
            checksum=doc.get("checksum", ""),
            env=doc.get("env", ""),
            status=doc.get("status", "applied"),
            rolled_back_at=doc.get("rolled_back_at"),
        )
    return rows


# ── Public API ───────────────────────────────────────────────────────

async def status(db, migrations: list[MigrationInfo] | None = None) -> StatusReport:
    """Compute what's applied, pending, drifted, or orphaned."""
    m_list = migrations if migrations is not None else discover_migrations()
    history = await _load_history(db)
    by_version = {m.version: m for m in m_list}

    applied: list[HistoryRow] = []
    drift: list[Drift] = []
    orphans: list[HistoryRow] = []

    for ver, row in sorted(history.items()):
        if row.status != "applied":
            continue
        if ver not in by_version:
            orphans.append(row)
            continue
        applied.append(row)
        current_sum = by_version[ver].checksum
        if row.checksum and current_sum and row.checksum != current_sum:
            drift.append(Drift(
                version=ver,
                name=row.name or by_version[ver].name,
                recorded_checksum=row.checksum,
                current_checksum=current_sum,
            ))

    applied_versions = {r.version for r in applied}
    pending = [m for m in m_list if m.version not in applied_versions]
    return StatusReport(
        applied=applied, pending=pending, drift=drift, orphans=orphans,
    )


def _next_pending_expected(applied_versions: set[str],
                            all_migrations: list[MigrationInfo]) -> list[MigrationInfo]:
    """Return pending migrations in strict order. Sequence rule: cannot
    apply version N+1 if version N is not applied."""
    out: list[MigrationInfo] = []
    for m in all_migrations:
        if m.version in applied_versions:
            continue
        out.append(m)
    return out


async def apply_pending(
    db,
    *,
    target: str | None = None,
    env: str | None = None,
    dry_run: bool = False,
) -> list[AppliedResult]:
    """Apply all pending migrations in order.

    * `target` — apply up to and including this version.
    * `env`    — override current env for dev_only gating (mostly tests).
    * `dry_run` — do everything except call .up() and write history."""
    all_migrations = discover_migrations()
    history = await _load_history(db)
    applied_versions = {v for v, r in history.items() if r.status == "applied"}
    pending = _next_pending_expected(applied_versions, all_migrations)
    if target:
        pending = [m for m in pending if m.version <= target]

    resolved_env = (env or _current_env()).lower()
    results: list[AppliedResult] = []
    for m in pending:
        if m.dev_only and resolved_env == "prod":
            logger.info(
                "skipping dev_only migration %s in prod env",
                m.version,
            )
            continue
        started = time.perf_counter()
        try:
            if not dry_run:
                await m.migration.up(db)
                duration_ms = int((time.perf_counter() - started) * 1000)
                await db[HISTORY_COLLECTION].update_one(
                    {"version": m.version},
                    {"$set": {
                        "version":     m.version,
                        "name":        m.name,
                        "description": m.description,
                        "applied_at":  datetime.now(timezone.utc),
                        "duration_ms": duration_ms,
                        "checksum":    m.checksum,
                        "env":         resolved_env,
                        "status":      "applied",
                        "rolled_back_at": None,
                    }},
                    upsert=True,
                )
            else:
                duration_ms = int((time.perf_counter() - started) * 1000)
            results.append(AppliedResult(
                version=m.version, name=m.name,
                duration_ms=duration_ms, ok=True,
            ))
        except Exception as e:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.exception("migration %s failed", m.version)
            results.append(AppliedResult(
                version=m.version, name=m.name,
                duration_ms=duration_ms, ok=False, error=repr(e),
            ))
            # Stop on first failure so we never skip over a broken step.
            break
    return results


async def rollback_last(
    db,
    *,
    target: str | None = None,
    env: str | None = None,
    force: bool = False,
) -> list[RollbackResult]:
    """Rollback the most recently applied migration.

    If `target` given, rollback ALL migrations with version > target
    (in reverse order). This lets you jump from "005" back to "003"
    by passing target="003".

    `force=True` allows rolling back irreversible migrations (which
    must still implement .down() — the flag is a safety catch, not a
    magic wand)."""
    all_migrations = {m.version: m for m in discover_migrations()}
    history = await _load_history(db)
    applied = sorted(
        [r for r in history.values() if r.status == "applied"],
        key=lambda r: r.version,
        reverse=True,
    )
    if not applied:
        return []

    if target is None:
        to_roll = [applied[0]]
    else:
        to_roll = [r for r in applied if r.version > target]

    resolved_env = (env or _current_env()).lower()
    results: list[RollbackResult] = []
    for row in to_roll:
        info = all_migrations.get(row.version)
        if info is None:
            results.append(RollbackResult(
                version=row.version, ok=False, duration_ms=0,
                error="migration file not found — orphan history row",
            ))
            break
        if info.irreversible and not force:
            results.append(RollbackResult(
                version=row.version, ok=False, duration_ms=0,
                error=(f"migration {row.version} is marked irreversible;"
                       " pass force=True to rollback anyway"),
            ))
            break
        started = time.perf_counter()
        try:
            await info.migration.down(db)
            duration_ms = int((time.perf_counter() - started) * 1000)
            await db[HISTORY_COLLECTION].update_one(
                {"version": row.version},
                {"$set": {
                    "status":         "rolled_back",
                    "rolled_back_at": datetime.now(timezone.utc),
                    "env":            resolved_env,
                }},
            )
            results.append(RollbackResult(
                version=row.version, ok=True, duration_ms=duration_ms,
            ))
        except NotImplementedError:
            results.append(RollbackResult(
                version=row.version, ok=False,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=(f"migration {row.version} .down() raised "
                       "NotImplementedError — no rollback path"),
            ))
            break
        except Exception as e:
            logger.exception("rollback of %s failed", row.version)
            results.append(RollbackResult(
                version=row.version, ok=False,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=repr(e),
            ))
            break
    return results


async def mark_applied(
    db,
    version: str,
    *,
    env: str | None = None,
) -> None:
    """Record a migration as already-applied WITHOUT running its .up().
    Used to import existing state when adopting the framework in a DB
    that has ad-hoc migrations already run."""
    all_migrations = {m.version: m for m in discover_migrations()}
    info = all_migrations.get(version)
    if info is None:
        raise ValueError(f"no migration file for version {version!r}")
    resolved_env = (env or _current_env()).lower()
    await _ensure_history_indexes(db)
    await db[HISTORY_COLLECTION].update_one(
        {"version": version},
        {"$set": {
            "version":     version,
            "name":        info.name,
            "description": info.description,
            "applied_at":  datetime.now(timezone.utc),
            "duration_ms": 0,
            "checksum":    info.checksum,
            "env":         resolved_env,
            "status":      "applied",
            "rolled_back_at": None,
            "imported":    True,
        }},
        upsert=True,
    )


async def verify_checksums(db) -> list[Drift]:
    """Return the list of applied migrations whose file has been
    modified since it was applied."""
    report = await status(db)
    return report.drift


def scaffold_new_migration(slug: str, directory: Path | None = None) -> Path:
    """Create a new migration file with the next available version
    number. Returns the created file path."""
    d = directory or MIGRATIONS_DIR
    existing = discover_migrations(d)
    if existing:
        last_ver = int(existing[-1].version)
    else:
        last_ver = 0
    new_ver = f"{last_ver + 1:03d}"
    slug_clean = re.sub(r"[^a-z0-9_]", "_", slug.lower()).strip("_")
    if not slug_clean:
        raise ValueError("slug produced empty filename")
    filename = f"{new_ver}_{slug_clean}.py"
    file_path = d / filename
    if file_path.exists():
        raise FileExistsError(f"{filename} already exists")
    template = f'''"""
migrations/{filename}
==={"=" * len(filename)}
{slug_clean}
"""
from __future__ import annotations

from ..base import Migration


class {slug_clean.title().replace("_", "")}Migration(Migration):
    version = "{new_ver}"
    name = "{slug_clean}"
    description = "TODO: describe what this migration does"
    dev_only = False
    irreversible = False

    async def up(self, db) -> None:
        # TODO: implement forward migration (idempotent).
        raise NotImplementedError

    async def down(self, db) -> None:
        # TODO: implement rollback. If genuinely irreversible, set
        # `irreversible = True` above and raise NotImplementedError here.
        raise NotImplementedError
'''
    file_path.write_text(template)
    return file_path
