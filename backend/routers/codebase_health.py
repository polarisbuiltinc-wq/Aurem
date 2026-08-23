"""
routers/codebase_health.py  —  Iter 212m-72 (Phase 2)
=====================================================
Five-category codebase health scanner.  Founder-facing endpoint
powering `/codebase-health` dashboard.

Endpoints (all founder-gated, project_id required):

  POST /api/aurem-dev/codebase-health/scan
       Body: { project_id, categories: [security, performance,
                                       code_quality, dependencies,
                                       database] }
       Returns: { score: 0-100, summary, breakdown: {<cat>: {...}} }

  POST /api/aurem-dev/codebase-health/fix
       Body: { project_id, finding_id, category }
       Creates a `cto_task` with the fix prompt + auto-runs it
       through the existing Loop pipeline.  Returns task_id.

All five category scanners are PURE deterministic static analysers
that walk the user's connected GitHub repo via the existing
`_list_repo_tree` + `_fetch_file` helpers from `security_scan.py`.
Zero LLM cost on the scan path — only the Fix button pays an LLM call
when the user actually wants ORA to write the patch.

The repo walk + file fetch is the shared bottleneck across all 5
scanners, so `scan()` does it ONCE then dispatches the cached
`{path: text}` dict to each requested category.  This means a
"Full scan" (all 5) costs the same GitHub-API budget as a single
category — just 4× more CPU on the user's static analysis.
"""
# arch: allow-http — GitHub tree walk (calls _gh_get service) (iter 212m-225)
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import JSONResponse

from cto_services.auth import current_dev, require_admin
from cto_services.db import get_db
from services.http import ext_client
from routers.security_scan import (
    _list_repo_tree, _list_repo_tree_with_sha, _fetch_file,
    _MAX_FILES, _MAX_BYTES_PER_FILE, _SCAN_EXTS, _SKIP_DIRS,
    _CONCURRENT_FETCHES,
)
from services.scan_cache import (
    get_cached_text_cache, put_cached_text_cache,
)

router = APIRouter(prefix="/codebase-health", tags=["Codebase Health"])
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Iter arch-2a — the 6 category scanners + SCANNERS + scoring helpers
# now live in services/codebase_health_core.py (pure functions, no
# router deps) so services/project_onboarding_scan.py can import them
# without an inverted service→router dependency. This module still
# imports them back for its own /scan and /fix endpoints below —
# behaviour is unchanged, only the ownership moved.
# ──────────────────────────────────────────────────────────────────────
from services.codebase_health_core import (                # noqa: E402
    _is_dockerfile, _scan_security, _scan_performance,
    _scan_code_quality, _scan_dependencies, _scan_database,
    _scan_docker_cis, SCANNERS, _norm_sev, _score_for_findings,
    _category_label,
)
from services.scanner_utils import (                       # noqa: E402
    is_scanner_rule_file as _is_scanner_rule_file,
    _SCANNER_RULE_FILES,
)


# The 6 category scanners, SCANNERS, _is_dockerfile, and the scoring
# helpers (_norm_sev/_score_for_findings/_category_label) used to be
# defined here. Iter arch-2a relocated them verbatim to
# services/codebase_health_core.py (imported above) to fix a
# service→router boundary violation. See that module for the actual
# implementations — nothing below this point was changed.


async def _build_text_cache(owner: str, repo: str, pat: str) -> dict[str, str]:
    """Walk the repo tree + fetch every scannable file.  Cached for the
    duration of a single /scan request so all 5 categories share the
    same fetch budget.

    Iter 212m-79 — also checks Redis for a previously-built bundle
    keyed on `owner/repo@tree_sha`.  Cross-pod cache hits skip the
    ~50-600 GitHub calls entirely (~60 s saved on large repos).  TTL
    24 h; key invalidates automatically on the next commit because the
    tree SHA changes.

    Iter 212m-221 — Two hardening changes to root-cause the
    intermittent 1.3s Cloudflare 502 on prod:
      * Explicit `Timeout(45)` on the outer `AsyncClient` — the old
        default-None meant a single stalled GH connection could hold
        the pod's event loop indefinitely, triggering Cloudflare's
        origin-idle-timeout intercept.
      * Structured latency log on every call (`scan.text_cache
        owner=… repo=… sha=… hit=… files=… ms=…`) so a future 502
        can be traced from the log stream alone.
    """
    import time as _time
    _t0 = _time.time()
    _hit  = False
    _files = 0

    _timeout = httpx.Timeout(45.0, connect=6.0, read=15.0)
    async with ext_client("github", timeout=httpx.Timeout(45.0, connect=6.0, read=15.0)) as client:
        blobs, tree_sha = await _list_repo_tree_with_sha(
            client, owner, repo, pat,
        )

        # ── Redis-backed dedup lookup ──────────────────────────────
        if tree_sha:
            cached = await get_cached_text_cache(owner, repo, tree_sha)
            if cached is not None:
                # Hit — skip GitHub entirely.  Re-apply the path
                # candidate filter in case _SCAN_EXTS changed between
                # writes (cheap; pure-Python loop over keys).
                filtered: dict[str, str] = {}
                for path, txt in cached.items():
                    if not path:
                        continue
                    if any(p in _SKIP_DIRS for p in path.split("/")):
                        continue
                    lower = path.lower()
                    if not (any(lower.endswith(ext) for ext in _SCAN_EXTS)
                            or lower.endswith("requirements.txt")
                            or lower.endswith("package.json")
                            or _is_dockerfile(lower)):
                        continue
                    filtered[path] = txt
                _hit  = True
                _files = len(filtered)
                logger.info(
                    "scan.text_cache owner=%s repo=%s sha=%s hit=1 "
                    "files=%d ms=%d",
                    owner, repo, tree_sha[:7], _files,
                    int((_time.time() - _t0) * 1000),
                )
                return filtered

        text_cache: dict[str, str] = {}
        candidates: list[dict] = []
        for b in blobs:
            path = b.get("path", "")
            if not path:
                continue
            if any(p in _SKIP_DIRS for p in path.split("/")):
                continue
            lower = path.lower()
            if not (any(lower.endswith(ext) for ext in _SCAN_EXTS)
                    or lower.endswith("requirements.txt")
                    or lower.endswith("package.json")
                    or _is_dockerfile(lower)):
                continue
            if b.get("size", 0) > _MAX_BYTES_PER_FILE:
                continue
            candidates.append(b)
            if len(candidates) >= _MAX_FILES:
                break

        sem = asyncio.Semaphore(_CONCURRENT_FETCHES)

        async def _one(blob):
            async with sem:
                t = await _fetch_file(client, owner, repo, blob["path"], pat)
            if t:
                text_cache[blob["path"]] = t

        await asyncio.gather(*[_one(b) for b in candidates])

        # ── Best-effort write-back; never blocks the response ─────
        if tree_sha and text_cache:
            try:
                await put_cached_text_cache(owner, repo, tree_sha, text_cache)
            except Exception as e:
                logger.debug("scan_cache put_cached failed: %r", e)

    _files = len(text_cache)
    logger.info(
        "scan.text_cache owner=%s repo=%s sha=%s hit=0 files=%d "
        "candidates=%d ms=%d",
        owner, repo, (tree_sha or "-")[:7], _files, len(candidates),
        int((_time.time() - _t0) * 1000),
    )
    return text_cache


@router.get("/cache-stats")
async def cache_stats(authorization: Optional[str] = Header(None)) -> dict:
    """Iter 212m-79 — surface Redis scan-cache hit-rate to founders.
    Iter 212m-158 — was a custom is_admin check; now routed through
    the shared `require_admin` helper for consistency."""
    await require_admin(authorization)
    from services.scan_cache import get_scan_cache_stats
    return get_scan_cache_stats()


@router.post("/scan")
async def scan(
    body: dict, authorization: Optional[str] = Header(None),
) -> dict:
    # 2026-08-22 fix — was require_admin (Iter 212m-158), but the
    # frontend route guard was ALREADY relaxed to <PrivateRoute> (any
    # logged-in user) back in a later iteration, and this handler
    # already fully scopes by (project_id, user_id) + has its own
    # rate limiting for non-founder accounts (line ~672) — the
    # admin-only gate here was leftover from before that frontend
    # change and silently 403'd every real paying customer who
    # clicked "Review findings →" from the chat reminder banner.
    user = await current_dev(authorization)
    user_id = user["user_id"]
    project_id = (body or {}).get("project_id")
    categories = (body or {}).get("categories") or list(SCANNERS.keys())
    categories = [c for c in categories if c in SCANNERS]
    if not project_id:
        raise HTTPException(400, "project_id required")
    if not categories:
        raise HTTPException(400, "At least one category required")

    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    # Iter 212m-75 — sliding-window rate limit (10 scans / hour / user / category).
    # Iter 212m-110 — admins, founders and is_unlimited accounts are
    # ALL exempt. Each call writes one log row to `scan_rate_limits`;
    # the prune step deletes rows older than the window so the
    # collection stays small. Returns 429 with `retry_after_seconds`
    # on the first denied category so the client can wait the right
    # amount.
    is_admin = bool(
        user.get("is_admin")
        or user.get("is_unlimited")
        or (user.get("tier") == "founder")
    )
    if not is_admin:
        denied_cat, retry_secs, remaining = await _check_scan_rate_limit(
            db, user_id, categories,
        )
        if denied_cat is not None:
            mins = max(1, int(round(retry_secs / 60.0)))
            raise HTTPException(429, {
                "error":               "scan_rate_limited",
                "category":            denied_cat,
                "message":             (f"You have used 10/10 scans for "
                                        f"'{denied_cat}' this hour. Try again "
                                        f"in {mins} minutes."),
                "retry_after_seconds": int(retry_secs),
            })
    else:
        remaining = {c: 999 for c in categories}

    proj = await db.cto_projects.find_one(
        {"project_id": project_id, "user_id": user_id},
        {"_id": 0, "github_owner": 1, "github_repo": 1, "github_token": 1,
         "auth_method": 1, "installation_id": 1, "user_id": 1},
    )
    if not proj:
        raise HTTPException(404, "Project not found")
    owner = proj.get("github_owner") or ""
    repo  = proj.get("github_repo") or ""
    # 2026-02-11 · Phase 3b (Bug 2 fix) — dual-auth token resolver.
    from services.pat_vault import get_repo_token
    pat   = await get_repo_token(proj)
    if not (owner and repo and pat):
        raise HTTPException(400, "Project missing GitHub linkage / PAT")

    try:
        text_cache = await _build_text_cache(owner, repo, pat)
    except HTTPException:
        # Iter 212m-216 — meaningful GH errors already carry the
        # right status + detail from `_gh_get`.  Do NOT re-wrap them
        # as 502 (that's what caused Cloudflare to intercept and
        # replace the body with its own "Bad gateway" HTML on prod
        # for months).  Just log + propagate.
        raise
    except Exception as e:
        # Genuine unexpected crash — log full context for founder
        # monitoring, but return a caller-actionable 502 with the
        # actual exception class in the detail so a screenshot alone
        # is enough to root-cause.
        logger.exception(
            "codebase_health.scan crashed inside _build_text_cache "
            "(user=%s, project=%s, owner=%s, repo=%s)",
            user_id, project_id, owner, repo,
        )
        raise HTTPException(
            502,
            f"github_fetch_crashed: {type(e).__name__}: {str(e)[:200]}",
        )

    breakdown: dict[str, dict] = {}
    all_findings: list[dict] = []
    # Iter 212m-193 — findings already fixed (commits on draft-PR
    # branches) must not resurrect on rescan: split them out, score
    # and count ACTIVE findings only.
    from services.fixed_findings import get_fixed_map, split_findings
    fixed_map = await get_fixed_map(db, user_id=user_id, project_id=project_id)
    total_fixed = 0
    for cat in categories:
        raw_findings = SCANNERS[cat](text_cache)
        findings, fixed_findings = split_findings(raw_findings, fixed_map)
        total_fixed += len(fixed_findings)
        # Cap to top 100 per category to keep the response tight.
        sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        findings.sort(key=lambda f: (sev_rank.get(f["severity"], 9),
                                      f.get("file", ""), f.get("line", 0)))
        capped = findings[:100]
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in findings:
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        breakdown[cat] = {
            "score":    _score_for_findings(findings),
            "counts":   counts,
            "total":    len(findings),
            "findings": capped,
            "fixed_count": len(fixed_findings),
            "fixed":       fixed_findings[:100],
        }
        all_findings.extend(findings)

    overall_score = _score_for_findings(all_findings)
    label, tone = _category_label(overall_score)
    total = sum(b["total"] for b in breakdown.values())
    payload = {
        "ok":            True,
        "score":         overall_score,
        "label":         label,
        "tone":          tone,
        "total":         total,
        "total_fixed":   total_fixed,
        "scanned_files": len(text_cache),
        "summary":       (
            f"{total} issues found across {len(categories)} categories — "
            f"{sum(1 for f in all_findings if f['severity']=='critical')} critical."
            + (f" {total_fixed} already fixed." if total_fixed else "")
        ),
        "breakdown":     breakdown,
        "scan_remaining": remaining,
    }
    # Iter 212m-127 — Persist the scan result so the Dashboard health
    # ring can read the most-recent score via GET /last without paying
    # the full scan cost on every page mount.  Best-effort: a Mongo
    # failure must NEVER block the user-visible scan response.
    try:
        # Iter 212m-177 — P1-5: a scan that read ZERO files scores 100
        # trivially and later contradicts real scans (PROD showed
        # 100-HEALTHY vs 0-CRITICAL for the same repo). Never persist it.
        if len(text_cache) > 0:
            await db.codebase_health_scans.insert_one({
                "user_id":       user_id,
                "project_id":    project_id,
                "score":         overall_score,
                "label":         label,
                "tone":          tone,
                "total":         total,
                "scanned_files": len(text_cache),
                "summary":       payload["summary"],
                "categories":    list(categories),
                "breakdown":     breakdown,
                "created_at":    time.time(),
            })
    except Exception as e:
        logger.debug("codebase_health_scans persist failed: %r", e)
    # Iter 212m-129 — Learning hook: persist a per-rule histogram of
    # this scan run so analytics can later answer "which rules trigger
    # most often for this user / project / across the platform".
    try:
        from services import ora_fix_learning as _ofl
        rule_counts: dict[str, int] = {}
        sev_counts:  dict[str, int] = {
            "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0,
        }
        for _f in all_findings:
            _rid = (_f.get("rule_id") or _f.get("rule")
                    or _f.get("title") or "unknown")
            rule_counts[_rid] = rule_counts.get(_rid, 0) + 1
            _sv = (_f.get("severity") or "").lower()
            if _sv in sev_counts:
                sev_counts[_sv] += 1
        await _ofl.record_scan_run(
            db, user_id=user_id, project_id=project_id,
            scanner="codebase_health",
            categories=list(categories),
            files_scanned=len(text_cache),
            counts=sev_counts,
            rule_counts=rule_counts,
            duration_ms=None,
            score=overall_score,
        )
    except Exception as _e:
        logger.debug("learning scan-run hook (health) soft-failed: %r", _e)
    # Iter 212m-75 — surface remaining quota per category in a header so
    # callers can render an inline counter without parsing the body.
    headers = {
        "X-Scan-Remaining": str(min(remaining.values()) if remaining else 0),
        "X-Scan-Remaining-Per-Category": ",".join(
            f"{c}:{n}" for c, n in remaining.items()
        ),
    }
    return JSONResponse(content=payload, headers=headers)


# ──────────────────────────────────────────────────────────────────────
# Iter 212m-127 — Dashboard health-ring lookup.  Returns the most recent
# persisted scan for the active project so the ring renders instantly
# without re-walking the GitHub tree.  Returns `score: null` (200, not
# 404) when the user hasn't scanned the project yet — the Dashboard
# already treats `null` as "ring hidden".
# ──────────────────────────────────────────────────────────────────────
@router.get("/last")
async def last_scan(
    project_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> dict:
    if not project_id:
        raise HTTPException(400, "project_id required")
    # 2026-08-22 fix — was require_admin, same regression as /scan
    # above: silently 403'd real paying customers polling their own
    # last scan result. Already scoped to this exact user_id in the
    # query below, so relaxing to current_dev can't leak another
    # user's scan data.
    user = await current_dev(authorization)
    user_id = user["user_id"]
    db = get_db()
    if db is None:
        # Don't 503 — frontend silently hides the ring on errors.
        return {"ok": True, "score": None}
    try:
        doc = await db.codebase_health_scans.find_one(
            {"user_id": user_id, "project_id": project_id,
             "scanned_files": {"$gt": 0}},   # Iter 212m-177 P1-5
            {"_id": 0},
            sort=[("created_at", -1)],
        )
    except Exception as e:
        logger.debug("codebase_health_scans read failed: %r", e)
        return {"ok": True, "score": None}
    if not doc:
        # Empty state — 200 with `score: null` instead of 404 noise.
        return {"ok": True, "score": None}
    # Iter 212m-147 — Defensive guard: a persisted (score=0, total=0)
    # row is logically impossible from a real scan (0 findings yields
    # score=100), so it can only come from a legacy bad write or a
    # crashed scan. Treat it as "no scan yet" so the top-bar ring
    # stays hidden instead of misleading the user with a red "0".
    _score = doc.get("score")
    _total = doc.get("total")
    if _score == 0 and (not _total or _total == 0):
        return {"ok": True, "score": None}
    return {
        "ok":            True,
        "score":         doc.get("score"),
        "label":         doc.get("label"),
        "tone":          doc.get("tone"),
        "total":         doc.get("total"),
        "scanned_files": doc.get("scanned_files"),
        "summary":       doc.get("summary"),
        "categories":    doc.get("categories") or [],
        # Iter 212m-176 — return the persisted breakdown so the
        # Codebase Health page can restore the last scan on reload
        # instead of showing "unscanned" after the user already paid.
        "breakdown":     doc.get("breakdown") or {},
        "created_at":    doc.get("created_at"),
    }


# ──────────────────────────────────────────────────────────────────────
# Iter 212m-75 — Sliding-window scan rate limiter.
#   • Bucket: (user_id, category)
#   • Window: 3600 seconds (1 hour, rolling)
#   • Cap:    10 successful scan starts per bucket
#   • Storage: scan_rate_limits collection (one doc per scan call)
#   • TTL: prune-on-read — every check deletes window-expired rows for
#         the caller so the collection stays bounded.
# ──────────────────────────────────────────────────────────────────────
_SCAN_RATE_WINDOW = 3600
_SCAN_RATE_CAP    = 10


async def _check_scan_rate_limit(
    db, user_id: str, categories: list[str],
) -> tuple[Optional[str], int, dict[str, int]]:
    """Returns (denied_category, retry_after_seconds, remaining_per_cat).

    If any requested category is over cap, returns the *first* one that
    is denied + the seconds until its oldest hit ages out of the window.
    On success, writes one entry per category and returns (None, 0,
    remaining-per-category dict).
    """
    now = time.time()
    cutoff = now - _SCAN_RATE_WINDOW
    coll = db.scan_rate_limits

    # Prune expired entries for this user (cheap — indexed).
    try:
        await coll.delete_many({"user_id": user_id, "ts": {"$lt": cutoff}})
    except Exception as e:
        logger.debug("scan_rate prune failed: %r", e)

    # Count hits per requested category in the current window.
    counts: dict[str, int] = {}
    oldest: dict[str, float] = {}
    for cat in categories:
        cur = coll.find(
            {"user_id": user_id, "category": cat, "ts": {"$gte": cutoff}},
            {"_id": 0, "ts": 1},
        ).sort("ts", 1)
        ts_list = [d["ts"] async for d in cur]
        counts[cat] = len(ts_list)
        if ts_list:
            oldest[cat] = ts_list[0]

    # First over-cap category wins the denial.
    for cat in categories:
        if counts.get(cat, 0) >= _SCAN_RATE_CAP:
            o = oldest.get(cat, now)
            retry = max(1, int((o + _SCAN_RATE_WINDOW) - now))
            remaining = {c: max(0, _SCAN_RATE_CAP - counts.get(c, 0))
                         for c in categories}
            return cat, retry, remaining

    # Allowed — log one entry per category atomically.
    try:
        await coll.insert_many([
            {"user_id": user_id, "category": cat, "ts": now}
            for cat in categories
        ])
    except Exception as e:
        # Storage failure must NEVER block a paying user's scan.
        logger.warning("scan_rate insert failed: %r", e)
    remaining = {
        c: max(0, _SCAN_RATE_CAP - (counts.get(c, 0) + 1)) for c in categories
    }
    return None, 0, remaining


# ──────────────────────────────────────────────────────────────────────
# Fix-button endpoint — creates a cto_task that fixes one finding.
# ──────────────────────────────────────────────────────────────────────
@router.post("/fix")
async def request_fix(
    body: dict, authorization: Optional[str] = Header(None),
) -> dict:
    # Iter 212m-190 — task-quota model: tier gate (health-scan fixes
    # need Pro+), 1 task per successful fix. No token pricing.
    from services.scan_fix_quota import assert_can_fix, record_scan_fixes
    user = await current_dev(authorization)
    user_id = user["user_id"]
    project_id  = (body or {}).get("project_id")
    finding_id  = (body or {}).get("finding_id") or ""
    title       = (body or {}).get("title") or "security_issue"
    file_path   = (body or {}).get("file") or ""
    line        = int((body or {}).get("line") or 0)
    message     = (body or {}).get("message") or ""
    fix_hint    = (body or {}).get("fix_hint") or ""
    if not project_id or not finding_id:
        raise HTTPException(400, "project_id and finding_id required")
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")
    is_unlimited_user = bool(
        user.get("is_admin")
        or user.get("is_unlimited")
        or (user.get("tier") == "founder")
    )
    # Gate BEFORE any work: tool access + 1 task remaining. Raises
    # 403 fix_not_available_on_tier / 402 insufficient_tasks.
    await assert_can_fix(user, "health-scan", count=1)
    tokens_cost = 0
    me = await db.dev_users.find_one(
        {"user_id": user_id}, {"_id": 0, "tokens_remaining": 1},
    )
    if not me:
        raise HTTPException(404, "User not found")
    new_balance = int(me.get("tokens_remaining") or 0)

    # Iter 212m-114 — REAL fix path. Previously this endpoint just
    # enqueued a cto_tasks record with kind:"health_fix" and returned
    # "Fix queued" (effectively a dummy — no background worker ever
    # consumed it). Now we run the same apply_finding_fix() pipeline
    # the Security Scan /fix uses: fetch file via PAT → LLM patch →
    # re-validate → commit. Tokens are REFUNDED on any failure.
    finding_payload = {
        "rule_id":   finding_id,
        "file":      file_path,
        "line":      line,
        "severity":  "medium",
        "title":     title,
        "message":   message,
        "snippet":   fix_hint,
    }
    from services.finding_fix_applier import apply_finding_fix
    from services import ora_fix_learning as _ofl
    import time as _t
    _t_start = _t.time()
    try:
        res = await apply_finding_fix(
            db=db, user=user, project_id=project_id, finding=finding_payload,
        )
    except Exception as e:
        logger.exception("health apply_finding_fix raised")
        res = {"ok": False, "error": f"unhandled: {e}"}
    _dur_ms = int((_t.time() - _t_start) * 1000)

    # Iter 212m-129 — Learning hook (single-finding codebase-health fix).
    try:
        await _ofl.record_fix_outcome(
            db, user_id=user_id, project_id=project_id,
            finding={**finding_payload, "category": "codebase_health",
                     "scanner": "codebase_health"},
            result=res, attempts=1, duration_ms=_dur_ms,
            tokens_charged=(tokens_cost if res.get("ok") else 0),
            scanner="codebase_health",
        )
    except Exception as _e:
        logger.debug("learning hook (health) soft-failed: %r", _e)

    if not res.get("ok"):
        # Refund tokens if deduction happened (founders deducted=0).
        if not is_unlimited_user and tokens_cost:
            try:
                await db.dev_users.update_one(
                    {"user_id": user_id},
                    {"$inc": {"tokens_remaining": tokens_cost}},
                )
                new_balance += tokens_cost
            except Exception as e:
                logger.warning("health refund failed: %r", e)
        err_code = res.get("error") or "unknown_error"
        if err_code == "patch_did_not_resolve_finding":
            raise HTTPException(422, {
                "error":          err_code,
                "message":        "AI patch did not resolve the finding — no commit pushed, tokens refunded.",
                "tokens_refunded": True,
            })
        if err_code in ("github_credentials_missing", "github_unauthorized"):
            raise HTTPException(401, {
                "error":          err_code,
                "message":        "Connect your GitHub PAT / OAuth before applying fixes.",
                "tokens_refunded": True,
            })
        # Iter 212m-114 (iter_26 follow-up) — match /security-scan/fix:
        # ownership-mismatch and missing-file should be 404, not 500.
        if err_code in ("project_not_found_or_not_yours",
                        "file_not_found", "file_empty_or_missing"):
            raise HTTPException(404, {
                "error":          err_code,
                "tokens_refunded": True,
            })
        raise HTTPException(500, {
            "error":          err_code,
            "tokens_refunded": True,
        })

    # Also persist a row to cto_tasks so the existing audit-log UI
    # surfaces this fix in the activity feed.
    # Iter 212m-190 — deduct exactly 1 task for the successful fix.
    if not is_unlimited_user:
        try:
            await record_scan_fixes(user_id, "health-scan", 1)
        except Exception as _e:
            logger.warning("task record failed (health fix): %r", _e)
    # Iter 212m-193 — persist fixed state so rescans don't resurrect it.
    from services.fixed_findings import record_fixed as _record_fixed
    await _record_fixed(
        db, user_id=user_id, project_id=project_id,
        finding=finding_payload,
        commit_sha=res.get("commit_sha") or "",
        html_url=res.get("html_url") or "",
        tool="health-scan",
    )
    import uuid as _uuid, time as _time
    task_id = f"task_{_uuid.uuid4().hex[:10]}"
    await db.cto_tasks.insert_one({
        "task_id":         task_id,
        "user_id":         user_id,
        "project_id":      project_id,
        "kind":            "health_fix",
        "status":          "completed",
        "finding_id":      finding_id,
        "finding_title":   title,
        "finding_file":    file_path,
        "finding_line":    line,
        "commit_sha":      res["full_sha"],
        "html_url":        res["html_url"],
        "created_at":      _time.time(),
        "completed_at":    _time.time(),
        "tokens_charged":  tokens_cost,
    })
    return {
        "ok":              True,
        "task_id":         task_id,
        "commit_sha":      res["commit_sha"],
        "full_sha":        res["full_sha"],
        "html_url":        res["html_url"],
        "tokens_charged":  tokens_cost,
        "new_balance":     new_balance,
        "message":         res["message"],
    }



# ══════════════════════════════════════════════════════════════════════
# Iter 212m-230 — Scanner Feedback Dashboard (Phase 7)
# ══════════════════════════════════════════════════════════════════════
# The fix_triage layer POSTs every false-positive it detects into the
# `scanner_feedback` Mongo collection.  This endpoint aggregates those
# rows into a rule-tuning dashboard: which rules generate the most FPs,
# on which paths, and how the rate trends over time.
#
# Founder story: "your scanners learn from every scan" — the platform's
# self-improving loop is now visible.
# ══════════════════════════════════════════════════════════════════════

@router.get("/scanner-feedback")
async def scanner_feedback(
    days: int = 30,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Aggregate false-positive feedback so a human can identify rules
    that need tuning.  Founder-only.

    Returns:
        {
            "window_days": int,
            "total_fps":   int,
            "by_rule":     [{"rule_id", "count", "example_files"}],
            "by_file":     [{"file", "count", "top_rule"}],
            "trend_daily": [{"date", "count"}],
            "recent":      [{finding + meta}, ...],
        }
    """
    await require_admin(authorization)
    db = get_db()
    if db is None:
        raise HTTPException(503, "DB not connected")

    days = max(1, min(int(days or 30), 180))
    cutoff = time.time() - days * 86400

    # ── Top rules by FP count -------------------------------------
    by_rule_cur = db.scanner_feedback.aggregate([
        {"$match": {"detected_at": {"$gte": cutoff}}},
        {"$group": {
            "_id":            "$finding.rule_id",
            "count":          {"$sum": 1},
            "example_files":  {"$addToSet": "$finding.file"},
        }},
        {"$sort":  {"count": -1}},
        {"$limit": 20},
    ])
    by_rule = []
    async for row in by_rule_cur:
        by_rule.append({
            "rule_id":       row.get("_id") or "unknown",
            "count":         int(row.get("count") or 0),
            "example_files": (row.get("example_files") or [])[:5],
        })

    # ── Top files by FP count -------------------------------------
    by_file_cur = db.scanner_feedback.aggregate([
        {"$match": {"detected_at": {"$gte": cutoff}}},
        {"$group": {
            "_id":       "$finding.file",
            "count":     {"$sum": 1},
            "top_rule":  {"$first": "$finding.rule_id"},
        }},
        {"$sort":  {"count": -1}},
        {"$limit": 20},
    ])
    by_file = []
    async for row in by_file_cur:
        by_file.append({
            "file":     row.get("_id") or "unknown",
            "count":    int(row.get("count") or 0),
            "top_rule": row.get("top_rule") or "",
        })

    # ── Daily trend (14d only) ------------------------------------
    trend_cutoff = time.time() - 14 * 86400
    trend_cur = db.scanner_feedback.aggregate([
        {"$match": {"detected_at": {"$gte": trend_cutoff}}},
        {"$group": {
            "_id":   {"$dateToString": {
                "format": "%Y-%m-%d",
                "date":   {"$toDate": {"$multiply": ["$detected_at", 1000]}},
            }},
            "count": {"$sum": 1},
        }},
        {"$sort":  {"_id": 1}},
    ])
    trend_daily = []
    async for row in trend_cur:
        trend_daily.append({
            "date":  row.get("_id"),
            "count": int(row.get("count") or 0),
        })

    total_fps = await db.scanner_feedback.count_documents({
        "detected_at": {"$gte": cutoff},
    })

    # ── Last 20 recent FPs (for a "sample" panel) ----------------
    recent_cur = db.scanner_feedback.find(
        {"detected_at": {"$gte": cutoff}},
        {"_id": 0, "finding": 1, "detected_at": 1, "source": 1},
    ).sort("detected_at", -1).limit(20)
    recent = [row async for row in recent_cur]

    return {
        "window_days":  days,
        "total_fps":    total_fps,
        "by_rule":      by_rule,
        "by_file":      by_file,
        "trend_daily":  trend_daily,
        "recent":       recent,
        "generated_at": time.time(),
    }
