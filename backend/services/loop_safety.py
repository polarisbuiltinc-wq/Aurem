"""
services/loop_safety.py — Iter 212m-115

Five production-safety primitives shared by Loop Mode + Finding Fix:

  1. validate_github_token()      — PAT pre-flight (fail-fast at start)
  2. acquire_loop_lock()          — concurrent-loop refusal per project
  3. release_loop_lock()          — clean up when loop terminates
  4. record_loop_failure()        — circuit-breaker counter
  5. is_loop_circuit_open()       — refuse-start when 3+ fails in 15 min
  6. github_request_with_retry()  — rate-limit-aware HTTP wrapper

Per-collection compound indexes ensure cross-project isolation:
  loop_locks         { project_id, user_id, loop_id, acquired_at }
                     unique index on (project_id, user_id) — at most ONE
                     active loop per project per user.
  loop_failures      { project_id, user_id, occurred_at, reason }
                     window-scan index.

Branch-per-fix (#5 of the founder's safety asks) lives in
services/github_api_writer.commit_files() via the new `target_branch`
parameter + the new commit_to_aurem_branch() helper.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

logger = logging.getLogger("aurem-dev.loop_safety")


# ─── 1 & 6. PAT pre-flight + rate-limit-aware GitHub HTTP ─────────────
async def validate_github_token(
    owner: str, repo: str, token: str,
) -> tuple[bool, Optional[str]]:
    """Hit GET /repos/{owner}/{repo} with the App installation token. Returns
    (ok, error_code). Fails-fast in <2 s instead of letting the loop
    crash at SHIP after Plan+Execute+Verify+Scan have spent tokens."""
    if not (owner and repo and token):
        return False, "missing_args"
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github+json",
        "User-Agent":    "aurem-loop-preflight",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as cx:
            r = await cx.get(f"https://api.github.com/repos/{owner}/{repo}",
                             headers=headers)
        if r.status_code == 200:
            return True, None
        if r.status_code == 401:
            return False, "github_rejected_401"
        if r.status_code == 403:
            # Could be rate-limited OR scope-missing.
            remaining = r.headers.get("x-ratelimit-remaining")
            if remaining == "0":
                return False, "github_rate_limited"
            return False, "github_rejected_403"
        if r.status_code == 404:
            return False, "repo_not_found_or_no_access"
        return False, f"github_status_{r.status_code}"
    except (httpx.TimeoutException, httpx.NetworkError) as e:
        logger.warning("GitHub preflight network err: %r", e)
        return False, "network_error"
    except Exception as e:                                # noqa: BLE001
        logger.exception("GitHub preflight unexpected err")
        return False, f"unexpected: {e}"


async def github_request_with_retry(
    method: str, url: str, *, headers: dict, json: Optional[dict] = None,
    params: Optional[dict] = None, max_retries: int = 2, timeout: float = 15.0,
) -> httpx.Response:
    """Wraps an httpx request with rate-limit-aware retry. If GitHub
    responds with 403 + x-ratelimit-remaining=0, we sleep until the
    `x-ratelimit-reset` epoch (capped at 30 s) and try once more. On
    a 5xx we exponential-backoff 2 s then 4 s."""
    # Iter 360 · Guard 17 — GitHub breaker: fast-fail when OPEN, record
    # 5xx/network outcomes (rate limits are NOT dependency failures).
    from services.retry_guard import get_breaker, BreakerOpenError
    br = get_breaker("github")
    if not br.allow():
        raise BreakerOpenError("github", br.retry_after_s())
    last_resp: Optional[httpx.Response] = None
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as cx:
                r = await cx.request(method, url, headers=headers,
                                     json=json, params=params)
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            br.record_failure(repr(e))
            raise
        last_resp = r
        if r.status_code < 400:
            br.record_success()
            return r
        # Rate limit handling.
        if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
            if attempt >= max_retries:
                return r
            reset = int(r.headers.get("x-ratelimit-reset") or 0)
            wait = max(0, min(30, reset - int(time.time())))
            logger.warning("GH rate limited — sleeping %ds (attempt %d/%d)",
                           wait, attempt + 1, max_retries + 1)
            await asyncio.sleep(wait + 1)
            continue
        # Iter 212m-179 — SECONDARY (burst) limit: 403/429 arrives with a
        # Retry-After header while x-ratelimit-remaining is still > 0.
        # Honour the server-provided wait (capped) instead of failing.
        _ra = r.headers.get("retry-after")
        if r.status_code in (403, 429) and _ra and _ra.isdigit() \
                and attempt < max_retries:
            wait = min(60, int(_ra))
            logger.warning("GH SECONDARY limit — sleeping %ds "
                           "(attempt %d/%d)", wait, attempt + 1,
                           max_retries + 1)
            await asyncio.sleep(wait + 1)
            continue
        # 5xx — backoff retry.
        if 500 <= r.status_code < 600 and attempt < max_retries:
            backoff = 2 ** (attempt + 1)
            logger.warning("GH %d — backing off %ds (attempt %d/%d)",
                           r.status_code, backoff, attempt + 1, max_retries + 1)
            await asyncio.sleep(backoff)
            continue
        # Non-retryable.
        if r.status_code >= 500:
            br.record_failure(f"github_status_{r.status_code}")
        return r
    if last_resp is not None and last_resp.status_code >= 500:
        br.record_failure(f"github_status_{last_resp.status_code}")
    return last_resp  # type: ignore[return-value]


def _age_seconds(value, now_dt: datetime) -> float:
    """Age of a TTL timestamp field in seconds. Handles both the fixed
    BSON-Date type and legacy `time.time()` float rows written before
    the 2026-08-27 TTL fix, so in-flight/old rows don't crash this
    comparison during the rollout window. Also normalizes Mongo's
    naive-UTC datetime read-back (driver default `tz_aware=False`)
    against our tz-aware `now_dt`."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return now_dt.timestamp() - value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (now_dt - value).total_seconds()


# ─── 2 & 3. Concurrent-loop lock ─────────────────────────────────────
async def acquire_loop_lock(
    db, project_id: str, user_id: str, loop_id: str,
) -> tuple[bool, Optional[dict]]:
    """Try to take the {project_id, user_id} lock for this loop_id.
    Returns (True, None) on success. Returns (False, existing_lock)
    if another loop_id already owns the lock.

    Stale locks (>15 min old) are forcibly released — covers the case
    where a backend process crashed without releasing.
    """
    if db is None:
        return True, None
    now_dt = datetime.now(timezone.utc)
    STALE_S = 15 * 60
    # 2026-08-26 — grace period for the "ghost sweep" below. A lock is
    # written synchronously by this function, but its matching
    # loop_sessions doc is only persisted later, inside the engine's
    # (async, fire-and-forget) `_do_plan()`. A crash/restart in that
    # narrow window leaves a lock with NO session doc at all — the
    # existing ghost-sweep only handled a session that reached a
    # *terminal* state, not "never created". 2 min is comfortably
    # longer than that window ever legitimately takes.
    NO_SESSION_GRACE_S = 120
    # 2026-08-27 — TTL fix: `acquired_at` is now a real BSON Date (was
    # `time.time()` float, which the `loop_locks.acquired_at` TTL index
    # can never expire — MongoDB's TTL monitor only acts on Date/Date[]
    # fields). Query below uses a datetime cutoff to match.
    try:
        await db.loop_locks.delete_many({
            "project_id": project_id,
            "user_id":    user_id,
            "acquired_at": {"$lt": now_dt - timedelta(seconds=STALE_S)},
        })
    except Exception as e:                                # noqa: BLE001
        logger.debug("loop_lock stale sweep failed: %r", e)
    # Iter 212m-145 — also sweep locks whose `loop_id` points to a
    # loop that has ALREADY terminated in loop_sessions. This handles
    # the worker-crash / cancel-fallback scenario where the engine
    # never got a chance to call release_loop_lock — instead of making
    # the user wait 15 min for stale_s, we detect the ghost lock and
    # free the project immediately.
    try:
        existing = await db.loop_locks.find_one(
            {"project_id": project_id, "user_id": user_id},
            {"_id": 0},
        )
        if existing and existing.get("loop_id"):
            sess = await db.loop_sessions.find_one(
                {"loop_id": existing["loop_id"]},
                {"_id": 0, "state": 1},
            )
            if sess and (sess.get("state") or "") in (
                # 2026-08-27 · I1 fix — "expired" was missing here, so
                # a loop that timed out via the 60s awaiting-confirm
                # sweep wasn't recognized as terminal by THIS immediate
                # ghost-sweep (it still self-healed after STALE_S=15min,
                # but not promptly). Belt-and-suspenders alongside the
                # loop_engine.py sweep fix above.
                "aborted", "failed", "completed", "expired",
            ):
                await db.loop_locks.delete_one(
                    {"project_id": project_id, "user_id": user_id,
                     "loop_id": existing["loop_id"]},
                )
                logger.info(
                    "[loop_safety] swept ghost lock for terminated "
                    "loop %s (state=%s)",
                    existing["loop_id"], sess.get("state"),
                )
            elif (sess is None
                  and _age_seconds(existing.get("acquired_at"), now_dt)
                      > NO_SESSION_GRACE_S):
                # The engine never got far enough to persist a session
                # doc at all (crash/restart right after lock acquire) —
                # same "abandoned lock" outcome, just never caught by
                # the state check above.
                await db.loop_locks.delete_one(
                    {"project_id": project_id, "user_id": user_id,
                     "loop_id": existing["loop_id"]},
                )
                logger.info(
                    "[loop_safety] swept ghost lock for loop %s — no "
                    "loop_sessions doc ever created (acquired %.0fs ago)",
                    existing["loop_id"],
                    _age_seconds(existing.get("acquired_at"), now_dt),
                )
    except Exception as e:                                # noqa: BLE001
        logger.debug("loop_lock ghost sweep failed: %r", e)
    try:
        await db.loop_locks.insert_one({
            "project_id":  project_id,
            "user_id":     user_id,
            "loop_id":     loop_id,
            "acquired_at": now_dt,
        })
        return True, None
    except Exception:
        existing = await db.loop_locks.find_one(
            {"project_id": project_id, "user_id": user_id},
            {"_id": 0},
        )
        return False, existing


async def release_loop_lock(
    db, project_id: str, user_id: str, loop_id: str,
) -> None:
    if db is None:
        return
    try:
        await db.loop_locks.delete_one({
            "project_id": project_id,
            "user_id":    user_id,
            "loop_id":    loop_id,
        })
    except Exception as e:                                # noqa: BLE001
        logger.warning("loop_lock release failed: %r", e)


async def ensure_loop_lock_index(db) -> None:
    """Create the unique compound index. Idempotent. Called once on
    backend boot."""
    if db is None:
        return
    try:
        await db.loop_locks.create_index(
            [("project_id", 1), ("user_id", 1)],
            unique=True, name="uq_active_loop_per_project_user",
        )
    except Exception as e:                                # noqa: BLE001
        logger.warning("loop_lock index create failed: %r", e)


# ─── 4 & 5. Circuit breaker (3 fails / 15 min) ────────────────────────
FAIL_WINDOW_S = 15 * 60
FAIL_THRESHOLD = 3


async def record_loop_failure(
    db, project_id: str, user_id: str, phase: str, reason: str,
) -> None:
    if db is None:
        return
    try:
        await db.loop_failures.insert_one({
            "project_id":  project_id,
            "user_id":     user_id,
            "phase":       phase,
            "reason":      (reason or "")[:500],
            # 2026-08-27 — TTL fix: real BSON Date (was `time.time()`
            # float — the `loop_failures.occurred_at` TTL index never
            # expired those rows).
            "occurred_at": datetime.now(timezone.utc),
        })
    except Exception as e:                                # noqa: BLE001
        logger.warning("loop_failures insert failed: %r", e)


async def is_loop_circuit_open(
    db, project_id: str, user_id: str,
) -> tuple[bool, int, Optional[int]]:
    """Returns (circuit_open, recent_fail_count, retry_after_seconds).
    Circuit is OPEN (refuse new starts) when >=FAIL_THRESHOLD failures
    happened in the last FAIL_WINDOW_S seconds."""
    if db is None:
        return False, 0, None
    now_dt = datetime.now(timezone.utc)
    cutoff = now_dt - timedelta(seconds=FAIL_WINDOW_S)
    try:
        recent = await db.loop_failures.find({
            "project_id":  project_id,
            "user_id":     user_id,
            "occurred_at": {"$gte": cutoff},
        }, {"_id": 0, "occurred_at": 1}).to_list(length=FAIL_THRESHOLD + 5)
    except Exception as e:                                # noqa: BLE001
        logger.warning("loop_failures query failed: %r", e)
        return False, 0, None
    count = len(recent)
    if count >= FAIL_THRESHOLD:
        oldest = min(_age_seconds(r["occurred_at"], now_dt) for r in recent)
        retry_after = max(1, int(FAIL_WINDOW_S - oldest))
        return True, count, retry_after
    return False, count, None


# ─── Branch-per-fix utilities ────────────────────────────────────────
def aurem_branch_name(kind: str, identifier: str) -> str:
    """Deterministic branch name for fixes/loops.
    Examples:
      aurem_branch_name("fix", "secret_aws") → "aurem/fix-secret_aws-<unix>"
      aurem_branch_name("loop", "abc12345")  → "aurem/loop-abc12345-<unix>"
    """
    import re
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", identifier or "x")[:40].strip("-") or "x"
    ts = int(time.time())
    return f"aurem/{kind}-{safe}-{ts}"


async def create_or_reuse_branch(
    *, owner: str, repo: str, base_branch: str, new_branch: str, token: str,
) -> tuple[bool, Optional[str]]:
    """Create `new_branch` off the tip of `base_branch`. Returns
    (ok, error). If the branch already exists (e.g. retry), returns
    (True, None) — caller can safely push to it."""
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github+json",
        "User-Agent":    "aurem-branch-helper",
    }
    base_url = f"https://api.github.com/repos/{owner}/{repo}"
    # Resolve base SHA.
    r = await github_request_with_retry(
        "GET", f"{base_url}/git/refs/heads/{base_branch}", headers=headers,
    )
    if r.status_code != 200:
        return False, f"base_ref_status_{r.status_code}"
    base_sha = (r.json() or {}).get("object", {}).get("sha")
    if not base_sha:
        return False, "base_sha_missing"
    # Create the new ref.
    r2 = await github_request_with_retry(
        "POST", f"{base_url}/git/refs", headers=headers,
        json={"ref": f"refs/heads/{new_branch}", "sha": base_sha},
    )
    if r2.status_code in (200, 201):
        return True, None
    if r2.status_code == 422:                              # already exists
        return True, None
    return False, f"create_branch_status_{r2.status_code}"


async def open_draft_pr(
    *, owner: str, repo: str, head_branch: str, base_branch: str,
    title: str, body: str, token: str,
) -> tuple[Optional[str], Optional[str]]:
    """Open a draft PR head → base. Returns (html_url, error)."""
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github+json",
        "User-Agent":    "aurem-pr-helper",
    }
    r = await github_request_with_retry(
        "POST", f"https://api.github.com/repos/{owner}/{repo}/pulls",
        headers=headers,
        json={"title": title[:255], "body": body[:65000],
              "head":  head_branch,  "base":  base_branch,
              "draft": True},
    )
    if r.status_code in (200, 201):
        return (r.json() or {}).get("html_url"), None
    return None, f"pr_status_{r.status_code}"


# ─── Overnight T7 (Wave 2 · ship-via-PR) — additional primitives ───
# Everything below is NEW (2026-08-28), additive only. Existing
# `aurem/fix-*` branches (finding_fix_applier) and the `aurem_branch_name()`
# helper above are UNTOUCHED — ship-via-PR uses its own namespace,
# `auremcto/ship-{slug}`, so the two systems can never collide.

SHIP_BRANCH_PREFIX = "auremcto/"


def ship_branch_name(slug: str) -> str:
    """Deterministic ship-PR branch name. Always under the
    `auremcto/` namespace so delete_ship_branch()'s guard applies."""
    import re
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", slug or "x")[:40].strip("-") or "x"
    ts = int(time.time())
    return f"{SHIP_BRANCH_PREFIX}ship-{safe}-{ts}"


async def add_pr_label(
    *, owner: str, repo: str, pr_number: int, label: str, token: str,
) -> tuple[bool, Optional[str]]:
    """Attach a label to a PR/issue. Returns (ok, error). Best-effort —
    a label-attach failure must never block a ship that already
    landed a real commit + PR."""
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github+json",
        "User-Agent":    "aurem-pr-helper",
    }
    r = await github_request_with_retry(
        "POST",
        f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/labels",
        headers=headers, json={"labels": [label]},
    )
    if r.status_code in (200, 201):
        return True, None
    return False, f"label_status_{r.status_code}"


async def close_pr(
    *, owner: str, repo: str, pr_number: int, token: str,
) -> tuple[bool, Optional[str]]:
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github+json",
        "User-Agent":    "aurem-pr-helper",
    }
    r = await github_request_with_retry(
        "PATCH", f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
        headers=headers, json={"state": "closed"},
    )
    if r.status_code == 200:
        return True, None
    return False, f"close_pr_status_{r.status_code}"


# Rollback-gap fix (2026-08-28, hardened 2026-08-30 · T2/R10) — live PR
# merge-state check. The rollback endpoint needs this BEFORE deciding
# whether to revert a commit on the base branch (already merged — safe,
# history-preserving) or close+retract the still-open PR (never merged
# — nothing to revert on the base branch, closing+deleting the
# throwaway branch is correct).
#
# T2/R10 hardening: two prior gaps closed here —
#   1. The caller could not tell "confirmed unmerged" apart from
#      "lookup errored" — both collapsed to `merged: False`, which
#      pushed an errored lookup toward close+retract (wrong: it might
#      actually be merged). Now exposes `ok` so the caller can treat
#      a failed lookup as "couldn't verify", never as a confirmed
#      state either way.
#   2. `merge_commit_sha` (the REAL landed commit — required to revert
#      squash/rebase merges correctly) was fetched from GitHub but
#      discarded. Now returned.
async def get_pr_status(
    *, owner: str, repo: str, pr_number: int, token: str,
) -> dict:
    """Returns {"ok": bool, "merged": bool, "state": str,
    "merge_commit_sha": Optional[str]}.

    `ok=False` means the live lookup itself failed (network blip, non-
    200) — the caller must treat this as "couldn't verify", NOT as a
    confirmed "unmerged" result. Never raises."""
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github+json",
        "User-Agent":    "aurem-pr-helper",
    }
    try:
        r = await github_request_with_retry(
            "GET", f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
            headers=headers,
        )
        if r.status_code == 200:
            j = r.json() or {}
            return {
                "ok": True,
                "merged": bool(j.get("merged")),
                "state": j.get("state") or "unknown",
                "merge_commit_sha": j.get("merge_commit_sha"),
            }
        logger.warning("get_pr_status(%s/%s#%s) HTTP %s",
                        owner, repo, pr_number, r.status_code)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("get_pr_status(%s/%s#%s) failed: %r", owner, repo, pr_number, e)
    return {"ok": False, "merged": False, "state": "unknown", "merge_commit_sha": None}


async def delete_ship_branch(
    *, owner: str, repo: str, branch: str, token: str,
) -> tuple[bool, Optional[str]]:
    """HARD namespace guard — only `auremcto/`-prefixed branches may
    ever be deleted through this function. Any other branch name is
    rejected + logged as GW_BLOCK, no GitHub call made. This is the
    one guard standing between "close an unmerged ship PR" and
    "accidentally delete `main`" or a legacy `aurem/fix-*` branch."""
    if not (branch or "").startswith(SHIP_BRANCH_PREFIX):
        logger.error(
            "GW_BLOCK delete_ship_branch refused non-%s branch: %r "
            "(owner=%s repo=%s)", SHIP_BRANCH_PREFIX, branch, owner, repo,
        )
        return False, "GW_BLOCK_non_namespaced_branch"
    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github+json",
        "User-Agent":    "aurem-branch-helper",
    }
    r = await github_request_with_retry(
        "DELETE",
        f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/{branch}",
        headers=headers,
    )
    if r.status_code in (204, 200):
        return True, None
    if r.status_code == 422:                     # already gone
        return True, None
    return False, f"delete_branch_status_{r.status_code}"


async def close_and_retract(
    *, owner: str, repo: str, pr_number: Optional[int],
    branch: Optional[str], token: str,
) -> dict:
    """Shared close+delete helper for any unmerged ship-PR path —
    used by BOTH the new confirm_ship-via-PR flow AND
    finding_fix_applier's existing draft-PR flow (which previously
    had no revert mechanism at all). Closing an ALREADY-MERGED PR is
    a no-op here by design — that case uses the existing audited
    reverse-commit path (user_rollback → loop_rollback →
    revert_commit), never branch deletion, never force-push."""
    out = {"pr_closed": False, "branch_deleted": False, "errors": []}
    if pr_number:
        ok, err = await close_pr(owner=owner, repo=repo, pr_number=pr_number, token=token)
        out["pr_closed"] = ok
        if err:
            out["errors"].append(f"close_pr:{err}")
    if branch:
        ok, err = await delete_ship_branch(owner=owner, repo=repo, branch=branch, token=token)
        out["branch_deleted"] = ok
        if err:
            out["errors"].append(f"delete_branch:{err}")
    return out


async def dispatch_pull_request_webhook(db, *, payload: dict, action: str) -> dict:
    """Overnight T7 — label-dispatch for GitHub `pull_request` webhook
    events. Extracted as a standalone, unit-testable function (called
    from routers/github_app.py::install_webhook AFTER signature
    verification — this function itself does no auth, it's pure
    label-routing logic over an already-trusted payload).

    `aura:ship`                 → ship status on loop_outcomes,
                                   matched on ship_branch = head ref.
    `auremcto/visibility-kit-*` → its OWN collection
                                   (visibility_kit_pr_events) — kept
                                   deliberately separate from
                                   loop_outcomes so the two label
                                   families can never cross-write
                                   each other's state.
    anything else                → log only, no state write.

    Returns a dict describing what was written (for tests + logs).
    """
    pr = payload.get("pull_request") or {}
    labels = [(l.get("name") or "") for l in (pr.get("labels") or [])]
    head_ref = ((pr.get("head") or {}).get("ref")) or ""
    merged = bool(pr.get("merged"))
    pr_number = pr.get("number")
    is_ship_label = "aura:ship" in labels
    kit_labels = [l for l in labels if l.startswith("auremcto/visibility-kit-")]

    if is_ship_label and head_ref:
        new_status = (
            "merged" if (action == "closed" and merged)
            else "closed" if action == "closed"
            else "open"
        )
        await db.loop_outcomes.update_one(
            {"ship_branch": head_ref},
            {"$set": {"pr_status": new_status, "pr_status_updated_at": time.time()}},
        )
        # T2/R10 fix (2026-08-30) — self-heal the STALE pre-merge SHA.
        # `loop_sessions.context.commit.full_sha` was captured once at
        # ship time from the throwaway branch commit; it never reflects
        # what actually landed on the base branch after a squash/rebase
        # merge. GitHub's own `merge_commit_sha` (present on this
        # webhook payload for a merged PR) IS the real landed commit —
        # persist it now so a later rollback reverts the right diff
        # even without re-querying the PR live.
        if new_status == "merged":
            real_merge_sha = pr.get("merge_commit_sha")
            if real_merge_sha:
                await db.loop_sessions.update_one(
                    {"context.commit.pr_branch": head_ref},
                    {"$set": {
                        "context.commit.sha": real_merge_sha[:7],
                        "context.commit.full_sha": real_merge_sha,
                        "context.commit.merge_commit_sha": real_merge_sha,
                    }},
                )
        try:
            await db.ship_pr_events.insert_one({
                "event": f"ship_pr_{new_status}", "pr_url": pr.get("html_url"),
                "pr_number": pr_number, "head_ref": head_ref, "ts": time.time(),
            })
        except Exception:
            pass
        return {"routed": "ship", "status": new_status, "head_ref": head_ref}

    if kit_labels:
        await db.visibility_kit_pr_events.update_one(
            {"pr_number": pr_number,
             "repo_full_name": (payload.get("repository") or {}).get("full_name")},
            {"$set": {"labels": kit_labels, "action": action, "merged": merged,
                      "updated_at": time.time()}},
            upsert=True,
        )
        return {"routed": "kit", "labels": kit_labels}

    logger.info("dispatch_pull_request_webhook: no matching label (labels=%s) "
                "— log-only, no state write", labels)
    return {"routed": "none", "labels": labels}
