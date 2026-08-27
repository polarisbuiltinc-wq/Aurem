"""
services/plan_scan_contract.py — 2026-08-27, P2 (Plan↔Scan Consistency
Contract, Journey/Intent-Grounding build round).

Enforces IN CODE — not by prompt alone — that a plan generated from a
prior scan/audit artifact only touches files the artifact actually
flagged. Root cause this exists for: a planner LLM given a vague
grounded task ("fix the critical issues") re-derived 4 PLAUSIBLE files
from the bug CATEGORIES in the report instead of the 9 files/lines the
scan actually cited — a category match, not a citation match.
"""
from __future__ import annotations

from typing import Optional


def _norm(p: Optional[str]) -> str:
    return (p or "").strip().lstrip("./").rstrip("/")


def check_plan_scan_consistency(plan: dict, source_findings: list[dict]) -> dict:
    """Pure, no I/O. Returns a coverage report:
      {
        total_findings, covered_count, deferred_count,
        deferred: [{filepath, line, description}, ...],
        mismatched_files: [path, ...],   # planned files with NO citable finding
      }
    `mismatched_files` non-empty means the plan must be BLOCKED before
    it's ever shown to the user for approval — every planned file must
    cite a real finding from the artifact that produced this plan.
    """
    plan_files = {_norm(p) for p in (plan.get("files_to_change") or []) if _norm(p)}
    finding_files = {_norm(f.get("filepath")) for f in source_findings if f.get("filepath")}

    mismatched = sorted(plan_files - finding_files)
    covered = [f for f in source_findings if _norm(f.get("filepath")) in plan_files]
    deferred = [f for f in source_findings if _norm(f.get("filepath")) not in plan_files]

    return {
        "total_findings": len(source_findings),
        "covered_count": len(covered),
        "deferred_count": len(deferred),
        "deferred": [
            {"filepath": f.get("filepath"), "line": f.get("line"),
             "description": f.get("description", "")}
            for f in deferred
        ],
        "mismatched_files": mismatched,
    }


def render_mismatch_message(mismatched_files: list[str], finding_files: list[str]) -> str:
    plural = "s" if len(mismatched_files) != 1 else ""
    return (
        f"Blocked before showing you this plan — it points at "
        f"{', '.join(mismatched_files)}, but the scan that started this "
        f"never flagged {'those files' if plural else 'that file'}. "
        f"The scan's findings are only in: {', '.join(sorted(finding_files)) or '(none)'}. "
        f"Re-run the scan or name the file directly if you want it fixed."
    )
