"""services/rollback_snapshot.py — Pillar 1 (founder-approved 2026-06).

Application-level pre-ship snapshot, independent of git history:
  • byte-exact contents of every file a ship will touch (fetched from
    the repo BEFORE the ship commit lands),
  • relevant DB state (loop session + sanitized project doc),
  • non-secret env/config fields,
stored gzip-JSON to Cloudflare R2 (reuses db_backup's client) under a
unique snapshot_id, indexed in Mongo `rollback_snapshots`.

Zero mocks: real GitHub reads, real R2 writes, real Mongo rows.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("aurem.rollback_snapshot")

R2_SNAP_PREFIX = "rollback_snapshots/"

# Project fields safe to capture as "env config" (never secrets/PATs).
_PROJECT_CONFIG_FIELDS = (
    "project_id", "github_owner", "github_repo", "branch",
    "auth_method", "default_branch", "language", "framework",
)


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


async def _capture_files(owner: str, repo: str, branch: str, token: str,
                          file_paths: list[str]) -> dict:
    """Fetch each path's current content; records absence (path added
    later) instead of raising so a restore always has a clean signal.

    Part B · W3 · 2026-08 — fetch_file() now raises BinaryFileError/
    UnsupportedEncodingError for content it can't safely decode
    (previously silently corrupted it). Snapshotting a binary file is
    not a scenario this pillar builds full support for this pass —
    caught here and recorded the same as "absent" so this function's
    documented "never raises" contract still holds for callers."""
    from services.github_api_writer import fetch_file
    from core.errors import BinaryFileError, UnsupportedEncodingError
    files: dict = {}
    for path in file_paths:
        try:
            content = await fetch_file(owner, repo, path, branch, token)
        except (BinaryFileError, UnsupportedEncodingError):
            files[path] = {"present": False, "content": None,
                           "sha256": None, "not_editable": True}
            continue
        if content is None:
            files[path] = {"present": False, "content": None, "sha256": None}
        else:
            files[path] = {"present": True, "content": content,
                           "sha256": _sha256(content)}
    return files


async def create_snapshot(
    db,
    *,
    owner: str,
    repo: str,
    branch: str,
    token: str,
    file_paths: list[str],
    user_id: str = "",
    project_id: str = "",
    loop_id: str = "",
    trigger: str = "manual",
) -> dict:
    """Capture pre-change state and persist it. Raises on failure —
    callers decide whether to fail open (ship path) or closed (API)."""
    from services.github_api_writer import _get_branch_head

    snapshot_id = f"snap_{uuid.uuid4().hex[:20]}"
    head = await _get_branch_head(owner, repo, branch, token)
    base_sha = head["sha"] if isinstance(head, dict) else str(head)

    files = await _capture_files(owner, repo, branch, token, file_paths)

    db_state: dict = {}
    if loop_id:
        sess = await db.loop_sessions.find_one({"loop_id": loop_id}) or {}
        sess.pop("_id", None)
        db_state["loop_session"] = json.loads(json.dumps(sess, default=str))
    if project_id and user_id:
        proj = await db.cto_projects.find_one(
            {"project_id": project_id, "user_id": user_id},
            {f: 1 for f in _PROJECT_CONFIG_FIELDS},
        ) or {}
        proj.pop("_id", None)
        db_state["project_config"] = json.loads(json.dumps(proj, default=str))

    payload = {
        "snapshot_id":     snapshot_id,
        "captured_at":     _iso(),
        "trigger":         trigger,
        "repo":            f"{owner}/{repo}",
        "branch":          branch,
        "base_commit_sha": base_sha,
        "files":           files,
        "db_state":        db_state,
        "project_config_snapshot": db_state.get("project_config", {}),
    }

    raw = gzip.compress(json.dumps(payload).encode())
    r2_key = f"{R2_SNAP_PREFIX}{snapshot_id}.json.gz"

    from services.db_backup import _r2_client
    client = _r2_client()
    bucket = os.environ["R2_BUCKET"]
    await asyncio.to_thread(
        client.put_object, Bucket=bucket, Key=r2_key, Body=raw,
        ContentType="application/gzip",
    )

    row = {
        "snapshot_id":     snapshot_id,
        "r2_key":          r2_key,
        "r2_bucket":       bucket,
        "size_bytes":      len(raw),
        "repo":            f"{owner}/{repo}",
        "branch":          branch,
        "base_commit_sha": base_sha,
        "file_manifest":   [
            {"path": p, "present": f["present"], "sha256": f["sha256"]}
            for p, f in files.items()
        ],
        "trigger":         trigger,
        "user_id":         user_id,
        "project_id":      project_id,
        "loop_id":         loop_id,
        "created_at":      _iso(),
    }
    await db.rollback_snapshots.insert_one(dict(row))
    logger.info("[snapshot] %s captured %d file(s) from %s/%s@%s → r2:%s",
                snapshot_id, len(files), owner, repo, branch, r2_key)
    row.pop("_id", None)
    return row


async def load_snapshot(db, snapshot_id: str) -> Optional[dict]:
    """Fetch the full snapshot payload back from R2. None if unknown."""
    row = await db.rollback_snapshots.find_one({"snapshot_id": snapshot_id})
    if not row:
        return None
    from services.db_backup import _r2_client
    client = _r2_client()
    obj = await asyncio.to_thread(
        client.get_object, Bucket=row["r2_bucket"], Key=row["r2_key"],
    )
    raw = await asyncio.to_thread(obj["Body"].read)
    return json.loads(gzip.decompress(raw))
