"""
services/scanner_utils.py — Iter 212m-225

Small helpers shared by both the codebase-health scanner router and
the bug_hunt scanner service.  Kept in `services/` so callers on
either side of the router/service boundary can import from a single
canonical location.

`is_scanner_rule_file(path) -> bool`
    True when `path` is one of AUREM's OWN scanner-rule-definition
    source files (bug_hunt_rules.py, vanguard_scanner.py, etc.).
    Callers use it to skip self-referential false-positives — the
    rule regexes for eval_usage / private_key / etc. literally
    appear inside their own definition files.
"""
from __future__ import annotations

# The tuple below is imported by BOTH the security scanner in
# routers/codebase_health.py AND the bug_hunt scanner in
# services/bug_hunt_rules.py.
#
# Iter 348b — EXPANDED after the PR #9 incident (564 lines gutted from
# routers/codebase_health.py, the live Health-Scan API router): the
# exclusion now covers the ENTIRE scanning/fixing pipeline, not just
# literal rule-definition files. Rationale: any file that implements
# scan or fix logic necessarily contains dangerous-looking patterns
# (regexes, eval/exec strings, secret formats, patch-application code)
# and must NEVER be scan input or an LLM-fix target — otherwise the
# "gut the scanner to silence the finding" self-cannibalism loop
# reappears in a new file.
_SCANNER_RULE_FILES: tuple[str, ...] = (
    # ── rule-definition files (original list) ──
    "services/bug_hunt_rules.py",
    "services/vanguard_scanner.py",
    "services/vanguard_verify_agent.py",
    "services/generation_rules.py",
    "services/mode_e_auditor.py",
    "services/full_scan_scanners.py",
    "routers/codebase_health.py",
    "routers/security_scan.py",
    # ── scan/fix pipeline routers ──
    "routers/fix_pipeline.py",
    "routers/admin_vanguard.py",
    "routers/vanguard_ci.py",
    # ── scan/fix pipeline services ──
    "services/full_scan_orchestrator.py",
    "services/architecture_health.py",
    "services/boilerplate_audit.py",
    "services/integration_health.py",
    "services/integration_health_cron.py",
    "services/finding_fix_applier.py",
    "services/fix_job_manager.py",
    "services/fix_triage.py",
    "services/fixed_findings.py",
    "services/ora_fix_learning.py",
    "services/post_task_scanner.py",
    "services/repo_heal.py",
    "services/scan_cache.py",
    "services/scan_fix_quota.py",
    "services/scanner_utils.py",
    "services/vanguard_audit.py",
    "services/vanguard_config.py",
    "services/loop_full_scan.py",
    "services/loop_safety.py",
    "services/scaffold_security_gate.py",
    # ── frontend UI that renders rule/finding strings ──
    "frontend/src/pages/BugHunt.jsx",
    "frontend/src/pages/CodebaseHealth.jsx",
    "frontend/src/pages/AdminVanguard.jsx",
    "frontend/src/pages/AdminSystemHealth.jsx",
)


def is_scanner_rule_file(path: str) -> bool:
    """True if `path` is one of the AUREM scanner rule-definition
    source files. Callers use this to skip self-referential false
    positives that would otherwise flood the report."""
    if not path:
        return False
    lower = path.replace("\\", "/").lower()
    return any(lower.endswith(suf.lower()) for suf in _SCANNER_RULE_FILES)


__all__ = ["is_scanner_rule_file", "_SCANNER_RULE_FILES"]
