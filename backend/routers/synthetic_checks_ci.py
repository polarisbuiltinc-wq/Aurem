"""
routers/synthetic_checks_ci.py — G1/G15 CI result ingestion (2026-08-20)

Same shared-secret auth pattern as routers/vanguard_ci.py (reuses the
same `AUREM_CI_INGEST_TOKEN` — no new secret, no DB credentials touch
CI). Replaces the previous design where g1_route_smoke_sweep.py /
g15_dependency_scan.py connected directly to `MONGO_URL`: every CI
workflow hardcodes `MONGO_URL=mongodb://localhost:27017` (a throwaway
service inside the ephemeral GitHub-hosted runner), so those writes
never reached the real app database — /admin/status/all's G1/G15
checks read `synthetic_checks` on the REAL db and could never see
anything but "no runs yet" (permanently gray), no matter how many
times CI actually ran.

POST /admin/synthetic-checks/ingest — ingest a g1 or g15 run result
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from cto_services.db import get_db
from routers.vanguard_ci import _verify_ci_auth

router = APIRouter(prefix="/admin/synthetic-checks", tags=["Admin-CI-ingest"])

# Keys the health-registry adapters (services/health_checks.py) read
# per kind — see _check_g1_route_sweep / _check_g15_deps.
_ALLOWED_KINDS = ("g1_route_sweep", "g15_dep_scan")
_MAX_LIST_ITEMS = 500  # cap so a runaway scan can't bloat Mongo


@router.post("/ingest")
async def ingest_synthetic_check(
    body: dict,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Body shape mirrors what the scripts used to write directly:
      g1_route_sweep:  {kind, base_url, total, failed, results:[...]}
      g15_dep_scan:    {kind, total_findings, high_critical, findings:[...]}
    """
    _verify_ci_auth(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    kind = (body.get("kind") or "").strip()
    if kind not in _ALLOWED_KINDS:
        raise HTTPException(400, f"unsupported kind: {kind!r}")

    doc: dict = {"kind": kind, "finished_at": datetime.now(timezone.utc)}
    if kind == "g1_route_sweep":
        doc["base_url"] = str(body.get("base_url") or "")[:256]
        doc["total"] = int(body.get("total") or 0)
        doc["failed"] = int(body.get("failed") or 0)
        doc["results"] = (body.get("results") or [])[:_MAX_LIST_ITEMS]
    else:
        doc["total_findings"] = int(body.get("total_findings") or 0)
        doc["high_critical"] = int(body.get("high_critical") or 0)
        doc["findings"] = (body.get("findings") or [])[:_MAX_LIST_ITEMS]

    result = await db.synthetic_checks.insert_one(doc)
    return {"ok": True, "id": str(result.inserted_id)}
