"""
services/synthetic_filter.py — Organic-user exclusion (Feb 2026)

Single source of truth for "is this dev_users row a real signup or
a test/synthetic account?". Consumed by:
  · /admin/pulse (raw + organic counts)
  · G2 marketing-truth guard
  · any future dashboard metric that must not double-count synthetic
    load-testing rows

Discovered patterns (verified against real preview data on
2026-08-02 during a founder-triggered denominator audit):
  · `^test-`, `^test_`, `@test\\.`, `@aurem\\.dev$`  (baseline)
  · `^e2e-`, `^e2e_`, `^oauth-pytest-`, `^test_reg_`
  · `^qa-`, `^audit-`, `^synthetic-`, `^bot-`, `^demo-`
  · `@aurem\\.test$`, `@x\\.io$`, `@qa\\.`, `@example\\.`,
    `@demo\\.`, `@aurem-audit\\.`, `@aurem-qa\\.`, `@aurem-test\\.`
"""
from __future__ import annotations

import re

_SYNTHETIC_PATTERNS = [
    # Local-part patterns
    r"^test-", r"^test_", r"^e2e-", r"^e2e_",
    r"^qa-", r"^qa_", r"^audit-", r"^audit_",
    r"^oauth-pytest-", r"^test_reg_", r"^signup-",
    r"^synthetic-", r"^demo-", r"^bot-",
    # Domain patterns
    r"@test\.", r"@example\.", r"@qa\.",
    r"@aurem\.dev$", r"@aurem\.test$", r"@x\.io$",
    r"@aurem-audit\.", r"@aurem-qa\.", r"@aurem-test\.",
    r"@demo\.",
]
_SYNTHETIC_REGEX = re.compile("|".join(_SYNTHETIC_PATTERNS), re.IGNORECASE)


def is_synthetic(email: str | None) -> bool:
    """True if this email matches any known test/synthetic pattern."""
    if not email:
        return False
    return bool(_SYNTHETIC_REGEX.search(email))


def synthetic_mongo_filter() -> dict:
    """Return a Mongo `$expr` filter that EXCLUDES synthetic rows.
    Use as `db.dev_users.count_documents(synthetic_mongo_filter())`
    to get the organic-user count in one query (no full scan needed
    beyond what Mongo's regex already does)."""
    return {
        "email": {
            "$exists": True,
            "$nin": [None, ""],
            "$not": {"$regex": "|".join(_SYNTHETIC_PATTERNS),
                     "$options": "i"},
        }
    }


__all__ = ["is_synthetic", "synthetic_mongo_filter"]
