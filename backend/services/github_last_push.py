"""
services/github_last_push.py — Iter arch-2a boundary-violation fix

Relocated VERBATIM from routers/version.py (no logic changes).
`_fetch_last_github_push` made a raw `httpx.AsyncClient` call directly
inside a router file — flagged by `services/architecture_health.py`'s
"http-call-outside-services" boundary rule. Moving it here (a plain
services-layer GitHub REST helper) fixes the violation without
changing behaviour: same cache dict, same TTL, same honest-empty
fallback when GITHUB_ACTIONS_TOKEN / GITHUB_REPO aren't set.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import httpx

_GH_PUSH_CACHE: dict = {"value": None, "expires_at": 0.0}
_GH_PUSH_CACHE_TTL_S = int(os.environ.get("GH_PUSH_CACHE_TTL_S", "60"))


async def fetch_last_github_push() -> Optional[dict]:
    """Return {'commit_sha', 'pushed_at', 'html_url', 'message'} for
    the most recent commit on the tracked repo's default branch, or
    None when GITHUB_ACTIONS_TOKEN / GITHUB_REPO are missing or the
    upstream call fails. Cached to avoid hammering the GitHub REST API.
    """
    now = time.time()
    if _GH_PUSH_CACHE["value"] is not None and now < _GH_PUSH_CACHE["expires_at"]:
        return _GH_PUSH_CACHE["value"]
    token = os.environ.get("GITHUB_ACTIONS_TOKEN")
    repo  = os.environ.get("GITHUB_REPO")
    if not token or not repo:
        # Cache the None too — otherwise every /version call retries
        # a missing config, wastes CPU.
        _GH_PUSH_CACHE["value"] = None
        _GH_PUSH_CACHE["expires_at"] = now + _GH_PUSH_CACHE_TTL_S
        return None
    try:
        url = f"https://api.github.com/repos/{repo}/commits?per_page=1"
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(url, headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            })
        if r.status_code != 200:
            _GH_PUSH_CACHE["value"] = None
            _GH_PUSH_CACHE["expires_at"] = now + _GH_PUSH_CACHE_TTL_S
            return None
        data = r.json() or []
        if not data:
            _GH_PUSH_CACHE["value"] = None
            _GH_PUSH_CACHE["expires_at"] = now + _GH_PUSH_CACHE_TTL_S
            return None
        c = data[0]
        # Public /version endpoint — drop `message` and `html_url` so
        # private-repo commit context (ticket refs, internal naming)
        # doesn't leak to unauthenticated visitors. `commit_sha` +
        # `pushed_at` are enough for AdminSystemHealth's Deploy Sync
        # card to render "Pushed to GitHub …".
        payload = {
            "commit_sha": (c.get("sha") or "")[:12],
            "pushed_at":  ((c.get("commit") or {}).get("committer") or {})
                          .get("date"),
        }
        _GH_PUSH_CACHE["value"] = payload
        _GH_PUSH_CACHE["expires_at"] = now + _GH_PUSH_CACHE_TTL_S
        return payload
    except Exception:
        _GH_PUSH_CACHE["value"] = None
        _GH_PUSH_CACHE["expires_at"] = now + _GH_PUSH_CACHE_TTL_S
        return None
