"""Iter 348 — self-scan exclusion regression locks (PR #173 incident:
Vanguard scanned its OWN rule-definition file bug_hunt_rules.py,
flagged its example regexes as exec/eval findings, and the /fix LLM
"resolved" them by gutting 397 lines — validation passed because
deleting the rules makes the rules stop firing).

Locks:
  1. The Vanguard scan candidate filter skips scanner-rule files and
     .vanguard/ output (source-level lock on the wired exclusion).
  2. /fix (apply_security_fix) hard-rejects findings targeting
     scanner-rule files with 400.
"""
import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from services.scanner_utils import is_scanner_rule_file

SRC = (Path(__file__).resolve().parents[1] / "routers" /
       "security_scan.py").read_text()


def test_rule_files_recognised():
    assert is_scanner_rule_file("backend/services/bug_hunt_rules.py")
    assert is_scanner_rule_file("services/vanguard_scanner.py")
    assert not is_scanner_rule_file("backend/routers/chat.py")


def test_expanded_pipeline_exclusion_covers_pr9_and_pr173_classes():
    # PR #173: rule-definition file. PR #9: the live Health-Scan API
    # router itself (564 lines gutted). BOTH classes + the whole
    # scan/fix pipeline must be excluded.
    for f in (
        "backend/services/bug_hunt_rules.py",       # PR #173
        "backend/routers/codebase_health.py",       # PR #9
        "backend/routers/security_scan.py",
        "backend/routers/fix_pipeline.py",
        "backend/services/finding_fix_applier.py",
        "backend/services/fix_triage.py",
        "backend/services/full_scan_orchestrator.py",
        "backend/services/repo_heal.py",
        "backend/services/loop_safety.py",
        "backend/services/scanner_utils.py",
        "frontend/src/pages/CodebaseHealth.jsx",
    ):
        assert is_scanner_rule_file(f), f"{f} missing from pipeline exclusion"


def test_scan_candidate_filter_wires_self_scan_exclusion():
    # The exclusion must live INSIDE the candidate loop of the scan
    # route (between the _SKIP_DIRS check and the extension check).
    start = SRC.index("# 2. Filter to scannable files.")
    end = SRC.index("# 3. Fetch + scan each file", start)
    block = SRC[start:end]
    assert "is_scanner_rule_file(path)" in block, (
        "PR #173 regression: Vanguard scan no longer skips its own "
        "rule-definition files"
    )
    assert '.vanguard/' in block, (
        "Vanguard marker/report output must never be scan input"
    )


def test_fix_endpoint_rejects_scanner_rule_file(monkeypatch):
    import routers.security_scan as ss

    async def _fake_dev(_auth):
        return {"user_id": "u1", "is_admin": True, "tier": "founder"}
    monkeypatch.setattr(ss, "current_dev", _fake_dev)

    async def _call():
        return await ss.apply_security_fix(
            {"project_id": "p1",
             "finding": {"file": "backend/services/bug_hunt_rules.py",
                          "id": "exec_usage"}},
            authorization="Bearer fake",
        )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_call())
    assert exc.value.status_code == 400
    assert "rule-definition" in str(exc.value.detail)
