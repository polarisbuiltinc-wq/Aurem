"""Guard 21 — OWASP/CWE misconfiguration + supply-chain static scan.

Checks (all must be clean, exit 1 otherwise):
  1. Supply chain: every backend dep pinned (==) in requirements.txt,
     frontend yarn.lock committed. Unpinned count MUST be 0.
  2. Misconfig: no debug=True app/uvicorn config; every routers/admin*
     file carries a router-level admin gate (admin_public.py exempt);
     global exception handler present (no raw stack traces to clients);
     no known default credentials in backend source.

The injection fuzz suite lives in tests/test_iter361_guard21_owasp.py
(runs in the blocking pytest lane / CI).
"""
from __future__ import annotations

import json
import os
import re
import sys

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_ROOT = os.path.dirname(BACKEND_ROOT)

_DEFAULT_CRED_PATTERNS = [
    re.compile(r"""password\s*(?:==|=)\s*["'](?:admin|password|changeme|admin123|password123|root|123456)["']""", re.I),
    re.compile(r"""["'](?:admin)["']\s*:\s*["'](?:admin|password)["']"""),
]

_SKIP_DIRS = {"__pycache__", "tests", "node_modules", ".venv", "venv"}


def scan_supply_chain(requirements_path: str | None = None,
                      yarn_lock_path: str | None = None) -> dict:
    req = requirements_path or os.path.join(BACKEND_ROOT, "requirements.txt")
    lock = yarn_lock_path or os.path.join(APP_ROOT, "frontend", "yarn.lock")
    unpinned: list[str] = []
    for line in open(req, encoding="utf-8"):
        s = line.strip()
        if not s or s.startswith(("#", "--")):
            continue
        if "==" not in s and " @ " not in s:
            unpinned.append(s)
    return {"unpinned_deps": unpinned,
            "unpinned_count": len(unpinned),
            "yarn_lock_present": os.path.isfile(lock)}


def check_admin_router_gates(routers_dir: str | None = None) -> list[str]:
    """Every routers/admin*.py (except admin_public.py) must attach a
    router-level admin dependency. Returns list of offending files."""
    rdir = routers_dir or os.path.join(BACKEND_ROOT, "routers")
    bad: list[str] = []
    for fn in sorted(os.listdir(rdir)):
        if not (fn.startswith("admin") and fn.endswith(".py")):
            continue
        if fn == "admin_public.py":
            continue
        src = open(os.path.join(rdir, fn), encoding="utf-8").read()
        if not re.search(r"dependencies\s*=\s*\[.*(require_admin_dep|_require_admin)", src, re.S):
            bad.append(fn)
    return bad


def scan_misconfig() -> dict:
    findings: list[dict] = []

    # debug=True in app/server construction
    for dp, dns, fns in os.walk(BACKEND_ROOT):
        dns[:] = [d for d in dns if d not in _SKIP_DIRS]
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dp, fn)
            for i, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                ls = line.strip()
                if ls.startswith("#"):
                    continue
                if re.search(r"\b(?:FastAPI|uvicorn\.run)\(.*debug\s*=\s*True", ls):
                    findings.append({"kind": "debug_mode", "file": os.path.relpath(p, APP_ROOT), "line": i})
                for pat in _DEFAULT_CRED_PATTERNS:
                    if pat.search(ls):
                        findings.append({"kind": "default_credential",
                                         "file": os.path.relpath(p, APP_ROOT), "line": i})

    for fn in check_admin_router_gates():
        findings.append({"kind": "ungated_admin_router", "file": f"backend/routers/{fn}"})

    main_src = open(os.path.join(BACKEND_ROOT, "main.py"), encoding="utf-8").read()
    if "@app.exception_handler(Exception)" not in main_src:
        findings.append({"kind": "no_global_exception_handler", "file": "backend/main.py"})

    return {"findings": findings, "finding_count": len(findings)}


def run_scan() -> dict:
    supply = scan_supply_chain()
    mis = scan_misconfig()
    ok = (supply["unpinned_count"] == 0 and supply["yarn_lock_present"]
          and mis["finding_count"] == 0)
    return {"supply_chain": supply, "misconfig": mis, "pass": ok}


def main() -> int:
    result = run_scan()
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
    else:
        sc = result["supply_chain"]
        print(f"Guard 21 security scan: unpinned deps={sc['unpinned_count']}, "
              f"yarn.lock={'ok' if sc['yarn_lock_present'] else 'MISSING'}, "
              f"misconfig findings={result['misconfig']['finding_count']}")
        for d in sc["unpinned_deps"]:
            print(f"  ✗ unpinned: {d}")
        for f in result["misconfig"]["findings"]:
            print(f"  ✗ {f['kind']}: {f['file']}" + (f":{f['line']}" if "line" in f else ""))
        print("PASS" if result["pass"] else "FAIL")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
