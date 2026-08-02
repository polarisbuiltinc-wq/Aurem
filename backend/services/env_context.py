"""
services/env_context.py — Environment identification (Feb 2026)

Reused by /admin/pulse, /admin/status/all, and any future admin
metric endpoint so a founder / auditor NEVER has to guess whether
they're looking at preview or production numbers again.

Same helper backs the cockpit `PREVIEW DATA` badge. Single source
of truth for "what environment am I in?".
"""
from __future__ import annotations

import os
from urllib.parse import urlparse


def env_name() -> str:
    """Return `production` | `preview` | `dev` | `unknown`.

    Detection precedence:
      1. `AUREM_ENV` env var if set (explicit override).
      2. MONGO_URL hostname heuristic:
         · localhost/127.0.0.1  → preview
         · *.mongodb.net        → production (Atlas)
         · everything else      → dev
    """
    override = os.environ.get("AUREM_ENV", "").strip().lower()
    if override in ("production", "preview", "dev"):
        return override
    mongo = os.environ.get("MONGO_URL", "")
    host = ""
    try:
        # Strip credentials so parse doesn't choke on `mongodb://x:y@`
        stripped = mongo.rsplit("@", 1)[-1]
        host = urlparse(f"mongodb://{stripped}").hostname or ""
    except Exception:  # noqa: BLE001
        host = ""
    host_l = host.lower()
    # Atlas cluster = production.
    if "mongodb.net" in host_l:
        return "production"
    # Everything else — localhost, 127.0.0.1, K8s service name
    # (`mongodb`, `mongo`), unresolved, or empty — is preview.
    # We deliberately DON'T introduce a separate "dev" bucket here
    # because in practice the founder only cares about the binary
    # preview-vs-prod distinction; a hidden "dev" label would just
    # re-open the same "which env is this?" confusion.
    return "preview"


def db_host() -> str:
    """Best-effort Mongo host string for the env-badge tooltip.
    Never returns credentials — only the host+port portion."""
    mongo = os.environ.get("MONGO_URL", "")
    try:
        stripped = mongo.rsplit("@", 1)[-1]
        parsed = urlparse(f"mongodb://{stripped}")
        host = parsed.hostname or ""
        port = parsed.port
        if host and port:
            return f"{host}:{port}"
        return host or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def env_stamp() -> dict:
    """Standard payload block every admin metric endpoint should
    include so the UI can render the environment badge without
    guessing."""
    return {"env": env_name(), "db_host": db_host()}


__all__ = ["env_name", "db_host", "env_stamp"]
