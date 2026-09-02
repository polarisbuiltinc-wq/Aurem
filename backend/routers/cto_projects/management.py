"""
routers/cto_projects/management.py — AUREM CTO Projects.
Project CRUD (add/list/remove/update), GitHub App PAT check/verify/
test, and read-only file-tree/file-content browsing + live-URL
auto-detect.

Split from the former monolithic routers/cto_projects.py on
2026-09-08 (responsibility-based extraction, no logic change). Uses
`_pkg.<name>` for anything patched at the package level by the
existing test suite (`current_dev`, `get_db`, `require_db`,
`gh_api_fetch_file`) — see preview.py's module docstring for why.
"""
import asyncio
import logging
import re
import time
import uuid
from typing import Optional

from fastapi import Header, HTTPException
from pydantic import BaseModel

from services.cto_projects_helpers import (
    _parse_repo, _run_project_indexing,
    _BROWSE_SKIP_DIRS, _BROWSE_SKIP_EXTS, _BROWSE_MAX_FILE_BYTES,
    _browse_keep_path,
)

import routers.cto_projects as _pkg
from . import router

logger = logging.getLogger(__name__)


class AddProject(BaseModel):
    name: str
    github_url: str
    github_token: Optional[str] = None  # PAT; fall back to user's OAuth token
    branch: str = "main"
    tech_stack: Optional[str] = None
    preview_url: Optional[str] = None   # public URL of the running site/app
    # 2026-02-10 · Phase 3a — GitHub App path (additive to PAT).
    # When present, the gate treats this as the App-install branch:
    # verifies the caller owns the installation, then persists the row
    # with auth_method="github_app" and installation_id set. `github_token`
    # is ignored in that branch (installation_id wins per Phase 3
    # decision #1). NEVER stored — installation access tokens are minted
    # fresh per-request via services.github_app.
    installation_id: Optional[int] = None
    # 2026-08-24 — GitHub-connect funnel stitching: the wizard passes its
    # localStorage funnel session so the server-side `repo_selected`
    # event (fired at add-success, the moment of truth) joins the same
    # journey as cta_click/oauth_redirect/linked.
    funnel_session: Optional[str] = None


async def get_repo_token(project: dict) -> Optional[str]:
    from services.pat_vault import get_repo_token as _svc_get_repo_token
    return await _svc_get_repo_token(project)


@router.get("/projects/{project_id}/check-pat")
async def check_project_pat(
    project_id: str,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Iter 153 FIX 3 — PAT health check.

    Decrypts the stored token, hits `GET https://api.github.com/user`,
    and reports `valid` / `expired` / `missing` along with the upstream
    `github-authentication-token-expiration` header if GitHub returned
    one. Used by Projects.jsx to toast the user if their PAT is gone
    or expiring within 7 days.
    """
    me = await _pkg.current_dev(authorization)
    user_id = me["user_id"]
    db = _pkg.get_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "github_token": 1,
         "auth_method": 1, "installation_id": 1, "user_id": 1},
    )
    if not proj:
        raise HTTPException(404, "project not found")
    from services.pat_vault import get_repo_token_or_error
    token, _auth_err, _auth_detail = await get_repo_token_or_error(proj)
    if not token:
        return {"ok": True, "state": "missing",
                "message": f"GitHub App auth unavailable ({_auth_err})"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as cx:
            r = await cx.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "aurem-cto-pat-check",
                },
            )
    except Exception as e:
        return {"ok": True, "state": "unknown",
                "message": f"check failed: {type(e).__name__}"}

    expires_at = r.headers.get("github-authentication-token-expiration")
    if r.status_code == 200:
        return {
            "ok": True, "state": "valid",
            "expires_at": expires_at,
            "login": (r.json() or {}).get("login"),
        }
    if r.status_code in (401, 403):
        return {
            "ok": True, "state": "expired",
            "message": "PAT rejected by GitHub — please rotate.",
            "expires_at": expires_at,
        }
    return {
        "ok": True, "state": "unknown",
        "message": f"GitHub returned HTTP {r.status_code}",
        "expires_at": expires_at,
    }




# ── Endpoints ────────────────────────────────────────────────────────────
@router.post("/projects/add")
async def add_project(body: AddProject, authorization: str = Header(None)) -> dict:
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    owner, repo = _parse_repo(body.github_url)

    # 2026-08-20 · funnel visibility — attempt event fires before any
    # verification, so a stalled/abandoned add still leaves a trace
    # even if the user never reaches success or a clean failure.
    from services.signup_guards import emit_funnel_event
    await emit_funnel_event(
        db, user_id=me["user_id"], event_type="project_add_attempt",
        metadata={"github_url": body.github_url,
                  "auth_mode": "installation" if body.installation_id else "pat"},
    )

    async def _fail(status_code: int, payload, reason: str):
        """Emit a project_add_failure funnel event, then raise. Every
        rejection branch below routes through here so a stalled/failed
        add finally leaves a trace in the Admin Activity Log instead
        of the silent gap that made real signups look inactive."""
        await emit_funnel_event(
            db, user_id=me["user_id"], event_type="project_add_failure",
            metadata={"github_url": body.github_url, "reason": reason,
                      "status_code": status_code},
        )
        raise HTTPException(status_code, payload)

    # 2026-09-01 — D2 (connect-flow refinement): picking a repo that's
    # ALREADY one of this user's projects used to either silently no-op
    # or produce a confusing generic error (the "Michael-loop" — "I
    # already connected, why is it saying connect again?"). Check BEFORE
    # any GitHub verification call so a duplicate never even hits the
    # network, and return the existing project so the UI can offer a
    # direct "Open my project" redirect instead of a dead end.
    existing = await db.cto_projects.find_one(
        {
            "user_id": me["user_id"],
            "github_owner": {"$regex": f"^{re.escape(owner)}$", "$options": "i"},
            "github_repo":  {"$regex": f"^{re.escape(repo)}$",  "$options": "i"},
        },
        {"_id": 0, "project_id": 1, "name": 1},
    )
    if existing:
        await _fail(409, {
            "error": "already_connected",
            "message": f"'{owner}/{repo}' is already your project '{existing.get('name')}'.",
            "project_id":   existing["project_id"],
            "project_name": existing.get("name"),
        }, "already_connected")

    # ═══════════════════════════════════════════════════════════════
    # 2026-02-10 · Phase 3a — dual-auth gate
    # ═══════════════════════════════════════════════════════════════
    # Accepts EITHER:
    #   (a) `installation_id` — App-install path (recommended, seamless)
    #   (b) `github_token`    — PAT path (legacy, still fully supported)
    #
    # When BOTH are provided, installation_id wins silently (Phase 3
    # decision #1). The PAT is ignored — never persisted, never
    # verified. This behavior is deliberate so a future wizard UX that
    # accidentally sends both can't corrupt state or double-charge
    # GitHub rate limits.
    installation_id = body.installation_id
    pat = (body.github_token or "").strip() or None

    # Values that both branches populate for the shared insert below.
    auth_method:     str
    encrypted_token: Optional[str]
    installation_active: Optional[bool] = None
    pat_verified_flag = True   # both branches verify against real GitHub

    import httpx as _httpx

    # ── Branch A: GitHub App install ────────────────────────────────
    if installation_id:
        # 2026-08-26 — now the SAME shared helper `update_project`'s
        # reconnect path uses, so the two flows can never verify
        # differently again (see services/github_app.py::
        # verify_installation_for_repo for the full root-cause note).
        from services.github_app import verify_installation_for_repo
        ok, err_code, err_msg = await verify_installation_for_repo(
            db, user_id=me["user_id"], installation_id=int(installation_id),
            owner=owner, repo=repo,
        )
        if not ok:
            status = 502 if err_code in (
                "github_probe_failed", "installation_probe_request_error",
            ) else 400
            await _fail(status, {"error": err_code, "message": err_msg}, err_code)
        auth_method         = "github_app"
        encrypted_token     = None   # never stored — token minted per-request
        installation_active = True

    # ── Branch B: PAT — REMOVED (founder directive 2026-06) ─────────
    # PATs are no longer accepted as an auth method, ever — not as
    # primary, not as fallback. Reject explicitly with an honest error
    # instead of silently ignoring the field.
    elif pat:
        await _fail(400, {
            "error":   "pat_not_supported",
            "message": (
                "Personal Access Tokens are no longer supported. "
                "Connect this repo via the AUREM GitHub App instead — "
                "it's the only supported auth method."
            ),
        }, "pat_not_supported")

    # ── Neither provided ────────────────────────────────────────────
    else:
        await _fail(400, {
            "error": "auth_required",
            "message": (
                "Connect this repo via the AUREM GitHub App — the only "
                "supported auth method (PAT support was removed)."
            ),
        }, "auth_required")

    proj_id = f"p_{uuid.uuid4().hex[:10]}"
    doc = {
        "project_id": proj_id, "user_id": me["user_id"],
        "name": body.name, "github_url": body.github_url,
        "github_owner": owner, "github_repo": repo,
        "github_token": encrypted_token,       # None for github_app branch
        "auth_method": auth_method,
        # 2026-02-10 · Phase 3a — installation binding (only set for
        # github_app branch; None/absent for PAT branch preserves
        # perfect backward compat with legacy rows).
        "installation_id":     int(installation_id) if installation_id else None,
        "installation_active": installation_active,
        "branch": body.branch, "tech_stack": body.tech_stack or "auto",
        "preview_url": (body.preview_url or "").strip() or None,
        "status": "connected", "tasks_done": 0,
        # Iter 212m-75 — async indexing pipeline. Endpoint returns
        # immediately; background task flips status to ready/error.
        "indexing_status":  "indexing",
        "indexing_error":   None,
        "indexed_at":       None,
        "indexing_started_at": time.time(),
        "created_at": time.time(),
    }
    await db.cto_projects.insert_one(doc)

    # Iter 212m-75 — fire-and-forget indexing wrapper. Wraps the legacy
    # build_brain_v2 with explicit status writes so the FE can poll
    # /indexing-status and show a progress spinner instead of guessing.
    #
    # Phase 3a: for the github_app branch, mint a fresh installation
    # token here so the indexer has a working credential. For PAT the
    # decrypted plaintext is already in `pat`.
    try:
        if auth_method == "github_app":
            from services.github_app import get_installation_token
            _ix_token, _ = await get_installation_token(int(installation_id))
        else:
            _ix_token = pat
        asyncio.create_task(_run_project_indexing(
            db=db, project_id=proj_id, user_id=me["user_id"],
            github_token=_ix_token, github_owner=owner, github_repo=repo,
            branch=body.branch or "main",
        ))
        # 2026-08-22 — auto background deep-scan so the Prompt Starter
        # panel has real "FROM YOUR REPO" findings the moment the user
        # lands on the empty chat, instead of waiting for their first
        # Ship or a founder-only manual health scan. Fire-and-forget,
        # same error-swallowing contract as the indexing task above.
        from services.project_onboarding_scan import run_onboarding_scan
        asyncio.create_task(run_onboarding_scan(
            db=db, user_id=me["user_id"], project_id=proj_id,
            github_token=_ix_token, github_owner=owner, github_repo=repo,
        ))
        # Onboarding Step 4 · S-B (2026-08-26) — the first-scan aha.
        # Separate from the scan above (that one quietly feeds the
        # Prompt Starter chips); this one produces the loud "I found
        # N things, here's the #1 one" results card + "Fix this for
        # me" CTA. Fire-and-forget, same error-swallowing contract.
        from services.onboarding_first_scan import trigger_first_scan
        asyncio.create_task(trigger_first_scan(
            db=db, user_id=me["user_id"], project_id=proj_id,
        ))
    except Exception as _bbe:
        logger.warning("indexing scheduler skipped: %r", _bbe)

    resp = {
        "ok":                True,
        "project_id":        proj_id,
        "owner":             owner,
        "repo":              repo,
        "auth_method":       doc["auth_method"],
        "indexing_status":   "indexing",
        "message":           "Indexing your repository in the background...",
        "pat_verified":      pat_verified_flag,
    }
    if auth_method == "github_app":
        resp["installation_id"] = int(installation_id)

    await emit_funnel_event(
        db, user_id=me["user_id"], event_type="project_add_success",
        metadata={"project_id": proj_id, "owner": owner, "repo": repo,
                  "auth_method": auth_method},
    )

    # 2026-08-27 · Journey Watch Phase 0 — canonical `project_connected`
    # event (user_id-keyed, same collection as chat_opened/graph_built/
    # first_loop_started) so Journey Watch's stall classifier has one
    # consistent source for every stage past the GitHub-connect flow.
    await emit_funnel_event(
        db, user_id=me["user_id"], event_type="project_connected",
        metadata={"project_id": proj_id, "owner": owner, "repo": repo,
                  "auth_method": auth_method},
    )

    # 2026-08-24 — GitHub Connect funnel: `repo_selected` was a declared
    # stage that NO code ever emitted (root cause of the perpetual
    # "Repo picked: 0"). Fire it server-side at the moment of truth — a
    # project row actually created — so the stage measures reality and
    # can never be lost to client-side flakiness. Deterministic
    # per-user fallback session keeps retries deduped.
    from routers.github_funnel import track_server_side as _gh_funnel_track
    await _gh_funnel_track(
        "repo_selected", source="wizard",
        session_id=body.funnel_session or f"srv:uid:{me['user_id']}",
        user_id=me["user_id"],
        meta={"project_id": proj_id, "auth_method": auth_method},
    )
    return resp


@router.get("/projects/{project_id}/indexing-status")
async def project_indexing_status(
    project_id: str,
    authorization: str = Header(None),
) -> dict:
    """Iter 212m-75 — poll endpoint for FE to track async indexing.
    Returns: {status: "indexing"|"ready"|"error", error, indexed_at}.
    """
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": me["user_id"]},
        {"_id": 0, "indexing_status": 1, "indexing_error": 1,
         "indexed_at": 1, "indexing_started_at": 1, "name": 1},
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    status = proj.get("indexing_status") or "ready"  # legacy rows = ready
    return {
        "ok":          True,
        "project_id":  project_id,
        "name":        proj.get("name"),
        "status":      status,
        "error":       proj.get("indexing_error"),
        "indexed_at":  proj.get("indexed_at"),
        "started_at":  proj.get("indexing_started_at"),
        "ready":       status == "ready",
    }


# ─────────────────────────────────────────────────────────────────────
# Iter 212 — Pre-save PAT verification (stateless, no DB write).
# Called by AddProject Step 2 with a debounce after the user pastes a
# token, so they get inline green/red feedback BEFORE clicking Connect.
#
# Uses POST (not GET) so the raw PAT never lands in browser history or
# proxy access logs — small but real security win vs. query strings.
# ─────────────────────────────────────────────────────────────────────
class VerifyPatBody(BaseModel):
    # 2026-08-24 — all fields optional: this endpoint's ONLY job is an
    # honest pat_not_supported rejection, so a stale caller with any
    # legacy body shape (repo / github_url / pat) must reach it instead
    # of bouncing off a confusing 422 validation error.
    repo: Optional[str] = None        # "owner/name" (legacy)
    pat:  Optional[str] = None        # ghp_… or github_pat_… (never used)
    github_url: Optional[str] = None  # older callers sent a full URL


@router.post("/projects/verify-pat")
async def verify_pat(
    body: VerifyPatBody,
    authorization: str = Header(None),
) -> dict:
    """2026-06 PAT-removal — PAT validation is permanently retired.
    Kept as an honest 200-shaped rejection (not a confusing 404) for
    any stale UI/API caller."""
    await _pkg.current_dev(authorization)
    return {
        "ok": False,
        "error": "pat_not_supported",
        "detail": ("Personal Access Tokens are no longer supported. "
                   "Connect this repo via the AUREM GitHub App — the "
                   "only supported auth method."),
    }


@router.get("/projects/list")
async def list_projects(authorization: str = Header(None)) -> dict:
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    projs = await db.cto_projects.find(
        {"user_id": me["user_id"]},
        {"_id": 0},        # need github_token presence; strip ciphertext below
    ).sort("created_at", -1).to_list(50)
    # Iter 206 — surface a boolean `has_pat` flag (without ever leaking
    # the encrypted token itself) so the Projects sidebar can render a
    # green/amber PAT pill per row.
    for p in projs:
        p["has_pat"] = bool(p.get("github_token"))
        p.pop("github_token", None)
    return {"ok": True, "projects": projs}


@router.delete("/projects/{project_id}")
async def remove_project(project_id: str, authorization: str = Header(None)) -> dict:
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    r = await db.cto_projects.delete_one({"project_id": project_id, "user_id": me["user_id"]})
    return {"ok": True, "deleted": r.deleted_count}


# Iter 170c — Codebase browsing for the right-side </> Code preview.
#
# When the user hits the `</> Code` toggle in PreviewPanel and there's
# no recently-shipped task to display, the panel used to fall back to
# the project's `preview_url` (just a URL string). The new flow:
#
#   GET  /cto/projects/{id}/tree                 → paths only
#   GET  /cto/projects/{id}/file?path=src/x.py   → single file content
#
# Both endpoints scope to the project's connected GitHub PAT (decrypted
# from Mongo) and the project's branch. They are read-only and never
# touch the working tree on disk; everything goes through the GitHub
# REST API so no `git` binary is required.
# (_BROWSE_SKIP_DIRS / _BROWSE_SKIP_EXTS / _BROWSE_MAX_FILE_BYTES /
#  _browse_keep_path now live in services/cto_projects_helpers.py —
#  see the re-export import block near the top of this file.)



# ───────────────────────────────────────────────────────────────────
# Iter 207 — PAT connection test. Replaces the "save and pray" flow
# in the PatModal: after the user saves a token we hit GitHub's
# `/repos/{owner}/{repo}` and surface a definitive pass/fail back to
# the modal so they know immediately if the token works.
# ───────────────────────────────────────────────────────────────────
@router.get("/projects/{project_id}/test-pat")
async def test_project_pat(
    project_id: str,
    authorization: str = Header(None),
) -> dict:
    """Verify the project's stored PAT (or fallback OAuth token) can
    read the connected GitHub repo. Returns a uniform shape so the
    frontend never has to branch on HTTP status:

      {ok: true,  repo: "owner/name", private: bool}
      {ok: false, error: "<human-readable reason>"}

    HTTP status is always 200 — error is encoded in `ok`. This keeps
    the React Query / axios paths simple.
    """
    me = await _pkg.current_dev(authorization)
    user_id = me["user_id"]
    db = _pkg.require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Project not found")

    owner = (proj.get("github_owner") or "").strip()
    repo  = (proj.get("github_repo")  or "").strip()
    if not (owner and repo):
        return {"ok": False, "error": "Project has no repo configured."}

    from services.pat_vault import get_repo_token_or_error
    gh_token, _auth_err, _auth_detail = await get_repo_token_or_error(proj)
    if _auth_err:
        raise HTTPException(403, f"GitHub App auth failed ({_auth_err}): {_auth_detail}")
    if not gh_token:
        return {
            "ok": False,
            "error": "GitHub App access is not linked for this project. "
                     "Reconnect via Projects → APP.",
        }

    import httpx
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {gh_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(url, headers=headers)
    except httpx.RequestError as e:
        logger.warning("test-pat: network error %s/%s: %r", owner, repo, e)
        return {"ok": False, "error": f"Couldn't reach GitHub ({type(e).__name__})."}

    if r.status_code == 200:
        try:
            data = r.json() or {}
        except Exception:  # noqa: BLE001
            data = {}
        return {
            "ok":      True,
            "repo":    data.get("full_name") or f"{owner}/{repo}",
            "private": bool(data.get("private", False)),
        }
    if r.status_code in (401, 403):
        return {
            "ok":    False,
            "error": "GitHub rejected the App credentials — the installation "
                     "may be suspended or revoked. Reconnect via "
                     "**Projects → APP** (AUREM GitHub App).",
        }
    if r.status_code == 404:
        return {
            "ok":    False,
            "error": f"Repo not found at github.com/{owner}/{repo}. The repo "
                     "may be private, or the App installation doesn't cover "
                     "it. On GitHub → the AUREM App → Configure → Repository "
                     "access, add this repo.",
        }
    return {
        "ok":    False,
        "error": f"GitHub returned HTTP {r.status_code}. Try reconnecting "
                 "via Projects → APP.",
    }



@router.get("/projects/{project_id}/tree")
async def get_project_tree(
    project_id: str,
    authorization: str = Header(None),
) -> dict:
    """Return the list of source-file paths in the connected GitHub repo
    at the project's pinned branch. Filtered to source files only
    (no node_modules, no binaries, no >200KB blobs).

    Used by PreviewPanel's `</> Code` toggle to let the user browse
    the live codebase without leaving the chat. Results are capped at
    300 files; truncated=True is returned if the tree was deeper.
    """
    me = await _pkg.current_dev(authorization)
    user_id = me["user_id"]
    db = _pkg.require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    from services.pat_vault import get_repo_token_or_error
    gh_token, _auth_err, _auth_detail = await get_repo_token_or_error(proj)
    if _auth_err:
        raise HTTPException(403, f"GitHub App auth failed ({_auth_err}): {_auth_detail}")
    owner = proj.get("github_owner") or ""
    repo  = proj.get("github_repo") or ""
    branch = proj.get("branch") or "main"
    if not (owner and repo and gh_token):
        raise HTTPException(400, "GitHub not connected to this project")

    import httpx
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {gh_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = (
        f"https://api.github.com/repos/{owner}/{repo}"
        f"/git/trees/{branch}?recursive=1"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(url, headers=headers)
        if r.status_code == 404:
            raise HTTPException(404, f"Branch {branch} not found on GitHub")
        if r.status_code == 401:
            raise HTTPException(401, "GitHub PAT invalid or expired")
        r.raise_for_status()
        data = r.json()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[tree] GitHub fetch failed: {e!r}")
        raise HTTPException(502, f"GitHub API error: {e}")

    items = []
    for node in (data.get("tree") or []):
        if node.get("type") != "blob":
            continue
        path = node.get("path") or ""
        size = int(node.get("size") or 0)
        if not _browse_keep_path(path, size):
            continue
        items.append({"path": path, "size": size})
    # Sort: README first, then root-level configs, then by depth, then alpha
    def _sort_key(it):
        p = it["path"].lower()
        depth = p.count("/")
        is_readme = 0 if p.startswith("readme") else 1
        is_root_config = 0 if depth == 0 and any(
            p.endswith(s) for s in ("package.json", "requirements.txt",
                                     "pyproject.toml", "dockerfile", ".env.example")
        ) else 1
        return (is_readme, is_root_config, depth, p)
    items.sort(key=_sort_key)
    truncated = bool(data.get("truncated")) or len(items) > 300
    items = items[:300]
    return {
        "ok": True, "project_id": project_id,
        "owner": owner, "repo": repo, "branch": branch,
        "files": items, "truncated": truncated,
    }


@router.get("/projects/{project_id}/file")
async def get_project_file(
    project_id: str,
    path: str,
    authorization: str = Header(None),
) -> dict:
    """Fetch a single file's content from the connected GitHub repo at
    the project's pinned branch. Capped at 200KB; bigger files return
    a truncated marker so the UI shows a clean message instead of OOM.
    """
    me = await _pkg.current_dev(authorization)
    user_id = me["user_id"]
    db = _pkg.require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0}
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    from services.pat_vault import get_repo_token_or_error
    gh_token, _auth_err, _auth_detail = await get_repo_token_or_error(proj)
    if _auth_err:
        raise HTTPException(403, f"GitHub App auth failed ({_auth_err}): {_auth_detail}")
    owner = proj.get("github_owner") or ""
    repo  = proj.get("github_repo") or ""
    branch = proj.get("branch") or "main"
    if not (owner and repo and gh_token):
        raise HTTPException(400, "GitHub not connected to this project")
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise HTTPException(400, "Invalid path")

    try:
        content = await _pkg.gh_api_fetch_file(owner, repo, path, branch, gh_token)
    except Exception as e:
        logger.warning(f"[file] fetch failed for {path}: {e!r}")
        raise HTTPException(502, f"GitHub API error: {e}")
    if content is None:
        raise HTTPException(404, f"File not found: {path}")
    truncated = False
    if len(content.encode("utf-8", errors="replace")) > _BROWSE_MAX_FILE_BYTES:
        # Trim to byte budget without breaking utf-8 mid-codepoint.
        b = content.encode("utf-8", errors="replace")[:_BROWSE_MAX_FILE_BYTES]
        content = b.decode("utf-8", errors="replace") + "\n\n# … (truncated)"
        truncated = True
    return {
        "ok": True, "project_id": project_id,
        "path": path, "content": content, "truncated": truncated,
    }


@router.get("/projects/{project_id}/detect-live-url")
async def detect_live_url(
    project_id: str,
    authorization: str = Header(None),
) -> dict:
    """S1-P4 — best-effort, deterministic (0-LLM) auto-detect of the
    project's live-site URL from repo config, checked BEFORE showing
    the existing manual AddLiveSiteModal. Tries vercel.json,
    netlify.toml, then package.json's `homepage` — in that order.
    Reuses the SAME _pkg.gh_api_fetch_file() call as /file (L17). Never
    invents a URL: {"ok": True, "url": ""} means "nothing found,
    fall back to manual entry", never an error."""
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": me["user_id"]},
        {"_id": 0, "repo_index_summary": 0, "brain_text": 0,
         "repo_index_blocks": 0, "last_commit_diff": 0},
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    from services.pat_vault import get_repo_token_or_error
    gh_token, auth_err, _detail = await get_repo_token_or_error(proj)
    owner = proj.get("github_owner") or ""
    repo = proj.get("github_repo") or ""
    branch = proj.get("branch") or "main"
    if auth_err or not (owner and repo and gh_token):
        return {"ok": True, "url": "", "source": None}

    from services.preview_capture import detect_live_url_from_config
    for candidate in ("vercel.json", "netlify.toml", "package.json"):
        try:
            content = await _pkg.gh_api_fetch_file(owner, repo, candidate, branch, gh_token)
        except Exception:
            content = None
        if not content:
            continue
        url = detect_live_url_from_config(candidate, content)
        if url:
            return {"ok": True, "url": url, "source": candidate}
    return {"ok": True, "url": "", "source": None}


class UpdateProject(BaseModel):
    github_token: Optional[str] = None
    branch: Optional[str] = None
    tech_stack: Optional[str] = None
    preview_url: Optional[str] = None
    installation_id: Optional[int] = None


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: str,
    body: UpdateProject,
    authorization: str = Header(None),
) -> dict:
    """Update PAT / branch / tech stack of an existing project."""
    me = await _pkg.current_dev(authorization)
    db = _pkg.require_db()
    updates = {k: v for k, v in body.model_dump().items() if v is not None and v != ""}
    if not updates:
        raise HTTPException(400, "Nothing to update")
    # 2026-06 PAT-removal — PATs are no longer accepted on update either.
    if "github_token" in updates and updates["github_token"]:
        raise HTTPException(400, "PATs are no longer supported. Connect via the AUREM GitHub App instead.")
    # 2026-08-20 — Auto-Reconnect Prompt. Re-attaching a project to a
    # fresh GitHub App installation (after the user revoked/reinstalled
    # it) takes over as the auth method, same precedence rule as
    # `add_project` (installation_id wins over any stored PAT).
    #
    # 2026-08-26 — ROOT CAUSE FIX. This used to set `auth_method=
    # "github_app"` WITHOUT ever setting `installation_active=True` —
    # `add_project` (new project) verified + set the flag; this
    # reconnect path (existing project) set neither the flag nor did
    # any verification at all, silently trusting the client-supplied
    # installation_id. `PatRequiredCTA.jsx` gates on
    # `auth_method === "github_app" && installation_active` — so a
    # reconnect could succeed on GitHub's side while the "not
    # connected" banner never cleared, because the one flag it reads
    # was never written. Now runs the SAME shared verification
    # `add_project` uses before trusting the reconnect.
    if "installation_id" in updates:
        proj = await db.cto_projects.find_one(
            {"project_id": project_id, "user_id": me["user_id"]},
            {"_id": 0, "github_owner": 1, "github_repo": 1},
        )
        if not proj:
            raise HTTPException(404, "Project not found")
        from services.github_app import verify_installation_for_repo
        ok, err_code, err_msg = await verify_installation_for_repo(
            db, user_id=me["user_id"],
            installation_id=int(updates["installation_id"]),
            owner=proj.get("github_owner") or "", repo=proj.get("github_repo") or "",
        )
        if not ok:
            status = 502 if err_code in (
                "github_probe_failed", "installation_probe_request_error",
            ) else 400
            raise HTTPException(status, {"error": err_code, "message": err_msg})
        updates["auth_method"]         = "github_app"
        updates["installation_active"] = True
    r = await db.cto_projects.update_one(
        {"project_id": project_id, "user_id": me["user_id"]},
        {"$set": updates},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "Project not found")
    # PAT / branch changed → invalidate the cached repo context blob
    try:
        from services.repo_context import invalidate_repo_context
        await invalidate_repo_context(project_id)
    except Exception:
        pass
    # 2026-08-20 — drop the short-TTL connection-status cache too so the
    # banner's next poll reflects the reconnect immediately instead of
    # waiting out the 8 s cache window.
    try:
        from routers.repo_status import _CACHE as _conn_cache
        _conn_cache.pop(project_id, None)
    except Exception:
        pass
    return {"ok": True, "updated_fields": list(updates.keys())}


