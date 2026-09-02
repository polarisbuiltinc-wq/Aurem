"""
routers/cto_projects/brain.py — AUREM CTO Projects.
Brain V2 (build/read) + Warm Start (4-agent parallel context preload).

Split from the former monolithic routers/cto_projects.py on
2026-09-08 (responsibility-based extraction, no logic change).
Uses `_pkg.<name>` for anything patched at the package level by the
existing test suite (`current_dev`, `get_db`) — see preview.py's
module docstring for why.
"""
import asyncio
import logging
import time
from typing import Optional

from fastapi import Header, HTTPException

import routers.cto_projects as _pkg
from . import router

logger = logging.getLogger(__name__)


# Iter 165 — Brain V2 endpoints (manual rebuild + read-only inspect)


@router.post("/projects/{project_id}/build-brain")
async def build_project_brain(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Trigger a full Brain V2 scan for `project_id`.

    Auto-called on `POST /projects/add`; also exposed manually so admin
    or settings UI can rebuild after a major refactor or branch swap.
    """
    me = await _pkg.current_dev(authorization)
    user_id = me["user_id"]
    db = _pkg.get_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Project not found")

    from services.pat_vault import get_repo_token_or_error
    gh_token, _auth_err, _auth_detail = await get_repo_token_or_error(proj)
    if _auth_err:
        raise HTTPException(403, f"GitHub App auth failed ({_auth_err}): {_auth_detail}")
    gh_owner = proj.get("github_owner") or ""
    gh_repo  = proj.get("github_repo") or ""
    branch   = proj.get("branch") or "main"
    if not (gh_token and gh_owner and gh_repo):
        raise HTTPException(400, "GitHub not connected to this project")

    from services.project_brain import build_brain_v2
    brain = await build_brain_v2(
        db, project_id, user_id, gh_token, gh_owner, gh_repo, branch,
    )
    return {
        "ok": True,
        "brain_version":    brain.get("version"),
        "structure_keys":   list((brain.get("structure") or {}).keys()),
        "stack":            brain.get("stack") or {},
        "task_count":       brain.get("task_count", 0),
        "next_refresh_at":  brain.get("next_full_refresh_at"),
        "hot_paths":        brain.get("hot_paths") or [],
    }


@router.get("/projects/{project_id}/brain")
async def get_project_brain(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Read-only view of the V2 brain — for the Settings page so
    users can confirm what the agent knows about their repo."""
    me = await _pkg.current_dev(authorization)
    user_id = me["user_id"]
    db = _pkg.get_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "project_id": 1},
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    from services.project_brain import get_brain_v2, format_brain_for_agent
    brain = await get_brain_v2(db, project_id, user_id)
    return {
        "ok": True,
        "exists": bool(brain),
        "brain": brain,
        "summary": format_brain_for_agent(brain) if brain else "",
    }


# Iter 165 — Warm Start endpoints. Fired on project SELECT so the next
# chat turn already has fresh commit history + file tree + stack
# context cached in MongoDB. 4 agents run in parallel, each capped at
# 8s so the slowest GitHub call can't stall the warm-start job.


@router.post("/projects/{project_id}/warm-start")
async def warm_start_project(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Trigger 4 parallel background agents to pre-load project context.

    Returns the `job_id` immediately so the frontend can stream progress
    via the status endpoint while the user types their first prompt.
    """
    import uuid as _uuid
    me = await _pkg.current_dev(authorization)
    user_id = me["user_id"]
    db = _pkg.get_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Project not found")

    from services.pat_vault import get_repo_token_or_error
    gh_token, _auth_err, _auth_detail = await get_repo_token_or_error(proj)
    if _auth_err:
        raise HTTPException(403, f"GitHub App auth failed ({_auth_err}): {_auth_detail}")
    gh_owner = proj.get("github_owner") or ""
    gh_repo  = proj.get("github_repo") or ""
    branch   = proj.get("branch") or "main"
    if not (gh_token and gh_owner and gh_repo):
        return {
            "ok": True, "job_id": None,
            "message": "No GitHub connection — warm-start skipped",
            "status": "no_token",
        }

    job_id = f"ws_{_uuid.uuid4().hex[:10]}"
    from datetime import datetime as _dt, timezone as _tz
    started_at = _dt.now(_tz.utc)
    await db.warm_start_jobs.insert_one({
        "job_id":        job_id,
        "project_id":    project_id,
        "user_id":       user_id,
        "status":        "running",
        # 2026-08-27 — TTL fix: real BSON Date (was `time.time()`
        # float — the `warm_start_jobs.started_at` TTL index (1h)
        # never expired these rows).
        "started_at":    started_at,
        "agents_done":   [],
        "agents_total":  ["brain", "recent", "structure", "stack", "graph"],
    })

    asyncio.create_task(_pkg._run_warm_agents(
        job_id=job_id, project_id=project_id, user_id=user_id,
        gh_token=gh_token, gh_owner=gh_owner, gh_repo=gh_repo,
        branch=branch, db=db,
    ))
    return {
        "ok": True, "job_id": job_id,
        "message": "Warming up — agents loading your project",
    }


async def _run_warm_agents(
    *, job_id: str, project_id: str, user_id: str,
    gh_token: str, gh_owner: str, gh_repo: str, branch: str, db,
) -> None:
    """Background: 4 agents in parallel. Each pushes its result into
    `warm_start_jobs` as soon as it finishes."""
    from services.project_brain import (
        _gh_list_files, _gh_read_small,
        build_brain_v2, get_brain_v2,
    )

    async def _mark_done(agent: str) -> None:
        try:
            # Iter 212m-15 — $addToSet (not $push) so the outer
            # `_bounded` wrapper can safely re-mark a timed-out agent
            # without double-counting and pushing the progress bar
            # past 100%.
            await db.warm_start_jobs.update_one(
                {"job_id": job_id},
                {"$addToSet": {"agents_done": agent}},
            )
        except Exception:
            pass

    # Agent 1 — Brain V2 build/refresh (skip if scanned <10 min ago)
    async def agent_brain() -> None:
        try:
            existing = await get_brain_v2(db, project_id, user_id)
            age = time.time() - float(existing.get("last_scan", 0) or 0)
            if not existing or age > 600:
                await build_brain_v2(
                    db, project_id, user_id,
                    gh_token, gh_owner, gh_repo, branch,
                )
        except Exception as e:
            logger.warning("warm-start brain agent: %r", e)
        finally:
            await _mark_done("brain")

    # Agent 2 — Recent commits via direct GitHub REST
    async def agent_recent() -> None:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=8.0) as cx:
                r = await cx.get(
                    f"https://api.github.com/repos/{gh_owner}/{gh_repo}/commits",
                    params={"per_page": 5, "sha": branch},
                    headers={
                        "Authorization": f"token {gh_token}",
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "aurem-warm-start",
                    },
                )
            commits_summary = ""
            if r.status_code == 200:
                items = r.json() or []
                lines: list[str] = []
                for c in items[:5]:
                    sha = (c.get("sha") or "")[:7]
                    commit_obj = c.get("commit") or {}
                    msg = (commit_obj.get("message") or "").splitlines()[0][:80]
                    author = (commit_obj.get("author") or {}).get("name", "")
                    lines.append(f"{sha} · {author} · {msg}")
                commits_summary = "\n".join(lines)[:600]
            if commits_summary:
                await db.warm_start_jobs.update_one(
                    {"job_id": job_id},
                    {"$set": {"recent_commits": commits_summary}},
                )
        except Exception as e:
            logger.warning("warm-start recent agent: %r", e)
        finally:
            await _mark_done("recent")

    # Agent 3 — File tree (root + backend + frontend, parallel)
    async def agent_structure() -> None:
        try:
            root, be, fe = await asyncio.gather(
                _gh_list_files(gh_token, gh_owner, gh_repo, "", branch),
                _gh_list_files(gh_token, gh_owner, gh_repo, "backend", branch),
                _gh_list_files(gh_token, gh_owner, gh_repo, "frontend/src", branch),
                return_exceptions=True,
            )
            def _l(x): return x if isinstance(x, list) else []
            tree_parts: list[str] = []
            if _l(root):
                tree_parts.append("ROOT: " + ", ".join(_l(root)[:15]))
            if _l(be):
                tree_parts.append("backend/: " + ", ".join(_l(be)[:15]))
            if _l(fe):
                tree_parts.append("frontend/src/: " + ", ".join(_l(fe)[:15]))
            tree = "\n".join(tree_parts)[:1000]
            if tree:
                await db.warm_start_jobs.update_one(
                    {"job_id": job_id},
                    {"$set": {"file_tree": tree}},
                )
        except Exception as e:
            logger.warning("warm-start structure agent: %r", e)
        finally:
            await _mark_done("structure")

    # Agent 4 — Stack (package.json + requirements.txt, parallel)
    async def agent_stack() -> None:
        try:
            pkg, req = await asyncio.gather(
                _gh_read_small(gh_token, gh_owner, gh_repo, "package.json", branch, 500),
                _gh_read_small(gh_token, gh_owner, gh_repo, "backend/requirements.txt", branch, 400),
                return_exceptions=True,
            )
            stack_parts: list[str] = []
            if isinstance(pkg, str) and pkg:
                stack_parts.append("package.json:\n" + pkg[:400])
            if isinstance(req, str) and req:
                stack_parts.append("requirements.txt:\n" + req[:300])
            stack_raw = "\n\n".join(stack_parts)[:800]
            if stack_raw:
                await db.warm_start_jobs.update_one(
                    {"job_id": job_id},
                    {"$set": {"stack_raw": stack_raw}},
                )
        except Exception as e:
            logger.warning("warm-start stack agent: %r", e)
        finally:
            await _mark_done("stack")

    # Agent 5 — Codebase Graph (hybrid regex + LLM top 20).
    # Skips the LLM step entirely if the graph was built < 1h ago.
    async def agent_graph() -> None:
        try:
            from services.graph_builder import build_graph, get_graph
            existing = await get_graph(db, project_id, user_id)
            age = time.time() - float(existing.get("built_at", 0) or 0)
            if not existing or age > 3600:
                await build_graph(
                    db, project_id, user_id,
                    gh_token, gh_owner, gh_repo,
                )
        except Exception as e:
            logger.warning("warm-start graph agent: %r", e)
        finally:
            await _mark_done("graph")

    try:
        # Iter 212m-15 — Cap every warm-start agent at 12 s so a slow LLM
        # call inside the graph builder can't keep the progress bar stuck
        # at 80 % (4/5 done). `_mark_done` always fires from each agent's
        # finally block; the outer wait_for here is the hard ceiling for
        # the whole job. Any agent that exceeds it is silently abandoned
        # (logged via the agent's own except path) — its data was never
        # critical for the next chat turn anyway, the brain/structure
        # agents that DID complete already populate the context cache.
        #
        # Iter 212m-127 — Per-agent timeout overrides. Production logs
        # showed `warm-start graph agent: timed out after 12s` whenever
        # all 20 top files needed re-LLM (first run for a new repo or
        # the file set rotated entirely). 12 s is fine for the brain/
        # structure/stack agents (single LLM call each) but the graph
        # builder makes one call per file in the worst case. Give it
        # 25 s; that still bounds the warm-start job but lets the
        # first-run case actually populate the graph instead of
        # logging a warning every time.
        _AGENT_TIMEOUTS = {
            "brain":     12.0,
            "recent":    12.0,
            "structure": 12.0,
            "stack":     12.0,
            "graph":     25.0,
        }

        async def _bounded(coro, label: str) -> None:
            timeout = _AGENT_TIMEOUTS.get(label, 12.0)
            try:
                await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "warm-start %s agent: timed out after %.0fs",
                    label, timeout,
                )
                await _mark_done(label)
            except Exception as e:
                logger.warning("warm-start %s agent: %r", label, e)
                await _mark_done(label)

        await asyncio.gather(
            _bounded(agent_brain(),     "brain"),
            _bounded(agent_recent(),    "recent"),
            _bounded(agent_structure(), "structure"),
            _bounded(agent_stack(),     "stack"),
            _bounded(agent_graph(),     "graph"),
            return_exceptions=True,
        )
        await db.warm_start_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "ready", "completed_at": time.time()}},
        )
    except Exception as e:
        logger.error("warm-start job %s crashed: %r", job_id, e)
        await db.warm_start_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "failed", "error": str(e)[:500]}},
        )


@router.get("/projects/warm-start/{job_id}/status")
async def warm_start_status(
    job_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Poll endpoint: returns the warm-start job's progress so the UI
    can render a percentage bar."""
    me = await _pkg.current_dev(authorization)
    db = _pkg.get_db()
    job = await db.warm_start_jobs.find_one(
        {"job_id": job_id, "user_id": me["user_id"]},
        {"_id": 0},
    )
    if not job:
        raise HTTPException(404, "Job not found")
    agents_done  = job.get("agents_done") or []
    agents_total = job.get("agents_total") or []
    progress = (len(agents_done) / max(len(agents_total), 1)) if agents_total else 0.0
    return {
        "ok":           True,
        "job_id":       job_id,
        "status":       job.get("status"),
        "progress":     round(progress, 2),
        "agents_done":  agents_done,
        "agents_total": agents_total,
        "ready":        job.get("status") == "ready",
        "started_at":   job.get("started_at"),
        "completed_at": job.get("completed_at"),
    }
