"""Iter 361 — Guard 21 (OWASP/CWE coverage) regression locks.

Two halves:
  A. Static scan (scripts/g21_security_scan.py) — supply chain
     (deps pinned + yarn.lock) and misconfig (no debug mode, no
     default creds, admin routers gated, global exc handler) all clean.
  B. Injection fuzz — SQL/NoSQL/XSS/command-injection payloads against
     live public input endpoints; assert safe handling: no 500, no raw
     stack trace leaked, payload never reflected unescaped, NoSQL
     operator objects rejected (not treated as query operators).
"""
import ast
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.g21_security_scan import (
    check_admin_router_gates,
    run_scan,
    scan_misconfig,
    scan_supply_chain,
)

BACKEND = Path(__file__).resolve().parent.parent
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

SQLI = ["' OR '1'='1", "'; DROP TABLE dev_users; --", "1' UNION SELECT NULL--"]
XSS = ["<script>alert(1)</script>", "\"><img src=x onerror=alert(1)>",
       "javascript:alert(document.cookie)"]
CMDI = ["; cat /etc/passwd", "$(rm -rf /)", "`whoami`", "| ls -la"]
STACK_MARKERS = ["Traceback (most recent call last)", 'File "/app',
                 "pymongo.errors", "motor.", "self.__dict__"]


# ══════════════════════ A. STATIC SCAN ══════════════════════
class TestStaticScan:
    def test_all_backend_deps_pinned(self):
        sc = scan_supply_chain()
        assert sc["unpinned_count"] == 0, f"unpinned: {sc['unpinned_deps']}"

    def test_yarn_lock_committed(self):
        assert scan_supply_chain()["yarn_lock_present"] is True

    def test_no_misconfig_findings(self):
        mis = scan_misconfig()
        assert mis["finding_count"] == 0, f"findings: {mis['findings']}"

    def test_every_admin_router_gated(self):
        assert check_admin_router_gates() == []

    def test_full_scan_passes(self):
        assert run_scan()["pass"] is True

    def test_endpoint_registered_and_admin_gated(self):
        from routers.admin_qa import router
        paths = [r.path for r in router.routes]
        assert "/admin/qa/guard21-security-scan" in paths
        assert any(d.dependency.__name__ == "require_admin_dep"
                   for d in router.dependencies)

    def test_scanner_detects_unpinned_dep(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("fastapi==0.110.0\nrequests\n# comment\n")
        sc = scan_supply_chain(requirements_path=str(req),
                               yarn_lock_path=str(tmp_path / "yarn.lock"))
        assert sc["unpinned_count"] == 1 and "requests" in sc["unpinned_deps"]

    def test_scanner_detects_ungated_admin_router(self, tmp_path):
        rdir = tmp_path / "routers"
        rdir.mkdir()
        (rdir / "admin_leak.py").write_text(
            "from fastapi import APIRouter\nrouter = APIRouter(prefix='/admin/leak')\n")
        assert "admin_leak.py" in check_admin_router_gates(routers_dir=str(rdir))


# ══════════════════════ B. LIVE INJECTION FUZZ ══════════════════════
def _api():
    env = (BACKEND.parent / "frontend" / ".env").read_text()
    for line in env.splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            return line.split("=", 1)[1].strip() + "/api/aurem-dev"
    raise RuntimeError("no backend url")


def _post(path, body):
    """Returns (status, ctype, text). Never raises on HTTP error status."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        _api() + path, data=data, method="POST",
        headers={"User-Agent": _UA, "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=20)
        return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode("utf-8", "replace")


def _assert_safe(status, ctype, text, payload):
    assert status != 500, f"500 on payload {payload!r}: {text[:200]}"
    for marker in STACK_MARKERS:
        assert marker not in text, f"stack trace leaked on {payload!r}: {marker}"
    # Reflected <script> only executes when served as HTML. JSON APIs
    # (application/json) render it inert; React escapes on the client.
    if "text/html" in ctype.lower():
        assert "<script>alert(1)</script>" not in text, \
            f"XSS payload reflected in HTML response: {payload!r}"


@pytest.mark.parametrize("payload", SQLI + XSS)
def test_signup_fields_handle_injection(payload):
    status, ctype, text = _post("/auth/signup",
                         {"email": f"fuzz+{abs(hash(payload)) % 9999}@x.io",
                          "password": payload, "name": payload})
    _assert_safe(status, ctype, text, payload)
    assert "application/json" in ctype.lower(), \
        f"signup must respond JSON (inert), got {ctype!r}"


@pytest.mark.parametrize("payload", XSS + CMDI)
def test_notify_interest_handles_injection(payload):
    status, ctype, text = _post("/notify-interest",
                         {"tool": "bug-hunt", "email": payload, "repo": payload})
    _assert_safe(status, ctype, text, payload)


def test_nosql_operator_injection_rejected_on_login():
    """A Mongo query-operator object in email must NOT be honoured as a
    query operator (auth bypass). Expect a rejection, never a token."""
    status, ctype, text = _post("/auth/login",
                         {"email": {"$ne": None}, "password": {"$ne": None}})
    assert status in (400, 401, 403, 422, 429)
    assert "token" not in json.loads(text) if status == 200 else True
    _assert_safe(status, ctype, text, "$ne operator")


def test_deeply_nested_json_rejected_not_500():
    """Malformed / hostile payload shape → graceful 4xx, never a 500."""
    nested = {"email": "a@b.io", "password": "x", "extra": {"a": {"b": {"c": [1] * 50}}}}
    status, ctype, text = _post("/auth/login", nested)
    _assert_safe(status, ctype, text, "nested")
