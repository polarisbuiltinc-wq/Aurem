"""
services/project_onboarding_scan.py — automatic background deep scan
on project connect.

2026-08-22 — closes a gap in the Prompt Starter panel's personalized
"FROM YOUR REPO" suggestions (routers/findings.py::starter_suggestions):
the only paths that ever wrote to `cto_open_findings` were a Loop Ship
run (services/loop_full_scan.py) or the founder-only manual
POST /codebase-health/scan endpoint. A user landing on the empty chat
right after connecting a project — exactly when the "what do I even
type?" onboarding problem happens — had nothing personalized to show
yet.

Runs the SAME 7-category scanner pipeline codebase_health.py exposes
manually (security, performance, code_quality, dependencies, database,
bug_hunt, docker) as a fire-and-forget background task, gated per
project via (user_id, project_id) exactly like every other
cto_open_findings writer. No tier/founder restriction here — this is
an internal system task triggered once on connect, not the founder-
only manual /scan endpoint (which stays admin-gated, unchanged).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ONBOARDING_SCAN_SOURCE = "onboarding_auto_scan"


async def run_onboarding_scan(
    *, db, user_id: str, project_id: str,
    github_token: str, github_owner: str, github_repo: str,
) -> None:
    """Fire-and-forget: scan a freshly-connected repo and persist
    critical/high findings to `cto_open_findings`. Errors are logged
    and swallowed — must never affect the project-add response or
    crash the caller's background task group (same contract as
    `routers/cto_projects.py::_run_project_indexing`)."""
    try:
        if not (github_token and github_owner and github_repo):
            return
        from routers.codebase_health import _build_text_cache, SCANNERS
        text_cache = await _build_text_cache(github_owner, github_repo, github_token)
        if not text_cache:
            return

        all_findings: list[dict] = []
        for scan_fn in SCANNERS.values():
            try:
                all_findings.extend(scan_fn(text_cache))
            except Exception as e:
                logger.debug("[onboarding-scan] category failed: %r", e)

        from services.loop_full_scan import persist_findings_to_backlog
        written = await persist_findings_to_backlog(
            db, user_id=user_id, project_id=project_id,
            findings=all_findings, scan_source=ONBOARDING_SCAN_SOURCE,
        )
        logger.info(
            "[onboarding-scan] project=%s scanned %d files, wrote %d "
            "critical/high findings",
            project_id, len(text_cache), written,
        )
    except Exception as e:
        logger.warning(
            "[onboarding-scan] failed for project=%s: %r", project_id, e,
        )
