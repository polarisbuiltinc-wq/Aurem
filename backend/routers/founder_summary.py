"""routers/founder_summary.py — 2026-08-24 (Pillar 6, Production-Readiness)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from cto_services.auth import require_admin_dep
from cto_services.db import get_db
from services.founder_summary import generate_founder_summary

router = APIRouter(prefix="/admin/founder-summary", tags=["founder-summary"])


class GenerateRequest(BaseModel):
    source: str
    technical_event: dict
    event_id: str | None = None


@router.post("/generate", dependencies=[Depends(require_admin_dep)])
async def generate(body: GenerateRequest) -> dict:
    db = get_db()
    return await generate_founder_summary(
        db, event_id=body.event_id, source=body.source,
        technical_event=body.technical_event,
    )


@router.get("/{event_id}", dependencies=[Depends(require_admin_dep)])
async def get_summary(event_id: str, view: str = "founder") -> dict:
    db = get_db()
    doc = await db.event_summaries.find_one({"event_id": event_id})
    if not doc:
        return {"error": "not_found"}
    doc.pop("_id", None)
    if view == "technical":
        return {"event_id": event_id, "view": "technical", "data": doc["technical_view"]}
    return {"event_id": event_id, "view": "founder", "data": doc["founder_view"]}
