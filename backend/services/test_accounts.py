"""Iter 356 — shared test/synthetic account detection.

Single source of truth for "is this a real customer or our own test
debris?". Extracted from the admin activation-funnel (Iter 196) so the
public marketing stats (Guard 2) and any future user-facing count use
the EXACT same exclusion rules.
"""
import re

TEST_PATTERNS = (
    "@aurem.test",  # anything on the synthetic domain
    "@aurem.dev_",  # PREVIEW/AUDIT suffixed rows
)
TEST_PREFIXES = (
    "test@", "test_", "qa-", "qa_",
    "audit_", "e2e-", "e2e_", "auto_",
    "oauth-", "oauth_", "mcp-", "mcp_",
)
_U_HEX_RE = re.compile(r"^u_[a-f0-9]{6,16}@", re.I)

# Chat sessions created by our own automated E2E runs (prod smoke tests,
# QA bots). These must NEVER appear in user-facing session lists.
E2E_SESSION_PREFIX_RE = re.compile(r"^(prod-e2e-|qa-e2e-|e2e-test-)")


def is_test_email(email: str | None) -> bool:
    e = (email or "").lower()
    if not e:
        return True  # blank email = synthetic
    if any(p in e for p in TEST_PATTERNS):
        return True
    if any(e.startswith(p) for p in TEST_PREFIXES):
        return True
    if _U_HEX_RE.match(e):
        return True
    return False
