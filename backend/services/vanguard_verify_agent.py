"""
services/vanguard_verify_agent.py
=================================

Iter 111 — Separate Vanguard verify agent (Anthropic-style "defending-
code-reference-harness"):

> ORA writes code → handed off to a SEPARATE agent (different prompt,
> different model) → re-reviews the patch for the 25 known vulnerability
> patterns PLUS its own LLM-grade judgement → only on PASS does the
> patch progress to commit.

Iter 169 — migrated to OpenRouter (anthropic/claude-sonnet-4-5-20250929).
The previous Emergent SDK dependency was dead weight after llm.py was
cleaned up — when EMERGENT_LLM_KEY was unset the verify pipeline
silently skipped LLM review, leaving only the regex floor. Now it
goes through the same OPENROUTER_API_KEY all other LLM calls use, so
the second-agent review actually runs in production.

Iter 212m-41 — soften the LLM blocking rule. The previous prompt told
the agent `pass` was false on ANY CRITICAL or HIGH finding, which made
Claude (now overly diligent) block routine commits on theoretical
HIGH risks (e.g. `localStorage.setItem("token", …)`, inline `style=`,
React's dangerous HTML prop in a tooltip, etc.). The regex floor
(`scan_file_blocks`) is what guarantees the real CRITICAL gates; the
LLM agent is now advisory for HIGH and only blocks on CRITICAL. Two
new env vars provide escape hatches:

  VANGUARD_VERIFY_BLOCK_LEVEL = CRITICAL | HIGH | OFF   (default CRITICAL)
  VANGUARD_VERIFY_ENABLED     = 1/0                     (default 1)

Public API
----------
    await verify_patch(file_blocks, repo_ctx=...) -> {
        "pass":     bool,
        "findings": [ {file, line, severity, rule, message} ],
        "summary":  str,
        "model":    str,
    }
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
from typing import Any, Optional

from .vanguard_scanner import scan_file_blocks, has_critical

logger = logging.getLogger(__name__)

# Separate-agent isolation: this is a DIFFERENT model/persona than ORA.
# Claude Sonnet 4.5 via OpenRouter — same key as the rest of the app.
# Iter 212g — use OpenRouter's dotted ID (4.5); the dash-date Anthropic
# native format returns HTTP 400 from OpenRouter.
_VERIFY_MODEL = os.environ.get(
    "VANGUARD_VERIFY_MODEL",
    "anthropic/claude-sonnet-4.5",
)

# Iter 212m-41 — env-tunable severity threshold + kill-switch.
_BLOCK_LEVEL = (
    os.environ.get("VANGUARD_VERIFY_BLOCK_LEVEL", "CRITICAL") or "CRITICAL"
).upper()
_ENABLED = (
    os.environ.get("VANGUARD_VERIFY_ENABLED", "1").lower()
    in ("1", "true", "yes", "on")
)


def _severity_blocks(severity: str) -> bool:
    """Should this finding actually block the commit?
    Honours VANGUARD_VERIFY_BLOCK_LEVEL.

      OFF       → never blocks
      CRITICAL  → only CRITICAL findings block
      HIGH      → CRITICAL or HIGH findings block
    """
    sev = (severity or "").upper()
    if _BLOCK_LEVEL == "OFF":
        return False
    if _BLOCK_LEVEL == "HIGH":
        return sev in ("CRITICAL", "HIGH")
    return sev == "CRITICAL"


# ──────────────────────────────────────────────────────────────────
# Iter 212m-132 — Diff-based scanning.
# ──────────────────────────────────────────────────────────────────
# THE PROBLEM (founder report):
#   Vanguard scans the ENTIRE file when reviewing a Loop patch, which
#   means pre-existing vulns in lines the patch didn't touch end up
#   blocking the commit. Result: Loop commits get blocked by issues
#   the user never asked us to introduce.
#
# THE FIX:
#   `changed_lines_for_file(base, new)` returns the 1-indexed set of
#   line numbers in the NEW content that differ from the base.
#   `filter_findings_to_changed_lines(findings, changed_lines_map)`
#   then drops any regex/LLM finding whose `line` is NOT in the
#   changed set. The whole pipeline keeps working unchanged when
#   `base_blocks` is None / missing (legacy callers, brand-new
#   files) — those scans stay full-file.
#
# Why line-set based instead of unified-diff:
#   • A unified-diff envelope would force the LLM to count hunk
#     offsets and lose continuity (model hallucinates line numbers).
#   • Sending the whole new file + the line-set as a JSON sidebar
#     keeps the LLM's review prompt close to what it sees today
#     (less prompt-engineering risk).
#   • Regex scanner already returns `line` per finding — we just
#     filter post-hoc; cheaper than rewriting `scan_file_blocks`.
# ──────────────────────────────────────────────────────────────────


def changed_lines_for_file(base: str, new: str) -> set[int]:
    """Return the 1-indexed set of line numbers in `new` that were
    added or modified relative to `base`.

    Uses `difflib.SequenceMatcher` opcodes:
      • "equal"   → unchanged, skip
      • "insert"  → all lines j1..j2 in new are NEW
      • "replace" → all lines j1..j2 in new replaced something in base

    `delete` opcodes don't contribute to "new lines" — they only
    removed something from base (we can't introduce a vuln by
    deleting lines).
    """
    if not new:
        return set()
    if not base:
        # New file — every non-empty line is "changed".
        return {i + 1 for i, ln in enumerate(new.splitlines())}
    base_lines = base.splitlines()
    new_lines  = new.splitlines()
    sm = difflib.SequenceMatcher(a=base_lines, b=new_lines,
                                 autojunk=False)
    changed: set[int] = set()
    for op, _i1, _i2, j1, j2 in sm.get_opcodes():
        if op in ("insert", "replace"):
            # j is 0-indexed into new_lines; we want 1-indexed line nums.
            for j in range(j1, j2):
                changed.add(j + 1)
    return changed


def changed_lines_map(base_blocks: dict[str, str],
                      new_blocks:  dict[str, str]) -> dict[str, set[int]]:
    """For each `path` in `new_blocks`, compute the changed-line set
    relative to `base_blocks[path]` (or empty base if missing —
    treated as a brand-new file).  Returns `{path: {line_nums}}`."""
    out: dict[str, set[int]] = {}
    for path, new_content in (new_blocks or {}).items():
        base = (base_blocks or {}).get(path, "") or ""
        out[path] = changed_lines_for_file(base, new_content or "")
    return out


def filter_findings_to_changed_lines(
    findings: list[dict],
    line_map: dict[str, set[int]],
) -> tuple[list[dict], list[dict]]:
    """Split `findings` into (kept_findings, dropped_findings).

    A finding is KEPT when EITHER:
      • its `file` is not in `line_map` (we don't have diff info →
        be safe, keep it), OR
      • its `line` is missing/0 (scanner didn't tell us where → keep
        so we don't silently drop a real vuln), OR
      • its `line` is in the changed set for that file.

    DROPPED findings are returned for audit/logging so we can show
    "X pre-existing issues skipped" in the verify summary.
    """
    kept:    list[dict] = []
    dropped: list[dict] = []
    for f in findings or []:
        path = f.get("file") or f.get("path") or ""
        line = f.get("line")
        if not path or path not in line_map:
            kept.append(f)
            continue
        if not line:  # 0 or None → keep (scanner couldn't pinpoint)
            kept.append(f)
            continue
        try:
            ln_int = int(line)
        except (TypeError, ValueError):
            kept.append(f)
            continue
        if ln_int in line_map[path]:
            kept.append(f)
        else:
            dropped.append({**f, "_skipped_reason": "pre_existing"})
    return kept, dropped


_VERIFY_SYSTEM = """You are the **Vanguard Verify Agent** — a dedicated security
reviewer that re-audits code patches BEFORE they are committed. You are
NOT the same agent that wrote the code. Your sole job is to find
vulnerabilities, dangerous patterns, and architectural risks that the
regex pre-scan may have missed.

Review the patch for ALL of these dimensions:
  1. Secrets / credentials / tokens / keys (any hard-coded value)
  2. Code injection — eval, exec, shell=True, dynamic imports, sql
     concatenation, template injection, untrusted deserialisation
  3. Path traversal / arbitrary file read or write
  4. Server-side request forgery (urlopen / requests on user-controlled URL)
  5. Cross-site scripting — innerHTML, dangerouslySetInnerHTML, raw
     html.unescape on user input, jQuery .html() with user data
  6. Insecure crypto — md5, sha1, custom RNG, hardcoded IV, ECB mode
  7. Hardcoded admin / debug paths or auth bypasses
  8. Open CORS (allow_origins=['*'] in production)
  9. Logic bombs — sleep(), while True with no break, recursion without
     base case
 10. Missing authorization / authentication on a sensitive route
 11. Direct SQL with user-controlled fragments
 12. Race conditions — async without lock, non-atomic check-then-write

SEVERITY RULES (Iter 212m-41 — production calibration):
  CRITICAL = an attacker can OWN the system or read other users' data
             RIGHT NOW with this exact patch deployed. Examples:
             real hard-coded API key, eval(user_input), SQL string
             concatenation on a user-controlled fragment, an unauth'd
             admin route.  Be SURE before you mark anything critical.
  HIGH     = real risk but needs preconditions (e.g. user already
             authenticated, internal-only endpoint, defence-in-depth).
             Examples: localStorage of a short-lived JWT,
             dangerouslySetInnerHTML of a static literal string.
  MEDIUM/LOW = code-smell, style, or theoretical risk.

DO NOT MARK CRITICAL/HIGH for:
  • Inline CSS via React's `style={{}}` prop (this is not XSS).
  • Routine `localStorage.setItem` / `getItem` for app state.
  • `console.log` / `console.error` in dev paths.
  • UI styling, JSX class names, refactors that don't touch auth or IO.
  • Adding a UI banner / pill / button / copy change.
  • Reusing an existing pattern that already exists elsewhere in the
    repo — if the pattern is widespread, the verdict is INFO at most.

You MUST respond with VALID JSON only, no prose:
{
  "pass": true | false,
  "findings": [
    {"file": "...", "line": 42, "severity": "CRITICAL|HIGH|MEDIUM|LOW",
     "rule": "short-slug", "message": "1-line explanation"}
  ],
  "summary": "1-paragraph executive summary"
}

Set `pass` to FALSE ONLY when there is at least one finding you
genuinely believe is CRITICAL by the rules above. Empty findings,
all MEDIUM/LOW, or HIGH-only findings → set `pass` to TRUE.
"""


def _has_executable_python(blocks: dict) -> bool:
    """True iff any .py file in the patch defines a function or class.
    Used to decide whether the E2B smoke-import gate is worth running."""
    for path, content in (blocks or {}).items():
        if not path.endswith(".py"):
            continue
        if not content:
            continue
        for ln in content.splitlines():
            s = ln.strip()
            if s.startswith("def ") or s.startswith("async def ") or s.startswith("class "):
                return True
    return False


async def _llm_review(file_blocks: dict, repo_ctx: str,
                     line_map: Optional[dict[str, set[int]]] = None) -> dict:
    """Make the second-agent LLM call. Iter 169 — uses OpenRouter
    (anthropic/claude-sonnet-4-5-20250929). Returns
    {pass, findings, summary, model}. On any error, returns pass=True
    with a note so we don't accidentally block the pipeline on
    transient infra; the regex scan stays the floor.

    Iter 212m-132 — When `line_map` is provided (diff-aware mode),
    we annotate the patch envelope so the LLM understands which
    line numbers are NEW vs PRE-EXISTING. We also instruct it via
    the system prompt to ONLY flag issues introduced by this patch.
    """
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return {"pass": True, "findings": [],
                "summary": "verify-agent skipped (no OPENROUTER_API_KEY)",
                "model": ""}

    # Build a compact patch envelope. Each file capped so we don't blow
    # context on huge edits — first/last 250 lines is plenty for review.
    envelope_parts: list[str] = [f"# Repo: {repo_ctx}", ""]
    diff_mode_active = line_map is not None and any(
        line_map.get(p) for p in (file_blocks or {})
    )
    if diff_mode_active:
        envelope_parts.append(
            "# DIFF MODE: For each file below, `CHANGED_LINES` lists "
            "the 1-indexed line numbers in the new content that this "
            "patch ADDED or MODIFIED. Pre-existing lines are shown "
            "for context only; do