"""
services/reasoning_evals.py — Iter 301 (Master QA Track 3)

TEST-INFRASTRUCTURE ONLY — do not delete on zero-live-caller grounds.
This module has no callers in `routers/`, `services/`, or `main.py`
BY DESIGN. It is a test-helper library consumed by
`tests/reasoning/test_verify_verdict_calibration.py`,
`test_plan_shape_validity.py`, `test_faithfulness_llm_judge.py`, and
`test_scan_finding_quality.py`. See docstring below for consumer test-
file details. A discovery audit that greps for live callers will
correctly find zero — that is expected, not evidence of dead code.

Behavioural evaluators for the three load-bearing AI-output surfaces
of the self-fix loop:

    1. Plan JSON        — output of `_generate_plan`  (Loop step 1)
    2. Verify verdict   — output of the independent verifier
                          (Loop step 3, judges Execute's diff)
    3. Scan findings    — output of `scaffold_security_gate.scan_files`
                          (Loop step 4, judges Verify's approved diff)

Founder rule (iter 301): only the check that genuinely requires a
JUDGMENT call gets an LLM. Everything else is deterministic Python
so the regression gate is trustworthy AND cheap.

    * `validate_plan_shape`      → deterministic  (schema + path grounding)
    * `calibrate_verdict`        → deterministic  (severity → verdict map)
    * `scan_finding_matches`    → deterministic  (uses real scan_files)
    * `llm_faithfulness_check`  → LLM-as-judge   (this + only this)
"""
from __future__ import annotations

import re
from typing import Optional

from services.faithfulness_judge import llm_faithfulness_check  # noqa: F401 — re-exported for backward compat


# ═══════════════════════════════════════════════════════════════════
# 1. PLAN SHAPE VALIDITY — deterministic
# ═══════════════════════════════════════════════════════════════════

# Contract the loop's plan phase must produce. Any violation of this
# is either a broken planner prompt or a broken parse — both
# regressions we care about catching, neither requires an LLM judge.
_REQUIRED_PLAN_KEYS = ("title", "steps", "files_to_change")

# Steps must not carry placeholder markers — those leak into the
# approval UI and force the user to guess what the AI is asking to do.
_STEP_PLACEHOLDER_RE = re.compile(
    r"\b(?:TODO|FIXME|XXX|TBD)\b|<[A-Z_][A-Z_ ]*>",
    re.IGNORECASE,
)


def validate_plan_shape(plan: dict,
                          known_paths: Optional[set[str]] = None) -> dict:
    """Assert the plan is well-formed AND grounded in the repo.

    Returns {ok, violations[]}. `violations` is an empty list when the
    plan is valid.

    Grounding rule: if `known_paths` is provided, every entry in
    `plan.files_to_change` must be a member. `known_paths` comes from
    `services.repo_map.build_repo_map` at call time; caller passes it
    in so this evaluator stays pure (no DB, no side effects).
    """
    v: list[str] = []
    if not isinstance(plan, dict):
        return {"ok": False, "violations": [
            f"plan is not a dict: {type(plan).__name__}"]}

    # Required keys.
    for k in _REQUIRED_PLAN_KEYS:
        if k not in plan:
            v.append(f"missing required key: {k!r}")

    # Steps — must be a non-empty list of non-empty strings, no
    # placeholder markers.
    steps = plan.get("steps")
    if steps is None:
        pass  # already reported above
    elif isinstance(steps, str):
        # Some callers stringify. Accept, but check for placeholders.
        if _STEP_PLACEHOLDER_RE.search(steps):
            v.append("steps contains placeholder marker "
                      "(TODO/FIXME/XXX/TBD/<...>)")
    elif isinstance(steps, list):
        if not steps:
            v.append("steps is an empty list")
        for i, s in enumerate(steps):
            if not isinstance(s, str) or not s.strip():
                v.append(f"steps[{i}] is not a non-empty string")
            elif _STEP_PLACEHOLDER_RE.search(s):
                v.append(f"steps[{i}] contains placeholder marker")
    else:
        v.append(f"steps must be list or str; got {type(steps).__name__}")

    # files_to_change — must be a list of non-empty strings, and if
    # `known_paths` provided, at most 1 ungrounded path (a new file
    # being created is legitimate; 2+ suggests hallucination).
    fs = plan.get("files_to_change")
    if fs is None:
        pass
    elif not isinstance(fs, list):
        v.append(f"files_to_change must be a list; got "
                  f"{type(fs).__name__}")
    else:
        for i, p in enumerate(fs):
            if not isinstance(p, str) or not p.strip():
                v.append(f"files_to_change[{i}] is not a non-empty string")
        if known_paths:
            ungrounded = [p for p in fs
                          if isinstance(p, str) and p.strip()
                          and p not in known_paths]
            if len(ungrounded) > 1:
                v.append(
                    f"{len(ungrounded)}/{len(fs)} files_to_change are "
                    f"not in the repo map — likely hallucination: "
                    f"{ungrounded[:5]}"
                )

    return {"ok": not v, "violations": v}


# ═══════════════════════════════════════════════════════════════════
# 2. VERDICT CALIBRATION — deterministic
# ═══════════════════════════════════════════════════════════════════

# Fixed mapping the verifier MUST honour. Deriving verdict from the
# evidence's peak severity removes model whim from a load-bearing
# gate — a "pass" verdict on a diff carrying a HIGH finding is a
# calibration bug, not a valid judgment call.
_ALLOWED_VERDICTS = frozenset({"pass", "fail", "needs_revision"})
_SEVERITY_ORDER = ("info", "low", "medium", "high", "critical")


def _peak_severity(evidence: dict) -> str:
    """Return the highest-severity finding in `evidence.findings[]`,
    or "info" when the list is empty / malformed."""
    findings = (evidence or {}).get("findings") or []
    peak_idx = 0
    for f in findings:
        sev = str((f or {}).get("severity", "")).lower()
        try:
            i = _SEVERITY_ORDER.index(sev)
        except ValueError:
            continue
        if i > peak_idx:
            peak_idx = i
    return _SEVERITY_ORDER[peak_idx]


def calibrate_verdict(verdict: str, evidence: dict) -> dict:
    """Return {ok, expected, actual, reason} — asserts the verdict is
    consistent with the peak severity of evidence.findings.

    Rules:
      * peak == critical  →  fail
      * peak == high      →  fail
      * peak == medium    →  needs_revision
      * peak == low/info  →  pass
    """
    actual = str(verdict or "").lower().strip()
    if actual not in _ALLOWED_VERDICTS:
        return {
            "ok": False,
            "expected": None,
            "actual":   actual,
            "reason":   f"verdict {actual!r} not in {sorted(_ALLOWED_VERDICTS)}",
        }

    peak = _peak_severity(evidence)
    if peak in ("critical", "high"):
        expected = "fail"
    elif peak == "medium":
        expected = "needs_revision"
    else:  # low, info
        expected = "pass"

    return {
        "ok":       (actual == expected),
        "expected": expected,
        "actual":   actual,
        "peak_severity": peak,
        "reason":   (
            f"peak severity is {peak!r} → verdict should be "
            f"{expected!r}; got {actual!r}"
            if actual != expected else "calibrated"
        ),
    }


# ═══════════════════════════════════════════════════════════════════
# 3. SCAN FINDING QUALITY — deterministic (delegates to real scan_files)
# ═══════════════════════════════════════════════════════════════════

async def scan_finding_matches(files: list[dict],
                                 expected_rule_id: Optional[str] = None,
                                 expected_severity: Optional[str] = None) -> dict:
    """Feed `files` to the real `scan_files()` gate + assert the
    findings match `expected_rule_id` / `expected_severity`.

    Returns {ok, actual_findings, expected_rule_id, expected_severity,
             mismatch_reason}."""
    from services.scaffold_security_gate import scan_files
    r = await scan_files(files)
    findings = r.get("findings") or []
    match_rule = (expected_rule_id is None
                   or any(f.get("rule_id") == expected_rule_id
                          for f in findings))
    match_sev = (expected_severity is None
                  or any(str(f.get("severity", "")).lower() ==
                          expected_severity.lower()
                          for f in findings))
    reason = None
    if not match_rule:
        rules_seen = sorted({f.get("rule_id") for f in findings})
        reason = f"rule {expected_rule_id!r} not fired; got {rules_seen}"
    elif not match_sev:
        sevs_seen = sorted({str(f.get("severity", "")).lower()
                             for f in findings})
        reason = (f"severity {expected_severity!r} not present; "
                   f"got {sevs_seen}")
    return {
        "ok": bool(match_rule and match_sev),
        "actual_findings":    findings,
        "expected_rule_id":   expected_rule_id,
        "expected_severity":  expected_severity,
        "mismatch_reason":    reason,
    }


# ═══════════════════════════════════════════════════════════════════
# 4. FAITHFULNESS — LLM-as-judge (the ONLY LLM call in this module)
#    Implementation lives in services/faithfulness_judge.py, imported
#    + re-exported above.
# ═══════════════════════════════════════════════════════════════════
