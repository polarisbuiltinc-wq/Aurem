"""Guard 18 — Universal timeout budget static audit.

Scans backend Python (AST) + frontend JS/JSX (regex) for outbound
network calls without an explicit timeout / abort signal.
Exit 1 on any violation. `g18-exempt` comment on the call line (or the
line above) skips a site — must include a reason.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_ROOT = os.path.dirname(BACKEND_ROOT)
FRONTEND_SRC = os.path.join(APP_ROOT, "frontend", "src")
FRONTEND_SCRIPTS = os.path.join(APP_ROOT, "frontend", "scripts")

PY_SKIP_DIRS = {"__pycache__", "tests", ".venv", "venv", "node_modules"}
JS_SKIP_DIRS = {"__tests__", "node_modules", "ui"}

# python callables that MUST carry timeout=
_HTTPX_FUNCS = {"get", "post", "put", "delete", "patch", "head", "request", "stream"}
_REQUESTS_FUNCS = {"get", "post", "put", "delete", "patch", "head", "request"}

_JS_FETCH_RE = re.compile(r"(?<![\w.$])(?:window\.)?fetch\s*\(")
_JS_AXIOS_CREATE_RE = re.compile(r"\baxios\.create\s*\(")
_JS_AXIOS_CALL_RE = re.compile(r"\baxios\.(?:get|post|put|delete|patch|request)\s*\(")
_JS_COMMENT_RE = re.compile(r"^\s*(\*|//|/\*)")


def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _line_exempt(lines: list[str], lineno: int) -> bool:
    for ln in (lineno - 1, lineno - 2):
        if 0 <= ln < len(lines) and "g18-exempt" in lines[ln]:
            return True
    return False


def audit_python_file(path: str) -> tuple[int, list[dict]]:
    src = open(path, encoding="utf-8", errors="replace").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return 0, []
    lines = src.split("\n")
    total, violations = 0, []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        flagged = False
        if name in {f"httpx.{f}" for f in _HTTPX_FUNCS} | {"httpx.AsyncClient", "httpx.Client"}:
            flagged = True
        elif name in {f"requests.{f}" for f in _REQUESTS_FUNCS}:
            flagged = True
        elif name in {"aiohttp.ClientSession", "urllib.request.urlopen", "request.urlopen"}:
            flagged = True
        if not flagged:
            continue
        total += 1
        has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
        if not has_timeout and not _line_exempt(lines, node.lineno):
            violations.append({"file": os.path.relpath(path, APP_ROOT),
                               "line": node.lineno, "call": name,
                               "kind": "python-no-timeout"})
    return total, violations


def _balanced_call(text: str, open_idx: int, cap: int = 6000) -> str:
    depth, i = 0, open_idx
    end = min(len(text), open_idx + cap)
    while i < end:
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx:i + 1]
        i += 1
    return text[open_idx:end]


def audit_js_file(path: str) -> tuple[int, list[dict]]:
    src = open(path, encoding="utf-8", errors="replace").read()
    lines = src.split("\n")
    line_starts = []
    pos = 0
    for ln in lines:
        line_starts.append(pos)
        pos += len(ln) + 1
    total, violations = 0, []

    def lineno_of(idx: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= idx:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    checks = [
        (_JS_FETCH_RE, ("signal", "timeout"), "js-fetch-no-signal"),
        (_JS_AXIOS_CREATE_RE, ("timeout",), "js-axios-create-no-timeout"),
        (_JS_AXIOS_CALL_RE, ("timeout", "signal"), "js-axios-no-timeout"),
    ]
    for pat, needles, kind in checks:
        for m in pat.finditer(src):
            ln = lineno_of(m.start())
            if _JS_COMMENT_RE.match(lines[ln - 1]):
                continue
            total += 1
            open_idx = src.index("(", m.start())
            call = _balanced_call(src, open_idx)
            if any(n in call for n in needles):
                continue
            if _line_exempt(lines, ln):
                continue
            violations.append({"file": os.path.relpath(path, APP_ROOT),
                               "line": ln, "call": m.group(0).strip("( "),
                               "kind": kind})
    return total, violations


def run_audit() -> dict:
    total, violations = 0, []
    for dp, dns, fns in os.walk(BACKEND_ROOT):
        dns[:] = [d for d in dns if d not in PY_SKIP_DIRS]
        for fn in fns:
            if fn.endswith(".py"):
                t, v = audit_python_file(os.path.join(dp, fn))
                total += t
                violations += v
    for root in (FRONTEND_SRC, FRONTEND_SCRIPTS):
        if not os.path.isdir(root):
            continue
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in JS_SKIP_DIRS]
            for fn in fns:
                if fn.endswith((".js", ".jsx", ".mjs", ".ts", ".tsx")) and ".test." not in fn:
                    t, v = audit_js_file(os.path.join(dp, fn))
                    total += t
                    violations += v
    return {"total_call_sites": total,
            "covered": total - len(violations),
            "violations": violations,
            "pass": len(violations) == 0}


def main() -> int:
    result = run_audit()
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
    else:
        print(f"Guard 18 timeout audit: {result['covered']}/{result['total_call_sites']} "
              f"outbound call sites covered")
        for v in result["violations"]:
            print(f"  ✗ {v['file']}:{v['line']} [{v['kind']}] {v['call']}")
        print("PASS" if result["pass"] else "FAIL")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
