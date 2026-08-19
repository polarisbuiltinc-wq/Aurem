"""
Deploy hardening — Starlette BaseHTTPMiddleware "No response returned" fix.

On 2026-02-12 a prod deploy failed to boot cleanly with:
    RuntimeError: No response returned.
        at main.py:1990 → return await call_next(request)
        inside _global_rate_limit_guard

Root cause: Starlette's BaseHTTPMiddleware + Python 3.11's anyio
BaseExceptionGroup unwind. When a downstream handler raises during
`call_next(request)`, the anyio task group re-raises the exception
as an ExceptionGroup; Starlette's plumbing can end up in a state
where no response object is produced, and ASGI dispatch then throws
"RuntimeError: No response returned" at request-scope.

Fix (in `_global_rate_limit_guard`):
  1. Wrap the skip-path `call_next` in try/except → JSONResponse 500
  2. Wrap `check_rate_limit_async` in try/except → fail-open belt+braces
  3. Wrap the main `call_next` in try/except → JSONResponse 500,
     plus Sentry surfacing so we don't lose signal on the
     ExceptionGroup unwind path.

These guards mean the middleware NEVER returns without producing a
response, which is the ASGI contract Starlette's inner plumbing
assumes.
"""


def test_global_rate_limit_guard_call_next_is_wrapped():
    src = open("/app/backend/main.py").read()

    # Skip path must be wrapped. 2026-08-19: widened to also catch
    # BaseExceptionGroup (bare `except Exception` missed anyio task
    # groups formed from a client-disconnect CancelledError, which is
    # the exact "RuntimeError: No response returned" seen in the
    # 2026-08-19 deploy logs).
    assert "except (Exception, BaseExceptionGroup) as _e:" in src
    # Rate-limit call itself must be defensively wrapped so a
    # future refactor can't accidentally re-introduce the crash.
    assert "check_rate_limit_async(f\"global-ip:{ip}\", _GLOBAL_RL_PER_MIN)" in src
    assert "except Exception as _rl_err:" in src
    # Main call_next MUST be wrapped — the actual fix.
    assert "middleware_call_next_crash" in src, (
        "The main call_next() try/except (added 2026-02-12 to fix "
        "the 'RuntimeError: No response returned' deploy failure) "
        "must be present. Its Sentry tag `middleware_call_next_crash` "
        "is the fingerprint. If it's missing, the deploy will fail "
        "the same way again."
    )


def test_middleware_returns_response_even_on_downstream_crash():
    """Sanity — the middleware body must have a JSONResponse return
    in ALL three try/except blocks (skip path, rate-limit check,
    main path)."""
    src = open("/app/backend/main.py").read()
    # Extract the middleware body (rough — from decorator to next @).
    start = src.index('@app.middleware("http")\nasync def _global_rate_limit_guard')
    end = src.index('# ── Iter 44 — Global exception handler', start)
    body = src[start:end]
    # Three JSONResponse returns: skip-path 500, 429 hit, main-path 500.
    assert body.count("JSONResponse(") >= 3
    # Sentry surface path documented.
    assert "middleware_call_next_crash" in body


def test_check_rate_limit_async_still_documented_fail_open():
    """The rate limiter itself must remain fail-open by design —
    the middleware's defensive wrap is belt+braces, not a
    replacement for the fail-open contract."""
    src = open("/app/backend/services/rate_limiter.py").read()
    assert "Non-raising by design" in src
    # The catch-all in the impl must still return via the fallback.
    assert "check_rate_limit(key, limit_per_minute)" in src
