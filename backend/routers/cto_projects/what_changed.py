"""
routers/cto_projects/what_changed.py — AUREM CTO Projects.
S2 "What changed" deterministic (0-LLM) diff summary.

Split from the former monolithic routers/cto_projects.py on
2026-09-08 (responsibility-based extraction, no logic change).
Uses `_pkg.<name>` for anything patched at the package level by the
existing test suite — see preview.py's module docstring for why.
"""
import logging

from fastapi import Header, HTTPException

import routers.cto_projects as _pkg
from . import router

logger = logging.getLogger(__name__)


@router.get("/projects/{project_id}/what-changed")
async def get_what_changed(
    project_id: str,
    authorization: str = Header(None),
) -> dict:
    """S2 — deterministic (0-LLM) "What changed" default view.
    Reuses the SAME latest-task lookup as /preview/pending-change and
    the SAME GitHub commit-diff shape as the local_tools.get_commit_diff
    orchestrator tool (L17), just wired through this router's simpler
    project-lookup convention (get_project_file's pattern) instead of
    the chat-only BINContext. Top 5 files with real added/removed
    counts + a patch snippet; "N more" for the rest; never hides a
    server-side change."""
    me = await _pkg.current_dev(authorization)
    user_id = me["user_id"]
    db = _pkg.require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id}, {"_id": 0},
    )
    if not proj:
        raise HTTPException(404, "Project not found")

    latest = await db.cto_tasks.find(
        {"project_id": project_id, "user_id": user_id, "status": "done"},
        {"_id": 0, "task_id": 1, "files_changed_simple": 1, "commit_sha": 1,
         "completed_at": 1},
    ).sort("completed_at", -1).limit(1).to_list(1)

    from services.preview_capture import summarise_change_classification
    if not latest or not latest[0].get("commit_sha"):
        summary = summarise_change_classification([])
        return {"ok": True, **summary, "files": [], "more": 0, "commit_sha": None}

    task = latest[0]
    files = task.get("files_changed_simple") or []
    summary = summarise_change_classification(files)
    sha = task["commit_sha"]

    from services.pat_vault import get_repo_token_or_error
    gh_token, auth_err, _detail = await get_repo_token_or_error(proj)
    owner = proj.get("github_owner") or ""
    repo = proj.get("github_repo") or ""
    if auth_err or not (owner and repo and gh_token):
        # Honest partial: we know WHAT changed (from the task record)
        # even if we can't reach GitHub right now for the diff detail.
        return {"ok": True, **summary, "files": [
            {"path": p, "classification": None, "additions": None,
             "deletions": None, "patch": None} for p in files[:5]
        ], "more": max(0, len(files) - 5), "commit_sha": sha,
            "diff_unavailable": True}

    import httpx
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {gh_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}",
                headers=headers,
            )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"[what-changed] GitHub commit fetch failed: {e!r}")
        return {"ok": True, **summary, "files": [
            {"path": p, "classification": None, "additions": None,
             "deletions": None, "patch": None} for p in files[:5]
        ], "more": max(0, len(files) - 5), "commit_sha": sha,
            "diff_unavailable": True}

    from services.preview_capture import classify_changed_file
    gh_files = data.get("files") or []
    detailed = [
        {
            "path": f["filename"],
            "classification": classify_changed_file(f["filename"]),
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
            "patch": (f.get("patch") or "")[:1200],
        }
        for f in gh_files[:5]
    ]
    return {
        "ok": True, **summary, "files": detailed,
        "more": max(0, len(gh_files) - 5), "commit_sha": sha,
        "diff_unavailable": False,
    }
