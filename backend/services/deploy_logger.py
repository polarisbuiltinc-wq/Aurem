"""Deploy event logging — Iter 289.5

Captures the running build's commit SHA and records a `deploy_events` doc
once per (server boot × commit_sha) so the Founder Timeline can render
"View commit →" links and the weekly board digest can include deploy diff.

Idempotency:
  Each (commit_sha, boot_id) tuple inserts AT MOST ONE doc. boot_id is unique
  per process start, so a hot-reload triggers exactly one entry.
  If the same commit is deployed twice (e.g. rollback then re-deploy), each
  boot still records a separate event — that's the correct behaviour, the
  Timeline shows distinct deploys.
"""
from __future__ import annotations

import os
import logging
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("deploy-events")

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DEFAULT_REPO = os.environ.get("AUREM_GITHUB_REPO", "polarisbuiltinc-wq/auremdev")
_BOOT_ID = uuid.uuid4().hex[:12]

# Iter 388z · Deploy Insights Panel · Option B follow-up.  The prod
# container strips .git, so `git rev-parse HEAD` returns None on prod
# and log_deploy_event() was giving up at that point ("no commit sha
# resolvable — skip").  That left app.state.deploy_event unset →
# /api/health kept surfacing stale build_hash / built_at from an old
# deploy_events row.  Extended the cascade to match what
# routers/version.py::_read_commit() already does successfully on
# prod: read BUILD_INFO.txt (post-commit hook stamps the fresh SHA
# there; the deploy pipeline snapshots the file with the build).
_BUILD_INFO_PATH_BACKEND = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "BUILD_INFO.txt")
)
_BUILD_INFO_PATH_REPO = os.path.join(_REPO_ROOT, "BUILD_INFO.txt")


def _safe_run(cmd: list[str]) -> Optional[str]:
    try:
        out = subprocess.check_output(cmd, cwd=_REPO_ROOT, stderr=subprocess.DEVNULL, timeout=4)
        return out.decode().strip() or None
    except Exception:
        return None


def _read_build_info_sha() -> Optional[str]:
    """Read the SHA from backend/BUILD_INFO.txt (post-commit hook
    output) — the fallback that /api/health/version cascade uses on
    prod when .git is stripped."""
    for p in (_BUILD_INFO_PATH_BACKEND, _BUILD_INFO_PATH_REPO):
        try:
            if os.path.exists(p):
                s = open(p, "r", encoding="utf-8").read().strip()
                # BUILD_INFO.txt content is just the raw SHA (7-40 hex).
                s = s.split()[0] if s else ""
                if s and all(c in "0123456789abcdef" for c in s.lower()):
                    return s
        except Exception:
            pass
    return None


def get_current_commit() -> dict:
    """Return {commit_sha, branch, message, author, timestamp_iso} for HEAD.

    Cascade (Iter 388z):
      1. `git rev-parse HEAD`               — dev / any container with .git
      2. `AUREM_DEPLOY_COMMIT` env var      — pipelines that stamp it
      3. `backend/BUILD_INFO.txt`           — post-commit-hook fallback
                                              (works on prod containers
                                              that strip .git but keep
                                              this file in the snapshot)
    """
    sha = (
        _safe_run(["git", "rev-parse", "HEAD"])
        or os.environ.get("AUREM_DEPLOY_COMMIT")
        or _read_build_info_sha()
    )
    branch = _safe_run(["git", "rev-parse", "--abbrev-ref", "HEAD"]) or os.environ.get("AUREM_DEPLOY_BRANCH", "main")
    message = _safe_run(["git", "log", "-1", "--pretty=%s"]) or ""
    author = _safe_run(["git", "log", "-1", "--pretty=%an"]) or ""
    ts = _safe_run(["git", "log", "-1", "--pretty=%cI"]) or None
    return {
        "commit_sha": sha,
        "branch": branch,
        "commit_message": message[:240],
        "commit_author": author,
        "commit_timestamp": ts,
    }


async def log_deploy_event(db, *, trigger: str = "boot", extra: Optional[dict] = None) -> Optional[dict]:
    """Insert a deploy_events doc once per (commit_sha, boot_id).

    Returns the inserted document (without _id) on success, None otherwise.
    """
    if db is None:
        return None
    info = get_current_commit()
    if not info.get("commit_sha"):
        logger.info("[deploy-log] no commit sha resolvable — skip")
        return None

    doc = {
        "trigger": trigger,
        "branch": info["branch"],
        "commit": info["commit_sha"],          # legacy field used elsewhere
        "commit_sha": info["commit_sha"],
        "commit_message": info["commit_message"],
        "commit_author": info["commit_author"],
        "commit_timestamp": info["commit_timestamp"],
        "repo": _DEFAULT_REPO,
        "boot_id": _BOOT_ID,
        "host": os.environ.get("HOSTNAME", ""),
        "env": os.environ.get("AUREM_ENV", "preview"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }
    try:
        # Idempotency only for trigger='boot' (prevents hot-reload duplicates).
        # Explicit CI/manual/webhook triggers are always recorded.
        if trigger == "boot":
            existing = await db.deploy_events.find_one(
                {"commit_sha": doc["commit_sha"], "boot_id": _BOOT_ID, "trigger": "boot"},
                {"_id": 1},
            )
            if existing:
                return None
        await db.deploy_events.insert_one(dict(doc))   # copy to avoid Mongo mutation
        logger.info(f"[deploy-log] recorded {info['commit_sha'][:7]} ({info['branch']}) trigger={trigger}")
        return {k: v for k, v in doc.items() if k != "_id"}
    except Exception as e:
        logger.warning(f"[deploy-log] insert failed: {e}")
        return None
