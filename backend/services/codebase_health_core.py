"""
services/codebase_health_core.py — Iter arch-2a boundary-violation fix

Relocated VERBATIM from routers/codebase_health.py (no logic changes).
These five category scanners + shared scoring helpers are PURE
functions (no FastAPI dependency, no router-only state) that operate
on a pre-fetched `{path: text}` cache. They previously lived inside
the router file, which meant `services/project_onboarding_scan.py`
had to import them FROM a router — an inverted (service→router)
dependency flagged by `services/architecture_health.py`'s boundary
scan. Moving them here lets both the router (its own /scan endpoint)
and the service (onboarding auto-scan) import from the same
service-layer module, with the router now importing FROM services —
the correct direction.
"""
from __future__ import annotations

import re

from services.vanguard_scanner import scan_text
from services.bug_hunt_rules import scan_bug_hunt
from services.full_scan_scanners import (
    scan_docker_cis as _scan_docker_cis_service,
    scan_http_headers as _scan_http_headers_service,
)
from services.scanner_utils import (
    is_scanner_rule_file as _is_scanner_rule_file,
    _SCANNER_RULE_FILES,
)


def _is_dockerfile(lower_path: str) -> bool:
    """True if `lower_path` looks like a real Dockerfile or Docker
    Compose manifest.  Case-insensitive; assumes caller has already
    lowered the path.

    Matches:
        Dockerfile, dockerfile, Dockerfile.prod, dockerfile.dev,
        <anything>/Dockerfile, docker-compose.yml, docker-compose.yaml,
        compose.yml, compose.yaml
    Does NOT match:
        docs/dockerfile-cheatsheet.md, my-dockerfile-notes.txt
    """
    if not lower_path:
        return False
    base = lower_path.rsplit("/", 1)[-1]
    if base == "dockerfile" or base.startswith("dockerfile."):
        return True
    if base in {"docker-compose.yml", "docker-compose.yaml",
                "compose.yml", "compose.yaml"}:
        return True
    return False


# ──────────────────────────────────────────────────────────────────────
# Category 1 — Security (delegates to Vanguard scan_text catalog)
# ──────────────────────────────────────────────────────────────────────
def _scan_security(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in text_cache.items():
        # Iter 212m-224 — skip scanner-definition files (self-ref false pos).
        if _is_scanner_rule_file(path):
            continue
        # Iter 212m-229 — Skip `.env` and `.env.*` files. Keys living
        # there are INTENTIONAL by construction (they're in
        # `.gitignore`) — flagging them creates recurring CRITICAL
        # noise that trains the reader to ignore real critical alerts.
        low = path.replace("\\", "/").lower()
        if (low == ".env" or low.endswith("/.env")
                or "/.env." in low
                or (low.split("/")[-1] if "/" in low else low).startswith(".env.")):
            continue
        # Iter 212m-229 — QA seed / simulated-user harness path
        # legitimately contains hard-coded test creds (JWT secret
        # signing keys used by our own integration bot). Downgrade
        # to INFO like other demo paths.
        for f in scan_text(text or "", filepath=path):
            sev = (f.get("severity") or "").upper()
            if "qa/simulated-user/" in path.replace("\\", "/").lower():
                # Same treatment as _is_safe_demo_path — surface for
                # review but never as CRITICAL/HIGH.
                if sev in ("CRITICAL", "HIGH"):
                    sev = "INFO"
                    f["downgraded"] = True
                    f["downgrade_reason"] = "qa harness — intentional test creds"
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
        "eval_usage":        "Replace eval() with `ast.literal_eval` or an explicit parser.",
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
    (re.compile(
        # Iter 212m-228 — Tighter N+1 detection:
        #   • REQUIRE the loop body to contain an ACTUAL per-item lookup
        #     (find/find_one/count with a bareword arg — NOT aggregate,
        #     to_list, or update).  Aggregation cursors and bulk ops are
        #     not N+1.
        #   • REQUIRE the db call to be indented (at least 4 spaces) so
        #     it's genuinely inside the loop body, not a sibling.
        #   • EXCLUDE `for X in await db.Y.aggregate(...)` / `.find(...).to_list()` —
        #     those are bulk iterations of an already-fetched batch.
        # This eliminates the false-positive avalanche where a `for` in
        # dict-comprehension near an unrelated await was miscounted.
        r'\bfor\s+\w+\s+in\s+(?!.*\b(?:aggregate|to_list|find\s*\()).*:'
        r'[^\n]*\n(?:[^\n]*\n){0,10}?'
        r'[ \t]{8,}(?:_?[a-zA-Z]\w*\s*=\s*)?await\s+\w*db\.\w+\.(?:find|find_one|count(?:_documents)?)\s*\('
     ),
     "n_plus_one",       "HIGH",
     "Database call inside a for-loop — collapse with `$in` batch query."),
]

def _scan_performance(text_cache: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    for path, text in text_cache.items():
        # Iter 212m-227 — Skip scanner rule-definition files. The
        # fix-hint strings inside them literally spell out patterns
        # like `.to_list(None)` and get flagged by the perf regex.
        if _is_scanner_rule_file(path):
            continue
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
                })
        # 2. Hard caps >2000 with no pagination
        for m in re.finditer(r'\.to_list\((\d{4,})\)', text):
            cap = int(m.group(1))
            if cap >= 2000:
                line = text[:m.start()].count("\n") + 1
                out.append({
                    "id":       f"db::{path}:{line}:hard_cap_{cap}",
                    "category": "database",
                    "severity": _norm_sev("MEDIUM"),
                    "file":     path, "line": line,
                    "title":    f"hard_cap_{cap}",
                    "message":  f"Loading {cap} documents at once — one request can kill the DB.",
                    "fix_hint": "Accept ?page=&limit= query params instead.",
                    "fix_tokens": 5,
                })
        # 3. No TTL on session/log/etc indices — detect collections
        # mentioned without an `expireAfterSeconds`. Best-effort heuristic.
        for m in re.finditer(r'await\s+db\.(\w*(?:session|log|temp|cache)\w*)\.insert', text):
            coll = m.group(1)
            line = text[:m.start()].count("\n") + 1
            # only flag once per file+collection
            key = f"db::{path}:0:no_ttl_{coll}"
            if any(o["id"] == key for o in out):
                continue
            out.append({
                "id":       key,
                "category": "database",
                "severity": _norm_sev("LOW"),
                "file":     path, "line": line,
                "title":    f"no_ttl_{coll}",
                "message":  f"`{coll}` writes detected — confirm a TTL index exists.",
                "fix_hint": f'Add `await db.{coll}.create_index("created_at", expireAfterSeconds=86400)`.',
                "fix_tokens": 5,
            })
    return out


# ──────────────────────────────────────────────────────────────────────
# Category 6 — Docker CIS Benchmark
# Iter 212m-190 — Rule bodies extracted to
# `services/full_scan_scanners.py` so Loop-Mode Full Scan can call
# them without importing the router layer. This wrapper preserves
# the historical `_scan_docker_cis(text_cache)` call site.
# ──────────────────────────────────────────────────────────────────────
def _scan_docker_cis(text_cache: dict[str, str]) -> list[dict]:
    return _scan_docker_cis_service(text_cache)


# ──────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────
def _norm_sev(sev: str) -> str:
    s = (sev or "").upper()
    if s == "WARNING":
        return "medium"
    return s.lower() if s in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"} else "medium"


SEV_WEIGHTS = {"critical": 25, "high": 8, "medium": 3, "low": 1, "info": 0}


def _score_for_findings(findings: list[dict]) -> int:
    """0-100 health score using a diminishing-returns curve.

    Iter 212m-164 — replaced the old linear `100 - sum(weights)` formula
    which cliff-edged at 4 criticals (4 × 25 = 100 → score 0).  Real
    repos routinely surface 5-15 critical findings on first scan and
    the founder needs to see the score MOVE as they fix issues, not
    sit stuck at 0.

    New curve:  score = round(100 · exp(-raw / 60))

    Reference points:
        0  issues   → score 100  (HEALTHY)
        5  medium   → raw 15  → score 78  (GOOD)
        5  high     → raw 40  → score 51  (NEEDS ATTENTION)
        2  critical → raw 50  → score 44  (NEEDS ATTENTION)
        4  critical → raw 100 → score 19  (CRITICAL RISK)
        9  critical → raw 225 → score 2   (CRITICAL RISK)

    The curve preserves severity ordering (criticals still dominate)
    but every fix produces a visible score delta, which is the whole
    point of a health gauge during pre-launch.
    """
    raw = sum(SEV_WEIGHTS.get(f.get("severity") or "low", 0) for f in findings)
    if raw <= 0:
        return 100
    import math
    return max(0, min(100, round(100 * math.exp(-raw / 60))))


def _category_label(score: int) -> tuple[str, str]:
    # Iter 212m-164 — thresholds re-tuned for the diminishing-returns
    # curve so the bands still split the score space roughly into
    # quarters under the new compression.
    if score <  20:
        return ("CRITICAL RISK",     "critical")
    if score <  50:
        return ("NEEDS ATTENTION",   "warn")
    if score <= 80:
        return ("GOOD",              "good")
    return                  ("HEALTHY",           "healthy")


SCANNERS = {
    "security":     _scan_security,
    "performance":  _scan_performance,
    "code_quality": _scan_code_quality,
    "dependencies": _scan_dependencies,
    "database":     _scan_database,
    "bug_hunt":     scan_bug_hunt,
    "docker":       _scan_docker_cis,
}
