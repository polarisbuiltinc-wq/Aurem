"""
services/loop_engine_helpers.py — safe mechanical extraction from
services/loop_engine.py (2026-08-26 coverage-floor extraction batch).

Standalone helpers that DON'T touch `LoopEngine`'s internal state or the
module-level `_LIVE` in-process registry, moved out verbatim (zero logic
changes) to shrink loop_engine.py. `services/loop_engine.py` imports every
name below at module level, so internal call sites inside `LoopEngine`
methods (and `resume_stale`/`sweep_expired_awaiting_confirmations`, which
stay in loop_engine.py) keep resolving them as bare globals exactly as
before, and `from services.loop_engine import X` elsewhere keeps working
unchanged (re-export semantics).

NOTE (per founder direction, 2026-08-26): the 3570-line `LoopEngine` class
itself is explicitly OUT of scope for this pass — see PRD.md for the
dedicated future item.
"""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: Optional[datetime] = None) -> str:
    return (d or _now()).isoformat()


def _new_event(loop_id: str, state, phase: str,
               step: int = 0, total_steps: int = 0,
               message: str = "", data: Optional[dict] = None,
               requires_user_action: bool = False) -> dict:
    """Canonical SSE event shape (founder's 1.6 spec)."""
    return {
        "loop_id":              loop_id,
        "state":                state.value,
        "phase":                phase,
        "step":                 step,
        "total_steps":          total_steps,
        "message":              message,
        "data":                 data or {},
        "timestamp":            _iso(),
        "requires_user_action": requires_user_action,
    }


# ─── Persistence helpers ──────────────────────────────────────────────

async def _persist_session(db, doc: dict) -> None:
    """Upsert the session doc, bumping `updated_at` so the staleness
    watchdog can find orphans (G3)."""
    doc["updated_at"] = _now()
    await db.loop_sessions.update_one(
        {"loop_id": doc["loop_id"]},
        {"$set": doc, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )


async def _log_error(db, loop_id: str, phase: str, error: str,
                     context: Optional[dict] = None) -> None:
    """Append an entry to the loop_errors collection (G1)."""
    try:
        await db.loop_errors.insert_one({
            "loop_id":   loop_id,
            "phase":     phase,
            "error":     str(error)[:4000],
            "context":   (context or {}),
            "timestamp": _now(),
        })
    except Exception as e:  # noqa: BLE001
        # Never let logging itself break the loop.
        logger.error("loop_errors insert failed: %r", e)


# ─── Plan persistence (24h TTL via background sweeper) ────────────────

async def _save_plan(db, loop_id: str, plan: dict) -> None:
    # Lazy import — avoids a circular import with services.loop_engine
    # (which imports this module at load time).
    from services.loop_engine import PLAN_TTL_S
    await db.loop_plans.update_one(
        {"loop_id": loop_id},
        {"$set": {"loop_id": loop_id, "plan": plan,
                  "created_at": _now(),
                  "expires_at": _now() + _td(PLAN_TTL_S)}},
        upsert=True,
    )


def _td(seconds: int):  # noqa: ANN201
    from datetime import timedelta
    return timedelta(seconds=seconds)


# ─── File backup (G4) ─────────────────────────────────────────────────

async def record_backup(db, loop_id: str, path: str, content: str) -> None:
    """Store pre-write copy so abort can restore the file.  Phase C
    wires this into the actual write path."""
    await db.loop_backups.insert_one({
        "loop_id":   loop_id,
        "path":      path,
        "content":   content,
        "timestamp": _now(),
    })


async def rollback(db, loop_id: str) -> list[dict]:
    """Return every backed-up (path, content) pair so the caller can
    restore them.  Engine itself never writes user files — Phase C's
    verify/execute flow will hand the list to a GitHub writer."""
    cur = db.loop_backups.find({"loop_id": loop_id})
    return [{"path": d["path"], "content": d["content"]} async for d in cur]


def new_loop_id() -> str:
    return f"loop_{uuid.uuid4().hex[:14]}"


async def load_session(db, loop_id: str) -> Optional[dict]:
    return await db.loop_sessions.find_one(
        {"loop_id": loop_id}, {"_id": 0},
    )


def _commit_message(user_msg: str) -> str:
    """Auto-derive a Conventional-Commit style message from the user's
    original request.  Phase C may swap in an LLM-written subject."""
    summary = (user_msg or "ORA update").strip().splitlines()[0][:60]
    return f"feat(ora): {summary} [loop-verified]"


async def _run_security_scan(user_id: str,
                             project_id: Optional[str]) -> dict:
    """Re-use the real security_scan logic in-process.  Phase C builds
    a synthetic Body+Header pair and dispatches `run_security_scan`
    directly so we get the same 13-rule output the UI's manual scan
    produces — no skipping, no auth dance.

    Returns the engine-friendly summary shape (or a stub if the user
    has no connected repo, which is the legitimate not-applicable
    case)."""
    if not project_id:
        return {"summary": {"total": 0, "by_severity": {}},
                "skipped_reason": "no_project"}
    # Pull the project's encrypted PAT + repo coords and reuse the
    # scanner's lower-level helpers so we don't need a JWT.
    from cto_services.db import get_db
    # Iter 319 · Bug 3 — restore the missing `_scan_text` import.
    # This function references `_scan_text(...)` at the scan_one
    # inner call site; the import block that once carried it was
    # truncated, producing NameError in every scan on every loop.
    # Iter arch-2a — now sourced from services/security_text_scanner.py
    # (was a service→router boundary violation importing from the
    # router; the rule table + matcher moved there verbatim).
    from services.security_text_scanner import _scan_text
    import httpx, asyncio as _asyncio
    db = get_db()
    if db is None:
        return {"summary": {"total": 0, "by_severity": {}},
                "skipped_reason": "no_db"}
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "github_owner": 1, "github_repo": 1, "github_token": 1,
         "auth_method": 1, "installation_id": 1, "user_id": 1},
    )
    if not proj:
        return {"summary": {"total": 0, "by_severity": {}},
                "skipped_reason": "no_project_doc"}
    owner = proj.get("github_owner") or ""
    repo  = proj.get("github_repo")  or ""
    # 2026-06 PAT-removal — App-only.
    from services.pat_vault import get_repo_token_or_error
    pat, _auth_err, _ = await get_repo_token_or_error(proj)
    if not (owner and repo and pat):
        return {"summary": {"total": 0, "by_severity": {}},
                "skipped_reason": _auth_err or "no_github_linkage"}
    # Re-run a trimmed scan inline — same rule library, capped 200
    # files for the engine path to keep phase budget under 120s.
    # arch: allow-router-import — scanner internals live in security_scan router until phase-4 refactor
    from routers.security_scan import (
        _list_repo_tree, _fetch_file, _SCAN_EXTS, _SKIP_DIRS,
        _MAX_BYTES_PER_FILE,
    )
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            blobs = await _list_repo_tree(client, owner, repo, pat)
        except Exception as e:                          # noqa: BLE001
            return {"summary": {"total": 0, "by_severity": {}},
                    "scan_error": repr(e)}
        candidates: list[dict] = []
        for b in blobs:
            path = b.get("path", "")
            parts = path.split("/")
            if any(p in _SKIP_DIRS for p in parts):
                continue
            if not any(path.lower().endswith(e) for e in _SCAN_EXTS):
                continue
            if b.get("size", 0) > _MAX_BYTES_PER_FILE:
                continue
            candidates.append(b)
            if len(candidates) >= 200:
                break
        sem = _asyncio.Semaphore(8)
        async def _scan_one(b):
            async with sem:
                text = await _fetch_file(client, owner, repo, b["path"], pat)
            return _scan_text(b["path"], text) if text else []
        all_findings = []
        for sub in await _asyncio.gather(
            *[_scan_one(b) for b in candidates], return_exceptions=False,
        ):
            all_findings.extend(sub)
    by_sev: dict[str, int] = {}
    for f in all_findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    return {
        "summary":       {"total": len(all_findings), "by_severity": by_sev},
        "findings":      all_findings[:100],
        "scanned_files": len(candidates),
    }


# Iter 212m-132 — Diff-only Loop scan helper.
async def _run_diff_security_scan(
    db, user_id: str, project_id: Optional[str],
    submitted_files: list[dict],
) -> dict:
    """Scan ONLY the files this loop just touched, and only flag
    findings whose line was added or modified by the patch.

    Returns a shape compatible with `_do_scan`'s consumers:
        {
          "summary":   {"total": N, "by_severity": {...}},
          "findings":  [...],
          "diff_mode": True,
          "scanned_files": M,
          "preexisting_skipped": K,
        }

    Why this exists (founder report — iter 212m-132):
      The full-repo scan in `_run_security_scan` was flagging
      pre-existing vulns in files the Loop never touched, blocking
      every commit at the SCAN phase even when the patch itself
      was clean.  We now restrict the scan to the patch surface AND
      use `vanguard_verify_agent.changed_lines_for_file` to drop
      findings outside the changed-line set.
    """
    if not submitted_files:
        return {"summary": {"total": 0, "by_severity": {}},
                "findings": [], "diff_mode": True,
                "skipped_reason": "no_submitted_files"}
    if not project_id:
        return {"summary": {"total": 0, "by_severity": {}},
                "findings": [], "diff_mode": True,
                "skipped_reason": "no_project"}
    if db is None:
        return {"summary": {"total": 0, "by_severity": {}},
                "findings": [], "diff_mode": True,
                "skipped_reason": "no_db"}

    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "github_owner": 1, "github_repo": 1,
         "github_branch": 1, "github_token": 1,
         "auth_method": 1, "installation_id": 1, "user_id": 1},
    )
    if not proj:
        return {"summary": {"total": 0, "by_severity": {}},
                "findings": [], "diff_mode": True,
                "skipped_reason": "no_project_doc"}

    # 2026-02-11 · Phase 3b (Bug 2 fix) — dual-auth token resolver.
    from services.pat_vault import get_repo_token
    # Iter 319 · Bug 3 — restore the missing `_scan_text` import
    # for the diff-only scan path. Same defect as `_run_security_scan`.
    # Iter arch-2a — now sourced from services/security_text_scanner.py
    # (was a service→router boundary violation; see note above).
    from services.security_text_scanner import _scan_text
    from services.github_api_writer import fetch_file as gh_fetch
    from services.vanguard_verify_agent import (
        changed_lines_for_file, filter_findings_to_changed_lines,
    )

    owner  = proj.get("github_owner") or ""
    repo   = proj.get("github_repo")  or ""
    branch = proj.get("github_branch") or "main"
    pat    = await get_repo_token(proj)
    if not (owner and repo and pat):
        return {"summary": {"total": 0, "by_severity": {}},
                "findings": [], "diff_mode": True,
                "skipped_reason": "no_github_linkage"}

    # Fetch the BASE content for each changed file (the head SHA on
    # the loop branch).  If a file doesn't exist on the base (new
    # file), `base` stays empty → every line counts as "changed".
    line_map: dict[str, set[int]] = {}
    findings: list[dict] = []
    skipped_preexisting = 0
    for f in submitted_files:
        path = f.get("path") or ""
        new_content = f.get("content") or ""
        if not path or not new_content:
            continue
        try:
            base_content = await gh_fetch(owner, repo, path, branch, pat)
        except Exception as e:                       # noqa: BLE001
            logger.debug("diff scan base fetch failed %s: %r", path, e)
            base_content = None
        base = base_content or ""
        # Compute changed-line set.
        line_map[path] = changed_lines_for_file(base, new_content)
        # Run the regex scan on the NEW content.
        raw = _scan_text(path, new_content)
        findings.extend(raw)

    # Drop pre-existing findings.
    kept, dropped = filter_findings_to_changed_lines(findings, line_map)
    skipped_preexisting = len(dropped)

    # Build severity histogram on kept findings only.
    sev: dict[str, int] = {
        "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
    }
    for fnd in kept:
        s = (fnd.get("severity") or "").lower()
        if s in sev:
            sev[s] += 1

    return {
        "summary":              {"total": len(kept), "by_severity": sev},
        "findings":             kept,
        "diff_mode":            True,
        "scanned_files":        len(submitted_files),
        "preexisting_skipped":  skipped_preexisting,
    }
