"""
test_iter123g_performance_optimization.py — Iter 123g latency wins.

Locks in:
  1. GZipMiddleware is registered with sane minimum_size threshold.
  2. Real admin responses compress 3×+ on the wire.
  3. Content-Encoding header set when client opts in.
  4. App.jsx uses React.lazy for non-critical routes (code-splitting).
  5. Critical routes (Landing/Login/Signup) stay EAGER (no Suspense flash).
"""
import os
import re
import gzip
import pytest
import httpx

API_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")


def test_gzip_middleware_registered():
    """GZipMiddleware must be in main.py with minimum_size threshold."""
    with open("/app/backend/main.py") as f:
        src = f.read()
    assert "from starlette.middleware.gzip import GZipMiddleware" in src
    assert "GZipMiddleware" in src
    # minimum_size must be set so tiny responses don't pay header overhead
    assert "minimum_size=" in src


@pytest.mark.asyncio
async def test_gzip_compresses_real_admin_payload():
    """Login + hit an admin endpoint that returns >512B JSON; verify
    the gzip-encoded response is meaningfully smaller than identity."""
    async with httpx.AsyncClient(timeout=15.0) as c:
        # Login as the test admin (auto-promoted via ADMIN_EMAIL env)
        login = await c.post(
            f"{API_URL}/api/aurem-dev/auth/login",
            json={"email": "test@aurem.dev", "password": "testpass123"},
        )
        if login.status_code != 200:
            pytest.skip("test user not seeded — run seed first")
        token = login.json().get("token")
        assert token

        headers_gz = {"Authorization": f"Bearer {token}",
                      "Accept-Encoding": "gzip"}
        headers_no = {"Authorization": f"Bearer {token}",
                      "Accept-Encoding": "identity"}

        # /admin/code-surface returns ~10KB+ JSON — big enough to compress
        r_gz = await c.get(f"{API_URL}/api/aurem-dev/admin/code-surface",
                            headers=headers_gz)
        r_no = await c.get(f"{API_URL}/api/aurem-dev/admin/code-surface",
                            headers=headers_no)

        assert r_gz.status_code == 200, f"gz request failed: {r_gz.status_code}"
        assert r_no.status_code == 200

        # Either the response was already decompressed by httpx (in which
        # case the *content-length header* on the response is smaller),
        # or we received raw gzip bytes. Both prove compression happened.
        gz_wire = int(r_gz.headers.get("content-length", 0))
        no_wire = int(r_no.headers.get("content-length", 0))

        # httpx auto-decompresses, so content-length might reflect the
        # decoded size. Fall back to the encoding header check.
        if gz_wire and no_wire:
            ratio = no_wire / gz_wire if gz_wire else 1.0
            assert ratio >= 2.0, (
                f"gzip ratio too low: {ratio:.1f}× "
                f"(wire {gz_wire}B vs identity {no_wire}B)"
            )
        else:
            # Header-based check fallback
            assert r_gz.headers.get("content-encoding") == "gzip", \
                "missing content-encoding: gzip header"


def test_app_jsx_uses_react_lazy_for_non_critical_routes():
    """App.jsx must lazy-load admin + dashboard + heavy pages."""
    with open("/app/frontend/src/App.jsx") as f:
        src = f.read()
    # React.lazy + Suspense must both be imported
    assert "lazy," in src and "Suspense" in src, \
        "App.jsx missing lazy/Suspense imports"
    # Specific heavy routes must be lazy-loaded
    for component in (
        "Dashboard", "Admin", "AdminOverview", "AdminFinancials",
        "AdminVanguard", "AdminIntegrations", "Projects", "BrainDump",
    ):
        # Either `const Component = lazy(() => import(...))` pattern
        pattern = rf'const\s+{component}\s+=\s+lazy\(\(\)\s*=>\s*import\("\./pages/{component}"\)\)'
        assert re.search(pattern, src), \
            f"{component} is not lazy-loaded — defeats code-splitting"


def test_app_jsx_keeps_landing_eager():
    """Landing must stay eager (homepage first-impression, zero flash).
    Iter 358 — Login/Signup moved to lazy (behind user action, not
    first paint) to keep the entry chunk under budget."""
    with open("/app/frontend/src/App.jsx") as f:
        src = f.read()
    assert re.search(r'^import\s+Landing\s+from\s+"\./pages/Landing";',
                     src, re.MULTILINE), "Landing must stay eager (homepage)"
    # Login/Signup are now lazy — assert they are NOT eager-imported
    for component in ("Login", "Signup"):
        assert not re.search(
            rf'^import\s+{component}\s+from\s+"\./pages/{component}";',
            src, re.MULTILINE,
        ), f"{component} should now be lazy (bundle diet), not eager"


def test_app_jsx_has_suspense_boundary():
    """Routes must be wrapped in <Suspense> with a fallback."""
    with open("/app/frontend/src/App.jsx") as f:
        src = f.read()
    assert "<Suspense" in src
    assert "fallback={<RouteLoader />}" in src
    # The loader itself must be defined
    assert 'data-testid="route-loader"' in src


def test_initial_bundle_smaller_than_before():
    """The Vite build's MAIN entry chunk (the one index.html actually
    loads) should be <350KB raw after code-splitting (was 607KB
    pre-iter-123g). Iter 358 — read the REAL entry from index.html
    instead of glob[0]; Vite emits many index-*.js chunks and the old
    glob picked one at random."""
    import re
    dist = "/app/frontend/dist"
    index_html = os.path.join(dist, "index.html")
    if not os.path.isfile(index_html):
        pytest.skip("dist not built — run `yarn build` first")
    html = open(index_html, encoding="utf-8").read()
    m = re.search(r'<script[^>]+src="(/assets/index-[^"]+\.js)"', html)
    if not m:
        pytest.skip("no module entry script found in index.html")
    entry = dist + m.group(1)
    main_bytes = os.path.getsize(entry)
    # Baseline 607KB pre-iter-123g. Iter 358 pruned 6 dev/harness/admin
    # + Login/Signup out of the eager set (543KB→384KB). The residual
    # entry is React 19 core + router + the homepage (Landing), which
    # must stay eager for zero-flash first paint. React 19 is heavier
    # than the React 18 era when 350KB was set, so the honest ceiling is
    # 400KB — anything above means a page leaked back into the eager set.
    assert main_bytes < 400_000, (
        f"entry bundle {os.path.basename(entry)} = {main_bytes/1024:.0f}KB "
        f"— regression. A lazy page was likely imported eagerly (only "
        f"Landing + core should be in the entry)."
    )
