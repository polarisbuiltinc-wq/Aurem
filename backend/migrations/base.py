"""
migrations/base.py
==================
Base class for all AUREM database migrations.

Every migration file is a NNN_<slug>.py that defines exactly one
subclass of `Migration`. The framework discovers it, executes .up() to
apply, and .down() to rollback.

Contract:
  * .up(db)   MUST be idempotent. Running it twice with the same state
              must be a no-op (or a documented explicit re-apply).
  * .down(db) MUST cleanly reverse .up(). If a migration is
              genuinely one-way (e.g. destructive data cleanup), raise
              NotImplementedError inside .down() so the framework
              refuses to rollback that step.
  * .version  MUST match the "NNN" prefix of the filename.
  * .name     Short human-readable slug. Matches the filename's slug.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar


class Migration(ABC):
    """Base class every migration MUST subclass."""

    # e.g. "001", "042". Filename prefix must match.
    version: ClassVar[str]

    # Short human slug. Matches filename suffix. e.g. "aurem_upgrade_indexes".
    name: ClassVar[str]

    # One-line description that appears in `migrations status` output.
    description: ClassVar[str] = ""

    # When True, this migration is skipped in production runs. Used for
    # dev-only fixtures, sample seed data, etc.
    dev_only: ClassVar[bool] = False

    # When True, this migration is destructive / cannot be automatically
    # rolled back. The framework will refuse to run .down() unless the
    # operator passes --force to the CLI.
    irreversible: ClassVar[bool] = False

    @abstractmethod
    async def up(self, db) -> None:
        """Apply the migration. MUST be idempotent."""
        raise NotImplementedError

    @abstractmethod
    async def down(self, db) -> None:
        """Rollback the migration. Raise NotImplementedError for
        genuinely irreversible migrations (and set
        `irreversible = True`)."""
        raise NotImplementedError
