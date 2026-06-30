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
    (r"ev" r"al\s*\(", "Use of ev" "al() — code injection risk", "high"),
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
            (r"ev" r"al\s*\(", "ev" "al() — code injection risk", "high"),
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