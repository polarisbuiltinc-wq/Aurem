"""Iter 359 — Guard 18 (Universal timeout budget) regression locks.

1. The live codebase has ZERO outbound network calls without an
   explicit timeout / abort signal (the guard itself).
2. The audit scanner actually catches violations (self-test on
   synthetic fixtures) — so a green run means something.
3. The g18-exempt escape hatch works.
4. The founder-gated QA endpoint is registered.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.timeout_audit import audit_js_file, audit_python_file, run_audit


def _tmp(content: str, suffix: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False)
    f.write(content)
    f.close()
    return f.name


class TestGuard18Live:
    def test_codebase_has_zero_timeout_violations(self):
        result = run_audit()
        assert result["violations"] == [], (
            "Guard 18 RED — outbound calls without timeout:\n"
            + "\n".join(f"{v['file']}:{v['line']} {v['call']}"
                        for v in result["violations"])
        )
        assert result["pass"] is True

    def test_audit_actually_scans_real_call_sites(self):
        result = run_audit()
        assert result["total_call_sites"] >= 150
        assert result["covered"] == result["total_call_sites"]


class TestScannerSelfTest:
    def test_catches_python_httpx_without_timeout(self):
        p = _tmp("import httpx\nr = httpx.get('https://x.com')\n", ".py")
        total, v = audit_python_file(p)
        os.unlink(p)
        assert total == 1 and len(v) == 1
        assert v[0]["kind"] == "python-no-timeout"

    def test_passes_python_httpx_with_timeout(self):
        p = _tmp("import httpx\nr = httpx.get('https://x.com', timeout=10)\n", ".py")
        total, v = audit_python_file(p)
        os.unlink(p)
        assert total == 1 and v == []

    def test_catches_asyncclient_without_timeout(self):
        p = _tmp("import httpx\nasync def f():\n"
                 "    async with httpx.AsyncClient() as c:\n        pass\n", ".py")
        _, v = audit_python_file(p)
        os.unlink(p)
        assert len(v) == 1

    def test_g18_exempt_comment_skips_site(self):
        p = _tmp("import httpx\n# g18-exempt: streaming keepalive\n"
                 "r = httpx.get('https://x.com')\n", ".py")
        _, v = audit_python_file(p)
        os.unlink(p)
        assert v == []

    def test_catches_js_fetch_without_signal(self):
        p = _tmp("const r = await fetch('/api/x', { method: 'POST' });\n", ".js")
        total, v = audit_js_file(p)
        os.unlink(p)
        assert total == 1 and v[0]["kind"] == "js-fetch-no-signal"

    def test_passes_js_fetch_with_signal(self):
        p = _tmp("const r = await fetch('/api/x', "
                 "{ signal: AbortSignal.timeout(10000) });\n", ".js")
        _, v = audit_js_file(p)
        os.unlink(p)
        assert v == []

    def test_catches_axios_create_without_timeout(self):
        p = _tmp("const api = axios.create({ baseURL: '/api' });\n", ".js")
        _, v = audit_js_file(p)
        os.unlink(p)
        assert len(v) == 1 and v[0]["kind"] == "js-axios-create-no-timeout"

    def test_js_comment_lines_ignored(self):
        p = _tmp(" *     return fetch('/api/x', {\n", ".js")
        total, v = audit_js_file(p)
        os.unlink(p)
        assert total == 0 and v == []


class TestEndpointRegistered:
    def test_route_exists_and_is_admin_gated(self):
        from routers.admin_qa import router
        paths = [r.path for r in router.routes]
        assert "/admin/qa/guard18-timeout-audit" in paths
        assert any(d.dependency.__name__ == "require_admin_dep"
                   for d in router.dependencies)
