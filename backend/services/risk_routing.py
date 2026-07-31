"""
services/risk_routing.py — Iter 367 · Item D · Phase 2

Risk-based routing for the AI executor: BEFORE the executor writes
a file we score the intended change and emit a 3-tier verdict:

    AUTO_SHIP          — low risk, proceed as usual
    WARN_SHIP          — medium risk, proceed but log a WARN row + narrate
    PAUSE_FOR_FOUNDER  — high risk, HALT the loop until the founder
                         reviews (mandatory 2-week shadow before this
                         halt actually blocks — see SHADOW MODE below).

SHADOW MODE (2 weeks mandatory before enforce):
  For the first `RISK_ROUTING_SHADOW_DAYS` days after the feature
  first ships, scoring runs on every executor step but PAUSE_FOR_FOUNDER
  does NOT actually block — it only logs and narrates. Only after
  `enforce_since` is reached does PAUSE_FOR_FOUNDER halt the loop.
  This gives the founder a full window to inspect scores in the
  `risk_scores` collection and adjust thresholds before real halts fire.

Signals we score (all pure functions of the file path + diff):
  • path_sensitivity   — does the change touch a sensitive path
                          (auth, payments, deploy, secrets, admin,
                          server bootstrap)?
  • diff_size          — how many lines changed?
  • new_dependency     — does the diff add a new package/import?
  • security_pattern   — regex hits for known-dangerous patterns
                          (eval, exec, shell=True, disable auth, etc.)
  • founder_flag       — does the file live under a founder-owned path?

Each signal contributes a bounded weight; the final score is
sigmoid-clipped to [0, 1]. Tier boundaries are:
  <=0.40  → AUTO_SHIP
  <=0.75  → WARN_SHIP
  else    → PAUSE_FOR_FOUNDER

The whole thing is FAIL-OPEN: any exception collapses to AUTO_SHIP
with a `error` tag so a scoring bug NEVER halts a loop.

Public API:
  score_change(path, before_bytes, after_bytes) -> RiskScore
  record_score(db, loop_id, user_id, project_id, phase, path, score)
  should_halt(db, score) -> bool
  admin_summary(db) -> dict for the /admin/qa dashboard
"""
from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Tier constants — locked scope names ────────────────────────────
TIER_AUTO_SHIP         = "AUTO_SHIP"
TIER_WARN_SHIP         = "WARN_SHIP"
TIER_PAUSE_FOR_FOUNDER = "PAUSE_FOR_FOUNDER"
ALL_TIERS = (TIER_AUTO_SHIP, TIER_WARN_SHIP, TIER_PAUSE_FOR_FOUNDER)

# ─── Tunables (env-overridable) ─────────────────────────────────────
# The 2-week mandatory shadow. `RISK_ROUTING_ENFORCE_SINCE` (ISO ts)
# is set at first shadow-mode start by record_score() and never bumped
# afterwards, so the enforce cut-over is deterministic per-deployment.
RISK_ROUTING_SHADOW_DAYS = float(
    os.environ.get("RISK_ROUTING_SHADOW_DAYS", "14"))
# Tier boundaries.
WARN_THRESHOLD  = float(os.environ.get("RISK_ROUTING_WARN_THRESHOLD",  "0.40"))
PAUSE_THRESHOLD = float(os.environ.get("RISK_ROUTING_PAUSE_THRESHOLD", "0.75"))


@dataclass
class RiskScore:
    tier:         str
    score:        float
    signals:      dict = field(default_factory=dict)
    path:         str = ""
    error:        Optional[str] = None
    def to_dict(self) -> dict:
        return asdict(self)


# ─── Sensitive-path patterns (path-level heuristic) ─────────────────
# Ordered specific → generic so `deploy/keys/prod.pem` scores higher
# than plain `deploy/`.
_PATH_WEIGHTS: list[tuple[re.Pattern, float, str]] = [
    (re.compile(r"^\.env(\..+)?$"),                    0.90, "env_secrets"),
    (re.compile(r"(?i)/secrets?/"),                    0.80, "secrets_dir"),
    (re.compile(r"(?i)(^|/)auth([/_.]|$)"),            0.55, "auth_code"),
    (re.compile(r"(?i)(^|/)payment[s]?([/_.]|$)"),     0.60, "payments"),
    (re.compile(r"(?i)(^|/)stripe"),                   0.60, "stripe"),
    (re.compile(r"(?i)(^|/)deploy"),                   0.50, "deploy"),
    (re.compile(r"(?i)(^|/)admin"),                    0.40, "admin_code"),
    (re.compile(r"(?i)(^|/)vault"),                    0.55, "vault"),
    (re.compile(r"(?i)(^|/)ftp_ssh_deploy"),           0.55, "byoh_deploy"),
    (re.compile(r"(?i)(^|/)server\.py$"),              0.35, "server_boot"),
    (re.compile(r"(?i)/main\.py$"),                    0.35, "app_bootstrap"),
    (re.compile(r"(?i)\.github/workflows/"),           0.45, "ci_workflow"),
    (re.compile(r"(?i)Dockerfile"),                    0.30, "docker"),
    (re.compile(r"(?i)requirements\.txt$"),            0.35, "python_deps"),
    (re.compile(r"(?i)package\.json$"),                0.35, "node_deps"),
]

# ─── Dangerous code patterns inside diffs ───────────────────────────
_DANGEROUS_RES: list[tuple[re.Pattern, float, str]] = [
    (re.compile(r"\beval\s*\("),                       0.50, "eval_call"),
    (re.compile(r"\bexec\s*\("),                       0.45, "exec_call"),
    (re.compile(r"shell\s*=\s*True"),                  0.40, "shell_true"),
    (re.compile(r"os\.system\s*\("),                   0.40, "os_system"),
    (re.compile(r"@app\.(?:get|post)\([\"']/admin/"),   0.35, "admin_route"),
    (re.compile(r"(disable_auth|skip_auth|no_auth)"),   0.60, "auth_bypass"),
    (re.compile(r"(?i)verify\s*=\s*False\b"),          0.40, "tls_disabled"),
    (re.compile(r"(?i)ALLOW_UNSAFE_"),                 0.30, "unsafe_env"),
    (re.compile(r"AKIA[0-9A-Z]{16}"),                  0.90, "aws_key"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"),               0.80, "openai_key"),
]


def _sigmoid_clip(x: float) -> float:
    """Map an unbounded positive sum → [0, 1] with a soft ceiling.
    Standard 1/(1+e^(-k*(x-x0))) with k=4, x0=1.0 gives:
      x=0.0 → 0.02   x=0.5 → 0.12   x=1.0 → 0.50
      x=1.5 → 0.88   x=2.0 → 0.98
    So a single signal already lands in AUTO_SHIP; two mid-signals
    push into WARN_SHIP; three+ or a heavy signal into PAUSE_FOR_FOUNDER.
    """
    return 1.0 / (1.0 + math.exp(-4.0 * (x - 1.0)))


def score_change(
    path:          str,
    before_bytes:  Optional[bytes] = None,
    after_bytes:   Optional[bytes] = None,
    added_lines:   Optional[int]   = None,
) -> RiskScore:
    """Score a proposed file change. Pure, side-effect-free.
    ALL exceptions collapse to AUTO_SHIP + error tag — never raise."""
    signals: dict = {}
    try:
        raw_total = 0.0

        # 1) Path sensitivity — highest weight wins (not additive to
        #    avoid double-counting nested matches like deploy/auth/x).
        top_path_w = 0.0
        top_path_tag = None
        for regex, w, tag in _PATH_WEIGHTS:
            if regex.search(path or ""):
                if w > top_path_w:
                    top_path_w = w
                    top_path_tag = tag
        if top_path_tag:
            signals["path"] = {"tag": top_path_tag, "weight": top_path_w}
            raw_total += top_path_w

        # 2) Diff size — approximate if no explicit line count.
        before = before_bytes or b""
        after  = after_bytes  or b""
        if added_lines is None:
            before_lines = before.count(b"\n")
            after_lines  = after.count(b"\n")
            added = max(0, after_lines - before_lines)
        else:
            added = int(added_lines)
        if added >= 500:
            signals["diff_size"] = {"lines": added, "weight": 0.50}
            raw_total += 0.50
        elif added >= 150:
            signals["diff_size"] = {"lines": added, "weight": 0.25}
            raw_total += 0.25
        elif added >= 50:
            signals["diff_size"] = {"lines": added, "weight": 0.10}
            raw_total += 0.10

        # 3) Dangerous-pattern regex hits (against the AFTER content).
        after_text = ""
        if after:
            try:
                after_text = after.decode("utf-8", errors="ignore")
            except Exception:
                after_text = ""
        pattern_hits = []
        for regex, w, tag in _DANGEROUS_RES:
            if regex.search(after_text):
                pattern_hits.append({"tag": tag, "weight": w})
                raw_total += w
        if pattern_hits:
            signals["dangerous_patterns"] = pattern_hits

        # 4) New dependency detection (crude but effective for
        #    Python + JS ecosystems).
        if path.endswith(("requirements.txt", "package.json", "pyproject.toml",
                           "yarn.lock", "package-lock.json")):
            before_pkgs = _extract_pkgs(before.decode("utf-8", errors="ignore"))
            after_pkgs  = _extract_pkgs(after_text)
            new_pkgs    = sorted(after_pkgs - before_pkgs)
            if new_pkgs:
                signals["new_dependencies"] = new_pkgs[:20]
                raw_total += min(0.50, 0.15 * len(new_pkgs))

        score = _sigmoid_clip(raw_total)
        tier = _tier_for(score)
        return RiskScore(tier=tier, score=round(score, 4),
                          signals=signals, path=path)
    except Exception as e:                                    # noqa: BLE001
        logger.warning("risk_routing.score_change failed: %r", e)
        return RiskScore(tier=TIER_AUTO_SHIP, score=0.0,
                          signals=signals, path=path,
                          error=f"{type(e).__name__}:{str(e)[:80]}")


def _extract_pkgs(text: str) -> set[str]:
    """Best-effort package-name extraction from a requirements/package
    manifest. Not intended to be perfect — just sufficient to detect
    a newly-added dependency."""
    out: set[str] = set()
    if not text:
        return out
    # requirements.txt lines: name==ver, name>=ver, name
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # requirements.txt style
        m = re.match(r"([A-Za-z0-9_.\-]+)\s*(?:==|>=|<=|~=|!=|>|<|;|$)", line)
        if m:
            out.add(m.group(1).lower())
    # package.json crude: "\"name\": \"^1.2.3\""
    for m in re.finditer(r'"([A-Za-z0-9_@\-/]+)"\s*:\s*"[\^~<>=0-9]', text):
        out.add(m.group(1).lower())
    return out


def _tier_for(score: float) -> str:
    if score <= WARN_THRESHOLD:  return TIER_AUTO_SHIP
    if score <= PAUSE_THRESHOLD: return TIER_WARN_SHIP
    return TIER_PAUSE_FOR_FOUNDER


# ─── DB helpers ────────────────────────────────────────────────────


async def _ensure_shadow_start(db) -> str:
    """Return the ISO ts when shadow mode began for this deployment.
    Stored once in `risk_routing_meta` — never updated afterwards, so
    the enforce cut-over is deterministic."""
    meta = await db.risk_routing_meta.find_one({"_key": "shadow_start"})
    if meta:
        return meta.get("started_at") or datetime.now(timezone.utc).isoformat()
    started = datetime.now(timezone.utc).isoformat()
    try:
        await db.risk_routing_meta.insert_one({
            "_key": "shadow_start", "started_at": started,
            "shadow_days": RISK_ROUTING_SHADOW_DAYS,
        })
    except Exception:
        pass
    return started


async def is_enforcing(db, *, now: Optional[datetime] = None) -> bool:
    """True after `RISK_ROUTING_SHADOW_DAYS` days of shadow.
    Before that, `should_halt` always returns False even for
    PAUSE_FOR_FOUNDER — real halts wait until the founder has had
    a full window to inspect scores."""
    started_iso = await _ensure_shadow_start(db)
    try:
        started = datetime.fromisoformat(
            started_iso.replace("Z", "+00:00"))
    except Exception:
        return False
    ref = now or datetime.now(timezone.utc)
    return (ref - started).total_seconds() >= (
        RISK_ROUTING_SHADOW_DAYS * 86400.0)


async def record_score(
    db, *,
    loop_id:    str,
    user_id:    str,
    project_id: Optional[str],
    phase:      str,          # "plan" | "execute" | "verify"
    path:       str,
    score:      RiskScore,
) -> None:
    """Log a score row for the founder dashboard + timeline. Fail-open."""
    try:
        await db.risk_scores.insert_one({
            "loop_id":    loop_id,
            "user_id":    user_id,
            "project_id": project_id,
            "phase":      phase,
            "path":       path,
            "tier":       score.tier,
            "score":      score.score,
            "signals":    score.signals,
            "error":      score.error,
            "ts":         datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:                                    # noqa: BLE001
        logger.warning("risk_routing.record_score failed: %r", e)


async def should_halt(db, score: RiskScore) -> bool:
    """Loop-engine decision — halt only when the tier is PAUSE_FOR_FOUNDER
    AND the mandatory 2-week shadow window has passed."""
    if score.tier != TIER_PAUSE_FOR_FOUNDER:
        return False
    return await is_enforcing(db)


async def admin_summary(db) -> dict:
    """Aggregate the last 30d of scores for the founder /admin/qa
    dashboard: counts per tier, top-K sensitive paths, current mode
    (shadow vs enforce), days until enforce."""
    started_iso = await _ensure_shadow_start(db)
    started = None
    try:
        started = datetime.fromisoformat(
            started_iso.replace("Z", "+00:00"))
    except Exception:
        pass
    now = datetime.now(timezone.utc)
    enforcing = (started is not None and
                 (now - started).total_seconds() >=
                 RISK_ROUTING_SHADOW_DAYS * 86400.0)
    days_until_enforce = None
    if started is not None and not enforcing:
        remaining_s = RISK_ROUTING_SHADOW_DAYS * 86400.0 - (
            now - started).total_seconds()
        days_until_enforce = max(0, round(remaining_s / 86400.0, 2))

    tier_counts = {t: 0 for t in ALL_TIERS}
    top_paths: dict[str, int] = {}
    total = 0
    cutoff = (now - timedelta(days=30)).isoformat()
    async for row in db.risk_scores.find({"ts": {"$gte": cutoff}}):
        tier_counts[row.get("tier", TIER_AUTO_SHIP)] = \
            tier_counts.get(row.get("tier"), 0) + 1
        path = row.get("path") or "?"
        top_paths[path] = top_paths.get(path, 0) + 1
        total += 1

    return {
        "mode":               "enforce" if enforcing else "shadow",
        "shadow_start":       started_iso,
        "shadow_days":        RISK_ROUTING_SHADOW_DAYS,
        "days_until_enforce": days_until_enforce,
        "warn_threshold":     WARN_THRESHOLD,
        "pause_threshold":    PAUSE_THRESHOLD,
        "window_days":        30,
        "total_scores":       total,
        "tier_counts":        tier_counts,
        "top_paths":          sorted(top_paths.items(),
                                     key=lambda kv: kv[1],
                                     reverse=True)[:20],
    }
