"""
services/write_guard.py — Guardrail rule #2 (2026-08 audit remediation,
Wave 1): a hard, code-reviewed deny-list of paths no AUREM-originated
write may ever touch, checked INSIDE the single vetted writer choke
point (`services/github_api_writer.commit_files`) so every current and
future caller inherits it automatically (audit rule #1 stays true).

Design Contract C1 (WARN then BLOCK): starts in "warn" mode — a hit is
logged + alerted but the write proceeds. An admin flips
`guard_config.path_guard.mode` to "block" (config, not code) once the
48h Preview WARN window shows zero false positives.

The deny-list itself is a hard constant, not admin-editable — only the
enforcement MODE is config (per the remediation plan's rollback table).
"""
from __future__ import annotations

import fnmatch
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("aurem.write_guard")

RULE_PATH_GUARD = "path_guard"

# Founder-locked (2026-08-28): .env.example is deliberately included —
# a real ship (loop_7014cd440aaf4c, the P6 drill) already touched it
# once. This list is a hard constant; only `mode` is config.
DENY_PATTERNS = [
    ".env", ".env.*",
    ".github/*", ".github/**/*",
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "poetry.lock", "Cargo.lock", "go.sum",
    "migrations/*", "*/migrations/*",
    "vercel.json", "netlify.toml",
    "docker-compose.yml", "docker-compose.*.yml",
    "firebase.json", "wrangler.toml",
    "*.tf", "secrets.*",
]


def matched_deny_pattern(path: str) -> Optional[str]:
    """Return the deny pattern a path matches, or None if it's clear.
    Matches both the full path (for directory-style patterns like
    `.github/**/*`) and the bare filename (for patterns like
    `*.tf` or `.env.*` regardless of which directory they land in)."""
    norm = (path or "").strip().lstrip("/")
    if not norm:
        return None
    base = norm.rsplit("/", 1)[-1]
    for pat in DENY_PATTERNS:
        if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(base, pat):
            return pat
    return None


async def get_mode(db, rule: str, default: str = "warn") -> str:
    """Reads `guard_config` — one doc per rule, `{_id: rule, mode}`.
    Missing doc or missing db (best-effort, never raises) = "warn",
    the safe default for every new guardrail per Design Contract C1."""
    if db is None:
        return default
    try:
        doc = await db.guard_config.find_one({"_id": rule})
        return (doc or {}).get("mode", default)
    except Exception:                                       # noqa: BLE001
        return default


async def check_write_paths(
    db, paths: list[str], *, owner: str = "", repo: str = "", branch: str = "",
) -> None:
    """Check every path about to be committed against the deny list.
    In "warn" mode: logs + alerts on a hit, never raises. In "block"
    mode: raises `core.errors.WriteGuardBlockedError` on a hit.
    No-op (fast return) when nothing matches — the common case."""
    hits = [(p, matched_deny_pattern(p)) for p in (paths or [])]
    hits = [(p, m) for p, m in hits if m]
    if not hits:
        return

    mode = await get_mode(db, RULE_PATH_GUARD)
    blocked_paths = [p for p, _ in hits]
    event = "GW_BLOCK_PATH" if mode == "block" else "GW_WARN_PATH"

    logger.warning(
        "[write_guard] %s — protected path(s) %s (owner=%s repo=%s "
        "branch=%s mode=%s)",
        event, blocked_paths, owner, repo, branch, mode,
    )
    if db is not None:
        try:
            await db.guardrail_events.insert_one({
                "event": event, "rule": RULE_PATH_GUARD, "mode": mode,
                "paths": blocked_paths, "owner": owner, "repo": repo,
                "branch": branch, "ts": datetime.now(timezone.utc),
            })
        except Exception:                                    # noqa: BLE001
            pass
        try:
            from services.founder_alerts import send_founder_alert
            await send_founder_alert(
                db,
                source_key=f"write_guard:{RULE_PATH_GUARD}:{owner}/{repo}",
                title=f"Guardrail {event} — protected path write attempt",
                detail=(f"paths={blocked_paths} owner={owner} repo={repo} "
                        f"branch={branch} mode={mode}"),
                level="warning" if mode == "warn" else "critical",
                guard="GW2",
            )
        except Exception:                                    # noqa: BLE001
            pass

    if mode == "block":
        from core.errors import WriteGuardBlockedError
        raise WriteGuardBlockedError(blocked_paths)
