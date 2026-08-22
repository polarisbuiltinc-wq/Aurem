"""services/rollback_two_phase.py — Pillar 1 (founder-approved 2026-06).

Two-phase, snapshot-based rollback:
  Phase 1 `preview_rollback`  — computes a real per-file diff between the
    snapshot's byte-exact pre-ship contents and the branch's CURRENT
    contents; issues a single-use preview_token (15-min expiry).
  Phase 2 `execute_rollback_from_snapshot` — requires the matching
    unexpired token + explicit confirm; restores snapshot contents via a
    real commit, then independently re-fetches and hash-verifies the
    restored state. Every attempt (success or failure) lands in the
    `rollback_attempts` ledger.
"""
from __future__ import annotations

import difflib
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("aurem.rollback_two_phase")

PREVIEW_TTL_MIN = 15
_DIFF_LINE_CAP = 400


def _now():
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat()


async def _current_contents(owner, repo, branch, token, paths):
    from services.github_api_writer import fetch_file
    # fetch_file returns None for missing paths (does not raise).
    return {p: await fetch_file(owner, repo, p, branch, token)
            for p in paths}


async def preview_rollback(db, *, snapshot_id: str, token: str) -> dict:
    from services.rollback_snapshot import load_snapshot

    snap = await load_snapshot(db, snapshot_id)
    if not snap:
        return {"ok": False, "reason": "snapshot_not_found",
                "snapshot_id": snapshot_id}

    owner, repo = snap["repo"].split("/", 1)
    branch = snap["branch"]
    paths = list(snap["files"].keys())
    current = await _current_contents(owner, repo, branch, token, paths)

    files_out, changed = [], 0
    for path in paths:
        pre = snap["files"][path]
        cur = current[path]
        if pre["present"] and cur is not None:
            if pre["content"] == cur:
                status = "unchanged"
            else:
                status = "would_restore"
        elif pre["present"] and cur is None:
            status = "would_restore"          # deleted since snapshot
        elif not pre["present"] and cur is not None:
            status = "added_since_snapshot"   # restore cannot delete (limitation, recorded)
        else:
            status = "unchanged"
        diff_lines = []
        if status == "would_restore":
            changed += 1
            diff_lines = list(difflib.unified_diff(
                (cur or "").splitlines(), (pre["content"] or "").splitlines(),
                fromfile=f"current/{path}", tofile=f"snapshot/{path}",
                lineterm="",
            ))[:_DIFF_LINE_CAP]
        files_out.append({"path": path, "status": status,
                          "diff": "\n".join(diff_lines)})

    preview_token = uuid.uuid4().hex
    await db.rollback_previews.insert_one({
        "preview_token": preview_token,
        "snapshot_id":   snapshot_id,
        "created_at":    _iso(),
        "expires_at":    (_now() + timedelta(minutes=PREVIEW_TTL_MIN)).isoformat(),
        "used":          False,
        "files_changed": changed,
    })
    return {
        "ok":            True,
        "snapshot_id":   snapshot_id,
        "repo":          snap["repo"],
        "branch":        branch,
        "base_commit_sha": snap["base_commit_sha"],
        "files":         files_out,
        "files_changed": changed,
        "preview_token": preview_token,
        "expires_in_min": PREVIEW_TTL_MIN,
        "note": ("Execute requires this preview_token + confirm=true. "
                 "Files added after the snapshot are NOT deleted by a "
                 "restore (recorded per-file as added_since_snapshot)."),
    }


async def execute_rollback_from_snapshot(
    db, *, snapshot_id: str, preview_token: str,
    initiated_by: str, token: str, confirm: bool = False,
) -> dict:
    from services.rollback_snapshot import load_snapshot, _sha256

    attempt_id = f"rba_{uuid.uuid4().hex[:16]}"

    async def _ledger(result: str, **extra):
        await db.rollback_attempts.update_one(
            {"attempt_id": attempt_id},
            {"$set": {"result": result, "finished_at": _iso(), **extra}},
        )

    if not confirm:
        return {"ok": False, "reason": "confirm_required",
                "hint": "Pass confirm=true — phase-2 commit is explicit."}
    if not token:
        return {"ok": False, "reason": "no_write_token_configured",
                "hint": "Set AUREM_DRILL_TOKEN (or GITHUB_ACTIONS_TOKEN) "
                        "with contents:write for the target repo."}

    prev = await db.rollback_previews.find_one({"preview_token": preview_token})
    if not prev or prev.get("snapshot_id") != snapshot_id:
        return {"ok": False, "reason": "preview_token_invalid"}
    if prev.get("used"):
        return {"ok": False, "reason": "preview_token_already_used"}
    try:
        expired = _now() > datetime.fromisoformat(prev.get("expires_at", ""))
    except ValueError:
        expired = True
    if expired:
        return {"ok": False, "reason": "preview_token_expired",
                "hint": "Re-run preview — state may have drifted."}

    snap = await load_snapshot(db, snapshot_id)
    if not snap:
        return {"ok": False, "reason": "snapshot_not_found"}

    await db.rollback_attempts.insert_one({
        "attempt_id":    attempt_id,
        "snapshot_id":   snapshot_id,
        "mechanism":     "snapshot_restore",
        "initiated_by":  initiated_by,
        "result":        "running",
        "failure_reason": None,
        "timestamp":     _iso(),
    })
    await db.rollback_previews.update_one(
        {"preview_token": preview_token}, {"$set": {"used": True}})

    owner, repo = snap["repo"].split("/", 1)
    branch = snap["branch"]
    restore = {p: f["content"] for p, f in snap["files"].items()
               if f["present"]}
    skipped_additions = [p for p, f in snap["files"].items()
                         if not f["present"]]
    if not restore:
        await _ledger("failed", failure_reason="nothing_to_restore")
        return {"ok": False, "reason": "nothing_to_restore",
                "attempt_id": attempt_id}

    try:
        from services.github_api_writer import commit_files, fetch_file
        res = await commit_files(
            owner=owner, repo=repo, branch=branch, token=token,
            files=restore,
            commit_message=(f"revert: restore snapshot {snapshot_id} "
                            f"(pre-ship state @ {snap['base_commit_sha'][:7]}) "
                            f"[AUREM rollback attempt {attempt_id}]"),
            author_name="AUREM Rollback",
            author_email="aurem-rollback@users.noreply.github.com",
        )
        restored_sha = res.get("sha") or ""

        # Independent read-back verification — never trust the write API.
        mismatches = []
        for path, content in restore.items():
            after = await fetch_file(owner, repo, path, branch, token)
            if after is None or _sha256(after) != _sha256(content):
                mismatches.append({"path": path, "error": "hash_mismatch"
                                   if after is not None else "missing_after_restore"})
        verified = not mismatches

        await _ledger("success" if verified else "restored_unverified",
                      verified=verified,
                      restored_commit_sha=restored_sha,
                      mismatches=mismatches,
                      skipped_additions=skipped_additions)
        return {"ok": True, "attempt_id": attempt_id,
                "restored_commit_sha": restored_sha,
                "verified": verified, "mismatches": mismatches,
                "files_restored": len(restore),
                "skipped_additions": skipped_additions}
    except Exception as e:  # noqa: BLE001
        reason = repr(e)
        if token:
            reason = reason.replace(token, "***TOKEN***")
        # Belt-and-braces: scrub any GitHub-token-shaped fragment too.
        reason = re.sub(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{4,}",
                        "***TOKEN***", reason)
        reason = re.sub(r"\bgithub_pat_[A-Za-z0-9_]{4,}", "***TOKEN***", reason)
        logger.exception("[rollback2 %s] execute failed", attempt_id)
        await _ledger("failed", failure_reason=reason[:500])
        return {"ok": False, "reason": "restore_commit_failed",
                "attempt_id": attempt_id, "detail": reason[:300]}
