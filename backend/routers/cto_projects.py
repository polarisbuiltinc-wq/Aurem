"""
routers/cto_projects.py — AUREM CTO multi-project system.
Connect existing client GitHub repos, run AI tasks (git pull → fix → push).
Mounted under /api/aurem-dev/cto/* to avoid clashing with /projects/* (new-project flow).
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from cto_services.auth import current_dev
from cto_services.db import get_db, require_db
from services.llm import call_llm
from services.usage import assert_has_budget, assert_has_task_budget, get_usage
from services.github_api_writer import (
    commit_files as gh_api_commit,
    revert_commit as gh_api_revert,
    fetch_file as gh_api_fetch_file,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cto", tags=["AUREM CTO Projects"])

# Detect whether `git` binary is available — production containers don't
# have it (Iter 21). When missing we route to the pure-HTTP GitHub API path.
_GIT_AVAILABLE = shutil.which("git") is not None
if not _GIT_AVAILABLE:
    logger.warning(
        "`git` binary not found on this server. CTO tasks will use the "
        "GitHub REST API path (no clone, no push subprocess)."
    )

WORKSPACE = Path(os.getenv("WORKSPACE_PATH", "/tmp/aurem-dev-projects"))
WORKSPACE.mkdir(parents=True, exist_ok=True)


# ── Live progress streams (Iter 73) ──────────────────────────────────────
# In-memory per-task asyncio.Queue used to fan out worker steps to the
# /cto/tasks/{id}/stream SSE endpoint so chat bubbles render a live
# "worker tape" (reading files… thinking… committing…) instead of a
# silent spinner.  Queues are popped once the task emits a done/fail
# terminal frame, or when the stream times out (5 min wall-clock).
_task_queues: dict[str, asyncio.Queue] = {}


def _frontend_subset(edits: dict[str, str]) -> dict[str, str]:
    """Pick the files we persist on the task doc for the right-side
    `<PreviewPane />` to render after ship.

    Iter 169 — expanded beyond pure frontend files. Previously only
    html/css/js/jsx/ts/tsx were kept, which meant a backend-only ship
    (e.g. a Python services edit) left the user's "</> Code" button
    pointing at literally nothing. Now we also keep `.py`, `.json`,
    `.yaml`, `.yml`, `.md`, `.sql`, `.sh`, `.toml`, and `.env.example`
    so backend ships show their actual code, not just the live URL.

    Cap: 12 files × 32 KB each = ~384 KB max stored per task. Anything
    bigger gets dropped (the user can still view the live URL)."""
    out: dict[str, str] = {}
    _ALLOWED_EXT = (
        ".html", ".css", ".js", ".jsx", ".ts", ".tsx",
        ".py", ".json", ".yaml", ".yml", ".md",
        ".sql", ".sh", ".toml",
    )
    for path, body in (edits or {}).items():
        if not isinstance(body, str):
            continue
        path_l = path.lower()
        if path_l.endswith(".env.example"):
            pass  # explicit allow
        elif not path_l.endswith(_ALLOWED_EXT):
            continue
        if len(body) > 32_000:
            continue
        out[path] = body
        if len(out) >= 12:
            break
    return out


async def _emit(task_id: str, step: str,
                kind: str = "step", pct: Optional[int] = None,
                **extra) -> None:
    """Push one progress frame onto the task's live SSE queue.

    Non-blocking; safe to call even if no consumer is listening (the queue
    just buffers up to 256 frames then drops oldest).

    Any **extra keyword args are merged into the frame so callers can ship
    structured payloads (e.g. `agents=["backend","frontend"]` for the
    parallel-mode worker tape)."""
    if not task_id:
        return
    q = _task_queues.get(task_id)
    if q is None:
        q = asyncio.Queue(maxsize=256)
        _task_queues[task_id] = q
    frame = {"type": kind, "step": step, "pct": pct, "ts": time.time()}
    if extra:
        # Don't let callers overwrite the canonical fields.
        for k, v in extra.items():
            if k not in frame:
                frame[k] = v
    try:
        q.put_nowait(frame)
    except asyncio.QueueFull:
        # Drop the oldest frame to make room for the new one rather than
        # blocking the worker — the SSE client will see a small gap.
        try:
            q.get_nowait()
            q.put_nowait(frame)
        except Exception:
            pass


# ── Models ───────────────────────────────────────────────────────────────
class AddProject(BaseModel):
    name: str
    github_url: str
    github_token: Optional[str] = None  # PAT; fall back to user's OAuth token
    branch: str = "main"
    tech_stack: Optional[str] = None
    preview_url: Optional[str] = None   # public URL of the running site/app


class TaskBody(BaseModel):
    project_id: str
    task: str
    files: List[str] = []
    context: str = ""
    auto_deploy: bool = False
    maxx_mode: bool = False     # iter 40: enable Two-Agent (DeepSeek + Claude review)


# ── Helpers ──────────────────────────────────────────────────────────────
def _parse_repo(url: str) -> tuple[str, str]:
    p = url.rstrip("/").replace(".git", "").replace("https://github.com/", "").split("/")
    if len(p) < 2:
        raise HTTPException(400, "Bad GitHub URL — expected https://github.com/owner/repo")
    return p[0], p[1]


async def _user_gh_token(user_id: str) -> Optional[str]:
    db = get_db()
    if db is None:
        return None
    u = await db.dev_users.find_one({"user_id": user_id}, {"_id": 0, "github": 1})
    return ((u or {}).get("github") or {}).get("access_token")


# ── Iter 43 — PAT encryption helpers ──────────────────────────────────
# Tokens stored in cto_projects.github_token are encrypted at rest via
# services.vault (per-customer HKDF-Fernet, v1:-prefixed ciphertext).
# Legacy rows persisted before this migration may still hold plaintext
# tokens — the decrypt helper transparently passes those through so the
# pipeline keeps working until migrations/002_encrypt_pats.py is run.

async def _encrypt_pat(user_id: str, token: Optional[str]) -> Optional[str]:
    if not token:
        return token
    if token.startswith("v1:"):
        return token   # already encrypted
    try:
        from services.vault import encrypt, is_vault_available
        if not is_vault_available():
            return token
        return await encrypt(user_id, token, kind="github_token")
    except Exception:
        return token   # fail-open: never block project creation on crypto


async def _decrypt_pat(user_id: str, token: Optional[str]) -> Optional[str]:
    if not token:
        return token
    if not token.startswith("v1:"):
        return token   # legacy plaintext — pass through
    try:
        from services.vault import decrypt
        return await decrypt(user_id, token, kind="github_token")
    except Exception:
        return None    # tamper / wrong user → treat as missing token


# Iter 165 — Brain V2 endpoints (manual rebuild + read-only inspect)


class BuildBrainBody(BaseModel):
    pass


@router.post("/projects/{project_id}/build-brain")
async def build_project_brain(
    project_id: str,
    body: BuildBrainBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Trigger a full Brain V2 scan for `project_id`.

    Auto-called on `POST /projects/add`; also exposed manually so admin
    or settings UI can rebuild after a major refactor or branch swap.
    """
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Project not found")

    gh_token = await _decrypt_pat(user_id, proj.get("github_token")) \
        or await _user_gh_token(user_id)
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
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
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


class WarmStartBody(BaseModel):
    pass


@router.post("/projects/{project_id}/warm-start")
async def warm_start_project(
    project_id: str,
    body: WarmStartBody,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Trigger 4 parallel background agents to pre-load project context.

    Returns the `job_id` immediately so the frontend can stream progress
    via the status endpoint while the user types their first prompt.
    """
    import uuid as _uuid
    me = await current_dev(authorization)
    user_id = me["user_id"]
    db = get_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Project not found")

    gh_token = await _decrypt_pat(user_id, proj.get("github_token")) \
        or await _user_gh_token(user_id)
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
    started_at = time.time()
    await db.warm_start_jobs.insert_one({
        "job_id":        job_id,
        "project_id":    project_id,
        "user_id":       user_id,
        "status":        "running",
        "started_at":    started_at,
        "agents_done":   [],
        "agents_total":  ["brain", "recent", "structure", "stack", "graph"],
    })

    asyncio.create_task(_run_warm_agents(
        job_id=job_id, project_id=project_id, user_id=user_id,
        gh