"""
services/github_app_config.py — Runtime cache for GitHub App credentials.

Mirrors the Stripe DB-override pattern (see services/stripe_client.py
`_RUNTIME_STRIPE_PRICE_IDS`). Multi-worker safe: every uvicorn worker
hydrates this dict at boot from `admin_settings._id="github_app_config"`
and hot-swaps via `POST /admin/github-app-config`.

This module intentionally stays SMALL — only cache + accessors. The
full GitHub App service (JWT signing, installation-token minting,
webhook signature verification, install/callback routes) is Phase 1.1
and lives in a separate `services/github_app.py` module built LATER.

Doc shape stored in Mongo (`admin_settings._id="github_app_config"`):
    {
      "_id":           "github_app_config",
      "app_id":        "123456",
      "app_slug":      "aurem-devops",
      "private_key":   "-----BEGIN RSA PRIVATE KEY-----\\n...\\n-----END RSA PRIVATE KEY-----\\n",
      "webhook_secret": "<opaque>",
      "updated_at":    <epoch>,
      "updated_by":    "<admin email>",
    }
"""
from __future__ import annotations

from typing import Optional

# In-process cache. Keys mirror the doc fields exactly (minus _id and
# audit metadata). An empty dict means "not configured yet" — the
# GitHub App integration should short-circuit / disable itself
# gracefully in that case.
_RUNTIME_GITHUB_APP: dict = {}

# Canonical field list — single source of truth for what an admin
# must paste to make the App usable. Keep in sync with:
#   • routers/admin.py::GitHubAppConfigBody
#   • frontend/src/pages/Admin.jsx::GitHubAppConfigCard
REQUIRED_FIELDS = ("app_id", "app_slug", "private_key", "webhook_secret")


def set_runtime_github_app_config(cfg: Optional[dict]) -> None:
    """Hot-swap the runtime GitHub App config for this process.

    Accepts a dict with keys from `REQUIRED_FIELDS`; any extras are
    silently ignored. Empty/missing values are treated as "unset" and
    result in an empty cache (integration effectively disabled).

    Called by:
      * `main.py` lifespan — boot-time hydration from Mongo.
      * `POST /admin/github-app-config` — founder rotates without restart.
    """
    global _RUNTIME_GITHUB_APP
    src = cfg or {}
    trimmed = {
        f: (str(src.get(f) or "")).strip()
        for f in REQUIRED_FIELDS
    }
    # All-or-nothing: unless every required field is present, treat
    # the cache as empty so callers can rely on a single truthiness
    # check (`if get_runtime_github_app_config():`).
    if all(trimmed.values()):
        _RUNTIME_GITHUB_APP = trimmed
    else:
        _RUNTIME_GITHUB_APP = {}


def get_runtime_github_app_config() -> dict:
    """Return a copy of the current runtime GitHub App config.

    Empty dict → integration not configured. Non-empty → all four
    fields present and non-blank (see `set_runtime_github_app_config`
    all-or-nothing guarantee).
    """
    return dict(_RUNTIME_GITHUB_APP)


def is_configured() -> bool:
    """Convenience check — True when every required field is set."""
    return bool(_RUNTIME_GITHUB_APP)


__all__ = [
    "REQUIRED_FIELDS",
    "set_runtime_github_app_config",
    "get_runtime_github_app_config",
    "is_configured",
]
