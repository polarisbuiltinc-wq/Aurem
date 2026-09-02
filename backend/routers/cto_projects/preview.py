"""
routers/cto_projects/preview.py — AUREM CTO Projects.
Trust Surfaces Round (S0-S5): pending-change detection, live-site
screenshot capture, and authenticated receipt streaming.

Split from the former monolithic routers/cto_projects.py on
2026-09-08 (responsibility-based extraction, no logic change).
Uses `_pkg.<name>` (dynamic package-attribute lookup, not
`from . import name`) for anything patched at the package level by
the existing test suite (`current_dev`, `require_db`) — this keeps
every existing `patch("routers.cto_projects.X", ...)` /
`monkeypatch.setattr(router_mod, "X", ...)` call site working
unchanged, since the lookup resolves against the live `__init__.py`
attribute at call time instead of freezing a copy at import time.
"""
import uuid
from datetime import datetime

from fastapi import Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import routers.cto_projects as _pkg
from . import router

logger = __import__("logging").getLogger(__name__)


class PreviewSessionBody(BaseModel):
    device: str = "phone"


@router.post("/projects/{project_id}/preview/session")
async def log_preview_session(
    project_id: str,
    body: PreviewSessionBody,
    authorization: str = Header(None),
) -> dict:
    """S4 — fire-and-forget ping so the admin monitor tile can show
    last-24h preview sessions by device. Never blocks/errors the
    Preview panel on a logging failure."""
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    device = body.device if body.device in ("phone", "tablet", "desktop") else "phone"
    from services.trust_surface_events import log_trust_event
    await log_trust_event(db, "preview_session", user_id=me["user_id"],
                           project_id=project_id, device=device)
    return {"ok": True}


# ─── Trust Surfaces Round (S0-S5), 2026-08-29 — S1-P3 "After fix" +
# S3-D4 receipts. See services/preview_capture.py for the reused
# Playwright-capture / R2-storage helpers (L17 reuse-first).


@router.get("/projects/{project_id}/preview/pending-change")
async def get_pending_change(
    project_id: str,
    authorization: str = Header(None),
) -> dict:
    """S1-P3/P5 — deterministic (0-LLM) read of whether this project
    has a change that hasn't gone live yet, and which routes it
    likely touches. Three honest states only: `pending` (a task is
    still running), `shipped_not_deployed` (code is on GitHub but a
    configured BYOH host hasn't been redeployed since), `clean`
    (nothing to show — matches the honest "No pending changes" copy
    the Preview panel renders)."""
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": me["user_id"]}, {"_id": 0},
    )
    if not proj:
        raise HTTPException(404, "Project not found")

    latest = await db.cto_tasks.find(
        {"project_id": project_id, "user_id": me["user_id"]},
        {"_id": 0, "task_id": 1, "status": 1, "files_changed_simple": 1,
         "commit_sha": 1, "completed_at": 1, "created_at": 1, "before_receipts": 1},
    ).sort("created_at", -1).limit(1).to_list(1)
    if not latest:
        return {"ok": True, "state": "clean", "routes": [], "files": []}
    task = latest[0]
    status = task.get("status")

    if status in ("queued", "planning", "fixing", "running"):
        return {
            "ok": True, "state": "pending", "task_id": task.get("task_id"),
            "routes": [], "files": [],
        }
    if status != "done":
        return {"ok": True, "state": "clean", "routes": [], "files": []}

    files = task.get("files_changed_simple") or []
    from services.preview_capture import classify_user_repo_change
    routes = classify_user_repo_change(files)

    cfg = await db.aurem_cto_deploy_configs.find_one(
        {"user_id": me["user_id"],
         "$or": [{"project_id": project_id}, {"project_id": None}, {"project_id": ""}]},
        {"_id": 0, "configured": 1},
    )
    if not cfg:
        # No BYOH host configured for this project — a direct GitHub
        # commit is the deploy (most host providers auto-deploy from
        # the branch). Nothing meaningfully "not live" to show.
        return {"ok": True, "state": "clean", "routes": [], "files": []}

    task_done_at = task.get("completed_at") or 0
    last_ok_run = await db.aurem_cto_deploy_runs.find(
        {"user_id": me["user_id"], "project_id": project_id, "status": "ok"},
        {"_id": 0, "finished_at": 1},
    ).sort("finished_at", -1).limit(1).to_list(1)
    if last_ok_run and last_ok_run[0].get("finished_at"):
        try:
            run_epoch = datetime.fromisoformat(
                last_ok_run[0]["finished_at"].replace("Z", "+00:00"),
            ).timestamp()
        except Exception:
            run_epoch = 0
        if run_epoch >= task_done_at:
            return {"ok": True, "state": "clean", "routes": [], "files": []}

    return {
        "ok": True, "state": "shipped_not_deployed",
        "task_id": task.get("task_id"), "commit_sha": task.get("commit_sha"),
        "routes": routes, "files": files,
        "before_receipts": task.get("before_receipts") or {},
    }


@router.get("/projects/{project_id}/preview/capture")
async def capture_preview_route(
    project_id: str,
    route: str = "/",
    device: str = "phone",
    authorization: str = Header(None),
) -> dict:
    """S1-P3/S3-D4 — capture a fresh screenshot of the project's live
    site at `route` for the chosen device, store it as a receipt, and
    return the R2 key for `/preview/receipt/{key}` to stream back.
    Honest failure: never a fake success — `ok: False` + `reason`."""
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": me["user_id"]},
        {"_id": 0, "preview_url": 1},
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    base = (proj.get("preview_url") or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "reason": "no_live_site_url"}
    if device not in ("phone", "tablet", "desktop"):
        device = "phone"
    target = base + (route if route.startswith("/") else f"/{route}")

    from services.preview_capture import capture_screenshot, upload_receipt
    image = await capture_screenshot(target, device)
    if not image:
        return {"ok": False, "reason": "capture_unavailable", "url": target}
    key = await upload_receipt(
        image, f"{project_id}/{uuid.uuid4().hex}.jpg",
    )
    if not key:
        return {"ok": False, "reason": "storage_unavailable", "url": target}
    return {"ok": True, "receipt_key": key, "url": target, "device": device}


@router.get("/projects/{project_id}/preview/receipt/{receipt_key:path}")
async def get_preview_receipt(
    project_id: str,
    receipt_key: str,
    authorization: str = Header(None),
) -> StreamingResponse:
    """Authenticated proxy — stream a stored receipt JPEG back. Never
    a public/presigned URL (keeps a customer's live-site screenshots
    behind the SAME auth as the rest of their project)."""
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": me["user_id"]}, {"_id": 0, "project_id": 1},
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    if not receipt_key.startswith(f"deploy-receipts/{project_id}/"):
        raise HTTPException(403, "Receipt does not belong to this project")
    from services.preview_capture import fetch_receipt
    data = await fetch_receipt(receipt_key)
    if not data:
        raise HTTPException(404, "Receipt not found or expired")
    import io as _io
    return StreamingResponse(_io.BytesIO(data), media_type="image/jpeg")
