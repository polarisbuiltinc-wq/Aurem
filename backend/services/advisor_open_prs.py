"""
services/advisor_open_prs.py — Open-PR fetch for the Ask Advisor panel.

Extracted from routers/advisor_context.py (2026-08-27, mechanical
split — no behaviour change) to keep that router under the platform's
file-size guard.
"""
from __future__ import annotations

from typing import Optional

import httpx


async def fetch_open_prs(gh_owner: Optional[str], gh_repo: Optional[str]) -> dict:
    """Fetch up to 10 open PRs for `gh_owner/gh_repo` via unauthenticated
    GitHub API. Public repos work; private repos 404 gracefully. Hard
    4s timeout so a slow GitHub can't stall the advisor."""
    open_prs: dict = {"items": [], "error": None, "count": 0}
    if not (gh_owner and gh_repo):
        open_prs["error"] = "repo_not_configured"
        return open_prs
    try:
        async with httpx.AsyncClient(timeout=4.0) as cx:
            r = await cx.get(
                f"https://api.github.com/repos/{gh_owner}/{gh_repo}/pulls",
                params={"state": "open", "per_page": 10, "sort": "updated"},
                headers={"Accept": "application/vnd.github+json"},
            )
            if r.status_code == 200:
                _prs = r.json() or []
                open_prs["count"] = len(_prs)
                for pr in _prs[:10]:
                    open_prs["items"].append({
                        "number":     pr.get("number"),
                        "title":      (pr.get("title") or "")[:200],
                        "author":     (pr.get("user") or {}).get("login"),
                        "draft":      bool(pr.get("draft")),
                        "created_at": pr.get("created_at"),
                        "updated_at": pr.get("updated_at"),
                        "url":        pr.get("html_url"),
                    })
            elif r.status_code == 404:
                # Private repo without a token, or repo doesn't
                # exist — treat as "no data available", never guess.
                open_prs["error"] = "repo_not_public_or_missing"
            else:
                open_prs["error"] = f"github_{r.status_code}"
    except Exception as e:                                       # noqa: BLE001
        open_prs["error"] = f"github_fetch_failed: {str(e)[:60]}"
    return open_prs
