"""
services/correction_rules.py — Iter 333 · Phase 1 + Iter 367 · Phase 2

Phase 1 (Iter 333) — Persistent correction rules:
  - NO LLM correction-detection — rules created ONLY via `/rule`.
  - Path scoping via `applies_to_paths` (fnmatch globs; empty = all).
  - Max 10 rules injected per prompt (MAX_RULES_PER_PROMPT).
  - Per-project `enforce` toggle in `correction_rule_settings`, default OFF
    (shadow mode: matches logged but not injected into executor prompt).
  - Instrumented success metric: `correction_rule_events` row + per-rule
    hit counters + `/rule report` aggregation.

Phase 2 (Iter 367 · Item C) — 14-day auto-graduation:
  Rules stay in shadow mode for `SHADOW_GRADUATION_DAYS` days. During
  that window they log matches but are NOT injected into the executor
  prompt. After the window closes, a scheduled job promotes any rule
  that has:
    • `hits >= SHADOW_GRADUATION_MIN_HITS` (proven relevance)
    • `active == True` (user hasn't disabled)
    • `graduated_at is None` (not already promoted)
  A graduated rule is treated as if project-level `enforce=True`
  applied to that specific rule — it starts influencing the executor
  regardless of the project's global enforce toggle.

Collections:
  correction_rules          — rules (Phase 1 + new graduation fields)
  correction_rule_settings  — per-project global enforce toggle
  correction_rule_events    — match log (Phase 1 metric)

Fail-open discipline: loop_engine callers wrap every call in try/except.
A rules failure must NEVER block a loop.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from typing import Optional

logger = logging.getLogger(__name__)

MAX_RULES_PER_PROJECT = 50
MAX_RULES_PER_PROMPT = 10
MAX_INSTRUCTION_LEN = 300
MAX_PATH_GLOBS = 10

# Phase 2 (Iter 367) — auto-graduation window.
SHADOW_GRADUATION_DAYS = 14
SHADOW_GRADUATION_MIN_HITS = 1

_PATHS_RE = re.compile(r"\bpaths:\s*(.+)$", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def resolve_active_project(db, user_id: str) -> Optional[str]:
    """Most-recent cto_projects row — same server-side resolution the
    suggestions router uses (client never passes project_id)."""
    proj = await db.cto_projects.find_one(
        {"user_id": user_id},
        {"_id": 0, "project_id": 1},
        sort=[("created_at", -1)],
    )
    return (proj or {}).get("project_id")


def parse_add_args(rest: str) -> tuple[str, list[str]]:
    """Split `/rule add <instruction> [paths: g1, g2]` into
    (instruction, applies_to_paths)."""
    rest = (rest or "").strip()
    globs: list[str] = []
    m = _PATHS_RE.search(rest)
    if m:
        globs = [g.strip() for g in m.group(1).split(",") if g.strip()]
        rest = rest[:m.start()].strip()
    return rest, globs[:MAX_PATH_GLOBS]


async def add_rule(db, user_id: str, project_id: Optional[str],
                   instruction: str,
                   applies_to_paths: Optional[list] = None) -> dict:
    instruction = (instruction or "").strip()
    if not instruction:
        return {"ok": False, "error": "empty_instruction",
                "hint": "Usage: /rule add <instruction> [paths: src/*, *.py]"}
    if len(instruction) > MAX_INSTRUCTION_LEN:
        return {"ok": False, "error": "instruction_too_long",
                "hint": f"Max {MAX_INSTRUCTION_LEN} chars."}
    globs = [str(g)[:200] for g in (applies_to_paths or [])][:MAX_PATH_GLOBS]
    n = await db.correction_rules.count_documents(
        {"user_id": user_id, "project_id": project_id})
    if n >= MAX_RULES_PER_PROJECT:
        return {"ok": False, "error": "rule_limit_reached",
                "hint": f"Max {MAX_RULES_PER_PROJECT} rules per project — "
                        "delete one first (/rule list, /rule delete <id>)."}
    doc = {
        "rule_id":            uuid.uuid4().hex[:12],
        "user_id":            user_id,
        "project_id":         project_id,
        "instruction":        instruction,
        "applies_to_paths":   globs,
        "active":             True,
        "source":             "slash_command",
        "hits":               0,
        "last_hit_at":        None,
        "created_at":         _now_iso(),
        # Phase 2 (Iter 367) — auto-graduation fields. A new rule starts
        # its 14-day shadow window at creation. `graduated_at` is None
        # until the scheduler auto-promotes it (or a founder does so
        # via the manual endpoint).
        "shadow_started_at":  _now_iso(),
        "graduated_at":       None,
        "graduated_reason":   None,
    }
    await db.correction_rules.insert_one(dict(doc))
    doc.pop("_id", None)
    return {"ok": True, "rule": doc}


async def list_rules(db, user_id: str, project_id: Optional[str]) -> list:
    cur = db.correction_rules.find(
        {"user_id": user_id, "project_id": project_id}, {"_id": 0},
    ).sort("created_at", 1)
    return await cur.to_list(length=MAX_RULES_PER_PROJECT)


async def delete_rule(db, user_id: str, rule_id: str) -> bool:
    res = await db.correction_rules.delete_one(
        {"user_id": user_id, "rule_id": rule_id})
    return bool(getattr(res, "deleted_count", 0))


async def get_enforce(db, user_id: str, project_id: Optional[str]) -> bool:
    """Per-project enforce toggle. DEFAULT False = shadow mode."""
    doc = await db.correction_rule_settings.find_one(
        {"user_id": user_id, "project_id": project_id}, {"_id": 0})
    return bool((doc or {}).get("enforce", False))


async def set_enforce(db, user_id: str, project_id: Optional[str],
                      enforce: bool) -> None:
    await db.correction_rule_settings.update_one(
        {"user_id": user_id, "project_id": project_id},
        {"$set": {"enforce": bool(enforce), "updated_at": _now_iso()}},
        upsert=True,
    )


async def load_active_rules(db, user_id: str,
                            project_id: Optional[str]) -> list:
    cur = db.correction_rules.find(
        {"user_id": user_id, "project_id": project_id, "active": True},
        {"_id": 0},
    ).sort("created_at", 1)
    return await cur.to_list(length=MAX_RULES_PER_PROJECT)


def match_rules(rules: list, paths: list) -> list:
    """Pure function: which rules apply to which of these file paths.
    Empty `applies_to_paths` on a rule = applies to every path.
    Returns [{rule, matched_paths}], capped at MAX_RULES_PER_PROMPT
    (oldest rules win — deterministic)."""
    out = []
    clean_paths = [str(p).strip() for p in (paths or []) if str(p).strip()]
    if not clean_paths:
        return out
    for rule in (rules or []):
        globs = rule.get("applies_to_paths") or []
        if not globs:
            matched = list(clean_paths)
        else:
            matched = [p for p in clean_paths
                       if any(fnmatch(p, g) for g in globs)]
        if matched:
            out.append({"rule": rule, "matched_paths": matched})
        if len(out) >= MAX_RULES_PER_PROMPT:
            break
    return out


def build_rules_block(matches: list) -> str:
    """Prompt block for ENFORCE mode only. Empty string when nothing
    matched, otherwise ends with a blank line so it slots cleanly
    between plan and file sections of the executor task text."""
    if not matches:
        return ""
    lines = ["PERSISTENT CORRECTION RULES (founder-set — MUST follow):"]
    for i, m in enumerate(matches[:MAX_RULES_PER_PROMPT], 1):
        lines.append(f"{i}. {m['rule']['instruction']}")
    return "\n".join(lines) + "\n\n"


async def record_rule_events(db, *, loop_id: str, user_id: str,
                             project_id: Optional[str], phase: str,
                             matches: list, mode: str) -> None:
    """The Phase 1 instrumented success metric: one event row per
    matched rule per loop-phase + per-rule hit counters."""
    now = _now_iso()
    for m in matches:
        rule = m["rule"]
        try:
            await db.correction_rule_events.insert_one({
                "loop_id":       loop_id,
                "user_id":       user_id,
                "project_id":    project_id,
                "rule_id":       rule["rule_id"],
                "instruction":   rule["instruction"][:120],
                "phase":         phase,
                "mode":          mode,
                "matched_paths": m["matched_paths"][:20],
                "ts":            now,
            })
            await db.correction_rules.update_one(
                {"rule_id": rule["rule_id"]},
                {"$inc": {"hits": 1}, "$set": {"last_hit_at": now}},
            )
        except Exception as e:                              # noqa: BLE001
            logger.warning("correction_rule_events write failed: %r", e)


async def rule_report(db, user_id: str,
                      project_id: Optional[str]) -> dict:
    """Aggregate the metric: per-rule hits, loops affected, mode split.
    Phase 2 (Iter 367) — surfaces `graduated_at` + `days_in_shadow` so
    the founder can see which rules are close to auto-promoting."""
    rules = await list_rules(db, user_id, project_id)
    rows = []
    total_events = 0
    loops: set = set()
    for r in rules:
        events = await db.correction_rule_events.find(
            {"rule_id": r["rule_id"]}, {"_id": 0, "loop_id": 1, "mode": 1},
        ).to_list(length=1000)
        total_events += len(events)
        loops.update(e["loop_id"] for e in events)
        rows.append({
            "rule_id":         r["rule_id"],
            "instruction":     r["instruction"][:80],
            "hits":            r.get("hits", 0),
            "last_hit_at":     r.get("last_hit_at"),
            "shadow_started_at": r.get("shadow_started_at"),
            "graduated_at":    r.get("graduated_at"),
            "graduated_reason": r.get("graduated_reason"),
            "days_in_shadow":  _days_in_shadow(r),
        })
    return {
        "rule_count":     len(rules),
        "total_matches":  total_events,
        "loops_affected": len(loops),
        "rules":          rows,
    }


# ─────────────────────────────────────────────────────────────────────
# Phase 2 (Iter 367 · Item C) — 14-day auto-graduation
# ─────────────────────────────────────────────────────────────────────


def _days_in_shadow(rule: dict) -> Optional[float]:
    """How many days a rule has been in shadow mode. Returns None if
    already graduated or if `shadow_started_at` is missing (legacy rules
    from Phase 1). Legacy rules are treated as ineligible until backfilled
    — that keeps the graduation job idempotent-safe."""
    if rule.get("graduated_at"):
        return None
    ss = rule.get("shadow_started_at")
    if not ss:
        return None
    try:
        started = datetime.fromisoformat(ss.replace("Z", "+00:00"))
    except Exception:
        return None
    return (datetime.now(timezone.utc) - started).total_seconds() / 86400.0


def _is_graduation_eligible(rule: dict,
                             min_age_days: float = SHADOW_GRADUATION_DAYS,
                             min_hits: int = SHADOW_GRADUATION_MIN_HITS,
                             now: Optional[datetime] = None) -> bool:
    """Pure predicate — same logic used by the batch sweep."""
    if rule.get("graduated_at"):
        return False
    if not rule.get("active", True):
        return False
    if int(rule.get("hits", 0)) < min_hits:
        return False
    ss = rule.get("shadow_started_at")
    if not ss:
        return False   # legacy Phase-1 rule — skip until backfilled
    try:
        started = datetime.fromisoformat(ss.replace("Z", "+00:00"))
    except Exception:
        return False
    ref = now or datetime.now(timezone.utc)
    age_days = (ref - started).total_seconds() / 86400.0
    return age_days >= min_age_days


async def graduate_shadow_eligible_rules(
    db,
    *,
    min_age_days: float = SHADOW_GRADUATION_DAYS,
    min_hits:    int    = SHADOW_GRADUATION_MIN_HITS,
    dry_run:     bool   = False,
    now:         Optional[datetime] = None,
) -> dict:
    """Sweep every rule and auto-promote the ones that satisfy:
      • active == True
      • graduated_at is None
      • hits >= min_hits
      • age (from shadow_started_at) >= min_age_days

    A promoted rule gets `graduated_at = now` + `graduated_reason =
    'auto_14day_hits'` and starts injecting into the executor prompt
    on the next loop (regardless of project settings.enforce).

    Idempotent. `dry_run=True` returns the list without writing.
    """
    ref = now or datetime.now(timezone.utc)
    eligible: list[dict] = []
    now_iso = ref.isoformat()
    reason = f"auto_{int(min_age_days)}day_hits"

    cur = db.correction_rules.find({"graduated_at": None, "active": True})
    async for r in cur:
        if _is_graduation_eligible(r, min_age_days, min_hits, ref):
            eligible.append({
                "rule_id":     r.get("rule_id"),
                "user_id":     r.get("user_id"),
                "project_id": r.get("project_id"),
                "instruction": (r.get("instruction") or "")[:80],
                "hits":        r.get("hits", 0),
                "days":        _days_in_shadow(r),
            })

    promoted = 0
    if not dry_run and eligible:
        for e in eligible:
            res = await db.correction_rules.update_one(
                # Guard against races: only promote if still not graduated.
                {"rule_id": e["rule_id"], "graduated_at": None},
                {"$set": {"graduated_at":     now_iso,
                          "graduated_reason": reason}},
            )
            if getattr(res, "modified_count", 0):
                promoted += 1
                logger.info(
                    "[correction_rules] graduated rule_id=%s user=%s "
                    "project=%s hits=%s",
                    e["rule_id"], e["user_id"], e["project_id"], e["hits"],
                )
    return {
        "eligible_count": len(eligible),
        "promoted":       promoted if not dry_run else 0,
        "dry_run":        dry_run,
        "ref_time":       now_iso,
        "min_age_days":   min_age_days,
        "min_hits":       min_hits,
        "rules":          eligible,
    }


def is_rule_effectively_enforced(rule: dict, project_enforce: bool) -> bool:
    """Runtime decision used by loop_engine to pick per-match mode.
    A rule is enforced when EITHER the project global toggle is on
    OR the rule has individually graduated."""
    if project_enforce:
        return True
    return bool(rule.get("graduated_at"))
