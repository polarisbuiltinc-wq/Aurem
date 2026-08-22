"""services/rollback_drill.py — Pillar 1 synthetic CI test harness.

Full real-infrastructure drill: seed → snapshot → ship a deliberately
breaking change → verify broken → preview → execute rollback → verify
byte-exact pre-ship restoration. Every step logged with timestamps to a
`rollback_drills` row; the restore itself flows through the SAME
two-phase code paths production uses (zero drill-only shortcuts).

Target configuration (backend/.env):
  AUREM_DRILL_REPO   — "owner/repo" of a disposable, WRITABLE test repo
  AUREM_DRILL_BRANCH — branch to drill on (default: main)
  AUREM_DRILL_TOKEN  — token with contents:read/write on that repo
                       (falls back to GITHUB_ACTIONS_TOKEN)
If the token lacks write access the drill fails honestly at the seed
step and records the exact GitHub error — no simulated success.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("aurem.rollback_drill")

DRILL_PATH = "drill/rollback_drill_target.py"

_GOOD = '''"""Rollback-drill target — known-good state."""


def health() -> str:
    return "ok"
'''

_BAD = '''"""Rollback-drill target — DELIBERATELY BROKEN by the drill."""

raise RuntimeError("deliberately broken by AUREM rollback drill")
'''


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


async def run_drill(db, initiated_by: str) -> dict:
    drill_id = f"drill_{uuid.uuid4().hex[:12]}"
    steps: list[dict] = []
    t0 = time.time()

    def _step(name: str, status: str, detail: str = ""):
        steps.append({"step": name, "status": status,
                      "detail": detail[:400], "ts": _iso()})
        logger.info("[drill %s] %s → %s %s", drill_id, name, status,
                    detail[:120])

    async def _finish(result: str, **extra):
        row = {"drill_id": drill_id, "initiated_by": initiated_by,
               "result": result, "steps": steps,
               "duration_s": round(time.time() - t0, 1),
               "created_at": _iso(), **extra}
        await db.rollback_drills.insert_one(dict(row))
        row.pop("_id", None)
        return row

    repo_full = os.environ.get("AUREM_DRILL_REPO", "").strip()
    branch = os.environ.get("AUREM_DRILL_BRANCH", "main").strip() or "main"
    token = (os.environ.get("AUREM_DRILL_TOKEN", "").strip()
             or os.environ.get("GITHUB_ACTIONS_TOKEN", "").strip())
    if not repo_full or "/" not in repo_full or not token:
        _step("config", "blocked",
              "AUREM_DRILL_REPO and a writable token are required")
        return await _finish("blocked")
    owner, repo = repo_full.split("/", 1)
    _step("config", "ok", f"target {repo_full}@{branch} path {DRILL_PATH}")

    from services.github_api_writer import commit_files, fetch_file
    from services.rollback_snapshot import create_snapshot
    from services.rollback_two_phase import (
        preview_rollback, execute_rollback_from_snapshot,
    )

    def _scrub(s: str) -> str:
        return s.replace(token, "***TOKEN***") if token else s

    # 1 — seed known-good state (real commit).
    try:
        seed = await commit_files(
            owner=owner, repo=repo, branch=branch, token=token,
            files={DRILL_PATH: _GOOD},
            commit_message=f"chore(drill): seed known-good state [{drill_id}]",
            author_name="AUREM Drill",
            author_email="aurem-drill@users.noreply.github.com",
        )
        _step("seed_good_state", "ok", f"commit {seed.get('sha','')[:10]}")
    except Exception as e:  # noqa: BLE001
        _step("seed_good_state", "failed", _scrub(repr(e)))
        return await _finish("failed", failed_at="seed_good_state")

    # 2 — snapshot the pre-break state (real R2 + Mongo).
    try:
        snap = await create_snapshot(
            db, owner=owner, repo=repo, branch=branch, token=token,
            file_paths=[DRILL_PATH], trigger="drill",
        )
        snapshot_id = snap["snapshot_id"]
        _step("snapshot", "ok",
              f"{snapshot_id} base {snap['base_commit_sha'][:10]} "
              f"r2:{snap['r2_key']}")
    except Exception as e:  # noqa: BLE001
        _step("snapshot", "failed", _scrub(repr(e)))
        return await _finish("failed", failed_at="snapshot")

    # 3 — ship the deliberately breaking change (real commit).
    try:
        brk = await commit_files(
            owner=owner, repo=repo, branch=branch, token=token,
            files={DRILL_PATH: _BAD},
            commit_message=f"feat(drill): DELIBERATELY breaking change [{drill_id}]",
            author_name="AUREM Drill",
            author_email="aurem-drill@users.noreply.github.com",
        )
        _step("ship_breaking_change", "ok", f"commit {brk.get('sha','')[:10]}")
    except Exception as e:  # noqa: BLE001
        _step("ship_breaking_change", "failed", _scrub(repr(e)))
        return await _finish("failed", failed_at="ship_breaking_change",
                             snapshot_id=snapshot_id)

    # 4 — verify the branch is actually broken.
    try:
        now_broken = await fetch_file(owner, repo, DRILL_PATH, branch, token)
        if now_broken is None or _h(now_broken) != _h(_BAD):
            _step("verify_broken", "failed", "branch content != BAD payload")
            return await _finish("failed", failed_at="verify_broken",
                                 snapshot_id=snapshot_id)
        _step("verify_broken", "ok", "branch confirmed broken")
    except Exception as e:  # noqa: BLE001
        _step("verify_broken", "failed", _scrub(repr(e)))
        return await _finish("failed", failed_at="verify_broken",
                             snapshot_id=snapshot_id)

    # 5 — phase-1 preview (must show the file as would_restore).
    pv = await preview_rollback(db, snapshot_id=snapshot_id, token=token)
    if not pv.get("ok") or pv.get("files_changed", 0) < 1:
        _step("preview", "failed", str(pv)[:300])
        return await _finish("failed", failed_at="preview",
                             snapshot_id=snapshot_id)
    _step("preview", "ok",
          f"{pv['files_changed']} file(s) would_restore, token issued")

    # 6 — phase-2 execute with explicit confirm.
    ex = await execute_rollback_from_snapshot(
        db, snapshot_id=snapshot_id, preview_token=pv["preview_token"],
        initiated_by=f"drill:{initiated_by}", token=token, confirm=True,
    )
    if not ex.get("ok"):
        _step("execute_rollback", "failed", str(ex)[:300])
        return await _finish("failed", failed_at="execute_rollback",
                             snapshot_id=snapshot_id)
    _step("execute_rollback", "ok",
          f"attempt {ex['attempt_id']} commit "
          f"{ex.get('restored_commit_sha','')[:10]} "
          f"verified={ex.get('verified')}")

    # 7 — independent final verification: byte-exact pre-ship state.
    try:
        after = await fetch_file(owner, repo, DRILL_PATH, branch, token)
        if after is None or _h(after) != _h(_GOOD):
            _step("verify_restored", "failed",
                  "restored content hash != pre-ship hash")
            return await _finish("failed", failed_at="verify_restored",
                                 snapshot_id=snapshot_id,
                                 attempt_id=ex["attempt_id"])
        _step("verify_restored", "ok",
              f"sha256 match {_h(_GOOD)[:16]}… — byte-exact restore")
    except Exception as e:  # noqa: BLE001
        _step("verify_restored", "failed", _scrub(repr(e)))
        return await _finish("failed", failed_at="verify_restored",
                             snapshot_id=snapshot_id,
                             attempt_id=ex["attempt_id"])

    return await _finish("passed", snapshot_id=snapshot_id,
                         attempt_id=ex["attempt_id"],
                         restored_commit_sha=ex.get("restored_commit_sha"))
