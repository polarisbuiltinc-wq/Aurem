"""duplication_scanner.py — code-duplication detection via jscpd (2026-08-26).

Phase 3a research found NO existing duplication tool in this repo (radon
does Halstead/complexity, not duplication). `jscpd` is a real, free,
multi-language (Python + JS/JSX) CLI — installed on demand via `npx`
(no new package.json/requirements.txt dependency to maintain). Runs in
~1-2s on the whole backend+frontend source tree, so it's scanned live
on every call (same posture as architecture_health.run_health_report()),
no separate trigger-and-persist step needed.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCAN_PATHS = [
    "backend/routers", "backend/services", "backend/core", "backend/cto_services",
    "frontend/src",
]
_IGNORE = (
    "**/tests/**,**/__pycache__/**,**/*.pyc,**/node_modules/**,"
    "**/*.test.js,**/__tests__/**,**/build/**,**/dist/**"
)


def run_duplication_scan(min_lines: int = 5, min_tokens: int = 50) -> dict:
    """Blocking — call via asyncio.to_thread from an async handler."""
    started = time.time()
    with tempfile.TemporaryDirectory() as out_dir:
        try:
            proc = subprocess.run(
                ["npx", "--yes", "jscpd@5.0.16", *_SCAN_PATHS,
                 "--min-lines", str(min_lines), "--min-tokens", str(min_tokens),
                 "--ignore", _IGNORE,
                 "--reporters", "json", "--output", out_dir, "--silent"],
                cwd=_REPO_ROOT, capture_output=True, text=True, timeout=60,
            )
        except Exception as e:
            return {"ok": False, "reason": f"jscpd invocation failed: {e!r}"}
        report_path = os.path.join(out_dir, "jscpd-report.json")
        if not os.path.exists(report_path):
            return {"ok": False, "reason": "no jscpd report produced",
                     "stderr": (proc.stderr or "")[-500:]}
        try:
            with open(report_path) as fh:
                raw = json.load(fh)
        except Exception as e:
            return {"ok": False, "reason": f"could not parse jscpd report: {e!r}"}

    total = raw.get("statistics", {}).get("total", {})
    clusters = []
    for dup in raw.get("duplicates", [])[:200]:
        a, b = dup.get("firstFile", {}), dup.get("secondFile", {})
        clusters.append({
            "format":     dup.get("format"),
            "lines":      (a.get("end", 0) - a.get("start", 0)),
            "file_a":     a.get("name"),
            "file_a_range": [a.get("start"), a.get("end")],
            "file_b":     b.get("name"),
            "file_b_range": [b.get("start"), b.get("end")],
        })
    clusters.sort(key=lambda c: c["lines"], reverse=True)

    return {
        "ok": True,
        "generated_at": started,
        "duration_ms": int((time.time() - started) * 1000),
        "duplicated_lines": total.get("duplicatedLines", 0),
        "total_lines": total.get("lines", 0),
        "duplication_pct": round(total.get("percentage", 0.0), 2),
        "clone_count": total.get("clones", 0),
        "files_scanned": total.get("sources", 0),
        "top_clusters": clusters[:20],
    }
