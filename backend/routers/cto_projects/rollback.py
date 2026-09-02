"""
routers/cto_projects/rollback.py — AUREM CTO Projects.
Revert a previously-pushed AUREM commit (`git revert --no-edit`,
never a force-push) via the same git-binary/GitHub-API dual path the
task workers use.

Split from the former monolithic routers/cto_projects.py on
2026-09-08 (responsibility-based extraction, no logic change). Uses
`_pkg.<name>` for anything patched at the package level by the
existing test suite (`current_dev`, `get_db`, `require_db`,
`gh_api_revert`, `_sh`, `WORKSPACE`, `_GIT_AVAILABLE`,
`_run_rollback_via_api`, `_run_rollback_with_git`) — see preview.py's
module docstring for why.
"""
import asyncio
import logging
import shutil
import time

from fastapi import BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

import routers.cto_projects as _pkg
from . import router

logger = logging.getLogger(__name__)


class RollbackBody(BaseModel):
    # User must echo "ROLLBACK" to confirm intent server-side too —
    # double safety on top of the two-click client confirmation.
    confirm: str


@router.post("/tasks/{task_id}/rollback")
async def rollback_task(
    task_id: str,
    body: RollbackBody,
    bg: BackgroundTasks,
    authorization: str = Header(None),
) -> dict:
    """Revert a previously-pushed AUREM commit on the project's repo.
    Uses `git revert --no-edit <sha>` so the rollback is itself a new
    commit (no force-push, full history preserved). Idempotent: a task
    that's already been rolled back returns 409."""
    me = await _pkg.current_dev(authorization)
    if (body.confirm or "").strip().upper() != "ROLLBACK":
        raise HTTPException(400, "Must confirm with 'ROLLBACK'")

    db = _pkg.require_db()
    t = await db.cto_tasks.find_one(
        {"task_id": task_id, "user_id": me["user_id"]}
    )
    if not t:
        raise HTTPException(404, "Task not found")
    if t.get("status") != "done":
        raise HTTPException(
            400,
            f"Only completed tasks can be rolled back (current: {t.get('status')})",
        )
    if not t.get("commit_sha"):
        raise HTTPException(400, "Task has no commit to revert")
    if t.get("rollback_sha"):
        raise HTTPException(409, "Task already rolled back")
    if t.get("rollback_status") in ("queued", "running"):
        raise HTTPException(409, "Rollback already in progress")
    if t.get("rollback_status") == "failed":
        raise HTTPException(
            409,
            "Previous rollback failed — manual intervention required",
        )

    proj = await db.cto_projects.find_one(
        {"project_id": t["project_id"], "user_id": me["user_id"]},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Parent project not found")

    from services.pat_vault import get_repo_token_or_error
    user_token, _auth_err, _auth_detail = await get_repo_token_or_error(proj)
    if not user_token:
        raise HTTPException(403, f"GitHub App auth failed ({_auth_err}): {_auth_detail}")

    await db.cto_tasks.update_one(
        {"task_id": task_id},
        {"$set": {
            "rollback_status": "queued",
            "rollback_started_at": time.time(),
        }},
    )
    bg.add_task(_pkg._run_rollback, task_id, proj, t["commit_sha"], user_token)
    return {"ok": True, "task_id": task_id, "rollback_status": "queued"}


# ── Rollback worker ──────────────────────────────────────────────────────
async def _rollback_log(task_id: str, step: str, status: str = "info"):
    """Append a step to the task's `rollback_steps` array."""
    db = _pkg.get_db()
    if db is None:
        return
    await db.cto_tasks.update_one(
        {"task_id": task_id},
        {"$push": {"rollback_steps": {"step": step, "status": status, "ts": time.time()}}},
    )


async def _run_rollback(task_id: str, proj: dict, commit_sha: str,
                         user_token: str) -> None:
    """Dispatcher — git-subprocess path locally, GitHub-API path in
    production where git isn't installed."""
    if _pkg._GIT_AVAILABLE:
        return await _pkg._run_rollback_with_git(task_id, proj, commit_sha, user_token)
    return await _pkg._run_rollback_via_api(task_id, proj, commit_sha, user_token)


async def _run_rollback_via_api(task_id: str, proj: dict, commit_sha: str,
                                  user_token: str) -> None:
    """Pure-API rollback — uses GitHub Git Data API to push a revert
    commit on top of branch HEAD. No force-push, full history preserved."""
    owner = proj["github_owner"]
    repo = proj["github_repo"]
    branch = proj.get("branch", "main")
    db = _pkg.get_db()

    def _scrub(s: str) -> str:
        return (s or "").replace(user_token or "", "***PAT***") if user_token else (s or "")

    async def _set(**fields):
        if db is not None:
            await db.cto_tasks.update_one({"task_id": task_id}, {"$set": fields})

    async def _prog(step: str, status: str = "info"):
        await _rollback_log(task_id, step, status)

    try:
        await _set(rollback_status="running")
        # Iter 212m-218 — attribute the revert commit to the real
        # developer instead of the historical "AUREM" bot identity.
        from services.git_identity import resolve_git_identity
        _author_name, _author_email = await resolve_git_identity(
            db, proj.get("user_id") or "",
        )
        result = await _pkg.gh_api_revert(
            owner=owner, repo=repo, branch=branch, token=user_token,
            commit_sha=commit_sha, progress=_prog,
            author_name=_author_name, author_email=_author_email,
        )
        await _set(
            rollback_status="done",
            rollback_sha=result["sha"],
            rollback_completed_at=time.time(),
        )
    except Exception as e:
        logger.exception(f"[rollback-api {task_id}] failed")
        safe = _scrub(str(e))
        await _rollback_log(task_id, f"❌ {safe}", "error")
        await _set(
            rollback_status="failed",
            rollback_error=safe,
            rollback_completed_at=time.time(),
        )


async def _run_rollback_with_git(task_id: str, proj: dict, commit_sha: str,
                                   user_token: str) -> None:
    """Clone, `git revert --no-edit <sha>`, push the revert commit."""
    ws = _pkg.WORKSPACE / f"rb_{task_id}"
    ws.mkdir(parents=True, exist_ok=True)
    repo_path = ws / "repo"
    owner = proj["github_owner"]
    repo = proj["github_repo"]
    branch = proj.get("branch", "main")
    # 2026-08-24 fix — GitHub App installation tokens must be passed as
    # the HTTPS PASSWORD (username "x-access-token"), not as the
    # username-only PAT-style embed. The old `https://{token}@github.com`
    # form makes git treat the token as a username with a blank
    # password, which then tries an interactive password prompt and
    # fails non-interactively ("could not read Password ... No such
    # device or address"). Confirmed via a real Preview/testbed E2E
    # ship drill (task submitted against polarisbuiltinc-wq/aurem-
    # rollback-testbed via App installation 152797252).
    clone_url = (f"https://x-access-token:{user_token}@github.com/{owner}/{repo}.git"
                if user_token else f"https://github.com/{owner}/{repo}.git")

    db = _pkg.get_db()

    def _scrub(s: str) -> str:
        """Strip the PAT from any error/log string before we persist it."""
        if not s or not user_token:
            return s or ""
        return s.replace(user_token, "***PAT***")

    async def _set(**fields):
        if db is not None:
            await db.cto_tasks.update_one({"task_id": task_id}, {"$set": fields})

    try:
        await _set(rollback_status="running")
        await _rollback_log(task_id, f"Cloning {owner}/{repo}@{branch}…")
        # Full history needed (no --depth=1) so the revert can find the sha
        # 2026-09-09 — offloaded to a thread (asyncio.to_thread) so these
        # synchronous `subprocess.run()` git calls (up to 120s) never block
        # the shared event loop. This function runs via FastAPI
        # BackgroundTasks on the SAME event loop as every other request
        # (including the trivial /health probe) — a blocked clone/push here
        # was the confirmed root cause of the nginx "/health upstream timed
        # out" bursts that made K8s mark the pod unhealthy mid-deploy.
        r = await asyncio.to_thread(
            _pkg._sh, ["git", "clone", "--branch", branch, clone_url, str(repo_path)],
            cwd=ws, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"git clone failed: {_scrub(r.stderr)[:300]}")
        await _rollback_log(task_id, "✅ Cloned", "success")

        await asyncio.to_thread(_pkg._sh, ["git", "config", "user.email", "cto@auremcto.com"], repo_path)
        await asyncio.to_thread(_pkg._sh, ["git", "config", "user.name", "AUREM"], repo_path)

        # Use `git revert` so we never force-push; it produces a new commit
        # that undoes the changes. `-m 1` lets us revert merge commits if
        # the original was a merge.
        revert = await asyncio.to_thread(
            _pkg._sh,
            ["git", "revert", "--no-edit", "-m", "1", commit_sha],
            repo_path, timeout=60,
        )
        if revert.returncode != 0:
            # Plain (non-merge) commits don't accept `-m`; retry without it
            await asyncio.to_thread(_pkg._sh, ["git", "revert", "--abort"], repo_path)
            revert = await asyncio.to_thread(
                _pkg._sh,
                ["git", "revert", "--no-edit", commit_sha],
                repo_path, timeout=60,
            )
        if revert.returncode != 0:
            raise RuntimeError(
                f"git revert failed (possibly conflicts): {_scrub(revert.stderr)[:300]}"
            )
        await _rollback_log(task_id, f"✏️ Reverted {commit_sha}", "success")

        push = await asyncio.to_thread(_pkg._sh, ["git", "push", "origin", branch], repo_path, timeout=90)
        if push.returncode != 0:
            raise RuntimeError(f"git push failed: {_scrub(push.stderr)[:300]}")

        new_sha_r = await asyncio.to_thread(_pkg._sh, ["git", "rev-parse", "--short", "HEAD"], repo_path)
        new_sha = new_sha_r.stdout.strip()
        await _rollback_log(task_id, f"🚀 pushed revert — {new_sha}", "success")
        await _set(
            rollback_status="done",
            rollback_sha=new_sha,
            rollback_completed_at=time.time(),
        )
    except Exception as e:
        logger.exception(f"[rollback {task_id}] failed")
        safe_msg = _scrub(str(e))
        await _rollback_log(task_id, f"❌ {safe_msg}", "error")
        await _set(
            rollback_status="failed",
            rollback_error=safe_msg,
            rollback_completed_at=time.time(),
        )
    finally:
        shutil.rmtree(ws, ignore_errors=True)


