"""services/minimal_edit.py — surgical single-edit fast path.

Session 6 · Item 3 (2026-07-31). Real-user QA: asking ORA to
"add a one-line comment at the very top of README.md" produced a
+36/-35 diff — the LLM effectively rewrote the entire file
instead of prepending one line. This contradicts the landing-page
promise of "Minimum-diff commits" (Swift mode).

Root cause: `services/loop_execute._generate_one` unconditionally
asks the LLM to "Rewrite the entire file content". Even when the
model dutifully returns "the same file plus one line", it drifts
whitespace/formatting/word-choice across dozens of untouched lines.

Fix: BEFORE the full-file-rewrite fallback, try a MINIMAL-EDIT
pass. Ask the LLM to describe the change as a compact JSON
operation (`prepend` / `append` / `insert_after_line` / `replace_line`
/ `delete_line`). If the LLM says the change is expressible that
way AND the operation applies cleanly, we use the surgical result
— diff is exactly the number of lines the op adds/removes, nothing
else. If NOT expressible (or the JSON is malformed), we transparently
fall back to the existing full-rewrite path — zero regression risk.

The public entry point is `try_minimal_edit()` — it returns either
`{"content": "<final file body>", "op": <op_dict>}` on a clean
surgical hit, or `None` when the caller should fall back to the
full-rewrite path.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ═══ Op-shape guards ════════════════════════════════════════════════
_ALLOWED_OPS = {
    "prepend", "append",
    "insert_after_line", "insert_before_line",
    "replace_line", "delete_line",
    "not_expressible",   # LLM signal: "please use full-rewrite path"
}


# ═══ Regex-based trigger for the surgical path ══════════════════════
# We only try the minimal-edit path when the USER prompt looks like a
# small-scope request. This keeps the extra LLM round-trip off the
# hot path for genuine refactor / rewrite / new-file tasks.
_TRIVIAL_HINTS = [
    re.compile(r"\badd\s+(?:a\s+|one\s+)?(?:one[- ]line\s+)?(?:line|comment|note|header|banner|todo|comment line)\b", re.I),
    re.compile(r"\b(?:prepend|append)\b", re.I),
    re.compile(r"\binsert\s+(?:a\s+)?(?:line|comment)\b", re.I),
    re.compile(r"\b(?:add|insert)\s+(?:one[- ]?)?(?:line|comment)\s+(?:at|to|on)\s+(?:the\s+)?(?:top|bottom|end|beginning|start)\b", re.I),
    re.compile(r"\b(?:remove|delete)\s+(?:the\s+\w+\s+)?(?:line|comment)\b", re.I),
    re.compile(r"\breplace\s+(?:the\s+)?(?:line|string)\b", re.I),
    re.compile(r"\bfix\s+(?:the\s+)?typo\b", re.I),
    re.compile(r"\brename\s+", re.I),
    re.compile(r"\bchange\s+(?:the\s+)?(?:one|first|last|single)\s+line\b", re.I),
]


def is_trivial_scope(user_message: str) -> bool:
    """Fast lexical check — does the request look like a small-scope
    edit? Only triggers the extra minimal-edit LLM call on plausible
    candidates, so bigger refactors bypass this path entirely."""
    if not user_message or len(user_message) > 500:
        # Very long prompts are almost certainly multi-step tasks — skip.
        return False
    return any(p.search(user_message) for p in _TRIVIAL_HINTS)


# ═══ Op appliers ════════════════════════════════════════════════════
def _apply_op(current: str, op: dict) -> Optional[str]:
    """Apply a validated operation to `current`. Returns the new file
    body, or None if the op cannot be applied cleanly."""
    kind = op.get("op")
    text = op.get("text", "")
    line = op.get("line")

    if kind == "not_expressible":
        return None

    if kind not in _ALLOWED_OPS:
        return None

    # Normalise trailing newline handling — we want a single \n at EOF
    # and we don't want the op to duplicate that.
    lines = current.splitlines(keepends=False)
    had_trailing_newline = current.endswith("\n") or not current

    if kind == "prepend":
        # Insert `text` at the very top. `text` may itself contain
        # newlines. Ensure a separator newline before the existing body.
        prefix = text if text.endswith("\n") else text + "\n"
        return prefix + current

    if kind == "append":
        # Insert at the very end.  Preserve trailing newline shape.
        body = current
        if body and not body.endswith("\n"):
            body += "\n"
        suffix = text if text.endswith("\n") else text + "\n"
        return body + suffix

    if kind in ("insert_after_line", "insert_before_line"):
        if not isinstance(line, int) or line < 1 or line > len(lines) + 1:
            return None
        idx = line if kind == "insert_after_line" else line - 1
        # Split multi-line text into individual lines.
        new_lines = lines[:idx] + text.split("\n") + lines[idx:]
        out = "\n".join(new_lines)
        if had_trailing_newline:
            out += "\n"
        return out

    if kind == "replace_line":
        if not isinstance(line, int) or line < 1 or line > len(lines):
            return None
        new_lines = lines[:line - 1] + text.split("\n") + lines[line:]
        out = "\n".join(new_lines)
        if had_trailing_newline:
            out += "\n"
        return out

    if kind == "delete_line":
        if not isinstance(line, int) or line < 1 or line > len(lines):
            return None
        new_lines = lines[:line - 1] + lines[line:]
        out = "\n".join(new_lines)
        if had_trailing_newline and new_lines:
            out += "\n"
        return out

    return None


# ═══ Prompt builders ════════════════════════════════════════════════
_MINIMAL_SYS = (
    "You are a surgical-edit assistant. The user's request is a "
    "SMALL-SCOPE change (one line to add / delete / replace, or a "
    "tiny prepend / append). Produce ONLY a JSON object describing "
    "the exact operation. NO commentary, NO code fences, NO "
    "explanation.\n\n"
    "Schema:\n"
    "  {\"op\": \"prepend\",           \"text\": \"...\"}\n"
    "  {\"op\": \"append\",            \"text\": \"...\"}\n"
    "  {\"op\": \"insert_after_line\", \"line\": <int, 1-based>, \"text\": \"...\"}\n"
    "  {\"op\": \"insert_before_line\", \"line\": <int, 1-based>, \"text\": \"...\"}\n"
    "  {\"op\": \"replace_line\",      \"line\": <int, 1-based>, \"text\": \"...\"}\n"
    "  {\"op\": \"delete_line\",       \"line\": <int, 1-based>}\n"
    "  {\"op\": \"not_expressible\"}   ← use ONLY when the change CANNOT "
    "                                 be expressed as a single line "
    "                                 operation on the given file.\n\n"
    "Rules:\n"
    "  • Line numbers are 1-based against the CURRENT FILE below.\n"
    "  • For prepend/append the `text` may include multiple lines "
    "    (\\n separated) but should be small (< 5 lines).\n"
    "  • If the request requires modifying more than one line at "
    "    a non-contiguous position, return {\"op\": \"not_expressible\"}.\n"
    "  • Return raw JSON only — no Markdown, no fences."
)


async def try_minimal_edit(
    *,
    user_message: str,
    plan: dict,
    path: str,
    current: str,
    user_id: Optional[str],
    call_llm_with_meta: Callable,
) -> Optional[dict]:
    """Return `{"content": <new file body>, "op": <op dict>}` on a
    clean surgical apply. Return `None` when the caller must fall
    back to the existing full-file-rewrite path.

    All failure modes (LLM decline, invalid JSON, op that doesn't
    apply cleanly, empty file for a `replace_line` request, etc.)
    are treated as `None` — the caller silently falls through to
    the full-rewrite path so no request ever fails outright because
    of the minimal-edit optimisation.
    """
    if not is_trivial_scope(user_message):
        return None
    if current is None:
        current = ""

    plan_bullets = "\n".join(f"- {b}" for b in (plan.get("bullets") or [])[:6])
    user_msg = (
        f"USER REQUEST:\n{user_message}\n\n"
        f"APPROVED PLAN:\n{plan.get('title','')}\n{plan_bullets}\n\n"
        f"FILE PATH: {path}\n"
        f"CURRENT FILE ({len(current)} bytes, {current.count(chr(10)) + 1} lines):\n"
        f"{current}\n"
        f"---\n"
        f"Emit ONE JSON object per the schema."
    )
    try:
        meta = await call_llm_with_meta(
            system=_MINIMAL_SYS,
            user=user_msg,
            max_tokens=600,
            mode="code",
            user_id=user_id,
            review_mode="pro",
        )
    except Exception as e:                              # noqa: BLE001
        logger.debug("[minimal-edit] LLM call failed for %s: %r", path, e)
        return None

    raw = ((meta or {}).get("content") or "").strip()
    # Some models add trivial noise around the JSON — strip common wraps.
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].lstrip()
    # Grab the first {...} block for robustness.
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        logger.debug("[minimal-edit] no JSON in response for %s: %s", path, raw[:120])
        return None
    try:
        op = json.loads(m.group(0))
    except Exception:
        logger.debug("[minimal-edit] malformed JSON for %s", path)
        return None

    if op.get("op") == "not_expressible":
        logger.info("[minimal-edit] LLM says not expressible for %s → full rewrite", path)
        return None

    new_content = _apply_op(current, op)
    if new_content is None:
        logger.info(
            "[minimal-edit] op=%r did not apply cleanly to %s → full rewrite",
            op.get("op"), path,
        )
        return None

    logger.info(
        "[minimal-edit] surgical apply for %s: op=%s (∆bytes=%+d)",
        path, op.get("op"), len(new_content) - len(current),
    )
    return {"content": new_content, "op": op}
