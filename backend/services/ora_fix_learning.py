"""
services/ora_fix_learning.py — Iter 212m-129

Phase-1 learning foundation for the scan + fix pipelines.

PURPOSE
  The chat-side learning loop (`ora_learning.py`) has been silently
  capturing low-confidence AUREM replies into `ora_learning_logs`
  for months.  The scan + fix pipelines, by contrast, have been
  generating thousands of useful data-points (which rules trigger,
  which fixes succeed, which patches the validator rejected, which
  errors retry-vs-terminate) and dropping them on the floor.

  This module is the FOUNDATION for fixing that.  Phase-1 scope is
  intentionally tight: log everything to two new Mongo collections
  so the data is there for a later vector-DB / recall layer
  (Phase-2 backlog).  NO embeddings, NO LLM round-trips, NO recall
  in the prompt yet.

COLLECTIONS

  ora_fix_learning   — one row per fix attempt.
    {
      learning_id   : "fl_..." (uuid)
      user_id, project_id
      rule_id, file, severity, line, title
      category       : "vanguard"|"security"|"code_quality"|...
      scanner        : "vanguard"|"bug_hunt"|"trufflehog"|...
      outcome        : "success"|"failure"
      error_code     : str|None   (only when outcome=="failure")
      retryable      : bool        (False for _TERMINAL_ERROR_CODES)
      attempts       : int         (how many retries before terminal)
      commit_sha, html_url, pr_url, branch : real GitHub coords (success)
      verified       : bool        (only when outcome=="success")
      tokens_charged : int
      duration_ms    : int
      created_at     : float       (epoch seconds)
    }
    Indexed by (user_id, rule_id, created_at) and (rule_id,
    outcome, created_at) so later analytics ("show me rules that
    fail more than they succeed") run on indexed scans.

  ora_scan_learning  — one row per scan run.
    {
      scan_id       : "sl_..." (uuid)
      user_id, project_id
      scanner       : "vanguard"|"codebase_health"|"bug_hunt"
      categories    : ["security", "performance", ...]
      files_scanned : int
      duration_ms   : int
      counts        : {"critical": 2, "high": 5, "medium": 11, ...}
      rule_counts   : {"eval_usage": 3, "sql_injection": 1, ...}
      score         : 0-100  (codebase_health only)
      created_at    : float
    }

PUBLIC API
  await record_fix_outcome(db, *, user_id, project_id, finding,
                           result, attempts=1, duration_ms=None,
                           tokens_charged=0, scanner=None)
  await record_scan_run(db, *, user_id, project_id, scanner,
                        categories, files_scanned, counts,
                        rule_counts, duration_ms, score=None)
  await get_rule_stats(db, *, user_id=None, rule_id=None,
                       since=None, limit=20)
      → analytics helper for the admin / founder dashboards.

DESIGN PRINCIPLES
  • Never raise.  Logging failures must never break a real fix or
    scan — that's the EXACT opposite of "trust" we promised the user.
  • Never block.  All writes are fire-and-forget from the caller's
    perspective (the function awaits Mongo but the caller can do it
    in a `try/except` of its own — most callers already do).
  • No PII.  We do NOT capture before/after file contents in Phase-1
    (privacy-by-default).  Phase-2 (recall layer) will add an
    opt-in `snippet_before`/`snippet_after` capture behind a per-
    project setting.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger("aurem-dev.ora_fix_learning")


# Match the terminal-error set used in routers/fix_pipeline.py so the
# retryable flag stays in sync.  (Imported lazily to avoid circular.)
_TERMINAL_ERROR_CODES: frozenset[str] = frozenset({
    "github_credentials_missing",
    "github_unauthorized",
    "insufficient_tokens",
    "insufficient_tokens_midbatch",
    "file_too_large",
    "project_not_found_or_not_yours",
    "missing_required_args",
})


def _finding_category(finding: dict) -> tuple[str, str]:
    """Derive (category, scanner) from a finding dict.  Both default
    to "unknown" so the rows still aggregate cleanly.

    Vanguard scanner emits findings WITHOUT a `category` field (the
    rule_id IS the category for them — sql_injection, secret_leak,
    etc.).  So we also look at rule_id when the explicit category
    fields are missing.
    """
    _VANGUARD_RULES = {
        "secret_leak", "sql_injection", "nosql_injection",
        "ssti", "lpdos", "redos", "chain", "eval_usage",
        "command_injection", "xxe", "path_traversal",
        "weak_crypto", "open_redirect", "deserialization",
    }
    cat = (
        finding.get("category")
        or finding.get("scanner")
        or finding.get("source")
        or ""
    ).lower().strip()
    scanner = (
        finding.get("scanner") or finding.get("source") or cat or ""
    ).lower().strip()
    rule_id = (finding.get("rule_id") or finding.get("rule") or "").lower()
    # Map Vanguard vuln classes to a single bucket so analytics
    # ("how often does sql_injection succeed?") don't fragment across
    # near-duplicate labels.
    if cat in _VANGUARD_RULES or rule_id in _VANGUARD_RULES:
        cat = "vanguard"
        if not scanner or scanner in _VANGUARD_RULES:
            scanner = "vanguard"
    return (cat or "unknown", scanner or "unknown")


async def record_fix_outcome(
    db,
    *,
    user_id: str,
    project_id: str,
    finding: dict,
    result: dict,
    attempts: int = 1,
    duration_ms: Optional[int] = None,
    tokens_charged: int = 0,
    scanner: Optional[str] = None,
) -> None:
    """Persist one fix attempt row.  Best-effort: Mongo failures are
    logged at WARNING but NEVER propagate."""
    if db is None:
        return
    try:
        ok = bool(result.get("ok"))
        err = (result.get("error") or "") if not ok else None
        retryable = (err not in _TERMINAL_ERROR_CODES) if err else False
        category, scanner_detected = _finding_category(finding)
        row: dict[str, Any] = {
            "learning_id":   f"fl_{uuid.uuid4().hex[:12]}",
            "user_id":       user_id,
            "project_id":    project_id,
            "rule_id":       (finding.get("rule_id")
                              or finding.get("rule")
                              or "unknown"),
            "file":          finding.get("file") or finding.get("path") or "",
            "line":          finding.get("line"),
            "severity":      (finding.get("severity") or "").lower() or None,
            "title":         finding.get("title") or finding.get("message") or "",
            "category":      category,
            "scanner":       (scanner or scanner_detected),
            "outcome":       "success" if ok else "failure",
            "error_code":    err,
            "retryable":     retryable,
            "attempts":      int(attempts or 1),
            "commit_sha":    result.get("full_sha") or result.get("commit_sha"),
            "html_url":      result.get("html_url"),
            "pr_url":        result.get("pr_url"),
            "branch":        result.get("branch"),
            "verified":      bool(result.get("verified")) if ok else None,
            "tokens_charged": int(tokens_charged or 0),
            "duration_ms":   int(duration_ms) if duration_ms is not None else None,
            "created_at":    time.time(),
        }
        await db.ora_fix_learning.insert_one(row)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("record_fix_outcome dropped sample: %r", e)


async def record_scan_run(
    db,
    *,
    user_id: str,
    project_id: Optional[str],
    scanner: str,
    categories: list[str],
    files_scanned: int,
    counts: dict[str, int],
    rule_counts: dict[str, int],
    duration_ms: Optional[int] = None,
    score: Optional[int] = None,
) -> None:
    """Persist one scan-run row."""
    if db is None:
        return
    try:
        row = {
            "scan_id":       f"sl_{uuid.uuid4().hex[:12]}",
            "user_id":       user_id,
            "project_id":    project_id,
            "scanner":       (scanner or "unknown").lower(),
            "categories":    list(categories or []),
            "files_scanned": int(files_scanned or 0),
            "counts":        {k: int(v or 0) for k, v in (counts or {}).items()},
            "rule_counts":   {k: int(v or 0) for k, v in (rule_counts or {}).items()},
            "duration_ms":   int(duration_ms) if duration_ms is not None else None,
            "score":         int(score) if score is not None else None,
            "created_at":    time.time(),
        }
        await db.ora_scan_learning.insert_one(row)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("record_scan_run dropped sample: %r", e)


async def get_rule_stats(
    db,
    *,
    user_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    since: Optional[float] = None,
    limit: int = 20,
) -> list[dict]:
    """Aggregate fix outcomes by rule_id.  Returns top-N rules by
    attempt count with their success/failure breakdown — what every
    founder dashboard's "most-fixed rules" widget needs.

    Filters:
      • user_id : restrict to a single user (default = all).
      • rule_id : restrict to one rule.
      • since   : epoch seconds floor (default = no floor).
    """
    if db is None:
        return []
    try:
        match: dict[str, Any] = {}
        if user_id:
            match["user_id"] = user_id
        if rule_id:
            match["rule_id"] = rule_id
        if since:
            match["created_at"] = {"$gte": float(since)}
        pipeline: list[dict] = []
        if match:
            pipeline.append({"$match": match})
        pipeline += [
            {"$group": {
                "_id": "$rule_id",
                "total":    {"$sum": 1},
                "success":  {"$sum": {"$cond": [{"$eq": ["$outcome", "success"]}, 1, 0]}},
                "failure":  {"$sum": {"$cond": [{"$eq": ["$outcome", "failure"]}, 1, 0]}},
                "attempts": {"$sum": "$attempts"},
                "last_at":  {"$max": "$created_at"},
            }},
            {"$sort":  {"total": -1, "last_at": -1}},
            {"$limit": int(limit)},
        ]
        cur = db.ora_fix_learning.aggregate(pipeline)
        out = []
        async for doc in cur:
            t = doc.get("total") or 0
            s = doc.get("success") or 0
            out.append({
                "rule_id":   doc["_id"],
                "total":     t,
                "success":   s,
                "failure":   doc.get("failure") or 0,
                "attempts":  doc.get("attempts") or 0,
                "success_rate": round(s / t, 3) if t else None,
                "last_at":   doc.get("last_at"),
            })
        return out
    except Exception as e:                                    # noqa: BLE001
        logger.warning("get_rule_stats failed: %r", e)
        return []


async def ensure_indexes(db) -> None:
    """Idempotent index creation — called from main.py lifespan."""
    if db is None:
        return
    try:
        await db.ora_fix_learning.create_index(
            [("user_id", 1), ("rule_id", 1), ("created_at", -1)],
            name="ix_ofl_user_rule_ts",
        )
        await db.ora_fix_learning.create_index(
            [("rule_id", 1), ("outcome", 1), ("created_at", -1)],
            name="ix_ofl_rule_outcome_ts",
        )
        await db.ora_fix_learning.create_index(
            [("project_id", 1), ("created_at", -1)],
            name="ix_ofl_project_ts",
        )
        await db.ora_scan_learning.create_index(
            [("user_id", 1), ("scanner", 1), ("created_at", -1)],
            name="ix_osl_user_scanner_ts",
        )
        await db.ora_scan_learning.create_index(
            [("project_id", 1), ("created_at", -1)],
            name="ix_osl_project_ts",
        )
        await db.ora_fabrication_incidents.create_index(
            [("source", 1), ("project_id", 1), ("route", 1), ("created_at", -1)],
            name="ix_fab_source_project_route_ts",
        )
        await db.ora_fabrication_incidents.create_index(
            [("signature", 1), ("created_at", -1)],
            name="ix_fab_signature_ts",
        )
        await db.ora_regression_patterns.create_index(
            [("pattern_id", 1)], unique=True, name="ix_regpat_id",
        )
    except Exception as e:                                    # noqa: BLE001
        logger.warning("ora_fix_learning ensure_indexes failed: %r", e)


# ─── Iter 212m-137 — Phase-2 recall layer ────────────────────────────
#
# Phase 1 logged every fix attempt to `ora_fix_learning` and stopped
# there.  Phase 2 closes the loop: before the LLM rewrites a file for
# a finding, we query past SUCCESSFUL fixes for the same rule_id (with
# a file-similarity boost) and inject the metadata into the prompt as
# a "PAST SUCCESSFUL FIXES" block.  This is the keyword-based variant
# of mem0/pgvector retrieval — same architectural goal (give the LLM
# precedent so it doesn't reinvent the patch shape every time), simpler
# implementation, zero new infra.
#
# Why keyword-based instead of vector-based:
#   • The dominant similarity signal for a fix is rule_id (e.g.
#     `eval_usage`, `sql_string_format`) — already exact-match.
#   • Secondary signal is file extension (.py vs .js patches diverge).
#   • Tertiary is owner-user (a founder's fix style probably matches
#     their other fixes more than a random user's).
#   All three are exact-match keys → Mongo aggregation is enough.
#
# When/if the dataset grows beyond ~50k fix rows we can revisit with
# a real vector store; the recall interface stays the same.


def _file_token_for_recall(path: str) -> str:
    """Reduces a file path to a coarse similarity token.

    Returns the file extension (e.g. `.py`, `.tsx`, `.jsx`).  Empty
    string for paths without an extension so we don't match dotfiles
    against each other accidentally.
    """
    if not path:
        return ""
    base = path.rsplit("/", 1)[-1]
    if "." not in base:
        return ""
    ext = base.rsplit(".", 1)[-1].lower()
    return f".{ext}" if ext else ""


async def recall_similar_fixes(
    db,
    *,
    rule_id: str,
    file_path: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = 3,
) -> list[dict]:
    """Return the top-N most-recent SUCCESSFUL past fixes for the given
    rule_id, ranked by relevance to the current target file + caller.

    Relevance ordering:
      1. Same user_id AND same file extension  → highest
      2. Same user_id only                     → second
      3. Same file extension only              → third
      4. Just same rule_id (global precedent)  → fallback

    Returns a list of dicts shaped like:
      {
        "rule_id":      str,
        "file":         str,
        "severity":     str,
        "commit_sha":   str,
        "html_url":     str,
        "scanner":      str,
        "title":        str,
        "created_at":   float,
        "match_class":  "user+ext"|"user"|"ext"|"global",
      }

    Best-effort: never raises.  Returns `[]` when db is None, rule_id
    is empty, or any Mongo error trips.
    """
    if db is None or not rule_id:
        return []

    ext = _file_token_for_recall(file_path or "")
    base_filter = {"rule_id": rule_id, "outcome": "success"}

    async def _q(filt: dict, k: int) -> list[dict]:
        try:
            cur = (
                db.ora_fix_learning
                .find(filt, {"_id": 0,
                             "rule_id": 1, "file": 1, "severity": 1,
                             "commit_sha": 1, "html_url": 1, "title": 1,
                             "scanner": 1, "created_at": 1})
                .sort("created_at", -1).limit(k)
            )
            return [doc async for doc in cur]
        except Exception as e:                                # noqa: BLE001
            logger.warning("recall_similar_fixes query failed: %r", e)
            return []

    seen: set[str] = set()
    out: list[dict] = []

    async def _accumulate(filt: dict, match_class: str, k: int) -> None:
        if len(out) >= limit:
            return
        for row in await _q(filt, k):
            key = (row.get("commit_sha")
                   or f"{row.get('file', '')}::{row.get('created_at', 0)}")
            if key in seen:
                continue
            seen.add(key)
            row["match_class"] = match_class
            out.append(row)
            if len(out) >= limit:
                return

    # Tier 1 — caller + same file ext.
    if user_id and ext:
        await _accumulate(
            {**base_filter, "user_id": user_id,
             "file": {"$regex": f"\\{ext}$", "$options": "i"}},
            "user+ext", limit * 2,
        )
    # Tier 2 — caller only.
    if user_id and len(out) < limit:
        await _accumulate(
            {**base_filter, "user_id": user_id},
            "user", limit * 2,
        )
    # Tier 3 — file ext only.
    if ext and len(out) < limit:
        await _accumulate(
            {**base_filter,
             "file": {"$regex": f"\\{ext}$", "$options": "i"}},
            "ext", limit * 2,
        )
    # Tier 4 — global precedent.
    if len(out) < limit:
        await _accumulate(base_filter, "global", limit * 2)

    return out[:limit]


def format_recall_block(recalled: list[dict]) -> str:
    """Render the list of past-fix records as a tight prompt block.

    Returns an empty string when `recalled` is empty so the caller can
    just concatenate without an `if` guard.
    """
    if not recalled:
        return ""
    lines = ["--- PAST SUCCESSFUL FIXES FOR THIS RULE (precedent) ---"]
    for i, r in enumerate(recalled, 1):
        ts  = r.get("created_at") or 0
        sev = r.get("severity") or "?"
        sha = (r.get("commit_sha") or "")[:8]
        f   = r.get("file") or "?"
        url = r.get("html_url") or ""
        cls = r.get("match_class") or "global"
        url_part = f" — {url}" if url else ""
        lines.append(
            f"  {i}. [{cls}] {f}  ·  sev={sev}  ·  commit={sha}{url_part}"
        )
    lines.append("--- END PRECEDENT ---")
    lines.append(
        "Use the precedent above as STYLE GUIDANCE only — fix the "
        "current finding in the same idiomatic shape that previously "
        "worked, but do NOT copy code from those commits verbatim. The "
        "current file may be structurally different."
    )
    return "\n".join(lines) + "\n\n"


# ─── Fabrication-learning loop (chat CitationGuard / ORA grounding) ──
#
# Scope, approved by founder:
#   • per-project + per-route ONLY — no cross-project basename
#     matching, no cross-user learning. Protects customer data
#     boundaries.
#   • Caution is injected into the next turn's prompt only after the
#     SAME (source, project_id, route) signature recurs 3+ times in
#     the trailing 30 days. Below that, we just log — no injection.
#
# This is a SEPARATE collection/pipeline from `ora_fix_learning`
# above (which learns from scan+fix outcomes, not chat fabrication).
# It never reuses `ora_council_retriever`, which is intentionally
# success-only and must stay that way.

_FAB_MAX_PATHS_STORED = 12
_FAB_PROMPT_TRUNC = 400


def _fabrication_signature(unverified_paths: list[str]) -> str:
    """Normalize the set of fabricated paths into a stable signature
    so repeats of the SAME hallucination on the SAME project+route
    count toward the same recurring pattern."""
    items = sorted(set((p or "").strip().lower() for p in (unverified_paths or []) if p))
    return "|".join(items)[:300] or "unknown"


async def record_fabrication_incident(
    db,
    *,
    source: str,               # "customer_chat" | "admin_ora_chat"
    project_id: Optional[str],
    route: str,
    user_prompt: str,
    unverified_paths: list[str],
    corrected: bool = False,
    user_id: Optional[str] = None,
) -> None:
    """Persist one fabrication incident. Best-effort: never raises,
    never blocks the chat response that called it."""
    if db is None or not unverified_paths:
        return
    try:
        paths = list(unverified_paths)[:_FAB_MAX_PATHS_STORED]
        row: dict[str, Any] = {
            "incident_id":  f"fab_{uuid.uuid4().hex[:12]}",
            "source":       source,
            "project_id":   (project_id or "home"),
            "route":        route or "unknown",
            "user_id":      user_id,
            "user_prompt":  (user_prompt or "")[:_FAB_PROMPT_TRUNC],
            "unverified_paths": paths,
            "signature":    _fabrication_signature(paths),
            "corrected":    bool(corrected),
            "created_at":   time.time(),
        }
        await db.ora_fabrication_incidents.insert_one(row)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("record_fabrication_incident dropped sample: %r", e)


async def recall_fabrication_caution(
    db,
    *,
    source: str,
    project_id: Optional[str],
    route: str,
    since_days: int = 30,
    min_count: int = 3,
) -> str:
    """Return a compact system-prompt caution string when this exact
    (source, project_id, route) bucket has hit >= `min_count`
    fabrication incidents in the trailing `since_days` days.

    Returns "" when below threshold, db is None, or on any error —
    fail-open so a learning-loop hiccup can never break a chat turn.
    """
    if db is None:
        return ""
    try:
        since = time.time() - (since_days * 86400)
        pid = (project_id or "home")
        cur = (
            db.ora_fabrication_incidents
            .find(
                {"source": source, "project_id": pid, "route": route or "unknown",
                 "created_at": {"$gte": since}},
                {"_id": 0, "unverified_paths": 1, "created_at": 1},
            )
            .sort("created_at", -1)
            .limit(50)
        )
        rows = [doc async for doc in cur]
        if len(rows) < min_count:
            return ""
        sample_paths: list[str] = []
        for row in rows[:5]:
            for p in (row.get("unverified_paths") or []):
                if p not in sample_paths:
                    sample_paths.append(p)
        sample = ", ".join(sample_paths[:5]) or "file paths"
        return (
            "── LEARNED CAUTION (fabrication history) ──\n"
            f"In the last {since_days} days, {len(rows)} prior replies on "
            "this project cited file paths that turned out fabricated "
            f"(e.g. {sample}). Before citing ANY file path this turn, you "
            "MUST call a read/search tool to verify it actually exists — "
            "do not answer from memory alone.\n"
        )
    except Exception as e:                                    # noqa: BLE001
        logger.warning("recall_fabrication_caution failed: %r", e)
        return ""


async def get_recurring_fabrication_patterns(
    db,
    *,
    since_days: int = 30,
    min_count: int = 1,
    limit: int = 50,
) -> list[dict]:
    """Admin-facing aggregation: recurring (source, project_id, route,
    signature) groups within the trailing window, most-recent first.
    Used by the /admin/qa/fabrication-patterns endpoint."""
    if db is None:
        return []
    try:
        since = time.time() - (since_days * 86400)
        pipeline: list[dict] = [
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {
                "_id": {"source": "$source", "project_id": "$project_id",
                          "route": "$route", "signature": "$signature"},
                "count":       {"$sum": 1},
                "corrected":   {"$sum": {"$cond": ["$corrected", 1, 0]}},
                "last_at":     {"$max": "$created_at"},
                "sample_paths": {"$last": "$unverified_paths"},
                "sample_prompt": {"$last": "$user_prompt"},
            }},
            {"$match": {"count": {"$gte": int(min_count)}}},
            {"$sort":  {"count": -1, "last_at": -1}},
            {"$limit": int(limit)},
        ]
        cur = db.ora_fabrication_incidents.aggregate(pipeline)
        out = []
        async for doc in cur:
            key = doc["_id"]
            out.append({
                "source":         key.get("source"),
                "project_id":     key.get("project_id"),
                "route":          key.get("route"),
                "signature":      key.get("signature"),
                "count":          doc.get("count") or 0,
                "corrected":      doc.get("corrected") or 0,
                "last_at":        doc.get("last_at"),
                "sample_paths":   doc.get("sample_paths") or [],
                "sample_prompt":  doc.get("sample_prompt") or "",
                "caution_active": (doc.get("count") or 0) >= 3,
            })
        return out
    except Exception as e:                                    # noqa: BLE001
        logger.warning("get_recurring_fabrication_patterns failed: %r", e)
        return []


# ─── Regression pattern registry (dev-bug "never recur" system) ─────
#
# 2026-08-19 — Founder asked whether AUREM's own "prevent fixed bugs
# from recurring" mechanism actually works. It turned out to be a
# manually-curated markdown file (/app/memory/RECURRING_ISSUES.md)
# plus, for one batch of patterns, a "regression lock" test that was
# PROVEN FAKE (grep for literal strings, not real behavior — swapping
# the two branches of a fixed bug still passed 3/3).
#
# This registry replaces the markdown-only approach with the SAME
# structured-collection + admin-visibility pattern already built for
# the fabrication-learning loop above, instead of maintaining two
# separate ad-hoc mechanisms. It does NOT try to be a fully automatic
# gate — it's a queryable, honest ledger: each known pattern records
# whether it has a REAL test, and that test's last live result.
_REGRESSION_COLL = "ora_regression_patterns"


async def seed_regression_pattern(
    db,
    *,
    pattern_id: str,
    title: str,
    symptom: str,
    root_cause: str,
    fix_locations: list[str],
    status: str,                  # "fixed" | "deferred" | "policy"
    test_ref: Optional[str] = None,  # "tests/foo.py::test_bar" or None
    doc_ref: str = "/app/memory/RECURRING_ISSUES.md",
) -> None:
    """Idempotent upsert — safe to re-run the seed script any time."""
    if db is None:
        return
    await db[_REGRESSION_COLL].update_one(
        {"pattern_id": pattern_id},
        {"$set": {
            "pattern_id": pattern_id, "title": title, "symptom": symptom,
            "root_cause": root_cause, "fix_locations": fix_locations,
            "status": status, "test_ref": test_ref, "doc_ref": doc_ref,
        }, "$setOnInsert": {"created_at": time.time()}},
        upsert=True,
    )


async def record_pattern_verification(
    db, *, pattern_id: str, passed: bool, detail: str = "",
) -> None:
    """Called after actually RUNNING a pattern's `test_ref` (not just
    checking it exists). Updates last_verified_at/passed on the
    pattern doc so the admin view never claims a stale green."""
    if db is None:
        return
    try:
        await db[_REGRESSION_COLL].update_one(
            {"pattern_id": pattern_id},
            {"$set": {
                "last_verified_at": time.time(),
                "last_verified_passed": bool(passed),
                "last_verified_detail": (detail or "")[:500],
            }},
        )
    except Exception as e:                                    # noqa: BLE001
        logger.warning("record_pattern_verification failed: %r", e)


async def list_regression_patterns(db) -> list[dict]:
    if db is None:
        return []
    try:
        cur = db[_REGRESSION_COLL].find({}, {"_id": 0}).sort("pattern_id", 1)
        return [doc async for doc in cur]
    except Exception as e:                                    # noqa: BLE001
        logger.warning("list_regression_patterns failed: %r", e)
        return []
