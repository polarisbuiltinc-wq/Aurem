"""
routers/codebase_health.py  —  Iter 212m-72 (Phase 2)
=====================================================
Five-category codebase health scanner.  Founder-facing endpoint
powering `/codebase-health` dashboard.

Endpoints (all founder-gated, project_id required):

  POST /api/aurem-dev/codebase-health/scan
       Body: { project_id, categories: [security, performance,
                                       code_quality, dependencies,
                                       database] }
       Returns: { score: 0-100, summary, breakdown: {<cat>: {...}} }

  POST /api/aurem-dev/codebase-health/fix
       Body: { project_id, finding_id, category }
       Creates a `cto_task` with the fix prompt + auto-runs it
       through the existing Loop pipeline.  Returns task_id.

All five category scanners are PURE deterministic static analysers
that walk the user's connected GitHub repo via the existing
`_list_repo_tree` + `_fetch_file` helpers from `security_scan.py`.
Zero LLM cost on the scan path — only the Fix button pays an LLM call
when the user actually wants ORA to write the patch.

The repo walk + file fetch is the shared bottleneck across all 5
scanners, so `scan()` does it ONCE then dispatches the cached
`{path: text}` dict to each requested category.  This means a
"Full scan" (all 5) costs the same GitHub-API budget as a single
category — just 4× more CPU on the user's static analysis.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import JSONResponse

from cto_services.auth import current_dev
from cto_services.db import get_db
from routers.security_scan import (
    _decrypt_pat, _list_repo_tree, _list_repo_tree_with_sha, _fetch_file,
    _MAX_FILES, _MAX_BYTES_PER_FILE, _SCAN_EXTS, _SKIP_DIRS,
    _CONCURRENT_FETCHES,
)
from services.vanguard_scanner import scan_text
from services.bug_hunt_rules import scan_bug_hunt
from services.scan_cache import (
    get_cached_text_cache, put_cached_text_cache,
)

router = APIRouter(prefix="/codebase-health", tags=["Codebase Health"])
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Category 1 — Security (delegates to Vanguard scan_text catalog)
# ──────────────────────────────────────────────────────────────────────
def _scan_security(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in text_cache.items():
        for f in scan_text(text or "", filepath=path):
            sev = (f.get("severity") or "").upper()
            out.append({
                "id":         f"sec::{path}:{f.get('line')}:{f.get('name')}",
                "category":   "security",
                "severity":   _norm_sev(sev),
                "file":       path,
                "line":       int(f.get("line") or 0),
                "title":      f.get("name") or "security_issue",
                "message":    f.get("desc") or f.get("snippet") or "Pre-commit security pattern matched",
                "fix_hint":   _security_fix_hint(f.get("name") or ""),
                "fix_tokens": 5,
            })
    return out


def _security_fix_hint(rule_id: str) -> str:
    mapping = {
        "secret_openai_key": "Move TOKEN to an environment variable and rotate the leaked key immediately.",
        "secret_github_pat": "Revoke this PAT, regenerate with fine-grained scopes, store in .env.",
        "sql_string_format": 'Use parameterised queries: cursor.execute("... WHERE id=%s", (uid,))',
        "eval_usage":        "Replace the dynamic-eval builtin with `ast.literal_eval` or an explicit parser.",
        "exec_usage":        "Replace exec() with a safer dispatch (dict of callables).",
        "requests_no_verify":"Remove `verify=False` — fix the certificate chain instead.",
        "innerHTML_assignment": "Use textContent or a sanitiser (DOMPurify / xss).",
        "dangerously_set_html": "Sanitise the HTML via DOMPurify before injection.",
    }
    return mapping.get(rule_id, "Apply the standard secure-coding pattern for this rule.")


# ──────────────────────────────────────────────────────────────────────
# Category 2 — Performance
# ──────────────────────────────────────────────────────────────────────
_PERF_RULES: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r'\.to_list\(None\)'),
     "unbounded_tolist", "HIGH",
     "Cursor returns ALL documents — will crash as the collection grows."),
    (re.compile(r'\.to_list\((\d{4,})\)'),
     "high_cap_tolist",  "MEDIUM",
     "Hard cap >= 1000 docs in memory.  Add pagination."),
    (re.compile(r'\.find_one\(\s*\{[^{}]+\}\s*\)'),
     "select_star",      "LOW",
     "find_one without projection — fetches every field.  Add `, {'_id': 0, ...}`."),
    (re.compile(r'for\s+\w+\s+in\s+[^:]+:[\s\S]{0,300}?await\s+\w*db\.\w+\.(find|find_one|count)'),
     "n_plus_one",       "HIGH",
     "Database call inside a for-loop — collapse with `$in` batch query."),
]

def _scan_performance(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in text_cache.items():
        if not (path.endswith(".py") or path.endswith(".js")
                or path.endswith(".jsx") or path.endswith(".ts")
                or path.endswith(".tsx")):
            continue
        for rx, rid, sev, desc in _PERF_RULES:
            for m in rx.finditer(text or ""):
                line = (text[:m.start()].count("\n") + 1) if text else 0
                out.append({
                    "id":         f"perf::{path}:{line}:{rid}",
                    "category":   "performance",
                    "severity":   _norm_sev(sev),
                    "file":       path,
                    "line":       line,
                    "title":      rid,
                    "message":    desc,
                    "fix_hint":   _perf_fix_hint(rid),
                    "fix_tokens": 5,
                })
    return out


def _perf_fix_hint(rid: str) -> str:
    return {
        "unbounded_tolist":
            "Replace `.to_list(None)` with `.skip(skip).limit(limit).to_list(limit)`.",
        "high_cap_tolist":
            "Use page/limit query params instead of a hard-coded cap.",
        "select_star":
            "Add a projection arg: `{'_id': 0, 'field_a': 1, 'field_b': 1}`.",
        "n_plus_one":
            "Collect the keys first, then do ONE `$in` batch query.",
    }.get(rid, "")


# ──────────────────────────────────────────────────────────────────────
# Category 3 — Code Quality
# ──────────────────────────────────────────────────────────────────────
_LARGE_FN_THRESHOLD = 80   # lines of body
_LARGE_FILE_THRESHOLD = 1000

def _scan_code_quality(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    fn_re = re.compile(r'^\s*(?:async\s+)?def\s+(\w+)\s*\(', re.MULTILINE)
    todo_re = re.compile(r'#\s*(?:TODO|FIXME|HACK|XXX)\b', re.IGNORECASE)
    bare_except = re.compile(r'^\s*except\s*:\s*$', re.MULTILINE)

    for path, text in text_cache.items():
        if not text:
            continue
        n_lines = text.count("\n") + 1
        if n_lines > _LARGE_FILE_THRESHOLD and (
            path.endswith(".py") or path.endswith(".jsx")
            or path.endswith(".tsx") or path.endswith(".js")
        ):
            out.append({
                "id":       f"q::{path}:0:large_file",
                "category": "code_quality",
                "severity": _norm_sev("MEDIUM"),
                "file":     path, "line": 0,
                "title":    "large_file",
                "message":  f"{n_lines} lines — ORA cannot refactor this safely.",
                "fix_hint": "Split into focused modules of <500 lines each.",
                "fix_tokens": 5,
            })
        # TODO comments
        for m in todo_re.finditer(text):
            line = text[:m.start()].count("\n") + 1
            out.append({
                "id":       f"q::{path}:{line}:todo",
                "category": "code_quality",
                "severity": _norm_sev("LOW"),
                "file":     path, "line": line,
                "title":    "todo_comment",
                "message":  "Open TODO/FIXME — easy to forget.",
                "fix_hint": "Either fix now or convert to a GitHub issue.",
                "fix_tokens": 3,
            })
        # Bare except (Python only)
        if path.endswith(".py"):
            for m in bare_except.finditer(text):
                line = text[:m.start()].count("\n") + 1
                out.append({
                    "id":       f"q::{path}:{line}:bare_except",
                    "category": "code_quality",
                    "severity": _norm_sev("MEDIUM"),
                    "file":     path, "line": line,
                    "title":    "bare_except",
                    "message":  "Bare `except:` swallows KeyboardInterrupt + SystemExit too.",
                    "fix_hint": "Catch `Exception` and log the traceback.",
                    "fix_tokens": 5,
                })
        # Large functions (Python only — JS is harder without an AST)
        if path.endswith(".py"):
            fn_starts = [(m.start(), m.group(1)) for m in fn_re.finditer(text)]
            for i, (start, name) in enumerate(fn_starts):
                end = fn_starts[i+1][0] if i+1 < len(fn_starts) else len(text)
                body_lines = text[start:end].count("\n")
                if body_lines > _LARGE_FN_THRESHOLD:
                    line = text[:start].count("\n") + 1
                    out.append({
                        "id":       f"q::{path}:{line}:large_fn",
                        "category": "code_quality",
                        "severity": _norm_sev("MEDIUM"),
                        "file":     path, "line": line,
                        "title":    "large_function",
                        "message":  f"`{name}` is {body_lines} lines — too big to test/refactor.",
                        "fix_hint": "Split into smaller helpers; each function should do one thing.",
                        "fix_tokens": 5,
                    })
    return out


# ──────────────────────────────────────────────────────────────────────
# Category 4 — Dependencies
# ──────────────────────────────────────────────────────────────────────
# Hard-coded CVE map for known-vulnerable versions. In a future iter
# this should hit the OSV.dev API live.
_CVE_DB: dict[str, list[tuple[str, str, str]]] = {
    # package: [(matched_version_prefix, CVE_id, severity)]
    "requests":  [("2.28.", "CVE-2023-32681", "HIGH")],
    "fastapi":   [("0.95.", "outdated", "MEDIUM")],
    "pyjwt":     [("1.",    "CVE-2022-29217", "CRITICAL")],
    "axios":     [("0.21.", "CVE-2021-3749",  "HIGH")],
    "lodash":    [("4.17.20", "CVE-2021-23337", "HIGH")],
    "next":      [("13.4.", "CVE-2024-46982", "HIGH")],
    "vite":      [("4.",    "outdated", "LOW")],
}


def _scan_dependencies(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    # requirements.txt
    for path, text in text_cache.items():
        if path.endswith("requirements.txt") or path.endswith("requirements-dev.txt"):
            for i, line in enumerate((text or "").splitlines(), start=1):
                m = re.match(r'^\s*([A-Za-z0-9_\-]+)\s*[=<>]+\s*([0-9][0-9A-Za-z.\-]*)', line)
                if not m:
                    continue
                pkg, ver = m.group(1).lower(), m.group(2)
                for prefix, cve, sev in _CVE_DB.get(pkg, []):
                    if ver.startswith(prefix):
                        out.append(_dep_finding(path, i, pkg, ver, cve, sev))
                        break
        if path.endswith("package.json"):
            try:
                import json
                pkg_json = json.loads(text or "{}")
                deps = {**(pkg_json.get("dependencies") or {}),
                        **(pkg_json.get("devDependencies") or {})}
                for pkg, ver_raw in deps.items():
                    ver = re.sub(r'^[^\d]*', '', str(ver_raw))
                    for prefix, cve, sev in _CVE_DB.get(pkg.lower(), []):
                        if ver.startswith(prefix):
                            out.append(_dep_finding(path, 0, pkg, ver, cve, sev))
                            break
            except Exception:
                pass
    return out


def _dep_finding(path: str, line: int, pkg: str,
                 ver: str, cve: str, sev: str) -> dict:
    msg_extra = (
        f"{cve} — upgrade required."
        if cve.startswith("CVE-")
        else "This version is past EOL and missing security patches."
    )
    return {
        "id":         f"dep::{path}:{line}:{pkg}",
        "category":   "dependencies",
        "severity":   _norm_sev(sev),
        "file":       path, "line": line,
        "title":      f"vulnerable: {pkg}=={ver}",
        "message":    f"{pkg}=={ver} → {msg_extra}",
        "fix_hint":   f"Bump `{pkg}` to the latest stable release.",
        "fix_tokens": 5,
    }


# ──────────────────────────────────────────────────────────────────────
# Category 5 — Database
# ──────────────────────────────────────────────────────────────────────
def _scan_database(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in text_cache.items():
        if not path.endswith(".py") or not text:
            continue
        # 1. AsyncIOMotorClient without pool config
        for m in re.finditer(r'AsyncIOMotorClient\([^)]*\)', text):
            inner = m.group(0)
            line = text[:m.start()].count("\n") + 1
            if ("maxPoolSize" not in inner) or ("maxIdleTimeMS" not in inner):
                out.append({
                    "id":       f"db::{path}:{line}:no_pool",
                    "category": "database",
                    "severity": _norm_sev("HIGH"),
                    "file":     path, "line": line,
                    "title":    "no_connection_pool",
                    "message":  "Motor client missing pool config — starves under traffic.",
                    "fix_hint": "Add maxPoolSize=50, minPoolSize=5, maxIdleTimeMS=30_000, connectTimeoutMS=10_000.",
                    "fix_tokens": 5,