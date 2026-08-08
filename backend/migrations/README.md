# AUREM Migration Framework

Production-grade schema migration runner for AUREM's MongoDB. Replaces
the earlier ad-hoc `python -m migrations.NNN_*` scripts with a
versioned, tracked, and reversible pipeline.

## Quick reference

```bash
# From /app/backend
python -m migrations status                    # what's applied, what's pending
python -m migrations up                         # apply all pending in order
python -m migrations up --target 003            # apply up to (including) 003
python -m migrations up --dry-run               # show what would run
python -m migrations down                       # rollback the most recent
python -m migrations down --target 001          # rollback back to state after 001
python -m migrations down --force               # allow rolling back .irreversible
python -m migrations verify                     # detect file-vs-history drift
python -m migrations new add_founder_metrics    # scaffold 003_add_founder_metrics.py
python -m migrations mark-applied 001           # record 001 as applied WITHOUT running it
                                                # (used to import pre-framework state)
```

## Writing a new migration

```python
# migrations/NNN_slug.py
from .base import Migration

class MyChangeMigration(Migration):
    version      = "003"           # MUST match filename prefix
    name         = "add_founder_metrics"
    description  = "Add founder_metrics collection + hot-path indexes."
    dev_only     = False           # True skips in prod runs
    irreversible = False           # True refuses .down() without --force

    async def up(self, db) -> None:
        # MUST be idempotent (safe to re-run with same state)
        await db.founder_metrics.create_index("user_id", unique=True)
        await db.founder_metrics.create_index([("timestamp", -1)])

    async def down(self, db) -> None:
        # MUST cleanly reverse up()
        # Or raise NotImplementedError if genuinely irreversible
        # (and set irreversible=True above).
        from pymongo.errors import OperationFailure
        for name in ["user_id_1", "timestamp_-1"]:
            try:
                await db.founder_metrics.drop_index(name)
            except OperationFailure:
                pass
```

**Rules the framework enforces:**

| Rule | Framework behaviour on violation |
|---|---|
| Filename prefix must match `class.version` | `RuntimeError` at discovery |
| Two files with same `version` | `RuntimeError` at discovery |
| `.up()` fails midway through a run | Stops on first failure; later versions never attempted |
| `.up()` on an already-applied version | Skipped (idempotent by history check) |
| Rollback of an `irreversible = True` migration | Refused unless `--force` |
| `.down()` raises `NotImplementedError` | Rollback aborts (no history mutation) |
| Applied migration's file mutated | Surfaces as `drift` in `status` / `verify` |
| History row exists for a deleted migration file | Surfaces as `orphan` in `status` |
| Migration marked `dev_only = True` | Skipped when `AUREM_ENV=prod` |

## History collection

Every apply/rollback event is written to `migration_history`:

```
{
  version:        "001",
  name:           "aurem_upgrade_indexes",
  description:    "…",
  applied_at:     ISODate,
  duration_ms:    NumberInt,
  checksum:       "sha256 hex of the migration file at apply time",
  env:            "dev" | "prod" | "test",
  status:         "applied" | "rolled_back",
  rolled_back_at: ISODate | null,
  imported:       true              // only present on mark-applied rows
}
```

Unique index on `version`.

## Environment detection

`AUREM_ENV` env var overrides. Otherwise inferred from `MONGO_URL`:
localhost / 127.0.0.1 → `dev`, anything else → `prod`.

## Adopting the framework on a live DB

If a database already had migrations applied via the old ad-hoc
scripts, run `mark-applied` for each one so the framework recognises
existing state without trying to re-run them:

```bash
python -m backend.migrations mark-applied 001
python -m backend.migrations mark-applied 002
python -m backend.migrations status         # confirm applied=2, pending=0
```

Because both existing migrations are idempotent, you *could* also just
run `up` — but the explicit `mark-applied` is cleaner and reflects
history accurately.

## Testing

Full E2E suite at `backend/tests/test_migration_framework.py` — 18
tests, hits real Mongo, drops throwaway DBs after each run. Run with:

```bash
cd /app/backend && python -m pytest tests/test_migration_framework.py -q
```
