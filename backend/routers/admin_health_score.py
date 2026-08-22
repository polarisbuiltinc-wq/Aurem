"""
routers/admin_health_score.py — Codebase Health Score endpoints
(2026-08-23). Admin-only. Every score is computed from real evidence
in `services/health_score.py` — this router just exposes it plus the
two write actions (trigger a coverage run, log an architecture review).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header

from cto_services.auth import require_admin_dep
from cto_services.db import get_db
from services.health_score import get_health_score

router = APIRouter(
    prefix="/admin/health-score",
    tags=["Admin-Health-Score"],
    dependencies=[Depends(require_admin_dep)],
)


@router.get("")
async def get_score() -> dict:
    db = get_db()
    return await get_health_score(db)


@router.post("/test-coverage/run")
async def trigger_coverage_run(authorization: Optional[str] = Header(None)) -> dict:
    """Fire-and-forget — a full pytest+coverage run takes minutes, well
    past this environment's ingress upstream timeout, so we launch it
    detached and let the admin poll GET /health-score for the fresh
    result (health_test_coverage_runs.generated_at) instead of holding
    the HTTP connection open."""
    import asyncio
    from services.health_coverage_scan import run_coverage_scan
    db = get_db()
    asyncio.create_task(run_coverage_scan(db))
    return {"status": "started", "note": "runs in background — poll GET /health-score, "
                                          "expect a fresh test_coverage.last_verified in a few minutes"}


@router.get("/architecture-review")
async def list_architecture_reviews(limit: int = 10) -> dict:
    db = get_db()
    if db is None:
        return {"reviews": []}
    rows = await db.architecture_review_log.find(
        {}, {"_id": 0},
    ).sort("date", -1).to_list(max(1, min(limit, 50)))
    return {"reviews": rows}


@router.post("/architecture-review")
async def submit_architecture_review(
    body: dict = Body(...),
    authorization: Optional[str] = Header(None),
) -> dict:
    """Body: {reviewer, notes, rubric: {coupling: 0-100, spof: 0-100, ...}}
    Real qualitative entries only — no default/placeholder rubric is
    ever auto-generated, per the founder's no-fabrication constraint."""
    db = get_db()
    rubric = body.get("rubric") or {}
    if not isinstance(rubric, dict) or not rubric:
        return {"ok": False, "error": "rubric dict with at least one numeric score is required"}
    doc = {
        "date": datetime.now(timezone.utc).isoformat(),
        "reviewer": body.get("reviewer") or "unknown",
        "notes": body.get("notes") or "",
        "rubric": rubric,
    }
    if db is not None:
        await db.architecture_review_log.insert_one(dict(doc))
    return {"ok": True, "review": doc}
