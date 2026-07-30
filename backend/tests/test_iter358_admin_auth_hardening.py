"""Iter 358 · Guard 16 (partial) — admin auth hardening locks.

Founder-mandated after RailShell merged the admin panel into the same
shell every user sees. UI-hiding is NOT proof — server-side gating is.

These locks make "forgetting to gate a new admin route" a BUILD FAILURE:
1. Every admin router (admin, admin_qa, admin_bin, admin_vanguard) must
   carry a ROUTER-LEVEL admin dependency, so new routes inherit the gate.
2. The only un-gated admin-prefixed router (admin_public) may expose
   ONLY the write-only /errors/report sink.
3. Live: a real non-founder token gets 403 on every /admin/* endpoint;
   no token gets 401; the founder still gets 200.
"""
from __future__ import annotations
import ast
import os
import json
import pathlib
import time
import urllib.request
import urllib.error

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
GATED_ROUTERS = ["admin.py", "admin_qa.py", "admin_bin.py", "admin_vanguard.py"]


def _router_has_dep(src: str) -> bool:
    """True if the APIRouter(...) call includes dependencies=[Depends(require_admin_dep...)]."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "APIRouter"):
            for kw in node.keywords:
                if kw.arg == "dependencies":
                    dump = ast.dump(kw.value)
                    if "require_admin_dep" in dump:
                        return True
    return False


def test_every_admin_router_has_router_level_gate():
    missing = []
    for fn in GATED_ROUTERS:
        src = (BACKEND / "routers" / fn).read_text()
        if not _router_has_dep(src):
            missing.append(fn)
    assert not missing, (
        "Admin routers missing router-level require_admin_dep gate: "
        f"{missing}. A new admin route on these could ship UNPROTECTED.")


def test_admin_public_router_only_exposes_error_sink():
    """The one un-gated admin-prefixed router must not grow read routes."""
    src = (BACKEND / "routers" / "admin_public.py").read_text()
    tree = ast.parse(src)
    routes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                        and getattr(d.func.value, "id", "") == "router"):
                    routes.append((d.func.attr.upper(),
                                   d.args[0].value if d.args else "?"))
    assert routes == [("POST", "/errors/report")], (
        f"admin_public may ONLY host the write-only error sink, found: {routes}")


def test_require_admin_dep_exists_and_delegates():
    src = (BACKEND / "cto_services" / "auth.py").read_text()
    assert "async def require_admin_dep" in src
    assert "Header(None)" in src
    # delegates to the canonical DB-backed require_admin
    m = src.split("async def require_admin_dep")[1].split("async def")[0]
    assert "require_admin(authorization)" in m


# ── Live locks (running backend + real accounts) ─────────────────────

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def _api():
    env = (BACKEND.parent / "frontend" / ".env").read_text()
    for line in env.splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            return line.split("=", 1)[1].strip() + "/api/aurem-dev"
    raise RuntimeError("no backend url")


def _open(url, data=None, method=None, token=None, timeout=15):
    """urllib with a browser UA — the preview sits behind Cloudflare,
    which 1010-blocks the default python-urllib UA at the edge."""
    headers = {"User-Agent": _UA, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


def _login(email, pw):
    data = json.dumps({"email": email, "password": pw}).encode()
    try:
        return json.load(_open(_api() + "/auth/login", data=data)).get("token")
    except Exception:
        return None


def _all_admin_eps():
    eps = []
    for fn in GATED_ROUTERS:
        src = (BACKEND / "routers" / fn).read_text()
        tree = ast.parse(src)
        prefix = "/admin"
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "APIRouter":
                for kw in node.keywords:
                    if kw.arg == "prefix":
                        prefix = kw.value.value
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in node.decorator_list:
                    if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                            and getattr(d.func.value, "id", "") == "router"):
                        p = d.args[0].value if d.args else "?"
                        eps.append((d.func.attr.upper(), prefix + p))
    return eps


def _sub(p):
    for k, v in {"{user_id}": "d", "{ticket_id}": "d", "{project_id}": "p_demo_a",
                 "{alert_id}": "d", "{flag}": "d", "{error_id}": "d",
                 "{loop_id}": "d", "{bin_id}": "d"}.items():
        p = p.replace(k, v)
    return p


@pytest.mark.timeout(180)
def test_live_non_founder_denied_on_every_admin_endpoint():
    tok = _login("qa-free-339k@aurem.dev", "QaFree75b9450d!")
    if not tok:
        pytest.skip("non-founder preview account unavailable")
    api = _api()
    leaks = []
    for m, p in _all_admin_eps():
        data = b"{}" if m in ("POST", "PUT") else None
        # A data breach = a non-founder getting a SUCCESSFUL (2xx)
        # response. 401/403 = denied; 429/5xx/network-error = transient
        # (still no data). Retry transients once so parallel-gate load
        # doesn't flag a connection reset as a "leak". Only a 2xx counts.
        code = None
        for _ in range(2):
            try:
                code = _open(api + _sub(p), data=data, method=m, token=tok).status
            except urllib.error.HTTPError as e:
                code = e.code
            except Exception:
                code = "ERR"
            if isinstance(code, int) and code < 500 and code != 429:
                break
            time.sleep(0.4)
        if code == 422 and p.endswith("/errors/report"):
            continue  # public write-only sink, no data
        if isinstance(code, int) and 200 <= code < 300:
            leaks.append((m, p, code))
    assert not leaks, f"NON-FOUNDER LEAK — endpoints returned 2xx: {leaks}"


def test_live_public_error_sink_open_but_read_gated():
    api = _api()
    # write sink: no auth, returns {ok}
    data = json.dumps({"message": "guard16 lock probe", "url": "/t"}).encode()
    assert json.load(_open(api + "/admin/errors/report", data=data)).get("ok") is True
    # read list: no auth → 401/403
    try:
        _open(api + "/admin/errors")
        assert False, "/admin/errors must require auth"
    except urllib.error.HTTPError as e:
        assert e.code in (401, 403)
