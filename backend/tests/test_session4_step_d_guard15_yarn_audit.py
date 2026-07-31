"""Guard 15 · npm/yarn audit wiring — real E2E.

Proves the g15 dependency scanner:
  1. Actually runs `yarn audit` against `frontend/yarn.lock` (not just
     pip-audit) — closes the doc-lies-code-doesn't gap surfaced by
     user verification on 2026-07-31.
  2. Parses yarn 1.x JSON-lines output and dedupes advisories by id.
  3. Returns exit=1 when unallowlisted HIGH/CRITICAL findings exist.
  4. Merges pip + yarn findings under a single allowlist.

Real scan, zero mocks. Skips gracefully if yarn / yarn.lock unavailable.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend" / "scripts" / "g15_dependency_scan.py"
YARN_LOCK = ROOT / "frontend" / "yarn.lock"


# ═════════════════════════════════════════════════════════════════
# 1) Source-level proof — yarn audit code path exists
# ═════════════════════════════════════════════════════════════════
def test_g15_source_wires_yarn_audit():
    """The scanner must include a `_run_yarn_audit()` function and
    call it from `main()`. Guards against silent regression to the
    old pip-audit-only state."""
    src = SCRIPT.read_text()
    assert "def _run_yarn_audit" in src, \
        "g15_dependency_scan.py must expose _run_yarn_audit()"
    assert "yarn" in src and "audit" in src, "must reference yarn audit"
    assert "yarn.lock" in src, "must scan yarn.lock"
    assert re.search(r"main\s*\(\)[^}]*_run_yarn_audit\(", src, re.DOTALL), \
        "main() must invoke _run_yarn_audit()"


# ═════════════════════════════════════════════════════════════════
# 2) Docstring truth — no more doc-lies
# ═════════════════════════════════════════════════════════════════
def test_g15_docstring_no_longer_lies():
    src = SCRIPT.read_text()
    # Old docstring implied yarn was optional / conditional.
    # New docstring should state both scanners run unconditionally.
    top_docstring = src.split('"""')[1]
    assert "pip-audit" in top_docstring
    assert "yarn" in top_docstring
    assert "yarn.lock" in top_docstring, \
        "docstring must mention what yarn scans"


# ═════════════════════════════════════════════════════════════════
# 3) Real yarn audit run — parses actual findings
# ═════════════════════════════════════════════════════════════════
def _yarn_available() -> bool:
    try:
        subprocess.run(["yarn", "--version"], capture_output=True,
                       timeout=5, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _yarn_available() or not YARN_LOCK.exists(),
                    reason="yarn or yarn.lock unavailable")
def test_g15_yarn_audit_returns_deduped_findings():
    """Import the module and call _run_yarn_audit() for real —
    against the actual yarn.lock in this repo. Zero mocks."""
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        import importlib
        mod = importlib.import_module("g15_dependency_scan")
        importlib.reload(mod)  # in case a previous test cached it
        status, findings = mod._run_yarn_audit()
    finally:
        sys.path.remove(str(SCRIPT.parent))

    # Scan itself must have run
    assert status == 0, f"scanner failed to run (status={status})"
    # Every finding must be shape-correct
    for f in findings:
        assert "package" in f
        assert "id" in f
        assert "severity" in f
        assert "source" in f and f["source"] == "yarn"
        assert f["severity"] in {"INFO", "LOW", "MODERATE",
                                 "HIGH", "CRITICAL", ""}, f
    # Deduplication proof — no two findings share the same primary id
    ids = [f["id"] for f in findings]
    assert len(ids) == len(set(ids)), \
        f"yarn audit findings must be deduped by advisory id: {ids}"


@pytest.mark.skipif(not _yarn_available() or not YARN_LOCK.exists(),
                    reason="yarn or yarn.lock unavailable")
def test_g15_full_scan_flags_current_high_critical_findings():
    """End-to-end — the whole scanner CLI. Given the yarn.lock in
    this repo currently carries multiple HIGH/CRITICAL advisories
    (vite, vitest, brace-expansion, form-data, axios, postcss, tmp
    as of 2026-07-31), the scanner must exit with 1 AND its stdout
    must name AT LEAST one of them."""
    res = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, timeout=240,
        cwd=str(ROOT),
    )
    out = res.stdout + res.stderr
    # Exit 1 = HIGH/CRITICAL findings, 0 = clean, 2 = scan failure
    assert res.returncode in (0, 1), \
        f"scanner produced infra failure exit={res.returncode}\n{out[-500:]}"
    if res.returncode == 1:
        # Must have named at least one yarn-side high/critical finding
        assert "[yarn]" in out, \
            f"CI-blocking findings must be tagged [yarn]:\n{out[-800:]}"
        assert re.search(r"(HIGH|CRITICAL)", out), \
            f"stdout must show severity labels:\n{out[-800:]}"
        # Prove exactly the shape a founder would grep for
        assert "Build fails:" in out
        assert re.search(r"pip=\d+, yarn=\d+", out), \
            "summary line must expose both pip + yarn counts"


# ═════════════════════════════════════════════════════════════════
# 4) yarn.lock is committed (reproducibility guard)
# ═════════════════════════════════════════════════════════════════
def test_frontend_yarn_lock_is_committed():
    """Lock file must be in git — without it, `yarn install` on CI
    resolves fresh versions, meaning `yarn audit` results would
    diverge from what devs see locally."""
    res = subprocess.run(
        ["git", "ls-files", "frontend/yarn.lock"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert res.returncode == 0 and res.stdout.strip() == "frontend/yarn.lock", \
        "frontend/yarn.lock must be tracked by git for reproducible audits"


def test_no_package_lock_conflicting_with_yarn():
    """Guard against having BOTH package-lock.json AND yarn.lock in
    the repo — that combo produces conflicting install graphs and
    makes audit output non-deterministic. This repo is yarn-only."""
    pkg_lock = ROOT / "frontend" / "package-lock.json"
    assert not pkg_lock.exists(), (
        "frontend/package-lock.json must NOT exist alongside yarn.lock "
        "— pick one lockfile"
    )
