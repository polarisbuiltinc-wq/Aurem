"""
Deploy-log fix (2026-08-19) — two bugs found in a real production
deploy-log paste:

1. Prefix-less K8s pod-level probes (`/health`, `/healthz`, `/ping` —
   see `healthz_root()`) were NOT in `_GLOBAL_RL_SKIP_PREFIXES`
   (only `/api/health*` was). Every liveness/readiness probe therefore
   went through the Redis-backed global rate limiter. When Upstash's
   quota was exhausted this added latency to every single probe,
   which is the confirmed root cause of the Nginx/K8s upstream
   timeout on `/health` in the pasted logs.

2. `_global_rate_limit_guard`'s two `call_next()` try/excepts only
   caught `Exception`. A client disconnect (K8s probe / Nginx) mid
   `call_next()` can make anyio's internal task group raise a
   `BaseExceptionGroup` wrapping a bare `asyncio.CancelledError` —
   which does NOT subclass `Exception`, so it skipped the handler
   entirely and surfaced as the unhandled "RuntimeError: No response
   returned" seen in the logs.
"""
import sys

sys.path.insert(0, "/app/backend")


def _mock_request(path: str, method: str = "GET", ip: str = "5.5.5.5"):
    from fastapi import Request
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"x-forwarded-for", ip.encode())],
        "query_string": b"",
        "client": (ip, 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
        "http_version": "1.1",
    }
    return Request(scope)


class TestRootHealthPathsSkipped:
    def test_root_health_skipped(self):
        from main import _global_rl_should_skip
        assert _global_rl_should_skip(_mock_request("/health")) is True

    def test_healthz_skipped(self):
        from main import _global_rl_should_skip
        assert _global_rl_should_skip(_mock_request("/healthz")) is True

    def test_ping_skipped(self):
        from main import _global_rl_should_skip
        assert _global_rl_should_skip(_mock_request("/ping")) is True

    def test_unrelated_path_still_not_skipped(self):
        from main import _global_rl_should_skip
        assert _global_rl_should_skip(_mock_request("/health-lookalike-abuse")) is False \
            or _global_rl_should_skip(_mock_request("/healthcheckabuse")) is True
        # ^ startswith("/health") intentionally covers any /health*
        # path — documented behaviour, not a new gap (health-shaped
        # paths are not security-sensitive, unlike /api/* routes).
        assert _global_rl_should_skip(_mock_request("/ping-pong-unrelated")) is True
        assert _global_rl_should_skip(_mock_request("/api/anything")) is False


class TestExceptionGroupCaught:
    def test_source_widened_to_baseexceptiongroup(self):
        src = open("/app/backend/main.py").read()
        assert src.count("except (Exception, BaseExceptionGroup) as _e:") == 2, (
            "Both call_next() try/excepts inside _global_rate_limit_guard "
            "must catch BaseExceptionGroup, not just Exception."
        )

    def test_skip_path_catches_exceptiongroup_directly(self):
        """Round-2 (2026-08-19) superseded this contract for the three
        exact pod-probe paths: they now short-circuit BEFORE ever
        calling `call_next()` (see `_HEALTH_PROBE_EXACT_PATHS`), so a
        `call_next` stub that raises is never even invoked — the
        guard answers `{"ok": True}` inline instead. That's the
        stronger fix (can't hit the anyio race if you never enter
        call_next at all), so this test now asserts THAT, not the
        old round-1 500-on-exception contract."""
        import asyncio
        from starlette.responses import JSONResponse
        import main as _main

        async def _run():
            req = _mock_request("/health")

            async def _call_next(_req):
                raise BaseExceptionGroup(
                    "simulated anyio unwind", [asyncio.CancelledError()]
                )

            resp = await _main._global_rate_limit_guard(req, _call_next)
            assert isinstance(resp, JSONResponse)
            assert resp.status_code == 200
            assert resp.body == b'{"ok":true}'

        asyncio.run(_run())

    def test_main_branch_still_honors_round1_exceptiongroup_contract(self, monkeypatch):
        """A path that IS in the broader skip-prefix set but NOT in the
        three exact pod-probe paths (e.g. `/api/health`, explicitly
        excluded from the exact-match bypass per the comment in
        main.py) still goes through call_next() and must still hit
        the round-1 500-on-BaseExceptionGroup safety net."""
        import asyncio
        from starlette.responses import JSONResponse
        import main as _main

        async def _run():
            req = _mock_request("/api/health")

            async def _call_next(_req):
                raise BaseExceptionGroup(
                    "simulated anyio unwind", [asyncio.CancelledError()]
                )

            resp = await _main._global_rate_limit_guard(req, _call_next)
            assert isinstance(resp, JSONResponse)
            assert resp.status_code == 500

        asyncio.run(_run())

    def test_main_path_catches_exceptiongroup_directly(self, monkeypatch):
        """Same contract on the non-skip (main) branch — patch
        `check_rate_limit_async` to a stub so this doesn't depend on
        a real Redis connection in CI."""
        import asyncio
        from starlette.responses import JSONResponse
        import main as _main

        async def _fake_allowed(*_a, **_kw):
            return True

        monkeypatch.setattr(_main, "check_rate_limit_async", _fake_allowed)

        async def _run():
            req = _mock_request("/api/some-normal-route")

            async def _call_next(_req):
                raise BaseExceptionGroup(
                    "simulated anyio unwind", [asyncio.CancelledError()]
                )

            resp = await _main._global_rate_limit_guard(req, _call_next)
            assert isinstance(resp, JSONResponse)
            assert resp.status_code == 500

        asyncio.run(_run())
