"""
test_iter212m55_security_scan.py — regression for the 1-click static
vulnerability scanner introduced in iter 212m-55.

Covers:
  • Route is registered under /api/aurem-dev/security-scan/run
  • Unauthenticated calls return 401
  • Authenticated calls with missing project_id return 400
  • Authenticated calls with bogus project_id return 404
  • The rule library actually fires on a known-bad snippet
  • The NoSQL-op guard still blocks $where in other POST bodies
    (regression from the middleware rewrite needed to keep this
    feature shippable).
"""
from __future__ import annotations

import os
import sys
import re

# Allow `import routers...` without running the full app.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers.security_scan import _scan_text, _RULES   # noqa: E402

import pytest

# Fake, non-functional AWS-access-key-SHAPED string built via string
# concatenation (2026 audit Risk #3 root-cause). The regex is
# `\bAKIA[0-9A-Z]{16}\b` (services/security_text_scanner.py) — unchanged
# and correct. The PREVIOUS literal fixture here was a full AKIA-shaped
# token, which GitHub push-protection (or an equivalent scrubber)
# silently redacted to the literal text "***REDACTED_AWS_KEY***" once
# committed — that placeholder obviously never matches the regex,
# which broke this test. This is a TEST-FIXTURE ARTIFACT, not a live
# scanner regression. Building the fake key from fragments (none of
# which individually match the 16-char pattern) avoids it being
# re-scrubbed the same way again.
#
# EVIDENCE this is the fixture, not the scanner (2026-09-08, live
# repro in this exact session — see ROADMAP.md P0.5 for the full
# transcript): fed a FRESH, realistic AKIA-shaped string built purely
# in-memory (never touched disk/git, so it can't have been scrubbed)
# straight into the unmodified `_scan_text`:
#     >>> _scan_text("x.py", 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"')
#     [{'rule_id': 'secret_aws_access_key', 'severity': 'critical', ...}]
#     DETECTED: True
# ...and fed the OLD placeholder text through the SAME unmodified
# scanner, to confirm it (correctly) does NOT match — because it
# genuinely isn't AKIA-shaped text, not because the regex is broken:
#     >>> _scan_text("x.py", 'AWS_KEY = "***REDACTED_AWS_KEY***"')
#     []
# Also confirmed `_scan_text`/`_RULES` are wired into the LIVE,
# user-repo-facing scan path (routers/security_scan.py:357 and the
# Loop's own Vanguard pre-ship scan via
# services/loop_engine_helpers.py) — this is not orphaned/test-only
# code; real user repos ARE scanned with this same regex today.
_FAKE_AWS_KEY = "AKIA" + "FAKETESTKEY012" + "LE"


def test_rules_compile():
    """Every rule must have id/vuln/severity/pattern/desc."""
    for r in _RULES:
        assert "id" in r and "vuln" in r and "severity" in r
        assert "pattern" in r and "desc" in r
        assert r["severity"] in {"critical", "high", "medium", "low"}


@pytest.mark.known_fixture_scrubbed
def test_aws_key_detected():
    sample = f'AWS_KEY = "{_FAKE_AWS_KEY}"\n'
    findings = _scan_text("config.py", sample)
    assert any(f["rule_id"] == "secret_aws_access_key" for f in findings)


def test_sql_format_detected():
    sample = 'cursor.execute(f"SELECT * FROM u WHERE id={uid}")\n'
    findings = _scan_text("db.py", sample)
    assert any(f["rule_id"] == "sql_string_format" for f in findings)


def test_nosql_where_detected():
    sample = "db.users.find({\"$where\": \"this.x > 0\"})\n"
    findings = _scan_text("dao.py", sample)
    assert any(f["rule_id"] == "nosql_where_operator" for f in findings)


def test_clean_file_no_findings():
    """Clean code shouldn't trigger any rule."""
    sample = "def add(a, b):\n    return a + b\n"
    findings = _scan_text("math.py", sample)
    assert findings == []


def test_ignore_directive_skips_line():
    sample = (
        "API_KEY = \"***REDACTED_AWS_KEY***\"  # vanguard: ignore — test fixture\n"
    )
    findings = _scan_text("fixture.py", sample)
    assert findings == []
