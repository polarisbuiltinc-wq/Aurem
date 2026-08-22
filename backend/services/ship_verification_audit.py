"""
services/ship_verification_audit.py — 2026-08-24 (Pillar 4, Production-Readiness)

Backend tool-call verification for the main Ship flow: confirms a
claimed "Ship to GitHub" commit ACTUALLY exists on GitHub via an
independent read-back call, rather than only trusting the write API's
own synchronous response.

Reuses the exact read-back pattern already proven in
`routers/fix_pipeline.py::_verify_commit_exists` (tri-state: True =
confirmed present, False = confirmed absent, None = inconclusive —
network/rate-limit noise is NEVER conflated with a real miss) and
`services/github_api_writer._get_commit_details` for the actual GET.

Deliberately fire-and-forget, AFTER `_do_ship` already marks the loop
COMPLETED — not a blocking gate on the critical ship path. `commit_
files()`'s own 2xx response with a real sha is already a legitimate
form of verification (GitHub's write API doesn't lie about what it
just wrote); this audit exists to catch the rarer case of replication
lag / a mismatched file set / a webhook-adjacent inconsistency, and
alerts the founder if it ever finds one — see PRD Pillar 4 notes for
why this was NOT wired as a blocking gate on loop_engine._do_ship
(that function is the single highest-blast-radius write path in the
product; Rule 7 mandates independent testing_agent verification for
any change to it, and an additive non-blocking audit gets the real
verification value without that risk).
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ship_verification_audit")


async def verify_shipped_commit(
    db,
    *,
    loop_id:            str,
    owner:               str,
    repo:                str,
    branch:              str,
    commit_sha:          str,
    expected_file_paths: list[str],
    token:               str,
) -> dict:
    """Independent read-back of a just-shipped commit. Returns
    {verified: True|False|None, ...} — None means inconclusive
    (network/rate-limit), never conflated with a real mismatch."""
    if not commit_sha:
        return {"verified": False, "reason": "no_commit_sha"}
    try:
        from services.github_api_writer import _get_commit_details
        data = await _get_commit_details(owner, repo, commit_sha, token)
    except Exception as e:
        logger.info("[ship-verify %s] read-back inconclusive: %r", loop_id, e)
        result = {"verified": None, "reason": "readback_error", "detail": str(e)[:200]}
        await _persist(db, loop_id, commit_sha, result)
        return result

    real_sha = data.get("sha")
    committed_files = {f.get("filename") for f in (data.get("files") or [])}
    expected = set(expected_file_paths or [])
    # Real GitHub commits always echo their own sha + at least the
    # files that were actually written. A subset check (not exact
    # equality) tolerates GitHub's own file-rename/merge normalization.
    files_match = expected.issubset(committed_files) if expected else True
    verified = bool(real_sha == commit_sha and files_match)

    result = {
        "verified":     verified,
        "real_sha":     real_sha,
        "expected_sha": commit_sha,
        "expected_files":  sorted(expected),
        "committed_files": sorted(committed_files),
    }
    if not verified:
        logger.warning("[ship-verify %s] MISMATCH: %r", loop_id, result)
        try:
            from services.founder_alerts import send_founder_alert
            await send_founder_alert(
                db,
                source_key=f"ship_verify_mismatch:{commit_sha[:12]}",
                title=f"Ship verification mismatch for loop {loop_id}",
                detail=(f"commit_files() reported success for {commit_sha[:12]} "
                        f"but the independent GitHub read-back doesn't match: "
                        f"{result}"),
                level="warning", guard="SHIP_VERIFY",
            )
        except Exception:
            pass
    await _persist(db, loop_id, commit_sha, result)
    return result


async def _persist(db, loop_id: str, commit_sha: str, result: dict) -> None:
    if db is None:
        return
    try:
        import time
        await db.ship_verification_audit.insert_one({
            "loop_id": loop_id, "commit_sha": commit_sha,
            "checked_at": time.time(), **result,
        })
    except Exception as e:
        logger.debug("[ship-verify %s] persist failed: %r", loop_id, e)
