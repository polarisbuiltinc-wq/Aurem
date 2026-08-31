"""
services/local_tools.py — First-party tools for AUREM orchestrator.

Iter 35 — Major upgrade to close the Emergent capability gap:

NEW TOOLS ADDED:
  read_repo_file      → read single file (existed)
  read_repo_files     → read UP TO 6 files IN PARALLEL (new — asyncio.gather)
  list_repo_files     → list repo tree, filterable by path/extension (new)
  search_repo         → grep pattern across all files in repo (new)
  read_multiple_lines → read specific line ranges from multiple files (new)

These 4 new tools bring AUREM from 1 local tool to 5, closing the
biggest practical gap vs Emergent (which can read 20 files at once via
mcp_view_bulk + mcp_glob_files pattern).

With parallel reads: a 6-file security fix that previously needed 6 tool
call iterations (6 × ~30s = 3 min wait) now takes 1 iteration (~30s).
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
import time
from typing import Optional

from cto_services.db import get_db
from .repo_context import _fetch_file as _gh_fetch_file
from .repo_context import GithubAuthError

# 2026-08-20 — shared error for the 3 tools that talk to GitHub
# directly. Gives the model an unambiguous, correct diagnosis instead
# of letting it guess ("file not found", "invalid query") when the
# real cause is a revoked App install / deleted PAT / expired OAuth.
def _github_access_revoked_error(owner: str, repo: str, status_code: int) -> dict:
    return {
        "ok": False,
        "error": (
            f"❌ GITHUB ACCESS REVOKED (HTTP {status_code}) for "
            f"{owner}/{repo}. This is NOT a missing file and NOT an "
            f"AUREM/auremcto.com API problem — GitHub itself rejected "
            f"the request. The user (or someone on their GitHub org) "
            f"has revoked the GitHub App installation, removed this "
            f"repo from it, deleted the Personal Access Token, or the "
            f"OAuth/PAT token expired. STOP calling repo tools — they "
            f"will all fail the same way until access is restored. "
            f"Tell the user clearly: 'GitHub access to {owner}/{repo} "
            f"was revoked — reconnect this repo from the project "
            f"settings (reinstall/grant the GitHub App, or add a new "
            f"token) to keep working.' Do not guess at file paths or "
            f"blame an unrelated API."
        ),
    }

# SEC-005 fix (2026-08-19) — a permissive path check (only blocking a
# leading `/` and `..`) let a filename containing shell metacharacters
# reach `orchestrator.py`'s post-edit build hook, which ran it inside
# `asyncio.create_subprocess_shell(...)` — a confirmed CRITICAL OS
# command-injection path. Reject any path outside this safe charset
# BEFORE the write (and the hook re-checks it independently too, since
# the hook's fix — argv-based subprocess, no shell — is the real lock;
# this is the deny-early defense-in-depth layer).
_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9_.\-/ ]")


def _is_safe_repo_path(path: str) -> bool:
    """True iff `path` contains only a conservative filename charset
    (letters, digits, space, dot, dash, underscore, `/`) — no shell
    metacharacters (`$`, backtick, `;`, `|`, `&`, parens, quotes,
    newlines, etc.) can ever reach a subprocess call downstream."""
    if not path or not isinstance(path, str):
        return False
    return not _UNSAFE_PATH_CHARS.search(path)

# ─── Iter 212m-152 — Mandatory syntax gate for write_repo_file ─────
# Runs `python -m py_compile` for .py, `node --check` for .js/.jsx,
# and `npx tsc --noEmit` for .ts/.tsx.  Each in a fresh tempfile so
# the project itself isn't touched.  Falls open on tooling errors
# (timeout, missing binary) so we never block a commit on local
# infra flake — better to ship than to deadlock.

def _run_syntax_check(*, content: str, file_path: str, ext: str) -> dict:
    """Returns {has_errors: bool, errors: str, skipped: bool, reason: str?}."""
    import os
    import subprocess
    import tempfile

    if not content:
        return {"has_errors": False, "errors": "",
                "skipped": True, "reason": "empty_content"}
    try:
        tmp = tempfile.NamedTemporaryFile(
            suffix=ext, mode="w", delete=False, encoding="utf-8",
        )
        tmp.write(content)
        tmp.close()
        tmp_path = tmp.name
    except Exception as e:                                # noqa: BLE001
        return {"has_errors": False, "errors": "",
                "skipped": True, "reason": f"tmp_write:{type(e).__name__}"}

    try:
        if ext == ".py":
            try:
                result = subprocess.run(
                    ["python", "-m", "py_compile", tmp_path],
                    capture_output=True, text=True, timeout=10,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                return {"has_errors": False, "errors": "",
                        "skipped": True,
                        "reason": f"py_check:{type(e).__name__}"}
            if result.returncode != 0:
                return {"has_errors": True,
                        "errors": (result.stderr or result.stdout or "")[:500],
                        "skipped": False}
        elif ext in (".js", ".jsx"):
            try:
                result = subprocess.run(
                    ["node", "--check", tmp_path],
                    capture_output=True, text=True, timeout=10,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                return {"has_errors": False, "errors": "",
                        "skipped": True,
                        "reason": f"js_check:{type(e).__name__}"}
            if result.returncode != 0:
                return {"has_errors": True,
                        "errors": (result.stderr or "")[:500],
                        "skipped": False}
        elif ext in (".ts", ".tsx"):
            # tsc may be slow; cap at 15 s and run with the frontend
            # tsconfig so JSX / module resolution match the project.
            try:
                result = subprocess.run(
                    ["npx", "tsc", "--noEmit", "--allowJs",
                     "--jsx", "preserve", "--target", "ES2020",
                     "--module", "esnext", "--moduleResolution", "node",
                     tmp_path],
                    capture_output=True, text=True, timeout=15,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                return {"has_errors": False, "errors": "",
                        "skipped": True,
                        "reason": f"ts_check:{type(e).__name__}"}
            if result.returncode != 0:
                # tsc surfaces errors on stdout, not stderr.
                err = (result.stdout or result.stderr or "")[:500]
                # Filter out "type errors" we don't actually care about
                # at this gate — we only block on PARSE errors (TS1xxx).
                # If the only errors are type-resolution (TS2xxx) we
                # let them through; downstream Vanguard / verify can
                # catch deeper issues.
                if "error TS1" in err:
                    return {"has_errors": True, "errors": err,
                            "skipped": False}
                # Type errors only → don't block the commit.
                return {"has_errors": False, "errors": "",
                        "skipped": True,
                        "reason": "ts_only_type_errors"}
        return {"has_errors": False, "errors": "", "skipped": False}
    finally:
        try:
            os.unlink(tmp_path)
        except Exception as _e:
            logger.debug("[silent-catch] services/local_tools.py:122 in _run_syntax_check — %r", _e)
            pass


from .web_skills import (
    WEB_TOOLS as _WEB_TOOLS,
    WEB_TOOL_SPECS as _WEB_TOOL_SPECS,
)
from .dev_skills import (
    DEV_TOOLS as _DEV_TOOLS,
    DEV_TOOL_SPECS as _DEV_TOOL_SPECS,
)
from .vercel_skills import (
    VERCEL_TOOLS as _VERCEL_TOOLS,
    VERCEL_TOOL_SPECS as _VERCEL_TOOL_SPECS,
)

logger = logging.getLogger(__name__)

MAX_FILE_CHARS = 15_000   # per file — iter 212i (was 12_000)
MAX_FILES_BULK = 6        # max files in one read_repo_files call


# ── Helper: resolve project from DB ──────────────────────────────────────────

async def _resolve_project(user_id: str, project_id: str) -> dict | None:
    """Return project doc or None if not found.

    Iter 212m-169 — This helper is NOW INTERNAL to
    `services/bin_context.py::build_bin_context`.  Every tool below
    reads user+project+PAT from `ctx["bin_ctx"]` (a BINContext) via
    `_repo_ctx_from(ctx)` instead of hitting the DB directly.

    Kept for legacy code paths that still resolve a project outside
    the request-scoped BINContext (e.g. `write_repo_file` fallback,
    admin tooling).  New code MUST use BINContext.

    Iter 205 — Critical fix: `cto_projects.github_token` is stored as
    ENCRYPTED ciphertext (Fernet `v1:…`). Tool functions calling GitHub's
    API with the raw ciphertext got `401 Bad credentials`. We now decrypt
    in-place and, when the project has no PAT (e.g. OAuth-only flow),
    fall back to the user's GitHub OAuth `access_token`.

    Iter 212m-169 — REMOVED the silent auto-infer that used to pick a
    project when caller passed null/empty project_id.  Silent inference
    could route a chat about Project 1 into Project 2's PAT.  If the
    caller doesn't pass a project_id we now return None and let the
    tool raise a clean error.
    """
    if not user_id:
        return None
    db = get_db()
    if db is None:
        return None

    pid_clean = (project_id or "").strip()
    if not pid_clean or pid_clean == "home":
        return None

    proj = await db.cto_projects.find_one(
        {"project_id": pid_clean, "user_id": user_id}
    )
    if proj is None:
        return None

    # 2026-02-11 · Phase 3b (Bug 2 fix) — dual-auth credential resolution.
    # `get_repo_token(proj)` handles both PAT (decrypt) and App-install
    # (fresh installation token). Falls back to org OAuth token when
    # neither is configured (legacy pre-Iter-211 rows).
    # 2026-06 PAT-removal — App-only, no OAuth fallback.
    from services.pat_vault import get_repo_token_or_error
    token, err_code, err_detail = await get_repo_token_or_error(proj)
    if err_code:
        logger.warning("local_tools._resolve_project: App auth failed "
                       "(%s): %s", err_code, err_detail)
    proj["github_token"] = token
    return proj


def _repo_ctx_from(ctx: dict) -> Optional[dict]:
    """Iter 212m-169 — BINContext accessor for repo tools.

    Returns a normalised dict:
      {ok, owner, repo, branch, token, is_founder, bin_id, pid}
    when the caller's ctx carries a valid BINContext whose bin_id
    matches ctx["user_id"] (defence in depth: rejects a ctx that was
    mutated to a different user mid-request).

    Returns None when:
      • ctx["bin_ctx"] is missing entirely (Home casual chat surface —
        the tool must refuse cleanly with a "select a project" hint),
      • ctx["bin_ctx"].bin_id does not equal ctx["user_id"] (privilege
        violation — should never happen, log-and-reject).

    Tools should call `_repo_ctx_from(ctx)` FIRST and refuse
    immediately when it returns None.  They should NEVER fall back to
    an independent DB lookup — that's the entire point of BINContext.
    """
    bc = (ctx or {}).get("bin_ctx")
    if bc is None:
        return None
    # Cross-user guard: bin_id MUST match ctx["user_id"].  If they
    # differ, someone mutated the ctx post-build — refuse hard.
    caller_uid = (ctx or {}).get("user_id") or ""
    if getattr(bc, "bin_id", None) and caller_uid and bc.bin_id != caller_uid:
        logger.warning(
            "_repo_ctx_from: bin_ctx.bin_id=%s != ctx.user_id=%s — refusing",
            bc.bin_id, caller_uid,
        )
        return None
    if not (getattr(bc, "repo_owner", "") and getattr(bc, "repo_name", "")):
        return None
    return {
        "ok":         True,
        "owner":      bc.repo_owner,
        "repo":       bc.repo_name,
        "branch":     bc.branch or "main",
        "token":      bc.pat,
        "is_founder": bool(getattr(bc, "is_founder", False)),
        "bin_id":     bc.bin_id,
        "pid":        bc.pid,
    }


_NO_BIN_CTX_ERROR = {
    "ok": False,
    "error": (
        "No project selected. Please select a project from the sidebar "
        "and try again — repo tools cannot run against Home."
    ),
    "error_class": "no_bin_ctx",
}


def _verify_ctx(ctx: dict):
    """Iter 212m-170 — ORAContext / BINContext accessor.

    Returns the request-scoped context object (ORAContext or its
    parent BINContext) if it's present AND valid.  Returns None
    otherwise so the caller can respond with a soft tool error
    envelope (raising HTTPException from inside a tool breaks the
    orchestrator's tool-loop; tools must return dicts).

    Validity checks:
      • ctx["bin_ctx"] is a BINContext (or ORAContext subclass)
      • bin_ctx.bin_id matches ctx["user_id"] (cross-user tamper
        detection — see local_tools._repo_ctx_from for the same
        check on the repo-tool path).
      • For ORAContext with ora_boundary_active=False, the caller
        must be a founder (defence in depth against a mutated ctx).
    """
    bc = (ctx or {}).get("bin_ctx")
    if bc is None:
        return None
    caller_uid = (ctx or {}).get("user_id") or ""
    if getattr(bc, "bin_id", None) and caller_uid and bc.bin_id != caller_uid:
        logger.warning(
            "_verify_ctx: bin_ctx.bin_id=%s != ctx.user_id=%s — refusing",
            bc.bin_id, caller_uid,
        )
        return None
    # If the ORA boundary flag is deactivated, is_founder must be True.
    boundary_off = getattr(bc, "ora_boundary_active", True) is False
    if boundary_off and not bool(getattr(bc, "is_founder", False)):
        logger.warning(
            "_verify_ctx: ora_boundary_active=False but is_founder=False "
            "— refusing (boundary tamper)",
        )
        return None
    return bc


def _slice_content(content: str, lines: list | None, max_chars: int) -> tuple[str, bool]:
    """Apply optional line-range slice, then hard-truncate. Returns (content, truncated)."""
    if isinstance(lines, list) and len(lines) == 2:
        try:
            start = max(int(lines[0]), 1)
            end   = max(int(lines[1]), start)
            content = "\n".join(content.splitlines()[start - 1:end])
        except Exception as _e:
            logger.debug("[silent-catch] services/local_tools.py:301 in _slice_content — %r", _e)
            pass
    total = len(content)
    truncated = total > max_chars
    if truncated:
        # iter 212k — surface the TOTAL char count in the marker so
        # ORA can intelligently request a narrower `lines=[start,end]`
        # slice instead of looping.
        content = (
            content[:max_chars]
            + f"\n... [truncated — {total} total chars, showing first "
            f"{max_chars}. Use lines=[start,end] arg to fetch specific "
            "sections]"
        )
    return content, truncated


# Iter 212m-4 — Chunked file-read helper. Pure (no I/O) so it can be
# unit-tested without mocking GitHub. Replaces the dumb truncate when
# `read_repo_file` returns a large file: small file passes through
# untouched, large file with explicit `lines=[start,end]` returns that
# slice with truncated=True + total_lines, large file without a hint
# returns the first 200 lines + a structural map (def / class /
# @router / export decl lines, capped at 40) so the LLM has navigation
# anchors instead of a blunt cut-off.
_CHUNK_LIMIT = 12_000
_STRUCTURE_RX = re.compile(
    r"\s*(def |async def |class |@router\."
    r"|export (default |function |const )|function )"
)


def _apply_chunking(content: str, args: dict | None) -> dict:
    """Return a `read_repo_file`-shaped envelope with the chunking
    contract documented above.

    NOTE: when caller supplies `lines=[s, e]`, indices are 0-based
    Python slice semantics (`lines[s:e]`) — `[10, 20]` returns lines
    10..19 inclusive. Bulk reads still use the legacy 1-based
    `_slice_content` for back-compat.
    """
    if content is None:
        content = ""
    if len(content) <= _CHUNK_LIMIT:
        return {"ok": True, "content": content, "truncated": False}

    src_lines = content.splitlines()
    total = len(src_lines)

    line_range = (args or {}).get("lines")
    if isinstance(line_range, list) and len(line_range) == 2:
        try:
            s, e = int(line_range[0]), int(line_range[1])
        except (TypeError, ValueError):
            s, e = 0, 0
        chunk = "\n".join(src_lines[s:e])
        return {
            "ok": True,
            "content": chunk,
            "truncated": True,
            "total_lines": total,
            "note": (
                f"Lines {s}-{e} of {total}. "
                "Use lines=[start,end] for other sections."
            ),
        }

    preview = "\n".join(src_lines[:200])
    structure = [
        f"L{i+1}: {ln.strip()}"
        for i, ln in enumerate(src_lines)
        if _STRUCTURE_RX.match(ln)
    ][:40]

    return {
        "ok": True,
        "content": preview,
        "truncated": True,
        "total_lines": total,
        "structure": structure,
        # Iter 212m-6 — explicit machine-readable hint so the LLM knows
        # it MUST call back with a specific line range, not answer from
        # the 200-line preview. The prose `note` stays for older tool
        # consumers; `next_call_required` is the new tight signal.
        "next_call_required": True,
        "next_call_hint": {
            "tool": "read_repo_file",
            "args_template": {"path": "<same path>", "lines": ["<start>", "<end>"]},
            "reason": "preview-only — answer would be incomplete without a targeted slice",
        },
        "note": (
            f"File has {total} lines. Showing 1-200. "
            "You MUST call this tool again with lines=[start,end] "
            "before answering — do not respond from the preview alone."
        ),
    }


# ── Iter 212m-7 — Repo Structure Cache ───────────────────────────────────────
#
# Lightweight in-memory symbol map per (project_id, filepath). Updated
# fire-and-forget after every successful read_repo_file so subsequent
# "what functions exist in admin.py" questions can be answered from
# cache without re-fetching from GitHub.
#
# Eviction:
#   • 100 projects max (FIFO eviction)
#   • 200 files per project max (FIFO eviction)
#   • 100 symbols per file max
# These caps keep total memory bounded at roughly:
#   100 projects × 200 files × 100 symbols × ~150 bytes ≈ 300 MB ceiling
# but typical usage is < 1 MB.

_REPO_STRUCTURE_CACHE: dict[str, dict[str, list[dict]]] = {}
_REPO_CACHE_MAX_PROJECTS = 100
_REPO_CACHE_MAX_FILES_PER_PROJECT = 200
_REPO_CACHE_MAX_SYMBOLS_PER_FILE = 100


def _extract_symbols(content: str) -> list[dict]:
    """Return ordered list of {line, symbol} dicts found in `content`.
    Same regex as `_apply_chunking`'s structure map, capped at 100
    symbols. Pure (no I/O)."""
    if not content:
        return []
    out: list[dict] = []
    for i, ln in enumerate(content.splitlines()):
        if _STRUCTURE_RX.match(ln):
            out.append({"line": i + 1, "symbol": ln.strip()})
            if len(out) >= _REPO_CACHE_MAX_SYMBOLS_PER_FILE:
                break
    return out


def _cache_set(project_id: str, path: str, symbols: list[dict]) -> None:
    """Insert into the structure cache with bounded growth."""
    if not project_id or not path:
        return
    # Project-level FIFO eviction.
    if (project_id not in _REPO_STRUCTURE_CACHE
            and len(_REPO_STRUCTURE_CACHE) >= _REPO_CACHE_MAX_PROJECTS):
        first_key = next(iter(_REPO_STRUCTURE_CACHE))
        _REPO_STRUCTURE_CACHE.pop(first_key, None)
    bucket = _REPO_STRUCTURE_CACHE.setdefault(project_id, {})
    # File-level FIFO eviction.
    if (path not in bucket
            and len(bucket) >= _REPO_CACHE_MAX_FILES_PER_PROJECT):
        first_path = next(iter(bucket))
        bucket.pop(first_path, None)
    bucket[path] = symbols


async def _update_structure_cache(
    project_id: str | None, path: str, content: str,
) -> None:
    """Fire-and-forget structure indexer. Never raises."""
    try:
        if not project_id or project_id == "home" or not path:
            return
        symbols = _extract_symbols(content or "")
        if not symbols:
            return
        _cache_set(project_id, path, symbols)
    except Exception as e:                              # noqa: BLE001
        logger.debug("structure cache update skipped: %r", e)


def _cache_get(project_id: str, path: str | None = None) -> dict | list | None:
    """Read from the structure cache. When `path` is given returns the
    symbol list for that file; otherwise returns the whole project map."""
    bucket = _REPO_STRUCTURE_CACHE.get(project_id)
    if bucket is None:
        return None
    if path is None:
        return bucket
    return bucket.get(path)


def _cache_invalidate(project_id: str, path: str | None = None) -> None:
    """Drop cached symbols when a file is written (or whole project on
    PAT rotation / repo disconnect)."""
    if not project_id:
        return
    if path is None:
        _REPO_STRUCTURE_CACHE.pop(project_id, None)
        return
    bucket = _REPO_STRUCTURE_CACHE.get(project_id)
    if bucket is not None:
        bucket.pop(path, None)



# ── TOOL 1: read_repo_file (single file) ─────────────────────────────────────

async def read_repo_file(ctx: dict, args: dict) -> dict:
    """Fetch one file from the connected repo.
    args: {path: str, lines?: [start, end]}
    """
    path       = (args or {}).get("path")

    if not path or not isinstance(path, str):
        return {"ok": False, "error": "Missing required arg `path`"}
    if path.startswith("/") or ".." in path.split("/"):
        return {"ok": False, "error": "Invalid path — no absolute paths or traversal"}

    # Iter 212m-169 — repo tools MUST read from BINContext, never
    # from an independent DB lookup.  Refuse hard when it's missing.
    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR

    owner, repo, branch, token = rc["owner"], rc["repo"], rc["branch"], rc["token"]
    project_id = rc["pid"]

    if not owner or not repo:
        return {"ok": False, "error": "Project has no resolved github_owner/repo"}

    try:
        content = await _gh_fetch_file(owner, repo, path, branch, token)
    except GithubAuthError as e:
        return _github_access_revoked_error(owner, repo, e.status_code)
    if content is None:
        # Iter 37: LOUD failure with concrete next-step. Previously the AI
        # would see this 404 and IGNORE it, plowing ahead with fabricated
        # analysis based on the path it guessed. Now we force course-correct.
        return {
            "ok":    False,
            "path":  path,
            "status": 404,
            "error": (
                f"❌ FILE NOT FOUND: `{path}` does not exist on "
                f"{owner}/{repo}@{branch}. STOP guessing paths. "
                "Your next tool call MUST be `list_repo_files` with a glob "
                "(e.g. `**/auth*.py`, `**/*router*.py`) to DISCOVER the "
                "real paths in this repo. Do not write a plan, do not "
                "produce a handoff brief, do not cite any file paths — "
                "until you have called list_repo_files and seen the "
                "actual layout. Most repos do not follow the layout you "
                "expect from training data."
            ),
        }

    # Iter 212m-4 — Chunked file reading. Small file passes through
    # untouched. Large file with explicit `lines=[s,e]` returns that
    # slice. Large file without a hint returns first 200 lines + a
    # structural map (def / class / @router / export anchors) so the
    # LLM can navigate intelligently.
    chunked = _apply_chunking(content, args or {})

    # Iter 212m-7 — fire-and-forget structure cache update. The cache
    # powers `get_repo_structure` so subsequent "what functions exist
    # in X.py" questions answer from memory without re-fetching from
    # GitHub. We pass the FULL content (not the chunked preview) so
    # the cached symbol list is complete.
    try:
        asyncio.create_task(
            _update_structure_cache(project_id, path, content),
        )
    except Exception as _e:
        logger.debug("[silent-catch] services/local_tools.py:556 in read_repo_file — %r", _e)
        pass

    return {
        **chunked,
        "path":   path,
        "branch": branch,
    }


# ── TOOL 2: read_repo_files (parallel multi-file) ────────────────────────────

async def read_repo_files(ctx: dict, args: dict) -> dict:
    """Fetch UP TO 6 files from the connected repo IN PARALLEL.
    This is the Emergent-equivalent of mcp_view_bulk.

    args: {paths: [str, ...], lines?: [start, end]}  — lines applied to all

    Returns {ok, files: [{path, content, ok, error?}], errors: [...]}
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    paths      = (args or {}).get("paths") or []
    line_range = (args or {}).get("lines")

    if not isinstance(paths, list) or not paths:
        return {"ok": False, "error": "Missing required arg `paths` (list of strings)"}

    # iter 212l — surface silent path drops to the LLM. Previously paths
    # 7..N were sliced off without notice and the model assumed it had
    # read all the files it asked for. Now we report the count and the
    # exact dropped paths in a `warning` field on the response.
    _raw_paths = [p for p in paths if isinstance(p, str) and p]
    paths = list(dict.fromkeys(_raw_paths))[:MAX_FILES_BULK]
    _dropped = list(dict.fromkeys(_raw_paths))[MAX_FILES_BULK:]

    # Iter 212m-169 — BINContext gate.
    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR
    owner, repo, branch, token = rc["owner"], rc["repo"], rc["branch"], rc["token"]
    if not owner or not repo:
        return {"ok": False, "error": "Project has no resolved github_owner/repo"}

    # Fetch all files concurrently
    async def _fetch_one(path: str) -> dict:
        if path.startswith("/") or ".." in path.split("/"):
            return {"ok": False, "path": path, "error": "Invalid path"}
        try:
            content = await _gh_fetch_file(owner, repo, path, branch, token)
            if content is None:
                return {"ok": False, "path": path, "error": f"`{path}` not found on {branch}"}
            content, truncated = _slice_content(content, line_range, MAX_FILE_CHARS)
            return {"ok": True, "path": path, "content": content, "truncated": truncated}
        except GithubAuthError as e:
            return {**_github_access_revoked_error(owner, repo, e.status_code), "path": path}
        except Exception as e:
            return {"ok": False, "path": path, "error": str(e)}

    results = await asyncio.gather(*[_fetch_one(p) for p in paths])

    ok_files  = [r for r in results if r.get("ok")]
    err_files = [r for r in results if not r.get("ok")]

    # Iter 37: if MORE THAN HALF the guessed paths 404'd, the AI is
    # almost certainly guessing layouts from training data instead of
    # this customer's actual repo. Return a LOUD warning so the AI must
    # call list_repo_files before doing anything else.
    failure_warning = None
    if len(paths) >= 3 and len(err_files) >= max(2, len(paths) // 2):
        failure_warning = (
            f"⚠️ HALLUCINATION RISK — {len(err_files)}/{len(paths)} of the "
            "paths you guessed do not exist in this repo. STOP. Your next "
            "tool call MUST be `list_repo_files` with a wide glob (e.g. "
            "`**/*.py`, `backend/**`, `**/auth*`) to see the ACTUAL layout. "
            "Do not write a plan or handoff brief until you have seen real "
            "paths. The repo's structure is different from what you "
            "expected — that's normal, every codebase is unique."
        )

    # iter 212l — combine the existing hallucination-risk warning with
    # the new dropped-paths warning. Both convey the same urgency to
    # the LLM ("you don't have what you think you have") so we surface
    # them in a single `warning` field; the orchestrator strips it onto
    # the system signal stream regardless.
    warning_lines: list[str] = []
    if failure_warning:
        warning_lines.append(failure_warning)
    if _dropped:
        warning_lines.append(
            f"⚠️ read_repo_files HARD-CAPS at {MAX_FILES_BULK} paths per "
            f"call. {len(_dropped)} path(s) were NOT fetched: "
            + ", ".join(_dropped)
            + ". Call `read_repo_file` (singular) in separate blocks "
            "for the dropped ones, or split the bulk call into batches."
        )
    combined_warning = "\n\n".join(warning_lines) if warning_lines else None

    return {
        "ok":      len(ok_files) > 0,
        "branch":  branch,
        "files":   list(results),
        "fetched": len(ok_files),
        "requested": len(_raw_paths),
        "dropped": _dropped,
        "errors":  [f"{r['path']}: {r.get('error','?')}" for r in err_files],
        **({"warning": combined_warning} if combined_warning else {}),
    }


# ── TOOL 2b: write_repo_file (single-file atomic commit) ─────────────────────

async def write_repo_file(ctx: dict, args: dict) -> dict:
    """Iter 212m-6 — Commit a single file directly to the connected repo.

    Closes the gap where chat-mode ORA could READ but not WRITE: small
    bug fixes had to round-trip through the `aurem-handoff` brief +
    "ship" confirmation flow even when the change was a one-line fix.

    Args:
      path             str   — repo-relative file path (no leading `/`, no `..`)
      content          str   — FULL new file body (we do not patch in-place)
      commit_message?  str   — defaults to "AUREM: edit {path}"

    Returns:
      {ok: true,  sha, html_url, path}       on success
      {ok: false, error, status?}            on failure

    Safety:
      • Vanguard REGEX scan runs before the commit; CRITICAL findings
        block the write and surface the rule name. LLM + E2B layers
        are skipped here (latency budget — task-queue path keeps them).
      • Path validation: no absolute paths, no `..` traversal, no
        binary content (we only accept str, not bytes).
      • Decrypted per-project PAT is used; if the project has no PAT,
        the call returns an actionable error instead of silently
        falling back to OAuth (which often lacks write scope).
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    path       = (args or {}).get("path")
    content    = (args or {}).get("content")
    commit_msg = (args or {}).get("commit_message") or f"chore: edit {path}"

    if not path or not isinstance(path, str):
        return {"ok": False, "error": "Missing required arg `path`."}
    if path.startswith("/") or ".." in path.split("/"):
        return {"ok": False, "error": "Invalid path — no absolute paths or traversal."}
    if not _is_safe_repo_path(path):
        return {"ok": False, "error": (
            "Invalid path — filenames may only contain letters, digits, "
            "spaces, dots, dashes, underscores, and `/` as a path "
            "separator. No shell/special characters."
        )}
    if not isinstance(content, str):
        return {"ok": False, "error": "Arg `content` must be a string (full file body)."}
    if len(content) > 200_000:
        return {"ok": False,
                "error": "File body exceeds 200KB cap — split into smaller files."}

    # ── Iter 286 (Track 0) — test-file lock ──────────────────────────
    # Loop-mode already enforces this via loop_diff_classifier +
    # held-out verifier + human approval before shipping. MCP
    # write_repo_file bypassed that entirely, letting an external
    # client (Claude Desktop, Cursor) silently overwrite the test
    # files that are meant to catch regressions. Block by default;
    # only Loop-mode's post-approval commit path passes
    # allow_test_file_change=True in `args`.
    try:
        from .loop_diff_classifier import is_test_or_fixture
    except Exception:
        is_test_or_fixture = lambda _p: False   # noqa: E731 — degrade safe
    if is_test_or_fixture(path) and not (args or {}).get("allow_test_file_change"):
        return {
            "ok": False,
            "error": (
                "test-file write blocked — test files are protected "
                "by the loop-pipeline test-file lock. Route this "
                "change through Loop mode so the held-out verifier "
                "reviews it, or ask a human maintainer to set "
                "allow_test_file_change=true in an approved patch."
            ),
            "gate":  "test_file_lock",
            "path":  path,
        }

    # Iter 212m-169 — BINContext gate.
    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR
    owner, repo, branch, token = rc["owner"], rc["repo"], rc["branch"], rc["token"]
    if not owner or not repo:
        return {"ok": False, "error": "Project has no resolved github_owner/repo."}
    if not token:
        return {
            "ok": False,
            "error": (
                "GitHub App access is unavailable for this project — "
                "write_repo_file needs write access. Reconnect the repo "
                "via Projects → APP (AUREM GitHub App)."
            ),
        }

    # Guardrail #2 (2026-08 audit remediation, Wave 1) — fast, friendly
    # pre-check before spending cycles on Vanguard/syntax gates below.
    # commit_files() re-checks this too (it's the authoritative choke
    # point) so this early check is purely a nicer error, not the only
    # enforcement. Best-effort: any failure to even reach the check
    # (e.g. db unavailable) is swallowed here, same as the identity
    # lookup below — commit_files()'s own guard call still applies.
    try:
        from core.errors import WriteGuardBlockedError
        from services.write_guard import check_write_paths as _check_write_paths
        from cto_services.db import get_db as _get_guard_db
        await _check_write_paths(_get_guard_db(), [path], owner=owner, repo=repo, branch=branch)
    except WriteGuardBlockedError as _wg_err:
        return {"ok": False, "error": str(_wg_err), "gate": "write_guard", "path": path}
    except Exception:                                       # noqa: BLE001
        pass

    # Item 2 (2026-08-31) — WCAG contrast guard. Only stylesheet/theme
    # files carry color-role custom properties; a plain regex `.css`/
    # `.scss`/theme-file check keeps this a no-op for every other
    # write (best-effort, never blocks — a palette that ORA can't
    # confidently nudge just ships as-is with the finding attached to
    # the tool result, rather than failing the write).
    _contrast_note = None
    if re.search(r"\.(css|scss)$", path, re.IGNORECASE) or "theme" in path.lower():
        try:
            from services.contrast_guard import check_and_nudge_css, describe_nudge
            _cg = check_and_nudge_css(content)
            if _cg["adjustments"]:
                content = _cg["content"]
                _contrast_note = _cg["adjustments"]
                # Item 2 (2026-08-31) — surface this nudge to the owner
                # IN CHAT, not just the logs. `ctx["palette_nudges"]` is
                # harvested by orchestrator.py the same way
                # `system_signals`/`tool_calls` already are, and rendered
                # as a before/after swatch pair by PaletteNudgeBubble.jsx.
                for _adj in _cg["adjustments"]:
                    ctx.setdefault("palette_nudges", []).append({
                        "path": path,
                        "before_hex": _adj["original_fg"],
                        "after_hex": _adj["nudged_fg"],
                        "before_ratio": _adj["before_ratio"],
                        "after_ratio": _adj["after_ratio"],
                        "note": describe_nudge(_adj),
                    })
        except Exception as _cg_e:
            logger.debug("contrast_guard skipped for %s: %r", path, _cg_e)

    # Iter 212m-6 — pre-commit vanguard regex pass. CRITICAL+HIGH
    # secrets block; everything else passes through. LLM/E2B layers
    # are off this hot path (chat latency budget); the task-queue
    # path keeps the full triple-layer verify.
    # SEC-007 (2026-08-20, founder-approved option c): widened from
    # CRITICAL-only to CRITICAL+HIGH via has_critical_or_high — stays
    # sync/fast (same regex-only scan, no added latency, no extra
    # model call), just a wider block list than before.
    try:
        from .vanguard_scanner import scan_file_blocks as _vg_scan, has_critical_or_high as _vg_crit
        findings = _vg_scan({path: content})
        if _vg_crit(findings):
            critical = [
                f for f in findings
                if (f.get("severity") or "").upper() in ("CRITICAL", "HIGH")
            ]
            return {
                "ok":       False,
                "error":    "Vanguard blocked the commit — critical/high finding(s) in patch.",
                "findings": [{
                    "rule":     c.get("name"),
                    "severity": c.get("severity"),
                    "file":     c.get("filepath"),
                    "line":     c.get("line"),
                } for c in critical[:5]],
            }
    except Exception as _ve:
        logger.warning("write_repo_file: vanguard scan failed: %r", _ve)
        # Don't block on scanner infra errors — same policy as task path.

    # Iter 212m-152 — MANDATORY SYNTAX GATE (Fix 2).
    # Runs after Vanguard but BEFORE the GitHub commit.  Blocks any
    # commit whose new content contains a syntax error.  Falls open
    # (allows the commit) on tooling errors or timeouts — better to
    # ship than block on a flaky local linter.
    _ext = ""
    try:
        _dot = path.rfind(".")
        if _dot >= 0:
            _ext = path[_dot:].lower()
    except Exception:
        _ext = ""
    if _ext in (".py", ".ts", ".tsx", ".js", ".jsx"):
        # 2026-09-02 — diagnostic instrumentation for the "Loop status
        # 60s timeout" root-cause investigation (NOT a fix — see
        # BUGS_LEDGER.md): `_run_syntax_check` calls `subprocess.run()`
        # SYNCHRONOUSLY with no `asyncio.to_thread`, so it blocks the
        # ENTIRE event loop for its duration. On a multi-file edit
        # this compounds (up to the per-language 10-15s cap EACH),
        # starving unrelated concurrent requests — e.g. the GET
        # /loop/{id}/status poll — long enough to hit the frontend's
        # 60s axios timeout and show a spurious "Loop status error".
        # Logged here so the block is visible in production instead
        # of only showing up as a mystery frontend timeout.
        _gate_t0 = time.monotonic()
        _gate = _run_syntax_check(content=content, file_path=path, ext=_ext)
        _gate_ms = (time.monotonic() - _gate_t0) * 1000
        if _gate_ms > 2000:
            logger.warning(
                "syntax_gate BLOCKED THE EVENT LOOP for %.0fms on %s — "
                "subprocess.run() is synchronous inside async "
                "write_repo_file (see BUGS_LEDGER.md, 'loop status "
                "60s timeout' root-cause finding)",
                _gate_ms, path,
            )
        if _gate.get("has_errors"):
            logger.warning(
                "syntax_gate BLOCKED commit: %s — %s",
                path, (_gate.get("errors") or "")[:100],
            )
            return {
                "ok":             False,
                "error":          "syntax_gate_blocked",
                "message":        (
                    "Syntax errors detected in your patch — commit blocked. "
                    "Fix the syntax and try again.\n"
                    + (_gate.get("errors") or "")
                ),
                "file":           path,
                "syntax_errors": _gate.get("errors"),
            }
        if _gate.get("skipped"):
            logger.warning(
                "syntax_gate SKIPPED: %s — %s",
                path, _gate.get("reason") or "unknown",
            )
        else:
            logger.info("syntax_gate PASSED: %s", path)

    # Commit via the existing atomic Git Data API writer.
    # Iter 212m-218 — resolve real developer identity + normalise
    # commit message to Conventional Commits + co-author trailer so
    # the commit shows the human as author and ORA as co-author.
    try:
        from .github_api_writer import commit_files as _commit_files
        from .git_identity import (
            resolve_git_identity, build_commit_message,
        )
        _db = None
        try:
            from cto_services.db import get_db as _get_db
            _db = _get_db()
        except Exception:                                # noqa: BLE001
            _db = None
        _author_name, _author_email = await resolve_git_identity(_db, user_id)
        _final_commit_msg = build_commit_message(user_message=commit_msg, summary=commit_msg)
        res = await _commit_files(
            owner=owner, repo=repo, branch=branch, token=token,
            files={path: content},
            commit_message=_final_commit_msg,
            author_name=_author_name, author_email=_author_email,
        )
    except Exception as e:                                # noqa: BLE001
        logger.warning("write_repo_file: commit_files crashed: %r", e)
        return {
            "ok":     False,
            "error":  f"Commit failed at the GitHub API layer ({type(e).__name__}).",
            "status": getattr(e, "status_code", None),
        }

    # Iter 212m-7 — write invalidates cached structure for this path
    # (next read will re-build the symbol map from fresh content).
    try:
        _cache_invalidate(project_id, path)
    except Exception as _e:
        logger.debug("[silent-catch] services/local_tools.py:849 in write_repo_file — %r", _e)
        pass

    # Iter 212m-13 — also drop the short-TTL GitHub-API cache so any
    # `read_repo_file` call later in this same turn sees the new
    # content (otherwise the LLM would write-then-read its own stale
    # body and conclude the patch wasn't applied).
    try:
        from .github_cache import invalidate_repo
        invalidate_repo(owner, repo, branch)
    except Exception as _e:
        logger.debug("[silent-catch] services/local_tools.py:859 in write_repo_file — %r", _e)
        pass

    return {
        "ok":       True,
        "path":     path,
        "branch":   branch,
        "sha":      res.get("sha"),
        "html_url": res.get("html_url"),
        "message":  commit_msg,
        "contrast_adjustments": _contrast_note,
    }


# ── TOOL 2c: get_repo_structure (cached symbol map) ──────────────────────────

async def get_repo_structure(ctx: dict, args: dict) -> dict:
    """Iter 212m-7 — Return the cached function/class/route symbol map
    for the connected project. Built lazily by `read_repo_file` calls
    in this process; not persisted. Use this instead of re-reading
    files when the user asks "what functions exist in X.py" or
    "list all routes" and the file has already been read this session.

    Args:
      path?  str  — when supplied, return symbols for that single file;
                    when omitted, return the whole project map.

    Returns:
      {ok, project_id, files_cached, symbols, path?}     on hit
      {ok: True, project_id, files_cached: 0, hint}      on cold cache
    """
    project_id = ctx.get("project_id")
    path = (args or {}).get("path")

    # Iter 212m-169 — BINContext gate.  get_repo_structure reads an
    # in-process cache keyed by project_id, but we still require a
    # BINContext to prove the caller owns that project (otherwise a
    # cache poisoning by any other user with the same project_id
    # would leak).
    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR
    project_id = rc["pid"]

    bucket = _cache_get(project_id)
    if not bucket:
        return {
            "ok":           True,
            "project_id":   project_id,
            "files_cached": 0,
            "symbols":      {},
            "hint":         (
                "Cache is empty for this project — call read_repo_file "
                "on at least one file first, then get_repo_structure "
                "will return its function/class/route map."
            ),
        }

    if path:
        syms = _cache_get(project_id, path)
        if syms is None:
            return {
                "ok":         True,
                "project_id": project_id,
                "path":       path,
                "cached":     False,
                "hint":       (
                    f"`{path}` is not in the cache. Call read_repo_file "
                    f"with path={path!r} first, then get_repo_structure "
                    f"will return its symbol map."
                ),
            }
        return {
            "ok":         True,
            "project_id": project_id,
            "path":       path,
            "cached":     True,
            "symbols":    syms,
            "count":      len(syms),
        }

    # No path arg → whole-project map.
    return {
        "ok":           True,
        "project_id":   project_id,
        "files_cached": len(bucket),
        "symbols": {
            p: syms for p, syms in bucket.items()
        },
    }



# ── TOOL 3: list_repo_files (repo tree / glob) ───────────────────────────────

async def _fetch_subtree_contents(owner: str, repo: str, branch: str,
                                  token: Optional[str], path: str,
                                  max_depth: int = 4) -> list[str]:
    """Walk a specific subtree using GitHub's Contents API.

    Used as a fallback when the recursive Trees API returns
    `truncated: true` and the path the user is asking about isn't in
    the (partial) recursive response. The Contents API only returns
    immediate children, so we BFS up to `max_depth` levels deep.

    This is the fix for: "AUREM apna repo properly scan kyon nahi karta
    — backend/pillars/ exists but tree shows nothing." GitHub truncates
    the recursive tree for any repo > ~7MB; deep folders silently vanish.
    """
    import httpx

    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    out: list[str] = []
    queue: list[tuple[str, int]] = [(path.strip("/"), 0)]

    from services.http import ext_client
    async with ext_client(
        "github",
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
    ) as c:
        while queue:
            current, depth = queue.pop(0)
            if depth > max_depth or len(out) >= 1000:
                continue
            url = (
                f"https://api.github.com/repos/{owner}/{repo}/"
                f"contents/{current}?ref={branch}"
            )
            try:
                r = await c.get(url, headers=headers)
                if r.status_code != 200:
                    continue
                items = r.json()
            except Exception:
                continue
            if isinstance(items, dict):
                # GitHub returns a single object for a file path, list for dir
                items = [items]
            for it in items:
                ipath = it.get("path") or ""
                itype = it.get("type")
                if itype == "file":
                    out.append(ipath)
                elif itype == "dir":
                    queue.append((ipath, depth + 1))
    return out


async def list_repo_files(ctx: dict, args: dict) -> dict:
    """List files in the connected repo tree — equivalent of mcp_glob_files.

    args:
      path?      str   — sub-directory to list (default: "" = root)
      pattern?   str   — glob pattern e.g. "*.py", "routers/*.py", "**/*.jsx"
      max?       int   — max results (default 150, cap 500)

    Returns {ok, tree: [str], total, truncated, source}

    Behaviour:
    - Tries GitHub's recursive Trees API first (fast, one call).
    - If GitHub reports the tree is truncated AND the caller asked for
      a specific `path`, falls back to the Contents API to walk that
      subtree directly. This is what unblocks deep / large repos where
      the recursive tree silently drops folders.
    """
    import httpx

    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    sub_path   = (args or {}).get("path") or ""
    pattern    = (args or {}).get("pattern") or ""
    max_items  = min(int((args or {}).get("max") or 150), 500)

    # Iter 212m-169 — BINContext gate.
    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR
    owner, repo, branch, token = rc["owner"], rc["repo"], rc["branch"], rc["token"]
    if not owner or not repo:
        return {"ok": False, "error": "Project has no resolved github_owner/repo"}

    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    # GitHub Trees API — recursive=1 fetches the whole tree in one call,
    # but for repos > ~7MB or > 100K entries it returns "truncated": true
    # and a PARTIAL list. We surface that flag and fall back below.
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    try:
        from services.http import ext_client
        async with ext_client(
            "github",
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0),
        ) as c:
            r = await c.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            return _github_access_revoked_error(owner, repo, e.response.status_code)
        return {"ok": False, "error": f"GitHub tree fetch failed: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"GitHub tree fetch failed: {e}"}

    gh_truncated = bool(data.get("truncated"))
    tree_items = [
        item["path"] for item in data.get("tree", [])
        if item.get("type") == "blob"
    ]
    source = "trees_recursive"

    # Subtree filter
    if sub_path:
        sub_path_clean = sub_path.strip("/")
        filtered = [
            p for p in tree_items
            if p.startswith(sub_path_clean + "/") or p == sub_path_clean
        ]
        # If the recursive tree was truncated AND nothing matched the
        # requested subtree, fall back to per-folder Contents API for
        # that subtree. This rescues deep folders the recursive call
        # dropped (the exact bug the user reported: backend/pillars/
        # invisible in a multi-megabyte repo).
        if gh_truncated and not filtered:
            filtered = await _fetch_subtree_contents(
                owner, repo, branch, token, sub_path_clean,
            )
            source = "contents_walk_fallback"
        tree_items = filtered

    # Filter by glob pattern
    if pattern:
        # Support both simple *.py and routers/*.py patterns
        tree_items = [
            p for p in tree_items
            if fnmatch.fnmatch(p, pattern)
            or fnmatch.fnmatch(p.split("/")[-1], pattern)
        ]

    over_max = len(tree_items) > max_items
    note_bits = [
        f"Showing {min(len(tree_items), max_items)} of {len(tree_items)} files"
    ]
    if over_max:
        note_bits.append(". Use `path` or `pattern` to narrow.")
    if gh_truncated and source != "contents_walk_fallback":
        # Tell ORA explicitly so it knows to retry with a `path` arg
        # instead of concluding "the folder doesn't exist".
        note_bits.append(
            " ⚠️ GitHub truncated this recursive tree response — some "
            "deep folders may be missing. To inspect a specific path "
            "reliably, re-call with `path=\"backend/pillars\"` (or "
            "whichever subtree you need) — the tool will then fall "
            "back to a per-folder walk that does not truncate."
        )

    result = {
        "ok":        True,
        "tree":      tree_items[:max_items],
        "total":     len(tree_items),
        "truncated": over_max,
        "gh_truncated": gh_truncated,
        "source":    source,
        "note":      "".join(note_bits),
    }
    # R4 (2026-08-31) — deterministic page-name resolver hint (L-02
    # fix). Only computed off the REAL tree just fetched above — never
    # fabricates a path. No-op when the user's message named no page
    # phrase at all (detect_category returns None).
    try:
        from services.page_resolver import resolve as _resolve_page, hint_text as _page_hint_text
        _hint = _page_hint_text(_resolve_page(tree_items, ctx.get("user_prompt") or ""))
        if _hint:
            result["page_resolver_hint"] = _hint
    except Exception as _pr_e:
        logger.debug("page_resolver hint skipped: %r", _pr_e)
    return result


# ── Full-repo local snapshot (iter 212m-179) ─────────────────────────
#
# The old search_repo fetched files one-by-one over the GitHub API and
# needed a hard budget (400 files / 15s) on big repos → PARTIAL results.
# Proper fix: pull the ENTIRE repo as ONE tarball request
# (GET /repos/{o}/{r}/tarball/{sha}), extract to /tmp, and search the
# complete tree locally (ripgrep, Python-walk fallback). Cached per
# HEAD SHA so repeat searches cost a single ref check until the branch
# moves. No git binary required → works on the PROD container too.

# Iter 212m-179b — /tmp gets swept by the platform (observed on both
# preview and prod: snapshot vanished between calls → every search
# re-downloaded, 13-16s each). Cache under /app instead — writable and
# stable for the container's lifetime. Gitignored via /app/.gitignore.
_SNAPSHOT_ROOT = "/app/.aurem_cache/repo_snapshots"
_SNAPSHOT_DL_TIMEOUT_S = 120.0
_SNAPSHOT_MAX_BYTES = 400 * 1024 * 1024
_PER_FILE_HIT_CAP = 50
_snapshot_locks: dict[str, asyncio.Lock] = {}


def _snapshot_lock(key: str) -> asyncio.Lock:
    if key not in _snapshot_locks:
        _snapshot_locks[key] = asyncio.Lock()
    return _snapshot_locks[key]


async def _repo_head_sha(owner: str, repo: str, branch: str,
                         token: str) -> Optional[str]:
    import httpx
    from services.http import ext_client
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    url = f"https://api.github.com/repos/{owner}/{repo}/git/ref/heads/{branch}"
    try:
        async with ext_client(
            "github",
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
        ) as c:
            r = await c.get(url, headers=headers)
            if r.status_code != 200:
                return None
            return ((r.json() or {}).get("object") or {}).get("sha")
    except Exception:                                     # noqa: BLE001
        return None


async def _ensure_repo_snapshot(
    owner: str, repo: str, branch: str, token: str,
) -> tuple[Optional[str], Optional[str]]:
    """Returns (snapshot_dir, error). One GitHub call when cached
    (HEAD ref check), two when the branch moved (ref + tarball)."""
    import os
    import shutil
    import tarfile
    import tempfile
    import httpx

    key = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{owner}__{repo}__{branch}")
    dest = os.path.join(_SNAPSHOT_ROOT, key)
    marker = os.path.join(dest, ".aurem_head_sha")

    async with _snapshot_lock(key):
        head = await _repo_head_sha(owner, repo, branch, token)
        if not head:
            # Ref check hiccup — a stale full snapshot beats no search.
            if os.path.exists(marker):
                return dest, None
            return None, "head_sha_unavailable"
        try:
            with open(marker, encoding="utf-8") as fh:
                if fh.read().strip() == head:
                    return dest, None      # cache hit — zero downloads
        except OSError as _e:
            logger.debug("[silent-catch] services/local_tools.py:1187 in _ensure_repo_snapshot — %r", _e)
            pass

        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        url = f"https://api.github.com/repos/{owner}/{repo}/tarball/{head}"
        tmp_tar = tempfile.NamedTemporaryFile(
            suffix=".tar.gz", delete=False, dir="/tmp")
        try:
            total = 0
            from services.http import ext_client
            async with ext_client(
                "github",
                timeout=httpx.Timeout(
                    connect=5.0, read=_SNAPSHOT_DL_TIMEOUT_S, write=5.0, pool=5.0,
                ),
            ) as c:
                async with c.stream("GET", url, headers=headers) as r:
                    if r.status_code != 200:
                        return None, f"tarball_status_{r.status_code}"
                    async for chunk in r.aiter_bytes():
                        total += len(chunk)
                        if total > _SNAPSHOT_MAX_BYTES:
                            return None, "tarball_too_large"
                        tmp_tar.write(chunk)
            tmp_tar.close()

            def _extract() -> None:
                tmp_dir = dest + ".extract"
                shutil.rmtree(tmp_dir, ignore_errors=True)
                os.makedirs(tmp_dir, exist_ok=True)

                def _tar_filter(member, path):
                    # Skip unsafe members (absolute symlinks etc.)
                    # instead of aborting the whole snapshot. Also skip
                    # files > 2MB — search ignores them anyway
                    # (--max-filesize 2M) and it trims disk footprint.
                    if member.isfile() and member.size > 2_000_000:
                        return None
                    try:
                        return tarfile.data_filter(member, path)
                    except tarfile.FilterError as _e:
                        logger.debug("[silent-catch] services/local_tools.py:1225 in _tar_filter — %r", _e)
                        return None

                try:
                    with tarfile.open(tmp_tar.name, "r:gz") as tf:
                        tf.extractall(tmp_dir, filter=_tar_filter)
                    roots = [d for d in os.listdir(tmp_dir)
                             if os.path.isdir(os.path.join(tmp_dir, d))]
                    if not roots:
                        raise RuntimeError("empty_tarball")
                    os.makedirs(_SNAPSHOT_ROOT, exist_ok=True)
                    shutil.rmtree(dest, ignore_errors=True)
                    os.rename(os.path.join(tmp_dir, roots[0]), dest)
                    with open(marker, "w", encoding="utf-8") as fh:
                        fh.write(head)
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)

            await asyncio.to_thread(_extract)
            return dest, None
        except Exception as e:                            # noqa: BLE001
            try:
                du = shutil.disk_usage(os.path.dirname(_SNAPSHOT_ROOT) or "/")
                disk = f"disk free={du.free // 1048576}MB"
            except OSError:
                disk = "disk=?"
            logger.warning("repo snapshot failed for %s/%s@%s: %r (%s)",
                           owner, repo, branch, e, disk)
            return None, f"snapshot_failed_{type(e).__name__}"
        finally:
            try:
                os.unlink(tmp_tar.name)
            except OSError as _e:
                logger.debug("[silent-catch] services/local_tools.py:1257 in _ensure_repo_snapshot — %r", _e)
                pass


def _search_snapshot_sync(root: str, pattern: str, compiled,
                          sub_path: str, ext: str) -> list[dict]:
    """Search the local snapshot COMPLETELY. ripgrep when available,
    pure-Python walk otherwise. Runs inside asyncio.to_thread."""
    import os
    import shutil as _sh
    import subprocess

    rg_bin = _sh.which("rg")
    if rg_bin:
        args = [rg_bin, "--no-heading", "--line-number", "--ignore-case",
                "--no-messages", "--hidden", "--no-ignore",
                "--max-columns", "300", "--max-columns-preview",
                "--max-count", str(_PER_FILE_HIT_CAP),
                "--max-filesize", "2M",
                "-g", "!.git/**"]
        if ext:
            args += ["-g", f"*{ext}"]
        if sub_path:
            args += ["-g", f"{sub_path.strip('/')}/**"]
        try:
            proc = subprocess.run(args + ["-e", pattern], cwd=root,
                                  capture_output=True, text=True, timeout=60)
            # rc 2 = pattern not valid rust-regex → fall through to the
            # Python walk which uses the already-compiled Python regex.
            if proc.returncode in (0, 1):
                matches = []
                for raw in proc.stdout.splitlines():
                    fpath, _, rest = raw.partition(":")
                    line_no, _, text = rest.partition(":")
                    if not (fpath and line_no.isdigit()):
                        continue
                    matches.append({"file": fpath, "line_no": int(line_no),
                                    "line": text.strip()[:280]})
                return matches
        except (subprocess.TimeoutExpired, OSError) as _e:
            logger.debug("[silent-catch] services/local_tools.py:1296 in _search_snapshot_sync — %r", _e)
            pass

    matches: list[dict] = []
    base = os.path.join(root, sub_path.strip("/")) if sub_path else root
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            if ext and not fn.endswith(ext):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            try:
                if os.path.getsize(full) > 2_000_000:
                    continue
                hits = 0
                with open(full, encoding="utf-8", errors="replace") as fh:
                    for line_no, line in enumerate(fh, 1):
                        if "\x00" in line:
                            break                          # binary
                        if compiled.search(line):
                            matches.append({"file": rel, "line_no": line_no,
                                            "line": line.strip()[:280]})
                            hits += 1
                            if hits >= _PER_FILE_HIT_CAP:
                                break
            except OSError:
                continue
    return matches


# ── TOOL 4: search_repo (grep across repo) ───────────────────────────────────

async def search_repo(ctx: dict, args: dict) -> dict:
    """Search for a pattern across ALL files in the connected repo.

    Iter 212m-179 — proper full-repo search. Primary path downloads a
    complete tarball snapshot (one API call, cached per HEAD SHA) and
    greps it locally → COMPLETE results, no partial budgets, ~2-6s
    even on 16k-file repos. The old per-file GitHub API scan survives
    only as a budgeted fallback when the snapshot can't be built.

    args:
      pattern   str   — text or regex to search for
      path?     str   — limit search to this directory
      ext?      str   — limit to files with this extension e.g. ".py"
      max?      int   — fallback-path cap on matching files (default 20)

    Returns {ok, matches: [{file, line_no, line}], total_matches, ...}
    """
    pattern    = (args or {}).get("pattern") or ""
    sub_path   = (args or {}).get("path") or ""
    ext        = (args or {}).get("ext") or ""
    max_files  = min(int((args or {}).get("max") or 20), 50)

    if not pattern:
        return {"ok": False, "error": "Missing required arg `pattern`"}

    # Iter 212m-169 — BINContext gate.
    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR
    owner, repo, branch, token = rc["owner"], rc["repo"], rc["branch"], rc["token"]

    if ext:
        ext = ext if ext.startswith(".") else "." + ext

    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error:
        compiled = re.compile(re.escape(pattern), re.IGNORECASE)

    snap_dir, snap_err = await _ensure_repo_snapshot(owner, repo, branch, token)
    if snap_dir:
        try:
            matches = await asyncio.to_thread(
                _search_snapshot_sync, snap_dir, pattern, compiled,
                sub_path, ext)
        except Exception as e:                            # noqa: BLE001
            logger.warning("snapshot search failed: %r — API fallback", e)
            matches = None
        if matches is not None:
            files_matched = len({m["file"] for m in matches})
            return {
                "ok":            True,
                "pattern":       pattern,
                "matches":       matches[:500],
                "total_matches": len(matches),
                "files_matched": files_matched,
                "source":        "full_repo_snapshot",
                "complete":      True,
                "budget_hit":    False,
                "note": (
                    f"Searched the ENTIRE repo — {len(matches)} matches "
                    f"in {files_matched} files."
                    if matches else
                    f"No matches for `{pattern}` — the ENTIRE repo was "
                    "searched (complete scan, not a budget cut)."
                ),
            }

    logger.warning("search_repo snapshot unavailable (%s) — using budgeted "
                   "API fallback", snap_err)
    res = await _search_repo_via_api(
        owner=owner, repo=repo, branch=branch, token=token,
        pattern=pattern, compiled=compiled, sub_path=sub_path, ext=ext,
        max_files=max_files)
    res["snapshot_error"] = snap_err
    return res


async def _search_repo_via_api(*, owner: str, repo: str, branch: str,
                               token: str, pattern: str, compiled,
                               sub_path: str, ext: str,
                               max_files: int) -> dict:
    """Legacy budgeted per-file GitHub API scan — FALLBACK ONLY when the
    full snapshot can't be built. May return partial results."""
    import httpx
    from services.http import ext_client
    import time as _time

    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    try:
        async with ext_client(
            "github",
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0),
        ) as c:
            r = await c.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"GitHub tree fetch failed: {e}"}

    gh_truncated = bool(data.get("truncated"))
    all_files = [
        item["path"] for item in data.get("tree", [])
        if item.get("type") == "blob"
    ]

    # Filter
    if sub_path:
        sub_path_clean = sub_path.strip("/")
        filtered = [f for f in all_files if f.startswith(sub_path_clean + "/")]
        # Same rescue path as list_repo_files: when the recursive tree
        # is truncated and the requested subtree has zero matches, walk
        # that subtree with the Contents API so deep folders aren't
        # silently dropped on large repos.
        if gh_truncated and not filtered:
            filtered = await _fetch_subtree_contents(
                owner, repo, branch, token, sub_path_clean,
            )
        all_files = filtered
    if ext:
        all_files = [f for f in all_files if f.endswith(ext)]

    # Iter 212m-178 — PROD perf fix. Before this, a rare pattern on a
    # large repo (16k+ files) fetched EVERY file one-by-one until 20
    # matches or exhaustion — 79s on TJSNDHU/Aurem, which stalled the
    # whole agentic advisor/analyze turn past the proxy limit. Cap the
    # number of files we actually fetch AND the wall-clock budget, and
    # prefer real source files over binaries/assets.
    _TEXT_EXT = (
        ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".html",
        ".json", ".md", ".yml", ".yaml", ".toml", ".go", ".rs", ".java",
        ".rb", ".php", ".c", ".cpp", ".h", ".sh", ".sql", ".txt", ".env",
        ".cfg", ".ini",
    )
    if not ext:
        _code = [f for f in all_files if f.lower().endswith(_TEXT_EXT)]
        if _code:
            all_files = _code
    _MAX_FILES_SCANNED = 400          # hard fetch cap
    _SEARCH_BUDGET_S   = 15.0         # hard wall-clock cap
    _search_started    = _time.monotonic()

    # Search files — cap at max_files matches, fetch in parallel batches of 10
    matches = []
    searched = 0
    fetched = 0
    batch_size = 10
    hit_budget = False

    for i in range(0, len(all_files), batch_size):
        if len(matches) >= max_files:
            break
        if fetched >= _MAX_FILES_SCANNED or \
                (_time.monotonic() - _search_started) > _SEARCH_BUDGET_S:
            hit_budget = True
            break
        batch = all_files[i:i + batch_size]

        async def _search_file(fpath: str) -> list[dict]:
            content = await _gh_fetch_file(owner, repo, fpath, branch, token)
            if content is None:
                return []
            hits = []
            for line_no, line in enumerate(content.splitlines(), 1):
                if compiled.search(line):
                    # iter 212k — per-line snippet 120 → 280 chars. The
                    # old cap chopped long lines (route decorators with
                    # path + comment, type signatures, etc.) so ORA
                    # only saw fragments.
                    hits.append({"file": fpath, "line_no": line_no,
                                 "line": line.strip()[:280]})
                    # iter 212k — per-file hit cap 5 → 50. Was the root
                    # cause of "ORA sees only 5 of 30 routes in admin.py":
                    # a file with 30 @router decorators returned the
                    # first 5 only, so ORA hallucinated "there are 5
                    # routes" or kept re-searching narrower patterns.
                    if len(hits) >= 50:
                        break
            return hits

        batch_results = await asyncio.gather(*[_search_file(f) for f in batch])
        fetched += len(batch)
        for file_hits in batch_results:
            matches.extend(file_hits)
            if file_hits:
                searched += 1
        if searched >= max_files:
            break

    return {
        "ok":           True,
        "pattern":      pattern,
        # iter 212k — global cap raised from `max_files * 5` to a flat
        # 500 so a focused search ("@router" in one file) can return all
        # 30+ hits even when max_files is small (e.g. 1).
        "matches":      matches[:500],
        "total_matches": len(matches),
        "files_fetched": fetched,
        "budget_hit":   hit_budget,
        "source":       "github_api_fallback",
        "complete":     not hit_budget,
        "note":         (
            f"Found {len(matches)} matches across {fetched} files."
            + (" Scan budget reached — narrow with `path`/`ext` for more."
               if hit_budget else "")
        ) if matches else (
            f"No matches for `{pattern}` in {fetched} files scanned."
            + (" Scan budget reached — narrow with `path`/`ext`."
               if hit_budget else "")
        ),
    }


# ── TOOL 5: semantic_search_repo (GitHub code search) ────────────────────────

async def semantic_search_repo(ctx: dict, args: dict) -> dict:
    """Search the connected repo by concept via GitHub Code Search.

    Better than `search_repo` (grep) for "find all files related to X" —
    GitHub's index returns relevant matches even when the literal string
    isn't present.

    args:
      query     str  — concept / symbol (natural language ok)
      language  str? — 'python', 'javascript', 'typescript'
      max       int? — max results (default 10, cap 20)
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    query      = ((args or {}).get("query") or "").strip()
    language   = ((args or {}).get("language") or "").strip()
    max_hits   = min(int((args or {}).get("max") or 10), 20)

    if not query:
        return {"ok": False, "error": "query is required"}

    # Iter 212m-169 — BINContext gate.
    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR
    owner, repo, token = rc["owner"], rc["repo"], rc["token"]
    if not owner or not repo:
        return {"ok": False, "error": "Project missing github_owner or github_repo"}

    gh_query = f"{query} repo:{owner}/{repo}"
    if language:
        gh_query += f" language:{language}"

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    import httpx
    from services.http import ext_client
    try:
        async with ext_client(
            "github",
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
        ) as c:
            r = await c.get(
                "https://api.github.com/search/code",
                params={"q": gh_query, "per_page": max_hits},
                headers=headers,
            )
            if r.status_code == 403:
                return {"ok": False,
                        "error": "GitHub search rate limited — try again in 30s"}
            if r.status_code == 422:
                return {"ok": False, "error": f"Invalid search query: {query}"}
            r.raise_for_status()
            data = r.json()
    except httpx.TimeoutException:
        return {"ok": False, "error": "GitHub search timed out"}
    except Exception as e:
        return {"ok": False,
                "error": f"GitHub search failed: {type(e).__name__}: {e}"}

    results = [
        {"path": item["path"], "score": round(item.get("score", 0), 2),
         "source": "github_search"}
        for item in data.get("items", [])
    ]

    # Fall back to a local TF-IDF pass over the cached codebase index
    # when GitHub Code Search came back thin (rate-limit, private repo
    # not yet indexed, etc.). Merge — dedup by path.
    if len(results) < 3:
        try:
            tfidf_hits = await _index_tfidf_search(
                query, user_id, project_id, max_hits - len(results),
            )
            seen = {r["path"] for r in results}
            for h in tfidf_hits:
                if h["path"] not in seen:
                    results.append(h)
                    seen.add(h["path"])
        except Exception as _e:
            logger.debug("[silent-catch] services/local_tools.py:1624 in semantic_search_repo — %r", _e)
            pass

    return {
        "ok":      True,
        "query":   query,
        "total":   data.get("total_count", 0) or len(results),
        "results": results[:max_hits],
        "hint": (
            f"Found {len(results)} files. "
            "Use read_repo_files to read the most relevant ones in parallel."
        ),
    }


async def _index_tfidf_search(query: str, user_id: str,
                              project_id: str, max_hits: int) -> list[dict]:
    """Bag-of-words overlap scorer over the cached codebase index.

    Returns at most `max_hits` results sorted by descending score.
    Empty list on any error so callers can no-op the fallback.
    """
    if max_hits <= 0:
        return []
    try:
        from cto_services.db import get_db as _gdb
        db = _gdb()
        if db is None:
            return []
        idx = await db.cto_codebase_index.find_one(
            {"user_id": user_id, "project_id": project_id},
            {"files": 1, "_id": 0},
        )
        if not idx or not idx.get("files"):
            return []
        q_words = {w for w in query.lower().split() if len(w) > 2}
        if not q_words:
            return []
        scored: list[dict] = []
        for f in idx["files"]:
            blob = (
                (f.get("snippet") or "")
                + " " + (f.get("path") or "")
                + " " + (f.get("role") or "")
            ).lower()
            overlap = sum(1 for w in q_words if w in blob)
            if overlap:
                scored.append({
                    "path":   f.get("path") or "",
                    "score":  round(overlap / max(len(q_words), 1), 2),
                    "source": "index_tfidf",
                })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:max_hits]
    except Exception as _e:
        logger.debug("[silent-catch] services/local_tools.py:1678 in _index_tfidf_search — %r", _e)
        return []


# ── TOOL 6: get_commit_diff (single-commit patch) ────────────────────────────

async def get_commit_diff(ctx: dict, args: dict) -> dict:
    """Return the diff for one commit.

    Pairs with the brain context's "recent commits" so the model can see
    exactly HOW similar work was done before writing new code.

    args:
      sha  str  — commit SHA (7 or 40 chars)
    """
    user_id    = ctx.get("user_id")
    project_id = ctx.get("project_id")
    sha        = ((args or {}).get("sha") or "").strip()

    if not sha:
        return {"ok": False,
                "error": "sha is required (get it from brain context recent commits)"}

    # Iter 212m-169 — BINContext gate.
    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR
    owner, repo, token = rc["owner"], rc["repo"], rc["token"]
    if not owner or not repo:
        return {"ok": False, "error": "Project missing github_owner or github_repo"}

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    import httpx
    from services.http import ext_client
    try:
        async with ext_client(
            "github",
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
        ) as c:
            r = await c.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}",
                headers=headers,
            )
            if r.status_code == 404:
                return {"ok": False, "error": f"Commit {sha} not found"}
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"GitHub API error: {e}"}

    files_changed = [
        {
            "path":      f["filename"],
            "status":    f["status"],
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
            "patch":     (f.get("patch") or "")[:600],
        }
        for f in (data.get("files") or [])[:8]
    ]
    commit_info = data.get("commit", {}) or {}
    author = commit_info.get("author") or {}
    return {
        "ok":           True,
        "sha":          sha[:7],
        "message":      commit_info.get("message", ""),
        "author":       author.get("name", ""),
        "date":         author.get("date", ""),
        "files_changed": files_changed,
        "total_files":   len(data.get("files") or []),
    }


# ── TOOL 7: get_repo_info (project metadata) ─────────────────────────────────

async def get_repo_info(ctx: dict, args: dict) -> dict:
    """Return connected project metadata: owner, repo, branch, tech_stack, last task."""
    # Iter 212m-169 — BINContext gate.  This tool is read-only project
    # metadata so we surface owner/repo/branch straight from bin_ctx.
    # Extra metadata (tech_stack, last_task) is fetched from the DB
    # ONLY after ownership is proven by the presence of a valid
    # BINContext for this user (bin_ctx.pid + bin_ctx.bin_id).
    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR
    db = get_db()
    extra: dict = {}
    if db is not None:
        try:
            proj = await db.cto_projects.find_one(
                {"project_id": rc["pid"], "user_id": rc["bin_id"]},
                {"_id": 0, "name": 1, "tech_stack": 1, "last_task": 1,
                 "tasks_done": 1},
            )
            if proj:
                extra = {
                    "name":       proj.get("name"),
                    "tech_stack": proj.get("tech_stack", "unknown"),
                    "last_task":  proj.get("last_task"),
                    "tasks_done": proj.get("tasks_done", 0),
                }
        except Exception as e:                       # noqa: BLE001
            logger.debug("get_repo_info metadata lookup failed: %r", e)
    return {
        "ok":           True,
        "project_id":   rc["pid"],
        "github_owner": rc["owner"],
        "github_repo":  rc["repo"],
        "branch":       rc["branch"],
        "has_pat":      bool(rc["token"]),
        **extra,
    }


# ── TOOL: save_finding (persist a conversational audit finding) ─────────────
#
# 2026-08-22 — closes a real gap: when a user asks "check my code for
# security problems" (or any bug/quality audit) conversationally, ORA
# would previously just write a nice markdown report (## CRITICAL /
# ## HIGH / ...) straight into the chat reply — and nothing was ever
# saved. The report vanished into scroll-back with no way to track,
# revisit, or get reminded about those issues later. `cto_open_findings`
# already exists and already backs the reminder banner
# (ScanStatusStrip.jsx) + the Prompt Starter panel's "FROM YOUR REPO"
# suggestions — it was just never fed by conversational audits, only
# by the automated scanner pipelines. This tool lets ORA write ONE
# finding at a time into that same real backlog while it narrates the
# audit, using the exact same persistence helper the scanners use.
async def save_finding(ctx: dict, args: dict) -> dict:
    """Persist one security/bug/quality finding discovered during a
    conversational audit into the user's real findings backlog (the
    same store the automated scanners write to) — call this once per
    finding whenever you report a CRITICAL/HIGH/MEDIUM/LOW audit
    result in chat, so it can be tracked and the user can be reminded
    later instead of it only existing as chat text."""
    rc = _repo_ctx_from(ctx)
    if rc is None:
        return _NO_BIN_CTX_ERROR

    a = args or {}
    title = (a.get("title") or "").strip()
    severity = (a.get("severity") or "").strip().lower()
    if not title:
        return {"ok": False, "error": "title is required"}
    if severity not in ("critical", "high", "medium", "low"):
        return {"ok": False, "error": "severity must be one of: critical, high, medium, low"}

    file_ = (a.get("file") or "").strip()
    try:
        line_ = int(a.get("line") or 0)
    except (TypeError, ValueError):
        line_ = 0

    rule_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "finding"
    finding = {
        "scanner":  "ora_chat_audit",
        "id":       rule_slug,
        "severity": severity,
        "file":     file_,
        "line":     line_,
        "title":    title[:200],
        "message":  (a.get("description") or "")[:500],
        "fix_hint": (a.get("fix_hint") or "")[:300],
    }

    db = get_db()
    if db is None:
        return {"ok": False, "error": "database unavailable"}

    from services.loop_full_scan import persist_findings_to_backlog
    try:
        written = await persist_findings_to_backlog(
            db, user_id=rc["bin_id"], project_id=rc["pid"],
            findings=[finding], scan_source="ora_chat_audit",
        )
    except Exception as e:                             # noqa: BLE001
        logger.warning("save_finding persist failed: %r", e)
        return {"ok": False, "error": "failed to save finding"}

    return {
        "ok":       True,
        "saved":    written > 0,
        "severity": severity,
        # 2026-08-23 — findings-to-fix bridge. Same deterministic id
        # formula as persist_findings_to_backlog (loop_full_scan.py)
        # so orchestrator.py can track this finding and, later, the
        # fix pipeline can write back "resolved" against the exact
        # same row.
        "finding_id": f"ora_chat_audit::{file_}:{line_}:{rule_slug}",
        "file":     file_,
        "line":     line_,
        "title":    finding["title"],
        "message":  finding["message"],
        "fix_hint": finding["fix_hint"],
        "note": (
            "Saved to the user's findings backlog — it will surface in "
            "their reminder strip and personalized suggestions."
            if severity in ("critical", "high") else
            "Recorded. Note: medium/low severity findings don't surface "
            "on the reminder strip by design — only critical/high do."
        ),
    }


# ── TOOL 8: execute_bash (read-only local pod filesystem) ────────────────────
#
# Iter 138 — closes the "ORA can't inspect /app/ files" gap that caused
# the model to hallucinate file contents or mis-use the aurem-handoff
# fence whenever the user asked to run a literal terminal command
# (`cat /app/...`, `find /app/backend/...`). The earlier catalog only
# had GitHub-API tools, so anything on the LOCAL pod filesystem was
# inaccessible. We now ship a tightly-scoped, read-only shell runner.
#
# Safety contract (do NOT relax without an audit):
#   - Allowlist of binaries (no shell builtins, no chained `; rm`)
#   - 15 s wall-clock cap
#   - 8 KB stdout / 1 KB stderr cap to prevent context bloat
#   - No env passthrough required — inherits the worker's env so
#     paths like /app are reachable but no extra secrets get exposed
#   - SEC-001 fix (2026-01-22): runs via `create_subprocess_exec` with
#     an argv list (NO shell). Any shell metacharacter (`|`,`;`,`&`,
#     `` ` ``,`$`,`>`,`<`) is hard-rejected before parsing. Pipes/
#     redirects/chaining/substitution are intentionally NOT supported
#     — a prior version gated only the first token then ran the full
#     string via `create_subprocess_shell`, which let a piped/chained
#     non-allowlisted command (e.g. `cat foo | rm -rf /`) execute.
_BASH_ALLOWED = {
    "cat", "head", "tail", "grep", "find", "ls", "wc",
    "sed", "awk", "echo", "pwd", "stat", "tree", "file",
    "which", "whereis", "basename", "dirname", "sort", "uniq",
    "cut", "tr", "true", "false",
}


async def execute_bash(ctx: dict, args: dict) -> dict:
    """Run a READ-ONLY bash command on the local pod filesystem.

    args:
      command: str — the bash command (first token must be allowlisted)

    SECURITY (Iter 212m-168): This tool exposes the local pod
    filesystem (`/app`, `/tmp`, `/var/log`, etc.) — which contains
    the internal AUREM codebase.  It MUST NOT be exposed to
    end-user (customer) chat sessions or the LLM will surface AUREM
    internal paths when the user asks about *their* connected repo
    ("which repo are you working on?" → LLM inspects /app/backend and
    reports auremcto internals — privacy + correctness bug).

    Only founder/admin sessions (ctx["is_founder"] is True) are
    allowed to invoke this tool.  Regular users see a clear refusal
    that redirects them to the GitHub-scoped tools.
    """
    import asyncio
    import shlex

    # Iter 212m-168 — HARD gate.  ctx.is_founder is populated by the
    # orchestrator from the authenticated user's role.  Anonymous /
    # regular / paid users never see this tool in the catalog either
    # (see orchestrator.py filter), but this belt-and-braces check
    # blocks any LLM that hallucinates the tool name from succeeding.
    #
    # Iter 212m-169 — Additional BINContext defence.  When a BINContext
    # is present (project-scoped chat), we ALSO require bin_ctx.is_founder.
    # This closes the theoretical bypass where a founder starts a chat
    # session (ctx.is_founder=True from JWT) but is currently scoped to
    # a project marked non-founder in bin_ctx — we defer to the stricter
    # of the two.  When there is no bin_ctx (Home chat), we fall back
    # to ctx.is_founder alone.
    if not bool(ctx.get("is_founder")):
        return {
            "ok": False,
            "error": (
                "execute_bash is restricted to founder/admin accounts. "
                "For the user's connected repo, use `read_repo_file`, "
                "`read_repo_files`, `list_repo_files`, `search_repo`, "
                "or `semantic_search_repo` — those are the ONLY tools "
                "that read the user's own GitHub repo.  Never inspect "
                "local pod paths (/app, /tmp, /var, /etc, /usr) — "
                "those are internal AUREM server paths, not the user's "
                "codebase."
            ),
        }
    _bc = (ctx or {}).get("bin_ctx")
    if _bc is not None and not bool(getattr(_bc, "is_founder", False)):
        return {
            "ok": False,
            "error": (
                "execute_bash is restricted to founder/admin accounts. "
                "Use GitHub-scoped tools instead."
            ),
        }

    cmd = (args or {}).get("command", "").strip()
    if not cmd:
        return {"ok": False, "error": "command is required"}

    # Iter 212m-170 — ORA ABSOLUTE BOUNDARY on execute_bash args.
    # Even for founders, /app/*, /tmp/*, /var/*, /etc/*, /usr/*,
    # /root/*, /home/* paths are OFF-LIMITS unless the founder has
    # explicitly enabled debug_mode on their ORAContext (which is
    # itself gated on is_founder=True at build time).  This means
    # the DEFAULT founder session cannot inspect the AUREM pod
    # filesystem — they must opt-in to debug_mode first (via the
    # admin panel toggle, tracked in ORAContext.debug_mode).
    #
    # Iter 388t — Bug 20 fix.  Founder-pod-debug mode (is_founder
    # AND no project attached) is a SECOND escape hatch specifically
    # for the founder's own no-project workspace.  It runs the
    # additional `validate_founder_pod_command` safety check
    # (no `;`/`&&`/`||` chaining, no `..` traversal, path args
    # confined to /app, /tmp, /var, /etc, /usr and outside the
    # secret denylist) BEFORE lifting the ora-boundary refusal.
    #
    # Non-founder sessions never reach this point (blocked above),
    # but the boundary check runs anyway as defence-in-depth.
    from services.ora_context import (
        path_hits_ora_boundary,
        validate_founder_pod_command,
    )
    _hit = path_hits_ora_boundary(cmd)
    if _hit is not None:
        # Founder + debug_mode → allow.  Founder + pod-mode (no
        # project) → allow after the founder-pod command validator
        # accepts the command.  Every other combination refuses.
        _founder_pod = bool(ctx.get("founder_pod_mode"))
        allow = (
            _bc is not None
            and bool(getattr(_bc, "is_founder", False))
            and bool(getattr(_bc, "debug_mode", False))
        )
        if not allow and _founder_pod:
            # Run the extra safety validator before unlocking.  If
            # the founder's command trips a rule (chaining, traversal,
            # path outside allowlist, secret denylist), we refuse
            # here with a specific reason so the founder can fix it.
            _ok, _reason = validate_founder_pod_command(cmd)
            if _ok:
                allow = True
            else:
                return {
                    "ok": False,
                    "error": (
                        f"execute_bash refused (founder-pod-mode safety): {_reason}"
                    ),
                    "error_class": "founder_pod_validation",
                }
        if not allow:
            # Iter 212m-171 — log to audit_log so /admin/boundary-probes
            # can surface it as an admin overview tile.
            try:
                from cto_services.db import get_db
                from datetime import datetime, timezone
                _db = get_db()
                if _db is not None:
                    await _db.audit_log.insert_one({
                        "ts": datetime.now(timezone.utc),
                        "event": "ora_boundary_violation",
                        "user_id": ctx.get("user_id"),
                        "is_founder": bool(getattr(_bc, "is_founder", False))
                                      if _bc else False,
                        "cmd_head": cmd[:120],
                        "hit": _hit,
                    })
            except Exception as _e:
                logger.debug("[silent-catch] services/local_tools.py:1922 in execute_bash — %r", _e)
                pass
            return {
                "ok": False,
                "error": (
                    f"execute_bash refused: the command references "
                    f"ORA-internal path `{_hit}` which is OFF-LIMITS in "
                    f"normal mode.  If you are a founder developing "
                    f"AUREM itself, enable Debug Mode in the admin "
                    f"panel and start a new chat session."
                ),
                "error_class": "ora_boundary_violation",
            }


    # SEC-001 fix (audit 2026-01-22) — the old gate only checked the
    # FIRST token against the allowlist but still ran the WHOLE string
    # via create_subprocess_shell. `cat foo | rm -rf /` passed the gate
    # (first token "cat") yet the shell happily executed the piped
    # `rm`. Same hole for `&&`, `;`, `$(...)`, backticks, `>` redirects.
    # Fix: (1) hard-reject any shell metacharacter outright — this is
    # a read-only inspection tool, it never needs pipes/redirects/
    # substitution/chaining; (2) run via create_subprocess_exec with
    # an argv list so there is no shell to interpret metacharacters
    # even if one slipped through.
    _SHELL_METACHARS = set('|;&`$><\n')
    if any(ch in cmd for ch in _SHELL_METACHARS):
        return {
            "ok": False,
            "error": (
                "execute_bash refused: pipes, redirects, command "
                "chaining (`|`, `;`, `&&`, `>`, `$()`, backticks) are "
                "not permitted. Run one plain read-only command at a "
                "time, e.g. `grep -rn pattern dir` (use its own flags "
                "instead of piping to `head`)."
            ),
            "error_class": "shell_metachar_blocked",
        }

    # Parse first token to gate against the allowlist. We use shlex so
    # quoted paths don't trip the parser.
    try:
        tokens = shlex.split(cmd)
        first_word = tokens[0]
    except (ValueError, IndexError) as e:
        return {"ok": False, "error": f"shell parse error: {e}"}

    # Strip a leading path so the LLM can write `/usr/bin/cat …` too.
    binary = first_word.rsplit("/", 1)[-1]
    if binary not in _BASH_ALLOWED:
        return {
            "ok": False,
            "error": (
                f"Command '{binary}' not allowed. Only read-only "
                f"commands permitted: {', '.join(sorted(_BASH_ALLOWED))}."
            ),
        }

    try:
        proc = await asyncio.create_subprocess_exec(
            *tokens,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=15.0,
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": stdout_b.decode("utf-8", errors="replace")[:8000],
            "stderr": stderr_b.decode("utf-8", errors="replace")[:1000],
            "exit_code": proc.returncode,
            "command": cmd[:200],
        }
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception as _e:
            logger.debug("[silent-catch] services/local_tools.py:1974 in execute_bash — %r", _e)
            pass
        return {"ok": False, "error": "Command timed out after 15s"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── Catalog ───────────────────────────────────────────────────────────────────

TOOL_SPECS: list[dict] = [
    {
        "name": "read_repo_file",
        "description": (
            "Fetch the FULL TEXT of ONE file in the connected repo by path. "
            "Use whenever you need to verify a bug claim, read code, or quote actual lines. "
            "Strongly preferred over asking the user to paste. "
            "For multiple files, prefer read_repo_files (parallel, faster)."
        ),
        "args_spec": {
            "path":  "string — repo-relative path e.g. 'backend/routers/auth.py'",
            "lines": "optional [start,end] line range, 1-indexed inclusive",
        },
    },
    {
        "name": "read_repo_files",
        "description": (
            "Fetch UP TO 6 files from the connected repo IN PARALLEL — same as "
            "read_repo_file but faster for multi-file tasks. Use this when you "
            "need to read multiple files before planning a fix. "
            "Example: security fix touching 5 routers = 1 call vs 5 sequential calls."
        ),
        "args_spec": {
            "paths": "array of strings — up to 6 repo-relative file paths",
            "lines": "optional [start,end] applied to ALL files",
        },
    },
    {
        "name": "write_repo_file",
        "description": (
            "Iter 212m-6 — Commit a SINGLE file directly to the connected "
            "repo with the supplied full content. Use this for SMALL surgical "
            "fixes you can ship in one round-trip (typo, missing import, "
            "single-function patch). DO NOT use for multi-file refactors — "
            "for those, emit an `aurem-handoff` brief instead so the task "
            "queue runs the full agent+verify pipeline.\n"
            "\n"
            "Vanguard regex scans the patch before commit; critical findings "
            "block the write. Returns the commit SHA + html_url. The file's "
            "FULL new body is required — we don't apply diffs in-place."
        ),
        "args_spec": {
            "path":            "string — repo-relative file path",
            "content":         "string — COMPLETE new file body (no diff, no ellipses)",
            "commit_message":  "optional string — defaults to 'AUREM: edit <path>'",
        },
    },
    {
        "name": "get_repo_structure",
        "description": (
            "Iter 212m-7 — Return cached function / class / @router / export "
            "symbol map for the connected project. Built lazily by every "
            "successful `read_repo_file` call in this session. Use this "
            "WHEN: the user asks 'what functions exist in X', 'list all "
            "routes', 'show the class names in foo.py' AFTER a prior read. "
            "Returns immediately from in-memory cache — no GitHub round-trip."
        ),
        "args_spec": {
            "path": "optional string — single-file scope. Omit for whole-project map.",
        },
    },
    {
        "name": "list_repo_files",
        "description": (
            "List files in the connected repo tree. Use to discover file structure "
            "before reading specific files. Supports glob patterns. "
            "Example: list all Python routers → path='backend/routers', ext='.py'"
        ),
        "args_spec": {
            "path":    "optional string — sub-directory to list (default: root)",
            "pattern": "optional glob pattern e.g. '*.py', 'routers/*.py', '**/*.jsx'",
            "max":     "optional int — max results (default 150)",
        },
    },
    {
        "name": "search_repo",
        "description": (
            "Search for a pattern across files in the connected repo. "
            "Use to find all occurrences of a bug pattern, import, or function. "
            "Example: find all verify_exp=False → pattern='verify_exp.*False', ext='.py'"
        ),
        "args_spec": {
            "pattern": "string — text or regex to search for",
            "path":    "optional string — limit to this directory",
            "ext":     "optional string — limit to this extension e.g. '.py'",
            "max":     "optional int — max matching files (default 20)",
        },
    },
    {
        "name": "semantic_search_repo",
        "description": (
            "Search the connected repo by CONCEPT or symbol using GitHub Code Search. "
            "USE THIS FIRST before search_repo when you need to find files related to "
            "a concept like 'authentication', 'rate limiting', 'payment processing'. "
            "Returns file paths ranked by relevance. Then use read_repo_files to read "
            "them. Example: query='JWT token validation' finds auth.py, middleware.py, "
            "utils/token.py even if they don't contain the exact phrase."
        ),
        "args_spec": {
            "query":    "string — concept or symbol to find (natural language ok)",
            "language": "optional string — 'python', 'javascript', 'typescript'",
            "max":      "optional int — max results, default 10",
        },
    },
    {
        "name": "get_commit_diff",
        "description": (
            "Get the full diff for a specific commit — shows exactly what files "
            "changed and how. Use this when the project brain shows recent commits "
            "and you want to understand HOW similar work was done before writing "
            "new code. Example: brain shows 'added Stripe webhook handler 2 days "
            "ago' → call get_commit_diff with that SHA to see the exact pattern used."
        ),
        "args_spec": {
            "sha": "string — commit SHA (7 or 40 chars, from brain context recent commits)",
        },
    },
    {
        "name": "get_repo_info",
        "description": (
            "Get connected project metadata: owner, repo, branch, tech stack, "
            "last task, tasks completed. Call this first if you're unsure what "
            "project is connected."
        ),
        "args_spec": {},
    },
    {
        "name": "save_finding",
        "description": (
            "Persist ONE security/bug/quality finding into the user's "
            "real, trackable findings backlog. Call this ONCE PER "
            "FINDING whenever you report a CRITICAL/HIGH/MEDIUM/LOW "
            "issue during a security review, bug hunt, or code-quality "
            "audit in chat — e.g. after saying '## CRITICAL — JWT secret "
            "has an insecure default fallback value (auth.py:42)', "
            "immediately call this "
            "tool with that same title/severity/file/line so it's saved, "
            "not just printed in the chat text. Without this call, the "
            "finding disappears the moment the chat scrolls — nothing "
            "gets tracked, reminded about later, or shown as 'fixed'."
        ),
        "args_spec": {
            "title":       "string — short finding title, e.g. 'JWT secret has an insecure default fallback value'",
            "severity":    "string — one of: critical, high, medium, low",
            "file":        "string (optional) — file path the issue is in",
            "line":        "integer (optional) — line number",
            "description": "string (optional) — 1-2 sentence explanation",
            "fix_hint":    "string (optional) — short suggested fix",
        },
    },
    {
        "name": "execute_bash",
        "description": (
            "Run a READ-ONLY bash command on the LOCAL pod filesystem "
            "(everything under /app/, /tmp/, /var/log/, etc.). Use this "
            "whenever the user asks you to 'run this terminal command', "
            "'cat /app/...', 'find /app/backend/...', or any inspection "
            "of files that are NOT in the connected GitHub repo. NEVER "
            "fabricate the output — always call this tool and return the "
            "EXACT stdout. Only safe read-only binaries are allowed: "
            "cat, head, tail, grep, find, ls, wc, sed, awk, echo, pwd, "
            "stat, tree, file, which, whereis, basename, dirname, sort, "
            "uniq, cut, tr. Hard caps: 15 s wall-clock, 8 KB stdout."
        ),
        "args_spec": {
            "command": "string — the bash command to run (read-only only)",
        },
    },
] + _WEB_TOOL_SPECS + _DEV_TOOL_SPECS + _VERCEL_TOOL_SPECS

# ── Dispatch table ────────────────────────────────────────────────────────────

LOCAL_TOOLS: dict[str, callable] = {
    "read_repo_file":       read_repo_file,
    "read_repo_files":      read_repo_files,
    "write_repo_file":      write_repo_file,
    "get_repo_structure":   get_repo_structure,
    "list_repo_files":      list_repo_files,
    "search_repo":          search_repo,
    "semantic_search_repo": semantic_search_repo,
    "get_commit_diff":      get_commit_diff,
    "get_repo_info":        get_repo_info,
    "save_finding":         save_finding,
    "execute_bash":         execute_bash,
    **_WEB_TOOLS,
    **_DEV_TOOLS,
    **_VERCEL_TOOLS,
}


async def invoke_local_tool(name: str, args: dict, ctx: dict) -> Optional[dict]:
    """Run a local tool. Returns None if `name` isn't a local tool.

    Iter 210 — every dispatch goes through `tool_executor.execute()`
    so an HTTP error (401/403/404/429/5xx) raised inside the tool gets
    mapped to a structured `system_signal` and appended to
    `ctx["system_signals"]`. The orchestrator harvests that list at
    the end of the agent loop and the SSE final-frame propagates it
    to `SystemSignalBanner.jsx` on the frontend.

    The LLM-facing return shape stays the same (`{"ok": bool, ...}`)
    so existing call sites don't break. On failure we surface
    `llm_facing = "Tool X could not complete."` instead of the raw
    error text — preventing the model from describing GitHub auth
    failures itself (R3 of the ORA system prompt).

    `ctx["tool_calls"]` is also tracked here so the CitationGuard can
    diff "what was claimed" vs "what was read" in this turn.
    """
    fn = LOCAL_TOOLS.get(name)
    if not fn:
        return None

    # Track every dispatch (used by CitationGuard + audit log)
    ctx.setdefault("tool_calls", []).append({
        "tool": name,
        "args": args or {},
    })

    from .tool_executor import execute as _tx_execute

    async def _runner():
        return await fn(ctx, args or {})

    out = await _tx_execute(name, _runner)
    if out.get("ok"):
        return out["data"]

    # Failed — capture typed signal for the frontend banner.
    sig = {
        "signal":      out["system_signal"],
        "severity":    out["severity"],
        "tool":        out["tool"],
        "http_status": out.get("http_status"),
    }
    ctx.setdefault("system_signals", []).append(sig)
    logger.warning(
        "invoke_local_tool: %s failed signal=%s status=%s class=%s",
        name, sig["signal"], sig.get("http_status"), out.get("error_class"),
    )
    # Iter 212m-6 — Surface the ERROR CLASS to the LLM so it can
    # self-correct (without leaking raw text, R3 anti-hallucination).
    # Previously every failure flattened to "Tool X could not complete"
    # which left the LLM looping with identical params. With the class
    # appended it knows whether to retry with auth, try a different
    # path, back off for rate limit, etc.
    _CLASS_MAP = {
        "auth":          "AUTH — PAT may be missing, expired, or lacks scope for this repo.",
        "not_found":     "NOT_FOUND — the path/resource doesn't exist. Call list_repo_files to discover real paths.",
        "rate_limit":    "RATE_LIMIT — GitHub API quota hit. Wait or back off; do not retry immediately.",
        "timeout":       "TIMEOUT — the call exceeded the budget. Try a narrower query.",
        "network":       "NETWORK — could not reach the upstream. Treat as transient.",
        "server":        "SERVER — upstream 5xx. Transient; retry once at most.",
        "bad_request":   "BAD_REQUEST — args are malformed. Re-read the tool spec before retrying.",
    }
    err_class = (out.get("error_class") or "").lower()
    detail = _CLASS_MAP.get(err_class)
    if detail:
        llm_facing = f"Tool {name} failed: {detail}"
    else:
        llm_facing = out["llm_facing"]
    # Neutral LLM-facing payload (NEVER include the raw error message)
    return {
        "ok":             False,
        "error":          llm_facing,
        "error_class":    err_class or None,
        "system_signal":  sig["signal"],
    }
