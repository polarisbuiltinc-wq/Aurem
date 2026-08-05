# iter 212m-9 — BYOH per-project deploy + UI endpoints

"""
aurem_cto.routers.deploy — P0 foundation.

SSH-driven `git pull && docker compose up -d` with live log streaming,
last-N run history, and one-click rollback.

Routes mount under /aurem-cto/deploy/* via the parent build_router().
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field

from cto_services.auth import current_dev
from cto_services.crypto import encrypt, decrypt, is_vault_available
from cto_services.db import require_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/deploy", tags=["AUREM Deploy"])

DEPLOY_TIMEOUT_SECONDS = 8 * 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scrub(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]+)",
                  "github_pat_***", text)
    text = re.sub(r"(?i)(authorization:\s*bearer\s+)\S+", r"\1***", text)
    return text[:4000]


class DeployConfigBody(BaseModel):
    host:         str = Field(..., min_length=3, max_length=255)
    port:         int = Field(22, ge=1, le=65535)
    username:     str = Field("root", min_length=1, max_length=64)
    private_key:  str = Field(..., min_length=40)
    repo_path:    str = Field(..., min_length=1, max_length=255)
    branch:       str = Field("main", min_length=1, max_length=64)
    compose_file: str = Field("docker-compose.yml", min_length=1, max_length=128)
    # Iter 212m-9 — optional per-project scope. When set, config saves
    # under (user_id, project_id) and overrides the user-level default
    # for that project. When omitted, behaves as before (user-level).
    project_id:   str = Field("", max_length=64)
    # Iter 367 (Item B) — deploy target selector. Default 'ssh' keeps
    # the existing SSH+docker-compose flow untouched. 'ftp' switches
    # to services.ftp_ssh_deploy.deploy_via_ftp (FTPS by default);
    # 'sftp' switches to services.ftp_ssh_deploy.deploy_via_ssh (SFTP
    # over SSH, key-auth preferred).
    target:       str = Field(
        "ssh",
        pattern="^(ssh|ftp|sftp)$",
        description="ssh = docker-compose remote deploy (default); "
                    "ftp = FTPS file upload; sftp = SFTP file upload.",
    )
    # For FTP: remote directory where files are uploaded (e.g. /public_html).
    # For SSH docker-compose: repo_path is used (existing behaviour).
    remote_dir:   str = Field("", max_length=255)
    # Optional TLS override for FTP — default True (FTPS). Only allow
    # plain FTP if the founder explicitly opted out (creds go in
    # cleartext otherwise, which is a real footgun).
    ftp_tls:      bool = True


def _serialize_cfg(row: dict | None) -> dict[str, Any]:
    if not row:
        return {"configured": False}
    return {
        "configured":    True,
        "host":          row.get("host"),
        "port":          row.get("port", 22),
        "username":      row.get("username", "root"),
        "repo_path":     row.get("repo_path"),
        "branch":        row.get("branch", "main"),
        "compose_file":  row.get("compose_file", "docker-compose.yml"),
        "private_key":   "•••••••• (write-only — never returned)",
        "updated_at":    row.get("updated_at"),
        "project_id":    row.get("project_id") or None,
        "scope":         "project" if row.get("project_id") else "user",
        # Iter 367 (Item B) — surface the deploy target + FTP-specific
        # fields so the UI can badge each config with its transport.
        "target":        row.get("target", "ssh"),
        "remote_dir":    row.get("remote_dir") or None,
        "ftp_tls":       row.get("ftp_tls", True),
    }


async def _find_cfg(db, user_id: str, project_id: str | None) -> dict | None:
    """Per-project lookup with fallback to user-level (project_id=None).
    Iter 212m-9: enables hybrid scoping so existing user-level configs
    keep working for projects that don't have a dedicated config yet."""
    if project_id:
        row = await db.aurem_cto_deploy_configs.find_one(
            {"user_id": user_id, "project_id": project_id}, {"_id": 0},
        )
        if row:
            return row
    return await db.aurem_cto_deploy_configs.find_one(
        {"user_id": user_id,
         "$or": [{"project_id": None}, {"project_id": {"$exists": False}}]},
        {"_id": 0},
    )


@router.get("/config")
async def get_config(authorization: str = Header(None)) -> dict[str, Any]:
    me = await current_dev(authorization)
    db = require_db()
    row = await _find_cfg(db, me["user_id"], None)
    return _serialize_cfg(row)


@router.get("/config/{project_id}")
async def get_config_for_project(project_id: str,
                                  authorization: str = Header(None)
                                  ) -> dict[str, Any]:
    """Iter 212m-9 — hybrid fallback. Returns the project-scoped config
    if one exists, otherwise the user-level default. UI uses the
    `scope` field to badge whether the user is editing the shared
    default or a per-project override."""
    me = await current_dev(authorization)
    db = require_db()
    row = await _find_cfg(db, me["user_id"], project_id)
    return _serialize_cfg(row)


@router.post("/config")
async def save_config(body: DeployConfigBody,
                      authorization: str = Header(None)) -> dict[str, Any]:
    me = await current_dev(authorization)
    db = require_db()
    if not is_vault_available():
        raise HTTPException(503, {
            "code": "vault_unavailable",
            "msg":  "AUREM_CTO_MASTER_KEY not set on this deployment — "
                    "ask an admin to configure the vault env var.",
        })
    pk = body.private_key.strip()
    # Iter 367 (Item B) — validation branches by target:
    #   ssh/sftp: private_key must be PEM (unchanged from Iter 212m-9)
    #   ftp:      private_key field carries the FTP password (arbitrary
    #             non-empty string). No PEM check applies.
    if body.target in ("ssh", "sftp"):
        if "BEGIN" not in pk or "PRIVATE KEY" not in pk:
            raise HTTPException(400, "private_key_must_be_pem")
        secret_kind = "ssh_private_key"
    else:  # ftp
        if len(pk) < 4:
            raise HTTPException(400, "ftp_password_too_short")
        # For FTP, the 'private_key' field carries the FTP password —
        # encrypted under a distinct vault kind so we never mix key
        # material with passwords in the same envelope.
        secret_kind = "ftp_password"
    if body.target == "ftp" and not body.remote_dir.strip():
        raise HTTPException(
            400, "remote_dir_required_for_ftp",
        )
    enc = await encrypt(me["user_id"], pk, kind=secret_kind)
    pid = (body.project_id or "").strip() or None
    await db.aurem_cto_deploy_configs.update_one(
        {"user_id": me["user_id"], "project_id": pid},
        {"$set": {
            "user_id":         me["user_id"],
            "project_id":      pid,
            "host":            body.host.strip(),
            "port":            body.port,
            "username":        body.username.strip(),
            "private_key_enc": enc,
            "secret_kind":     secret_kind,
            "repo_path":       body.repo_path.strip(),
            "branch":          body.branch.strip(),
            "compose_file":    body.compose_file.strip(),
            "target":          body.target,
            "remote_dir":      body.remote_dir.strip(),
            "ftp_tls":         body.ftp_tls,
            "updated_at":      _now_iso(),
        }},
        upsert=True,
    )
    return {"ok": True, "project_id": pid, "target": body.target}


@router.delete("/config")
async def delete_config(project_id: str = "",
                        authorization: str = Header(None)) -> dict[str, Any]:
    me = await current_dev(authorization)
    db = require_db()
    pid = project_id.strip() or None
    await db.aurem_cto_deploy_configs.delete_one(
        {"user_id": me["user_id"], "project_id": pid},
    )
    return {"ok": True, "project_id": pid}


def _deploy_command(cfg: dict, mode: str = "deploy") -> str:
    repo = cfg.get("repo_path", "").rstrip("/")
    branch = cfg.get("branch", "main")
    compose = cfg.get("compose_file", "docker-compose.yml")
    if not repo:
        return "echo 'no repo_path configured' && exit 2"
    if mode == "dry_run":
        # D-35 — safe staging check. Verifies SSH auth, repo access and
        # docker-compose validity WITHOUT pulling code or restarting
        # containers. Used to gate the real-deploy button on production
        # dogfood projects.
        seq = (
            f"cd {repo} && "
            f"git fetch --all --prune && "
            f"docker compose -f {compose} config --quiet && "
            f"echo DRY_RUN_OK"
        )
    elif mode == "rollback":
        seq = (
            f"cd {repo} && "
            f"git reset --hard HEAD~1 && "
            f"docker compose -f {compose} up -d --remove-orphans"
        )
    elif mode == "revert_to":
        sha = cfg.get("_revert_sha", "").strip()
        if not re.fullmatch(r"[0-9a-f]{7,64}", sha):
            return "echo 'bad sha' && exit 2"
        seq = (
            f"cd {repo} && "
            f"git fetch --all --prune && "
            f"git revert --no-edit {sha} && "
            f"git push origin {branch} && "
            f"docker compose -f {compose} up -d --build --remove-orphans"
        )
    else:
        seq = (
            f"cd {repo} && "
            f"git fetch --all --prune && "
            f"git checkout {branch} && "
            f"git pull --ff-only origin {branch} && "
            f"docker compose -f {compose} pull && "
            f"docker compose -f {compose} up -d --remove-orphans --build"
        )
    return (
        f"set -e; ({seq}) && "
        f"echo \"DEPLOY_HEAD=$(git -C {repo} rev-parse HEAD)\""
    )


async def _run_deploy_remote(user_id: str, run_id: str,
                              cfg: dict, command: str) -> None:
    import asyncssh
    db = require_db()

    try:
        private_key = await decrypt(user_id, cfg.get("private_key_enc", ""),
                                     kind="ssh_private_key")
    except Exception as e:
        await db.aurem_cto_deploy_runs.update_one(
            {"run_id": run_id},
            {"$set": {"status": "failed",
                       "error": f"vault_decrypt_failed: {type(e).__name__}",
                       "finished_at": _now_iso()}},
        )
        return

    async def _append(line: str) -> None:
        await db.aurem_cto_deploy_runs.update_one(
            {"run_id": run_id},
            {"$push": {"output": _scrub(line)},
             "$set":  {"last_update": _now_iso()}},
        )

    try:
        async with asyncio.timeout(DEPLOY_TIMEOUT_SECONDS):
            async with asyncssh.connect(
                cfg["host"], port=int(cfg.get("port", 22)),
                username=cfg.get("username", "root"),
                client_keys=[asyncssh.import_private_key(private_key)],
                known_hosts=None,
                connect_timeout=15,
            ) as conn:
                await _append(f"$ {command}")
                async with conn.create_process(command) as proc:
                    head_sha: Optional[str] = None

                    async def _pipe(stream, tag):
                        nonlocal head_sha
                        async for line in stream:
                            stripped = line.rstrip()
                            if stripped.startswith("DEPLOY_HEAD="):
                                head_sha = stripped.split("=", 1)[1].strip()
                            await _append(f"{tag} {stripped}")
                    await asyncio.gather(
                        _pipe(proc.stdout, "·"),
                        _pipe(proc.stderr, "!"),
                    )
                    rc = await proc.wait()
                    await db.aurem_cto_deploy_runs.update_one(
                        {"run_id": run_id},
                        {"$set": {
                            "status":      "ok" if rc == 0 else "failed",
                            "exit_code":   rc,
                            "head_sha":    head_sha,
                            "finished_at": _now_iso(),
                        }},
                    )
    except asyncio.TimeoutError:
        await _append(f"!! deploy timed out after {DEPLOY_TIMEOUT_SECONDS}s")
        await db.aurem_cto_deploy_runs.update_one(
            {"run_id": run_id},
            {"$set": {"status": "timeout", "finished_at": _now_iso()}},
        )
    except Exception as e:
        await _append(f"!! deploy crashed: {type(e).__name__}: {str(e)[:200]}")
        await db.aurem_cto_deploy_runs.update_one(
            {"run_id": run_id},
            {"$set": {"status": "failed", "finished_at": _now_iso(),
                       "error": f"{type(e).__name__}: {str(e)[:200]}"}},
        )


async def _run_deploy_ftp_or_sftp(
    user_id:  str,
    run_id:   str,
    cfg:      dict,
    files:    dict,   # {rel_path: bytes|str}
) -> None:
    """Iter 367 (Item B) — runs the FTP or SFTP deploy path via
    services.ftp_ssh_deploy. Decrypts the stored secret (FTP password
    or SSH private key) and streams progress into the same
    `aurem_cto_deploy_runs.output` array the SSH path uses."""
    import base64
    db = require_db()
    target = cfg.get("target", "ssh")
    kind   = cfg.get("secret_kind") or (
        "ftp_password" if target == "ftp" else "ssh_private_key")

    async def _append(line: str) -> None:
        await db.aurem_cto_deploy_runs.update_one(
            {"run_id": run_id},
            {"$push": {"output": _scrub(line)},
             "$set":  {"last_update": _now_iso()}},
        )

    try:
        secret = await decrypt(user_id, cfg.get("private_key_enc", ""),
                                kind=kind)
    except Exception as e:
        await db.aurem_cto_deploy_runs.update_one(
            {"run_id": run_id},
            {"$set": {"status": "failed",
                       "error": f"vault_decrypt_failed: {type(e).__name__}",
                       "finished_at": _now_iso()}},
        )
        return

    # Normalize files: accept bytes OR str OR {content_b64: ...}
    normalized: dict[str, bytes] = {}
    for path, val in (files or {}).items():
        if isinstance(val, dict) and "content_b64" in val:
            normalized[path] = base64.b64decode(val["content_b64"])
        elif isinstance(val, (bytes, bytearray)):
            normalized[path] = bytes(val)
        else:
            normalized[path] = str(val).encode("utf-8")

    from services.ftp_ssh_deploy import deploy_via_ftp, deploy_via_ssh
    await _append(
        f"$ transport={target} host={cfg.get('host')} "
        f"remote_dir={cfg.get('remote_dir')} files={len(normalized)}")

    try:
        async with asyncio.timeout(DEPLOY_TIMEOUT_SECONDS):
            if target == "ftp":
                res = await deploy_via_ftp(
                    host=cfg["host"], port=int(cfg.get("port", 21)),
                    user=cfg.get("username", ""), password=secret,
                    remote_dir=cfg.get("remote_dir", "/"),
                    files=normalized,
                    tls=bool(cfg.get("ftp_tls", True)),
                )
            else:  # sftp
                res = await deploy_via_ssh(
                    host=cfg["host"], port=int(cfg.get("port", 22)),
                    user=cfg.get("username", "root"),
                    key_pem=secret if "BEGIN" in secret else None,
                    password=secret if "BEGIN" not in secret else None,
                    remote_dir=cfg.get("remote_dir", "/"),
                    files=normalized,
                )
    except asyncio.TimeoutError:
        await _append(f"!! {target} deploy timed out after "
                       f"{DEPLOY_TIMEOUT_SECONDS}s")
        await db.aurem_cto_deploy_runs.update_one(
            {"run_id": run_id},
            {"$set": {"status": "timeout",
                       "finished_at": _now_iso()}},
        )
        return
    except Exception as e:
        await _append(f"!! {target} crashed: {type(e).__name__}: "
                       f"{str(e)[:200]}")
        await db.aurem_cto_deploy_runs.update_one(
            {"run_id": run_id},
            {"$set": {"status": "failed", "finished_at": _now_iso(),
                       "error": f"{type(e).__name__}: {str(e)[:200]}"}},
        )
        return

    ok = bool(res.get("ok"))
    for line in res.get("errors") or []:
        await _append(f"! {line}")
    await _append(f"→ uploaded={res.get('uploaded', 0)} "
                   f"target={res.get('target', target)} ok={ok}")
    await db.aurem_cto_deploy_runs.update_one(
        {"run_id": run_id},
        {"$set": {
            "status":      "ok" if ok else "failed",
            "exit_code":   0 if ok else 1,
            "uploaded":    res.get("uploaded", 0),
            "finished_at": _now_iso(),
            "error":       res.get("fatal") or
                            (res.get("errors") and res["errors"][0])
                            or None,
        }},
    )


class DeployRunBody(BaseModel):
    mode:      str = Field("deploy",
                            pattern="^(deploy|rollback|revert_to|dry_run)$")
    sha:       str = Field("", max_length=64)   # only used when mode=revert_to
    message_id: str = Field("", max_length=64)  # optional chat-message link
    project_id: str = Field("", max_length=64)  # optional dogfood project link
    # Iter 367 (Item B) — files for FTP/SFTP target. Values may be
    # base64-encoded strings (via `{"content_b64": "..."}`) or plain
    # text. Ignored when target=ssh (SSH path uses git-pull on remote).
    files:     dict = Field(default_factory=dict,
                             description="{rel_path: content} — FTP/SFTP only.")


@router.post("/run")
async def run_deploy(body: DeployRunBody = DeployRunBody(),
                     authorization: str = Header(None)) -> dict[str, Any]:
    me = await current_dev(authorization)
    db = require_db()
    pid = (body.project_id or "").strip() or None
    cfg = await _find_cfg(db, me["user_id"], pid)
    if not cfg:
        raise HTTPException(400, "deploy_not_configured")

    # D-35 — Production dogfood guard was originally gated on the
    # `is_production_dogfood` flag stored on `onboarding_projects`.
    # That collection is defunct and no writer sets this flag anywhere
    # in the codebase, so the guard was permanently dormant. When the
    # dogfood workflow is revived, add the flag to `cto_projects` and
    # re-implement the 24h dry-run check here.

    target = cfg.get("target", "ssh")

    # Iter 367 (Item B) — FTP/SFTP branch. Skips the docker-compose
    # command construction entirely and routes to ftp_ssh_deploy.
    if target in ("ftp", "sftp"):
        if body.mode not in ("deploy",):
            raise HTTPException(
                400,
                f"mode='{body.mode}' not supported for target='{target}' "
                "(only 'deploy' is valid for FTP/SFTP transports).",
            )
        if not body.files:
            raise HTTPException(
                400,
                "files_required — FTP/SFTP deploys need a non-empty "
                "`files` map ({rel_path: content_b64 | str}).",
            )
        run_id = uuid.uuid4().hex[:16]
        await db.aurem_cto_deploy_runs.insert_one({
            "run_id":      run_id,
            "user_id":     me["user_id"],
            "mode":        body.mode,
            "target":      target,
            "host":        cfg.get("host"),
            "remote_dir":  cfg.get("remote_dir"),
            "message_id":  body.message_id or None,
            "project_id":  body.project_id or None,
            "command":     f"[{target}] upload {len(body.files)} file(s) → "
                            f"{cfg.get('host')}:{cfg.get('remote_dir')}",
            "status":      "running",
            "exit_code":   None,
            "output":      [],
            "started_at":  _now_iso(),
            "last_update": _now_iso(),
            "finished_at": None,
        })
        asyncio.create_task(
            _run_deploy_ftp_or_sftp(me["user_id"], run_id, cfg, body.files),
            name=f"aurem-cto-deploy-{target}:{run_id}",
        )
        return {"run_id": run_id, "mode": body.mode,
                "target": target, "status": "running"}

    # ── SSH + docker-compose (legacy default) ─────────────────────
    if body.mode == "revert_to":
        cfg = {**cfg, "_revert_sha": body.sha}
    run_id = uuid.uuid4().hex[:16]
    cmd = _deploy_command(cfg, body.mode)
    await db.aurem_cto_deploy_runs.insert_one({
        "run_id":      run_id,
        "user_id":     me["user_id"],
        "mode":        body.mode,
        "target":      "ssh",
        "host":        cfg.get("host"),
        "branch":      cfg.get("branch", "main"),
        "message_id":  body.message_id or None,
        "project_id":  body.project_id or None,
        "command":     cmd,
        "status":      "running",
        "exit_code":   None,
        "output":      [],
        "started_at":  _now_iso(),
        "last_update": _now_iso(),
        "finished_at": None,
    })
    asyncio.create_task(_run_deploy_remote(me["user_id"], run_id, cfg, cmd),
                        name=f"aurem-cto-deploy:{run_id}")
    return {"run_id": run_id, "mode": body.mode,
            "target": "ssh", "status": "running"}


@router.get("/log/{run_id}")
async def get_log(run_id: str,
                  since: int = 0,
                  authorization: str = Header(None)) -> dict[str, Any]:
    me = await current_dev(authorization)
    db = require_db()
    doc = await db.aurem_cto_deploy_runs.find_one(
        {"run_id": run_id, "user_id": me["user_id"]},
        {"_id": 0},
    )
    if not doc:
        raise HTTPException(404, "run_not_found")
    full = doc.get("output", []) or []
    return {
        "run_id":      run_id,
        "status":      doc.get("status"),
        "exit_code":   doc.get("exit_code"),
        "head_sha":    doc.get("head_sha"),
        "since":       since,
        "next_cursor": len(full),
        "lines":       full[since:],
        "started_at":  doc.get("started_at"),
        "finished_at": doc.get("finished_at"),
    }


@router.get("/history")
async def history(authorization: str = Header(None)) -> dict[str, Any]:
    me = await current_dev(authorization)
    db = require_db()
    cur = db.aurem_cto_deploy_runs.find(
        {"user_id": me["user_id"]},
        {"_id": 0, "output": 0},
    ).sort("started_at", -1).limit(20)
    rows = [d async for d in cur]
    return {"runs": rows}


@router.get("/runs")
async def list_runs(project_id: str = "",
                    limit: int = 20,
                    authorization: str = Header(None)) -> dict[str, Any]:
    """Iter 212m-9 — alias for /history with optional `project_id`
    filter. The Deploy panel uses this to show only the runs that
    target the currently-open project."""
    me = await current_dev(authorization)
    db = require_db()
    q: dict[str, Any] = {"user_id": me["user_id"]}
    pid = project_id.strip()
    if pid:
        q["project_id"] = pid
    limit = max(1, min(limit, 100))
    cur = db.aurem_cto_deploy_runs.find(
        q, {"_id": 0, "output": 0},
    ).sort("started_at", -1).limit(limit)
    rows = [d async for d in cur]
    return {"runs": rows, "project_id": pid or None}


@router.get("/runs/{run_id}/logs")
async def runs_logs(run_id: str,
                    since: int = 0,
                    authorization: str = Header(None)) -> dict[str, Any]:
    """Iter 212m-9 — alias for /log/{run_id} matching the
    `/deploy/runs/{run_id}/logs` REST shape the UI prompt requested."""
    return await get_log(run_id, since=since, authorization=authorization)
