"""admin_projects_brain.py — Projects, tasks, project brain (memory/decisions), architecture views.

Extracted from routers/admin.py during Phase 2 architecture split (2026-02-11).
Contains 16 handler(s)/helper(s):

  GET  /admin/projects   GET /admin/tasks   GET /admin/architecture
  GET  /admin/project-brain/{project_id}
  POST /admin/project-brain/{project_id}/decision (DELETE too)
  POST /admin/project-brain/{project_id}/preference (DELETE too)
  GET  /admin/brain/{project_id}/dump
  POST /admin/brain/{project_id}/replay
  GET  /admin/brain/{project_id}/recent-commits
  GET  /admin/code-surface   GET /admin/architecture-health
  GET  /admin/postscan-issues

Every handler + helper is COPIED VERBATIM from the pre-split admin.py.
"""
from __future__ import annotations

import logging
import os
import asyncio
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Depends
from pydantic import BaseModel

from cto_services.auth import current_dev, require_admin_dep
from cto_services.db import get_db, require_db
from services.usage import get_usage
# Iter 212m-71 — 60 s TTL cache for the heavy admin aggregations
# (activation funnel, dev_users buckets, etc.). Founders click around
# the admin panel rapidly; without this every click fires 5+ heavy
# aggregations against Mongo.
from services.admin_analytics_cache import (
    cached_agg,
    invalidate as _cache_invalidate,
    mongo_swr_cache,
)

logger = logging.getLogger(__name__)
# Iter 358 — router-level admin gate (defense-in-depth). EVERY route on
# this router is denied to non-founders at the router boundary, so a new
# endpoint added later is protected by default. Individual handlers keep
# their inline `await _require_admin(...)` too (harmless redundancy).
# The one intentionally-public sink (/admin/errors/report) lives on the
# separate, un-gated routers/admin_public.py at the same URL.

router = APIRouter(
    prefix="/admin", tags=["Admin-projects-brain"],
    dependencies=[Depends(require_admin_dep)],
)

from routers._admin_common import _require_admin  # noqa: E402


@router.get("/projects")
async def list_all_projects(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    db = require_db()
    projects = await db.cto_projects.find(
        {}, {"_id": 0, "github_token": 0},
    ).sort("created_at", -1).limit(200).to_list(200)
    return {"projects": projects, "total": len(projects)}


@router.get("/tasks")
async def list_all_tasks(
    status: str = "",
    limit: int = 50,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    db = require_db()
    query: dict = {}
    if status:
        query["status"] = status
    tasks = await db.cto_tasks.find(
        query, {"_id": 0, "steps": 0, "rollback_steps": 0},
    ).sort("created_at", -1).limit(limit).to_list(limit)
    return {"tasks": tasks, "total": len(tasks)}


@router.get("/architecture")
async def get_architecture(authorization: Optional[str] = Header(None)):
    await _require_admin(authorization)
    import asyncio
    import httpx
    from services.external_services_registry import (
        REGISTRY, is_configured, should_probe,
    )
    db = get_db()
    services: dict = {"MongoDB": {
        "status": "live" if db is not None else "down",
        "latency_ms": 0,
    }}
    # Iter 124 — PARALLEL probes (was sequential — worst case 8 svcs × 4s = 32s
    # which is enough to trip Cloudflare 524 under cold-start CPU contention).
    # Now total wall-clock = slowest single probe ≈ 4s cap.
    probe_targets = [svc for svc in REGISTRY if should_probe(svc)]

    from services.http import ext_client
    async def _probe_one(svc):
        try:
            t0 = time.time()
            # Per-call client so a hung connect doesn't share state with peers.
            async with ext_client("internal_probe", timeout=httpx.Timeout(4.0)) as c:
                r = await c.get(
                    svc.probe_url,
                    headers={"User-Agent": "AUREM-arch-probe/1.0"},
                )
            elapsed_ms = round((time.time() - t0) * 1000)
            if r.status_code < 500:
                return svc.display_name, {
                    "status": "live", "latency_ms": elapsed_ms,
                }
            return svc.display_name, {
                "status": "degraded", "latency_ms": elapsed_ms,
                "note": f"HTTP {r.status_code}",
            }
        except Exception as e:
            return svc.display_name, {
                "status": "unreachable", "latency_ms": 0,
                "note": str(e)[:80],
            }

    if probe_targets:
        # asyncio.gather with timeout guard — even if every probe somehow
        # exceeds its own 4s budget, we never let the whole endpoint hang.
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(_probe_one(s) for s in probe_targets),
                               return_exceptions=False),
                timeout=8.0,
            )
            for name, info in results:
                services[name] = info
        except asyncio.TimeoutError:
            # Mark anything missing as "unreachable" — never crash the page.
            for svc in probe_targets:
                services.setdefault(svc.display_name, {
                    "status": "unreachable", "latency_ms": 0,
                    "note": "probe timed out",
                })

    # Iter 123f — integrations grid is also generated from the registry.
    # `mongodb` is special-cased because there's no env key for it (the
    # db handle itself is the truth).
    integrations: dict[str, bool] = {"mongodb": db is not None}
    for svc in REGISTRY:
        integrations[svc.integration_id] = is_configured(svc)

    missing = [k for k, v in integrations.items() if not v]
    note = (
        f"{sum(integrations.values())}/{len(integrations)} integrations configured."
        + (f" Missing: {', '.join(missing[:6])}{'…' if len(missing) > 6 else ''}."
           if missing else " All systems wired.")
    )
    return {
        "services": services,
        "integrations": integrations,
        "note": note,
    }


@router.get("/project-brain/{project_id}")
async def admin_project_brain(
    project_id: str,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    from services.project_brain import get_brain_full
    brain = await get_brain_full(require_db(), project_id)
    return brain or {"project_id": project_id, "empty": True}


class BrainDecisionBody(BaseModel):
    title: str
    reason: str


@router.post("/project-brain/{project_id}/decision")
async def admin_brain_add_decision(
    project_id: str,
    body: BrainDecisionBody,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    from services.project_brain import add_decision
    await add_decision(require_db(), project_id, body.title, body.reason)
    return {"ok": True}


class BrainPreferenceBody(BaseModel):
    preference: str


@router.post("/project-brain/{project_id}/preference")
async def admin_brain_add_preference(
    project_id: str,
    body: BrainPreferenceBody,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    from services.project_brain import add_preference
    await add_preference(require_db(), project_id, body.preference)
    return {"ok": True}


@router.delete("/project-brain/{project_id}/decision")
async def admin_brain_delete_decision(
    project_id: str,
    title: str,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    from services.project_brain import delete_decision
    n = await delete_decision(require_db(), project_id, title)
    return {"ok": True, "removed": n}


@router.get("/brain/{project_id}/dump")
async def admin_brain_dump(
    project_id: str,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Returns exactly what ORA sees for this project.

    Used when "ORA gave wrong answer" — founder can compare the user's
    question against the literal context block that was injected into
    the system prompt. Includes raw brain document for decision/pref
    inline deletion + the assembled string for diff debugging.
    """
    await _require_admin(authorization)
    db = require_db()
    proj = await db.cto_projects.find_one({"project_id": project_id})
    if not proj:
        raise HTTPException(404, "Project not found")

    # Fetch the PAT so the assembled context includes remote commits
    # (matches what ORA would see for this user in a real chat turn).
    # 2026-02-11 · Phase 3b (Bug 2 fix) — get_repo_token unifies PAT
    # + github_app auth.
    token = None
    try:
        from routers.cto_projects import _user_gh_token
        from services.pat_vault import get_repo_token
        token = await get_repo_token(proj) \
            or await _user_gh_token(proj["user_id"])
    except Exception:
        token = None

    brain_doc = await db.project_brains.find_one({"project_id": project_id}) or {}
    # Strip Mongo _id from the raw doc so it stays JSON-serialisable
    brain_doc.pop("_id", None)

    from services.project_brain import get_brain_context
    repo_full = f"{proj.get('github_owner', '')}/{proj.get('github_repo', '')}"
    try:
        assembled = await get_brain_context(
            db, project_id, repo_full, github_token=token,
        )
    except Exception as e:
        assembled = f"(error assembling context: {e})"

    return {
        "project_id":           project_id,
        "repo":                 repo_full,
        "raw_brain":            brain_doc,
        "assembled_context":    assembled,
        "context_length_chars": len(assembled),
        "has_github_commits":   "Recent GitHub commits" in assembled,
        "has_aurem_commits":    "Recent commits AUREM" in assembled,
        "has_decisions":        bool(brain_doc.get("decisions")),
        "has_preferences":      bool(brain_doc.get("team_preferences")
                                     or brain_doc.get("preferences")),
        "had_pat":              bool(token),
    }


@router.get("/code-surface")
async def code_surface(authorization: Optional[str] = Header(None, alias="Authorization")):
    """Walk load-bearing source dirs and return live counts.

    Drift-proof replacement for the hand-maintained CODE_SURFACE constant
    on the Architecture page — the frontend reads from here so a new
    file in routers/ or pages/ shows up immediately."""
    await _require_admin(authorization)
    import os
    base = "/app"
    scan = {
        "routers":    "backend/routers",
        "services":   "backend/services",
        "pages":      "frontend/src/pages",
        "components": "frontend/src/components",
    }
    surface: dict[str, list[dict]] = {k: [] for k in scan}
    for category, rel in scan.items():
        full = os.path.join(base, rel)
        if not os.path.isdir(full):
            continue
        for fname in sorted(os.listdir(full)):
            if fname.startswith((".", "_")):
                continue
            if not fname.endswith((".py", ".jsx", ".tsx", ".js", ".ts")):
                continue
            fpath = os.path.join(full, fname)
            try:
                with open(fpath, encoding="utf-8") as fh:
                    content = fh.read()
            except Exception:
                continue
            lines = content.count("\n")
            desc = ""
            for raw in content.splitlines()[:10]:
                t = raw.strip()
                if not t:
                    continue
                if t.startswith(('"""', "'''")):
                    desc = t.strip("\"'").strip()
                    break
                if t.startswith("/*") or t.startswith("//") or t.startswith("*"):
                    desc = t.lstrip("/*").lstrip("/ *").strip()
                    break
                if (t.startswith("import") or t.startswith("from")
                        or t.startswith("<") or t.startswith("{")):
                    continue
                if t.startswith("#") and not t.startswith("#!"):
                    desc = t.lstrip("# ").strip()
                    break
            surface[category].append({
                "file":  fname,
                "lines": lines,
                "desc":  desc[:80],
                "path":  os.path.join(rel, fname),
            })
    return {
        "ok":          True,
        "surface":     surface,
        "total_files": sum(len(v) for v in surface.values()),
    }


@router.get("/architecture-health")
async def architecture_health(
    summary: bool = False,
    authorization: Optional[str] = Header(None),
):
    """Run the architecture health report.

    Query params:
        summary=true → return a short text body instead of full JSON
                       (useful for one-line Admin tab headlines).
    """
    await _require_admin(authorization)
    from services.architecture_health import (
        run_health_report, summarise,
    )
    report = run_health_report()
    if summary:
        return {"ok": True, "summary": summarise(report),
                "counts": {
                    "bloated":     len(report["bloated_files"]),
                    "complex":     len(report["complexity_hits"]),
                    "circular":    len(report["circular_imports"]),
                    "violations":  len(report["boundary_violations"]),
                }}
    return {"ok": True, "report": report}


@router.get("/brain/{project_id}/recent-commits")
async def brain_recent_commits(
    project_id: str,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Return the last N (default 12) commit events from a project brain.

    Each row carries the SHA, one-line description, files touched, and
    a UTC timestamp. The BrainDump page renders these as a list with
    "Show diff →" buttons that dispatch `ora:prefill` so the user lands
    in chat with `get_commit_diff(<sha>)` pre-filled.
    """
    await _require_admin(authorization)
    db = require_db()
    brain_doc = await db["project_brains"].find_one(
        {"project_id": project_id},
        {"event_log": 1, "_id": 0},
    )
    if not brain_doc:
        return {"project_id": project_id, "commits": []}

    events = brain_doc.get("event_log") or []
    commits = [e for e in events if e.get("type") == "commit"]
    # Newest first; cap at 12 rows to keep the UI tight.
    commits = list(reversed(commits))[:12]

    rows = []
    for ev in commits:
        ts = ev.get("ts")
        rows.append({
            "sha":               (ev.get("sha") or "")[:40],
            "short_sha":         (ev.get("sha") or "")[:7],
            "description":       (ev.get("description") or "").strip().splitlines()[0][:160],
            "files":             ev.get("files") or [],
            "correction_applied": bool(ev.get("correction_applied")),
            "ts":                ts.isoformat() if hasattr(ts, "isoformat") else ts,
        })
    return {"project_id": project_id, "commits": rows}


@router.post("/brain/{project_id}/replay")
async def admin_brain_replay(
    project_id: str,
    payload: dict,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Read-only ORA tester. Given a project's assembled brain context,
    answer a question without writing to MongoDB, without invoking
    Vanguard/Mode-D, and without firing any commit. Used to debug
    'ORA gave a wrong answer' cases — founder can iterate on the
    question text and see how ORA's response changes.
    """
    await _require_admin(authorization)
    question = (payload or {}).get("question", "").strip()
    if not question:
        raise HTTPException(400, "question required")
    if len(question) > 2000:
        raise HTTPException(400, "question too long (max 2000 chars)")

    db = require_db()
    proj = await db.cto_projects.find_one({"project_id": project_id})
    if not proj:
        raise HTTPException(404, "Project not found")

    # Match the PAT resolution used by the real chat path so the replay
    # answer is comparable to what a user would actually get.
    # 2026-02-11 · Phase 3b (Bug 2 fix) — get_repo_token unifies PAT
    # + github_app auth.
    token = None
    try:
        from routers.cto_projects import _user_gh_token
        from services.pat_vault import get_repo_token
        token = await get_repo_token(proj) \
            or await _user_gh_token(proj["user_id"])
    except Exception:
        token = None

    from services.project_brain import get_brain_context
    from services.llm import call_llm

    repo_full = f"{proj.get('github_owner', '')}/{proj.get('github_repo', '')}"
    brain_ctx = await get_brain_context(
        db, project_id, repo_full, github_token=token,
    )

    system = (
        "You are ORA, AUREM's AI engineer. You know this about the project:\n\n"
        + (brain_ctx or "(no project memory recorded yet)")
        + "\n\nAnswer the user's question directly using only the context "
          "above. Do not write code, do not propose commits — this is a "
          "read-only diagnostic session."
    )
    try:
        answer = await call_llm(
            messages=[{"role": "user", "content": question}],
            system=system, max_tokens=600, temperature=0.2,
        )
    except Exception as e:
        raise HTTPException(502, f"LLM call failed: {e}")

    return {
        "project_id":    project_id,
        "question":      question,
        "answer":        answer,
        "brain_chars":   len(brain_ctx),
        "context_used":  bool(brain_ctx),
    }


@router.delete("/project-brain/{project_id}/preference")
async def admin_brain_delete_preference(
    project_id: str,
    preference: str,
    authorization: Optional[str] = Header(None),
):
    await _require_admin(authorization)
    from services.project_brain import delete_preference


@router.get("/postscan-issues")
async def admin_postscan_issues(
    authorization: Optional[str] = Header(None),
    limit: int = 50,
):
    """Recent post-task scanner findings (vanguard regex + lint)."""
    await _require_admin(authorization)
    db = require_db()
    rows: list[dict] = []
    try:
        cursor = db.post_task_scans.find(
            {},
            {"_id": 0, "task_id": 1, "project_id": 1, "user_id": 1,
             "severity": 1, "rule": 1, "file": 1, "match": 1,
             "created_at": 1},
            sort=[("created_at", -1)],
            limit=max(1, min(int(limit or 50), 200)),
        )
        async for r in cursor:
            m = (r.get("match") or "")[:80]
            r["match"] = m
            rows.append(r)
    except Exception as e:
        logger.warning("admin/postscan-issues: %r", e)
    return {"rows": rows, "count": len(rows)}
