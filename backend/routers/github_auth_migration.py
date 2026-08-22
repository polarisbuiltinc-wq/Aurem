"""routers/github_auth_migration.py — PAT→GitHub-App migration (2026-06).

Founder-gated, propose-then-execute:
  GET  /admin/github-auth/pat-inventory   — counts + per-project readiness
  POST /admin/github-auth/migrate         — {"execute": false} dry-run default;
                                            {"execute": true} flips rows the App
                                            already covers; {"purge_tokens": true}
                                            additionally nulls stored PATs +
                                            user OAuth access_tokens (only run
                                            after explicit founder sign-off).
Rows the App does NOT cover are never guessed — they're marked
`auth_required` so the UI prompts a proper App reconnect.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from cto_services.auth import require_admin_dep
from cto_services.db import get_db

logger = logging.getLogger("aurem.github_auth_migration")

router = APIRouter(prefix="/admin/github-auth", tags=["Admin-GitHub-Auth"],
                   dependencies=[Depends(require_admin_dep)])


async def _installation_map() -> dict[str, int]:
    """repo full_name (lowercase) → installation_id, via the App."""
    from services.github_app import list_installation_repos, list_installations
    out: dict[str, int] = {}
    for inst in await list_installations():
        for r in await list_installation_repos(inst["id"]):
            out[r.get("full_name", "").lower()] = inst["id"]
    return out


async def _classify(db):
    """Classify every non-App project row against live App coverage."""
    coverage = await _installation_map()
    coverable, uncoverable = [], []
    cur = db.cto_projects.find(
        {"$or": [{"auth_method": {"$ne": "github_app"}},
                 {"installation_id": {"$in": [None, ""]}}]},
        {"_id": 0, "project_id": 1, "user_id": 1, "auth_method": 1,
         "github_owner": 1, "github_repo": 1,
         "github_token": 1, "installation_id": 1},
    )
    async for p in cur:
        full = f"{p.get('github_owner','')}/{p.get('github_repo','')}".lower()
        item = {"project_id": p["project_id"],
                "repo": full,
                "auth_method": p.get("auth_method") or "(missing→legacy pat)",
                "has_stored_pat": bool(p.get("github_token"))}
        iid = coverage.get(full)
        if iid:
            item["installation_id"] = iid
            coverable.append(item)
        else:
            uncoverable.append(item)
    return coverable, uncoverable


@router.get("/pat-inventory")
async def pat_inventory():
    db = get_db()
    coverable, uncoverable = await _classify(db)
    oauth_users = await db.dev_users.count_documents(
        {"github.access_token": {"$exists": True, "$nin": [None, ""]}})
    return {
        "coverable_by_app": coverable,
        "not_covered_by_app": uncoverable,
        "users_with_stored_oauth_token": oauth_users,
        "note": ("POST /admin/github-auth/migrate with execute=true flips "
                 "coverable rows to github_app. Uncovered rows get "
                 "auth_required=true — user must reconnect via the App."),
    }


class MigrateReq(BaseModel):
    execute: bool = False
    purge_tokens: bool = False


@router.post("/migrate")
async def migrate(req: MigrateReq):
    db = get_db()
    coverable, uncoverable = await _classify(db)
    report = {"dry_run": not req.execute,
              "would_flip": len(coverable),
              "would_mark_auth_required": len(uncoverable),
              "flipped": 0, "marked_auth_required": 0,
              "pats_purged": 0, "oauth_tokens_purged": 0}
    if not req.execute:
        report["coverable"] = coverable
        report["not_covered"] = uncoverable
        return report

    for item in coverable:
        r = await db.cto_projects.update_one(
            {"project_id": item["project_id"]},
            {"$set": {"auth_method": "github_app",
                      "installation_id": item["installation_id"],
                      "github_token": None,
                      "pat_migrated_at": time.time()},
             "$unset": {"auth_required": ""}})
        report["flipped"] += r.modified_count
    for item in uncoverable:
        r = await db.cto_projects.update_one(
            {"project_id": item["project_id"]},
            {"$set": {"auth_required": True,
                      "github_token": None,
                      "pat_migrated_at": time.time()}})
        report["marked_auth_required"] += r.modified_count
        report["pats_purged"] += r.modified_count

    if req.purge_tokens:
        r1 = await db.cto_projects.update_many(
            {"github_token": {"$nin": [None, ""]}},
            {"$set": {"github_token": None}})
        r2 = await db.dev_users.update_many(
            {"github.access_token": {"$exists": True, "$nin": [None, ""]}},
            {"$unset": {"github.access_token": ""}})
        report["pats_purged"] += r1.modified_count
        report["oauth_tokens_purged"] = r2.modified_count

    logger.info("[github-auth-migrate] %s", report)
    return report
