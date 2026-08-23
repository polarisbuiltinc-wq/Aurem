"""
services/security_text_scanner.py — Iter arch-2a boundary-violation fix

Relocated VERBATIM from routers/security_scan.py (no logic changes).
`_scan_text` is a pure regex-rule matcher used by both the security-scan
router AND `services/loop_engine.py` (ship-time inline scan + diff-only
scan). loop_engine previously imported it FROM the router — an inverted
(service→router) dependency flagged by `services/architecture_health.py`'s
boundary scan. Moving the rule table + matcher here fixes that once and
for all (no more "restore the missing _scan_text import" incidents).
"""
from __future__ import annotations

import re

# ─── Static rule library — each rule is a (id, severity, pattern) ───
# Severity: critical / high / medium / low — purely advisory; the UI
# colour-codes by this. Patterns are kept tight (anchors, word
# boundaries) to keep false-positive rate down on real-world code.

_RULES: list[dict] = [
    # ── Secret-key leak ──
    {"id": "secret_aws_access_key",   "vuln": "secret_leak", "severity": "critical",
     "pattern": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
     "desc": "Hardcoded AWS access key id"},
    {"id": "secret_openai_key",       "vuln": "secret_leak", "severity": "critical",
     "pattern": re.compile(r"\bsk-[a-zA-Z0-9]{32,}\b"),
     "desc": "Hardcoded OpenAI / DeepSeek style API key"},
    {"id": "secret_github_pat",       "vuln": "secret_leak", "severity": "critical",
     "pattern": re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
     "desc": "Hardcoded GitHub Personal Access Token"},
    {"id": "secret_stripe_live",      "vuln": "secret_leak", "severity": "critical",
     "pattern": re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b"),
     "desc": "Hardcoded Stripe LIVE secret key"},
    {"id": "secret_private_key",      "vuln": "secret_leak", "severity": "critical",
     "pattern": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)?PRIVATE KEY-----"),
     "desc": "Embedded private key block"},

    # ── SSTI ──
    {"id": "ssti_jinja_user_render",  "vuln": "ssti", "severity": "high",
     "pattern": re.compile(r"Template\(\s*request\.|Template\(\s*body\.|render_template_string\("),
     "desc": "Server-side template render of user-controlled input"},

    # ── SQL injection ──
    {"id": "sql_string_format",       "vuln": "sql_injection", "severity": "critical",
     "pattern": re.compile(r"""(execute|executemany)\s*\(\s*[fF]?["'][^"']*\{[^}]+\}"""),
     "desc": "f-string SQL query — use parameterised cursors"},
    {"id": "sql_percent_format",      "vuln": "sql_injection", "severity": "high",
     "pattern": re.compile(r"""(execute|executemany)\s*\(\s*["'][^"']*%s[^"']*["']\s*%\s*"""),
     "desc": "%-format SQL query — use cursor.execute(query, params)"},

    # ── NoSQL injection ──
    {"id": "nosql_where_operator",    "vuln": "nosql_injection", "severity": "high",
     "pattern": re.compile(r"""["']\$where["']\s*:"""),
     "desc": "MongoDB $where allows arbitrary JS execution"},
    {"id": "nosql_raw_body_query",    "vuln": "nosql_injection", "severity": "medium",
     "pattern": re.compile(r"""\.find\(\s*(request\.json|body\.dict|body\.\*\*|\*\*body|\*\*payload)"""),
     "desc": "Mongo query built from raw request body"},

    # ── ReDoS — known catastrophic patterns ──
    {"id": "redos_nested_quantifier", "vuln": "redos", "severity": "high",
     "pattern": re.compile(r"""re\.(compile|match|search|sub)\s*\(\s*r?["'][^"']*\([^)]*[+*][^)]*\)[+*]"""),
     "desc": "Nested quantifier — vulnerable to catastrophic backtracking"},

    # ── LPDoS ──
    {"id": "lpdos_no_body_limit_fastapi", "vuln": "lpdos", "severity": "medium",
     "pattern": re.compile(r"@(app|router)\.(post|put|patch)\("),
     "desc": "FastAPI write endpoint — confirm body size middleware is mounted",
     "max_per_file": 1},

    # ── Clipboard ──
    {"id": "clipboard_external_paste", "vuln": "clipboard", "severity": "low",
     "pattern": re.compile(r"navigator\.clipboard\.readText\s*\("),
     "desc": "Reads clipboard — sanitise before rendering as code"},

    # ── Replay attack ──
    {"id": "replay_jwt_no_jti",       "vuln": "replay", "severity": "medium",
     "pattern": re.compile(r"""jwt\.encode\(\s*\{[^}]*\}"""),
     "desc": "JWT signed without jti — add unique id + iat for replay defence",
     "post_filter": lambda m: ('"jti"' not in m and "'jti'" not in m)},
]


def _scan_text(path: str, text: str) -> list[dict]:
    """Run all rules over one file's content; return findings list."""
    findings: list[dict] = []
    per_file_count: dict[str, int] = {}
    for line_idx, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        # Skip lines flagged with vanguard:ignore (same convention as
        # the commit-time scanner).
        if "vanguard: ignore" in line or "security-scan: ignore" in line:
            continue
        for rule in _RULES:
            m = rule["pattern"].search(line)
            if not m:
                continue
            pf = rule.get("post_filter")
            if pf and not pf(m.group(0)):
                continue
            cap = rule.get("max_per_file")
            if cap:
                seen = per_file_count.get(rule["id"], 0)
                if seen >= cap:
                    break
                per_file_count[rule["id"]] = seen + 1
            findings.append({
                "rule_id":  rule["id"],
                "vuln":     rule["vuln"],
                "severity": rule["severity"],
                "file":     path,
                "line":     line_idx,
                "snippet":  line.strip()[:200],
                "desc":     rule["desc"],
            })
            break
    return findings
