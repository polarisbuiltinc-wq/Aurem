"""
routers/cto_projects/graph.py — AUREM CTO Projects.
Codebase Graph endpoints (hybrid regex + LLM top-20): build/read,
Mermaid diagram, guided tour, search, diff-impact analysis.

Split from the former monolithic routers/cto_projects.py on
2026-09-08 (responsibility-based extraction, no logic change).
Uses `_pkg.<name>` for anything patched at the package level by the
existing test suite (`current_dev`, `get_db`) — see preview.py's
module docstring for why.
"""
import asyncio
import logging
from typing import Optional

from fastapi import Header, HTTPException

import routers.cto_projects as _pkg
from . import router

logger = logging.getLogger(__name__)


# Iter 165 — Codebase Graph endpoints (hybrid regex + LLM top-20).


@router.post("/projects/{project_id}/build-graph")
async def build_project_graph(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Trigger a hybrid (regex + LLM-top-20) graph build in background.
    Returns immediately — frontend polls `/projects/{id}/graph` until
    `status == 'ready'`."""
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
    if not (gh_token and gh_owner and gh_repo):
        raise HTTPException(400, "GitHub not connected")
    from services.graph_builder import build_graph
    asyncio.create_task(build_graph(
        db, project_id, user_id, gh_token, gh_owner, gh_repo,
    ))
    return {"ok": True, "message": "Graph building in background"}


@router.get("/projects/{project_id}/graph")
async def get_project_graph(
    project_id: str,
    full: bool = False,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Light read by default (excludes heavy `nodes` field). Pass
    `?full=true` to fetch the full expanded graph for FE detail view.

    Iter 212m-113 — Per-project gating is enforced by the
    {project_id, user_id} compound key in `get_graph` / `get_graph_full`.
    Cross-repo data leak is impossible — a request with another user's
    project_id returns {status:'not_built'}."""
    me = await _pkg.current_dev(authorization)
    user_id = me["user_id"]
    db = _pkg.get_db()
    from services.graph_builder import get_graph, get_graph_full
    doc = (
        await get_graph_full(db, project_id, user_id)
        if full
        else await get_graph(db, project_id, user_id)
    )
    if not doc:
        return {"ok": True, "status": "not_built", "graph": None}
    return {"ok": True, "status": "ready", "graph": doc}


# Iter 212m-215 — Mermaid architecture diagram (GitDiagram approach)
# ------------------------------------------------------------------
# Two-step LLM pipeline (Gemini 2.5 Flash via OpenRouter) that turns
# the graph_builder output into an interactive Mermaid flowchart.
# The code is cached inside `project_graphs.mermaid_code` so the
# viewer path is a single Mongo read; users only trigger a
# regeneration on explicit "Regenerate diagram" click OR when the
# underlying graph has a new `tree_sha`.
@router.post("/projects/{project_id}/graph/mermaid")
async def build_project_mermaid(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    me = await _pkg.current_dev(authorization)
    user_id = me["user_id"]
    db = _pkg.get_db()
    from services.mermaid_diagram import build_and_persist_mermaid
    out = await build_and_persist_mermaid(db, project_id, user_id)
    if not out.get("ok"):
        raise HTTPException(status_code=400,
                            detail=out.get("reason") or "diagram build failed")
    return out


@router.get("/projects/{project_id}/graph/tour")
async def get_graph_tour(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Iter 212m-113 — Guided onboarding tour. Returns a
    dependency-ordered walkthrough of the project's most important
    files (API → Service → Data → UI). Gated by project_id+user_id
    just like /graph. Zero new LLM cost — reads cached descriptions
    from the existing graph doc."""
    me = await _pkg.current_dev(authorization)
    user_id = me["user_id"]
    db = _pkg.get_db()
    from services.graph_builder import get_graph_full
    doc = await get_graph_full(db, project_id, user_id)
    if not doc:
        return {"ok": True, "status": "not_built", "tour": []}
    nodes = doc.get("nodes") or {}
    layers = doc.get("layers") or {}
    # Dependency order: API entry-points first (they call services,
    # services call data). UI shown last for full-stack apps.
    order = ["Config", "Data", "Service", "API", "Hook", "UI", "Util"]
    tour: list[dict] = []
    for layer in order:
        for path in (layers.get(layer) or [])[:3]:
            node = nodes.get(path) or {}
            tour.append({
                "step":        len(tour) + 1,
                "layer":       layer,
                "path":        path,
                "description": node.get("description") or "",
                "symbols":     (node.get("symbols") or [])[:5],
            })
            if len(tour) >= 12:
                break
        if len(tour) >= 12:
            break
    return {"ok": True, "status": "ready", "tour": tour,
            "project_id": project_id, "file_count": doc.get("file_count", 0)}


@router.get("/projects/{project_id}/graph/search")
async def search_graph(
    project_id: str,
    q: str = "",
    limit: int = 20,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Iter 212m-113 — Fuzzy-ish search across paths + descriptions +
    symbols. Pure server-side scoring (no LLM). Gated by
    project_id+user_id. Returns top-N matches with a relevance score."""
    me = await _pkg.current_dev(authorization)
    user_id = me["user_id"]
    db = _pkg.get_db()
    from services.graph_builder import get_graph_full
    doc = await get_graph_full(db, project_id, user_id)
    if not doc:
        return {"ok": True, "status": "not_built", "results": []}
    q = (q or "").strip().lower()
    if not q:
        return {"ok": True, "results": []}
    nodes = doc.get("nodes") or {}
    hits: list[dict] = []
    for path, node in nodes.items():
        path_l   = path.lower()
        desc_l   = (node.get("description") or "").lower()
        syms_l   = [s.lower() for s in (node.get("symbols") or [])]
        score = 0
        if q in path_l.rsplit("/", 1)[-1]:
            score += 100
        if path_l.endswith(q):
            score += 50
        if q in path_l:
            score += 25
        if q in desc_l:
            score += 30
        if any(q == s for s in syms_l):
            score += 80
        if any(q in s for s in syms_l):
            score += 20
        if score > 0:
            hits.append({
                "path":        path,
                "layer":       node.get("layer"),
                "description": node.get("description") or "",
                "symbols":     (node.get("symbols") or [])[:5],
                "score":       score,
            })
    hits.sort(key=lambda h: h["score"], reverse=True)
    return {"ok": True, "results": hits[:max(1, min(limit, 100))],
            "query": q, "total_matches": len(hits)}


@router.post("/projects/{project_id}/graph/impact")
async def graph_impact(
    project_id: str,
    body: dict,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Iter 212m-113 — Diff Impact Analysis. Body: {files: [paths]}.
    Returns the transitive set of files that import the given set
    (one hop, capped at 50). Useful right before a Loop Mode ship to
    surface the blast radius. Gated by project_id+user_id, no LLM."""
    me = await _pkg.current_dev(authorization)
    user_id = me["user_id"]
    db = _pkg.get_db()
    files = list((body or {}).get("files") or [])
    if not files:
        raise HTTPException(400, "files[] required")
    from services.graph_builder import get_graph_full
    doc = await get_graph_full(db, project_id, user_id)
    if not doc:
        return {"ok": True, "status": "not_built", "impacted": []}
    edges = doc.get("edges") or []
    target_set = set(files)
    impacted: dict[str, list[str]] = {}
    for e in edges:
        src = e.get("from")
        dst = e.get("to")
        if dst in target_set and src not in target_set:
            impacted.setdefault(src, []).append(dst)
        if len(impacted) >= 50:
            break
    out = [
        {"path": p, "reason": f"imports {', '.join(deps[:3])}"
                              + (f" +{len(deps) - 3} more" if len(deps) > 3 else "")}
        for p, deps in impacted.items()
    ]
    return {"ok": True, "changed": files,
            "impacted": out, "blast_radius": len(out)}
