"""
services/task_diff.py
=====================
Iter 114 — Compute file-level diffs + collect popup data for live task UI.

Public helpers:
  build_files_read(contents)           → [{name, lines_count}]
  build_files_changed(before, after)   → [{name, lines_added, lines_removed,
                                           line_number, old_value, new_value}]
  shape_vanguard_findings(findings)    → [{rule, file, line, severity, status}]
"""
from __future__ import annotations


def _lines(s: str | None) -> list[str]:
    if not s:
        return []
    return s.splitlines()


def build_files_read(contents: dict[str, str | None]) -> list[dict]:
    """Build the live-tape "files read" list. Only counts files we
    actually received (non-None body)."""
    out: list[dict] = []
    for name, body in (contents or {}).items():
        if body is None:
            continue
        out.append({
            "name":        name,
            "lines_count": len(_lines(body)),
        })
    return out


def build_files_changed(
    before: dict[str, str | None],
    after:  dict[str, str | None],
) -> list[dict]:
    """For each file in `after`, compare line-by-line with `before` and
    emit:
        - lines_added   : count of lines added
        - lines_removed : count of lines removed
        - line_number   : 1-based line of the first differing line
                          (None when the file is brand-new)
        - old_value     : the old line at line_number  (None when new file)
        - new_value     : the new line at line_number  (None when deleted)
    """
    rows: list[dict] = []
    for name, new_body in (after or {}).items():
        new_lines = _lines(new_body)
        old_body  = (before or {}).get(name)
        old_lines = _lines(old_body)

        # Quick raw add/remove counts (independent of first-diff line)
        # — use set semantics within a line index to keep this O(n).
        added   = max(0, len(new_lines) - len(old_lines))
        removed = max(0, len(old_lines) - len(new_lines))
        # For matching-length region, refine: count lines that actually changed
        common = min(len(old_lines), len(new_lines))
        for i in range(common):
            if old_lines[i] != new_lines[i]:
                added   += 1
                removed += 1

        first_diff_idx = None
        old_value     = None
        new_value     = None
        if old_body is None:
            # Brand-new file → no "line that changed" — use first line
            if new_lines:
                first_diff_idx = 1
                new_value      = new_lines[0]
        else:
            for i in range(max(len(old_lines), len(new_lines))):
                a = old_lines[i] if i < len(old_lines) else None
                b = new_lines[i] if i < len(new_lines) else None
                if a != b:
                    first_diff_idx = i + 1
                    old_value      = a
                    new_value      = b
                    break

        rows.append({
            "name":          name,
            "lines_added":   added,
            "lines_removed": removed,
            "line_number":   first_diff_idx,
            "old_value":     (old_value[:240] if old_value else None),
            "new_value":     (new_value[:240] if new_value else None),
        })
    return rows


def shape_vanguard_findings(findings, status: str = "blocked") -> list[dict]:
    """Slim each Vanguard finding to the popup contract.
    `status` is "blocked" when verify_patch failed, "fixed" when a
    later reviewer rewrite resolved the issue, "clean" for synthetics."""
    out: list[dict] = []
    for f in (findings or []):
        out.append({
            "rule":     (f.get("rule") or f.get("name") or f.get("type") or "unknown")[:64],
            "file":     (f.get("file") or "")[:120],
            "line":     f.get("line"),
            "severity": (f.get("severity") or "MEDIUM").upper()[:12],
            "status":   status,
        })
    return out



# ──────────────────────────────────────────────────────────────────────
# Iter 388g — Unified-diff hunks for inline ORA diff view
# ──────────────────────────────────────────────────────────────────────
# Existing `build_files_changed` above emits ONE line per file (first
# diff line only) — perfect for the compact Iter 114 side popup.
# The Path A ORA diff bubble needs full unified-diff hunks with per-
# line old_n/new_n gutter numbers.  This helper produces that shape
# WITHOUT touching `build_files_changed` so both consumers stay stable.
def build_unified_diff_hunks(
    before: str | None,
    after:  str | None,
    *,
    context: int = 2,
) -> list[dict]:
    """Compute unified-diff hunks between two file contents.

    Returns a list of hunks:
        [{
          "old_start": int,       # 1-based line where hunk begins (old)
          "new_start": int,       # 1-based line where hunk begins (new)
          "lines": [
            {"tag": " ", "text": "...", "old_n": 3, "new_n": 3},
            {"tag": "-", "text": "...", "old_n": 4, "new_n": None},
            {"tag": "+", "text": "...", "old_n": None, "new_n": 4},
            ...
          ],
        }, ...]

    `context` is the number of unchanged context lines to keep around
    each change block (matches `diff -u` default of 3, tuned down to 2
    for chat-bubble compactness).
    """
    old_lines = _lines(before)
    new_lines = _lines(after)

    # difflib's SequenceMatcher gives us opcodes; we translate to hunks.
    from difflib import SequenceMatcher
    sm = SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)

    hunks: list[dict] = []
    current: dict | None = None
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            # Bridge unchanged rows between change blocks — keep only
            # `context` before/after so hunks stay tight.
            length = i2 - i1
            if current is None:
                # Only trailing context of the previous hunk needed —
                # but we haven't opened one yet.
                lead = old_lines[max(i1, i2 - context): i2]
                lead_j = new_lines[max(j1, j2 - context): j2]
                # Track for the NEXT hunk's leading context.
                current = None  # noqa: F841 — clarity
                # Nothing to emit; the leading-context lines will be
                # picked up when the next change opens a hunk.
                continue
            # Extend the current hunk with up to `context` bridging
            # rows, then close.
            take = min(length, context)
            for k in range(take):
                current["lines"].append({
                    "tag":   " ",
                    "text":  old_lines[i1 + k],
                    "old_n": i1 + k + 1,
                    "new_n": j1 + k + 1,
                })
            # If the equal block is longer than 2*context, close this
            # hunk here — next change opens a fresh one.
            if length > context * 2:
                hunks.append(current)
                current = None
            elif length > context:
                # Bridge fully consumed → still close.
                hunks.append(current)
                current = None
        else:
            # Any change → open a hunk (with leading context) if none.
            if current is None:
                lead_i = max(0, i1 - context)
                lead_j = max(0, j1 - context)
                current = {
                    "old_start": lead_i + 1,
                    "new_start": lead_j + 1,
                    "lines":     [],
                }
                for k in range(lead_i, i1):
                    current["lines"].append({
                        "tag":   " ",
                        "text":  old_lines[k],
                        "old_n": k + 1,
                        "new_n": lead_j + (k - lead_i) + 1,
                    })
            # Emit removals then insertions (matches unified-diff order).
            for k in range(i1, i2):
                current["lines"].append({
                    "tag":   "-",
                    "text":  old_lines[k],
                    "old_n": k + 1,
                    "new_n": None,
                })
            for k in range(j1, j2):
                current["lines"].append({
                    "tag":   "+",
                    "text":  new_lines[k],
                    "old_n": None,
                    "new_n": k + 1,
                })
    if current is not None:
        hunks.append(current)
    return hunks
