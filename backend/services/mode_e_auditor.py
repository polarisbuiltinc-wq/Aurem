"""
services/mode_e_auditor.py
Mode E — full-repo audit. Generates a structured priority report without
writing any code. The user can pick any item and trigger a Mode C task
to fix it.

Scans for:
  1. Security holes (hardcoded secrets, exposed keys, SQL injection risk)
  2. Critical bugs (unhandled exceptions, missing error handling)
  3. Tech debt (duplicate code patterns, long functions, TODO/FIXME)
  4. Performance issues (N+1 query patterns, missing indexes, wildcard
     imports)
  5. Quick wins (missing .env.example, no README, no requirements.txt)

Static regex passes run first (zero LLM cost). A single LLM call then
catches logic-level issues. Per-file content is capped at the first 200
lines to keep the prompt small; final report is capped at ~1200 tokens.
"""

from __future__ import annotations
import re
import asyncio
from datetime import datetime, timezone
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.llm import call_llm_with_meta
from services.ora_council_logger import log_conversational


# ─────────────────────────────────────────────────────────────────────────────
# Intent detection
# ─────────────────────────────────────────────────────────────────────────────

AUDIT_SIGNALS = [
    # Iter 162 audit-pass — these now require EXPLICIT audit/scan/review
    # intent. Removed the loose `what's wrong with`, bare `code quality`,
    # and bare `tech debt` patterns that were silently routing things
    # like "what's wrong with the build" (real debug request) and "the
    # tech debt is killing us" (venting conversation) through a full
    # repo audit. False positives like that produced a long expensive
    # report when the user wanted a one-line answer.
    r"\baudit\b(\s+my|\s+the|\s+this|\s+repo|\s+codebase|\s+code|\s+project)",
    r"\b(security\s+(audit|scan|review|check)|secrets?\s+(scan|leak\s+check))\b",
    r"\breview\s+(my|the|this)\s+(entire|whole|full)?\s*(code|repo|codebase|project)\b",
    r"\bscan\s+(my|the)\s+(repo|codebase|code|project)\b",
    r"\bcheck\s+(my\s+)?(entire|whole|full)\s+(repo|codebase)\b",
    r"\bfind\s+(all\s+)?(issues|bugs|problems|vulnerabilities)\s+in\b",
    r"\bvuln(erabilit(y|ies))?\s+(scan|check|audit)\b",
    r"\bhealth\s+check\b\s+(my\s+)?(repo|codebase|code)",
    r"\bowasp\b",
    r"\b(refactor|cleanup)\s+plan\s+for\s+(my|the)\s+(repo|codebase|project)\b",
]

AUDIT_PATTERN = re.compile("|".join(AUDIT_SIGNALS), re.IGNORECASE)


def is_audit_request(message: str) -> bool:
    return bool(AUDIT_PATTERN.search(message))


# ─────────────────────────────────────────────────────────────────────────────
# Static regex scanners (zero LLM cost)
# ─────────────────────────────────────────────────────────────────────────────

SECURITY_PATTERNS = [
    (r"""(?:password|secret|api_key|apikey|token|private_key)\s*=\s*['"][^'"]{6,}['"]""",
     "Hardcoded secret/credential", "critical"),
    (r"eval\s*\(", "Use of eval() — code injection risk", "high"),
    (r"subprocess\.call\s*\(.*shell\s*=\s*True", "shell=True in subprocess — injection risk", "high"),
    (r"SELECT\s+\*\s+FROM.*\+\s*\w+", "Possible SQL injection — string concat in query", "high"),
    (r"verify\s*=\s*False", "SSL verification disabled", "medium"),
    (r"DEBUG\s*=\s*True", "DEBUG=True in production config", "medium"),
    (r"0\.0\.0\.0.*allow_origins.*\*", "Wildcard CORS with 0.0.0.0 host", "medium"),
]

QUALITY_PATTERNS = [
    (r"except\s*:", "Bare except clause — catches everything including KeyboardInterrupt", "medium"),
    (r"print\s*\(", "print() statement (use logging instead in production)", "low"),
    (r"#\s*(TODO|FIXME|HACK|XXX)\b", "Unresolved TODO/FIXME", "low"),
    (r"time\.sleep\s*\(\s*[1-9]", "Blocking sleep in async context risk", "medium"),
    (r"\.append\(.*\)\s*\n.*\.append\(", "Repeated append pattern — consider list comprehension", "low"),
]

PERFORMANCE_PATTERNS = [
    (r"for\s+\w+\s+in.*:\s*\n\s*(await\s+)?db\.", "Possible N+1 query in loop", "high"),
    (r"import\s+\*\s+from", "Wildcard import — increases bundle size", "low"),
    (r"\.find\(\s*\{\s*\}\s*\)", "find({}) without limit — fetches entire collection", "high"),
    (r"json\.loads.*json\.dumps|json\.dumps.*json\.loads", "Redundant JSON encode/decode", "low"),
]


def static_scan_file(filepath: str, content: str) -> list[dict]:
    """Runs all regex patterns against one file. Returns list of findings."""
    findings = []
    lines = content.split("\n")
    ext = filepath.rsplit(".", 1)[-1].lower() if "." in filepath else ""

    all_patterns = []
    if ext in ("py",):
        all_patterns += SECURITY_PATTERNS + QUALITY_PATTERNS + PERFORMANCE_PATTERNS
    elif ext in ("js", "jsx", "ts", "tsx"):
        all_patterns += [
            (r"""(?:password|apiKey|api_key|secret|token)\s*[=:]\s*['"][^'"]{6,}['"]""",
             "Hardcoded secret in JS/TS", "critical"),
            (r"console\.log\s*\(", "console.log left in production code", "low"),
            (r"eval\s*\(", "eval() — code injection risk", "high"),
            (r"dangerouslySetInnerHTML", "dangerouslySetInnerHTML — XSS risk if unsanitised", "high"),
            (r"localStorage\.setItem.*(?:token|password|secret)", "Sensitive data in localStorage", "high"),
        ]

    for pattern, message, severity in all_patterns:
        for i, line in enumerate(lines, 1):
            if re.search(pattern, line, re.IGNORECASE):
                findings.append({
                    "filepath": filepath,
                    "line": i,
                    "message": message,
                    "severity": severity,
                    "snippet": line.strip()[:100],
                    "source": "static",
                })
                break  # one finding per pattern per file

    # Compound rule: dangerouslySetInnerHTML + eval() in same file
    if ext in ("js", "jsx", "ts", "tsx"):
        has_dangerous_html = any(
            f["message"].startswith("dangerouslySetInnerHTML") for f in findings
        )
        has_eval = any("eval()" in f["message"] for f in findings)
        if has_dangerous_html and has_eval:
            findings.append({
                "filepath": filepath,
                "line": 0,
                "message": "Compound risk: dangerouslySetInnerHTML + eval() in same file — chained XSS/code injection",
                "severity": "critical",
                "snippet": "",
                "source": "static_compound",
            })

    return findings


def static_scan_all(file_blocks: dict) -> list[dict]:
    """Scans all files. Returns all findings sorted by severity.
    Iter 50: merges Vanguard 007 findings (AWS/GitHub/Stripe/OpenAI/PEM/etc.)
    on top of our local regex pack so Mode E catches the same secrets
    the design linter blocks on Mode C."""
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_findings = []
    for filepath, content in file_blocks.items():
        all_findings.extend(static_scan_file(filepath, content))

    # Iter 50 — pull in Vanguard 007 catalog. Map its severity ladder
    # (CRITICAL/HIGH/MEDIUM) onto Mode E's lowercase one.
    try:
        from services.vanguard_scanner import scan_file_blocks as _vg_scan
        sev_map = {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium"}
        for f in _vg_scan(file_blocks):
            all_findings.append({
                "filepath": f.get("filepath", ""),
                "line":     f.get("line", 0),
                "severity": sev_map.get(f.get("severity", "HIGH"), "high"),
                "message":  f"Vanguard 007 — {f.get('name', 'unknown')}",
                "snippet":  f.get("snippet", "")[:160],
                "source":   "vanguard_007",
            })
    except Exception:
        pass

    all_findings.sort(key=lambda x: severity_order.get(x["severity"], 4))
    return all_findings


# ─────────────────────────────────────────────────────────────────────────────
# LLM deep audit (logic-level issues static can't find)
# ─────────────────────────────────────────────────────────────────────────────

AUDIT_SYSTEM = """You are a senior code auditor reviewing a production codebase.

Identify issues that STATIC ANALYSIS CANNOT FIND:
- Business logic bugs
- Missing authentication on routes that need it  
- Race conditions in async code
- Missing input validation
- Architectural problems (circular dependencies, god objects)
- Missing error handling for external API calls

Output format (strict, no other text):
ISSUE: <one-line description>
FILE: <filepath>
SEVERITY: critical|high|medium|low
FIX: <one-sentence fix>
---
(repeat for each issue, max 8 issues)"""


async def llm_deep_audit(file_summaries: str, repo_ctx: str) -> list[dict]:
    """LLM audit for logic-level issues. Returns list of issue dicts."""
    try:
        resp = await call_llm_with_meta(
            system=AUDIT_SYSTEM,
            user=f"Repo: {repo_ctx}\n\nCode summaries:\n{file_summaries[:3000]}",
            mode="chat",
            max_tokens=800,
        )
        raw = (resp or {}).get("content", "") if isinstance(resp, dict) else str(resp or "")
    except Exception:
        return []

    issues = []
    current = {}
    for line in raw.split("\n"):
        line = line.strip()
        if line == "---":
            if current.get("description"):
                issues.append(current)
            current = {}
        elif line.startswith("ISSUE:"):
            current["description"] = line.replace("ISSUE:", "").strip()
        elif line.startswith("FILE:"):
            current["filepath"] = line.replace("FILE:", "").strip()
        elif line.startswith("SEVERITY:"):
            current["severity"] = line.replace("SEVERITY:", "").strip().lower()
        elif line.startswith("FIX:"):
            current["fix"] = line.replace("FIX:", "").strip()
            current["source"] = "llm"

    if current.get("description"):
        issues.append(current)

    return issues[:8]


# ─────────────────────────────────────────────────────────────────────────────
# Quick wins checker
# ─────────────────────────────────────────────────────────────────────────────

def check_quick_wins(file_tree: list[str]) -> list[dict]:
    """Checks for missing standard files. No LLM, no file reading."""
    wins = []
    tree_lower = [f.lower() for f in file_tree]

    checks = [
        (".env.example", "No .env.example file — new devs won't know what env vars are needed", "medium"),
        ("readme.md", "No README.md — project has no documentation entry point", "medium"),
        ("requirements.txt", "No requirements.txt — Python dependencies not pinned", "high"),
        ("package.json", None, None),  # skip if no JS
        (".gitignore", "No .gitignore — secrets might be committed accidentally", "high"),
        ("dockerfile", "No Dockerfile — deployment not containerized", "low"),
    ]

    has_python = any(f.endswith(".py") for f in file_tree)
    has_js     = any(f.endswith((".js", ".jsx", ".ts", ".tsx")) for f in file_tree)

    for filename, message, severity in checks:
        if message is None:
            continue
        if filename == "requirements.txt" and not has_python:
            continue
        if filename == "package.json" and not has_js:
            continue
        if not any(filename in f for f in tree_lower):
            wins.append({
                "description": message,
                "filepath": filename,
                "severity": severity,
                "source": "quick_win",
                "fix": f"Create a `{filename}` file",
            })

    return wins


# ─────────────────────────────────────────────────────────────────────────────
# Report builder
# ─────────────────────────────────────────────────────────────────────────────

def build_audit_report(
    static_findings: list[dict],
    llm_findings: list[dict],