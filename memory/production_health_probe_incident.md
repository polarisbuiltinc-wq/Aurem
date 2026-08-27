# Production Deploy Log Incident — 2026-08-27 (/health timeouts + raw "No response returned")

## Symptom (from real production logs, shared by founder)
- nginx: repeated `upstream timed out (110: Connection timed out) while reading response header from upstream ... GET /health`
- A raw, un-prefixed `RuntimeError: No response returned.` traceback (anyio.EndOfStream / BaseExceptionGroup) — distinct from the ALREADY-caught-and-logged `main ERROR rate_limit_guard: downstream raised on GET /api/aurem-dev/admin/status/all — RuntimeError('No response returned.')` line (that one is `_global_rate_limit_guard`'s own hardening working correctly).
- Noisy `services.rate_limiter WARNING rate_limiter: Redis path failed (TimeoutError), fell back to in-memory` every ~5-8s — inspected `services/rate_limiter.py`, already has a 2s socket timeout, a retry cooldown gate, warning-throttling, and fails OPEN (never blocks the request) — assessed as noisy-but-safe, not a crash contributor. Not touched this pass.

## Root cause found (CONFIRMED)
`main.py::_health_latency_sampler_mw` (registered AFTER `_global_rate_limit_guard`, so Starlette makes it the OUTER wrapper) called `call_next()` on BOTH its branches (`skip` and measured) with **zero exception handling** — the exact same BaseHTTPMiddleware + Python 3.11 `BaseExceptionGroup` client-disconnect race that was found and fixed in `_global_rate_limit_guard` on 2026-08-19 (see `tests/test_deploy_2026_08_19_health_probe_and_exceptiongroup.py`'s own docstring), but that earlier fix only covered the INNER middleware — this OUTER one reintroduced the identical unguarded pattern. Since it wraps every request (including `/health` on the `skip` branch), a probe/client disconnect mid-response here could crash regardless of the inner guard's hardening — explaining the raw, un-prefixed traceback distinct from the properly-caught `rate_limit_guard:`-prefixed ones.

## Fix
`main.py`: both `call_next()` calls in `_health_latency_sampler_mw` now wrap in `except (Exception, BaseExceptionGroup) as _e:`, log, and return a safe `JSONResponse(500)` — identical pattern to `_global_rate_limit_guard`.

## Tests
- Updated `tests/test_deploy_2026_08_19_health_probe_and_exceptiongroup.py::test_source_widened_to_baseexceptiongroup` (count 2→4, now scoped per-function).
- Added `TestHealthLatencySamplerHardened` (3 new tests): skip-branch catches `BaseExceptionGroup` → 500; measured-branch catches it too → 500; a real normal response still passes through unaffected.
- Full targeted suite (middleware/health_probe/rate_limit-related, 102 tests) — 2 pre-existing failures confirmed unrelated via `git stash` (`test_login_burst_rate_limiter_exists_and_binds`, `test_rate_limit_failure_logs_and_fails_open`), 0 new failures.
- Backend restarted, `/health` returns 200 in ~2ms locally.

## Honest scope limit
This fixes a CONFIRMED, evidenced code-level gap. It does NOT prove it's the ONLY cause of the production timeouts (per the earlier handoff note: founder still wants a real post-deploy nginx `/health` observation window of 20-30 minutes before this class of issue is declared fully closed — I have no production access to provide that observation myself). The Redis warning noise was assessed as safe/non-blocking and left as-is.
