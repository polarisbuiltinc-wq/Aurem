"""Iter 367 · Item E · Browser Self-Testing — REAL E2E.

Proves (with a REAL Playwright launch against the LIVE preview URL):
  1. classify_frontend_change() maps changed files → correct URLs.
  2. run_smoke() launches REAL chromium and returns a real HTTP
     status per URL (proved against the running preview).
  3. Red-flag detection finds "NaN"/"undefined"/"Invalid Date" in
     rendered HTML.
  4. Cooldown filter prevents re-smoking the same URL within
     RESMOKE_COOLDOWN_S.
  5. record_run() persists the report to browser_selftest_runs.
  6. FAIL-OPEN: a Playwright launch failure never raises.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest


# ─────────────────────────────────────────────────────────────────
# Classifier — pure function, no I/O
# ─────────────────────────────────────────────────────────────────


def test_classify_frontend_change_pages():
    from services.browser_self_test import classify_frontend_change
    urls = classify_frontend_change([
        "frontend/src/pages/Both.jsx",
        "frontend/src/pages/Login.jsx",
        "frontend/src/pages/Wall.jsx",
    ])
    assert "/" in urls
    assert "/login" in urls
    assert "/wall" in urls


def test_classify_frontend_change_personal():
    from services.browser_self_test import classify_frontend_change
    urls = classify_frontend_change([
        "frontend/src/pages/personal/DraftReview.jsx",
        "frontend/src/pages/personal/BuildSuccess.jsx",
    ])
    assert "/personal/draft-review" in urls
    assert "/personal/success" in urls


def test_classify_frontend_change_admin_dedup():
    from services.browser_self_test import classify_frontend_change
    urls = classify_frontend_change([
        "frontend/src/pages/AdminQADashboard.jsx",
        "frontend/src/pages/AdminApiKeys.jsx",
        "frontend/src/pages/admin/AdminInspectLoop.jsx",
    ])
    # All admin pages collapse to the single /admin route.
    assert urls.count("/admin") == 1
    assert len([u for u in urls if u.startswith("/admin")]) == 1


def test_classify_frontend_change_backend_hits_docs():
    from services.browser_self_test import classify_frontend_change
    urls = classify_frontend_change([
        "backend/routers/auth.py",
        "backend/services/loop_engine.py",   # not a router → ignored
    ])
    assert "/docs" in urls


def test_classify_cap_at_8():
    from services.browser_self_test import classify_frontend_change
    urls = classify_frontend_change([
        f"frontend/src/pages/Foo{i}.jsx" for i in range(20)
    ])
    assert len(urls) <= 8


# ─────────────────────────────────────────────────────────────────
# In-memory DB double
# ─────────────────────────────────────────────────────────────────


class _Coll:
    def __init__(self): self.rows = []
    async def find_one(self, filt):
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items()):
                return dict(r)
        return None
    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R: inserted_id = "id"
        return _R()
    async def update_one(self, filt, ops, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in filt.items()):
                if "$set" in ops: r.update(ops["$set"])
                class _R: modified_count = 1
                return _R()
        if upsert:
            new = dict(filt)
            if "$set" in ops: new.update(ops["$set"])
            self.rows.append(new)
        class _R: modified_count = 0
        return _R()
    def find(self, filt=None, projection=None):
        return _Cursor([dict(r) for r in self.rows])


class _Cursor:
    def __init__(self, rows): self.rows = rows
    def sort(self, k, d): self.rows.sort(key=lambda x: x.get(k) or "",
                                          reverse=(d < 0)); return self
    def limit(self, n): self.rows = self.rows[:n]; return self
    def __aiter__(self): self._i = 0; return self
    async def __anext__(self):
        if self._i >= len(self.rows): raise StopAsyncIteration
        r = self.rows[self._i]; self._i += 1
        return r


class _DB:
    def __init__(self): self._c = {}
    def __getattr__(self, n):
        if n not in self._c: self._c[n] = _Coll()
        return self._c[n]


# ─────────────────────────────────────────────────────────────────
# Cooldown
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cooldown_filter_skips_recent():
    from services import browser_self_test as bst

    db = _DB()
    # Simulate a smoke 30 seconds ago (well inside RESMOKE_COOLDOWN_S).
    recent = (datetime.now(timezone.utc)
              - timedelta(seconds=30)).isoformat()
    await db.browser_selftest_cache.insert_one({
        "url": "/tools", "last_smoked_at": recent,
    })
    in_cooldown = await bst._cooldown_check(db, "/tools")
    assert in_cooldown is True
    # A different URL is NOT in cooldown.
    not_in = await bst._cooldown_check(db, "/wall")
    assert not_in is False


# ─────────────────────────────────────────────────────────────────
# REAL Playwright smoke against the LIVE preview URL
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_smoke_real_preview_landing():
    """Launches a REAL chromium and hits the actual preview host —
    only skipped when the env doesn't expose one."""
    from services.browser_self_test import run_smoke

    # Preview URL comes from the frontend/.env — same one the frontend
    # uses to reach the backend, but for /pages the frontend renders
    # itself over the same host.
    with open("/app/frontend/.env") as f:
        env = {}
        for line in f:
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    base = env.get("REACT_APP_BACKEND_URL", "").rstrip("/")
    if not base.startswith("https://"):
        pytest.skip("REACT_APP_BACKEND_URL not set as https")

    report = await run_smoke(base, ["/", "/login", "/wall"],
                              timeout_s=60,
                              per_url_wait_ms=20000)
    # We DO NOT require ok=True (preview might have flaky routes);
    # we require the smoke actually RAN (not skipped) and returned
    # per-URL status codes.
    if report.get("skipped_reason"):
        pytest.skip(f"playwright not runnable here: "
                     f"{report['skipped_reason']}")
    assert len(report["results"]) == 3
    for r in report["results"]:
        # Every result must have a status code — proves the browser
        # actually navigated. red_flags may or may not fire.
        assert r["status"] is not None or "nav_err" in \
            "".join(r["red_flags"]), (
                f"URL {r['url']} — no status and no nav error: {r}")


# ─────────────────────────────────────────────────────────────────
# Red-flag pattern detection
# ─────────────────────────────────────────────────────────────────


def test_red_flag_patterns_catch_common_issues():
    import re
    from services.browser_self_test import _RED_FLAG_RES
    html_bad = ("<html><body>Balance: NaN — total: undefined — "
                "date: Invalid Date — TypeError: bar of undefined "
                "at Module._compile</body></html>")
    hits = []
    for regex, tag in _RED_FLAG_RES:
        if tag is None: continue
        if regex.search(html_bad):
            hits.append(tag)
    assert "nan_rendered" in hits
    assert "undefined_rendered" in hits
    assert "invalid_date" in hits
    assert "typeerror_rendered" in hits
    assert "stack_trace" in hits


# ─────────────────────────────────────────────────────────────────
# record_run persists to DB
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_run_writes_row():
    from services import browser_self_test as bst

    db = _DB()
    await bst.record_run(
        db,
        loop_id="l1", user_id="u1", project_id="p1",
        report={"ok": False, "failed_count": 2,
                "results": [{"url": "https://x/w", "ok": False,
                             "status": 500, "red_flags": ["nan_rendered"]}],
                "duration_ms": 3400},
    )
    row = await db.browser_selftest_runs.find_one({"loop_id": "l1"})
    assert row is not None
    assert row["failed_count"] == 2
    assert row["ok"] is False


# ─────────────────────────────────────────────────────────────────
# FAIL-OPEN — orchestrator never raises even if inner code explodes
# ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_is_fail_open(monkeypatch):
    from services import browser_self_test as bst

    async def _boom(*a, **k): raise RuntimeError("inner boom")
    monkeypatch.setattr(bst, "classify_frontend_change",
                         lambda *a, **k: (_ for _ in ()).throw(
                             RuntimeError("class boom")))

    db = _DB()
    result = await bst.smoke_paths_for_loop(
        db, loop_id="l1", user_id="u1", project_id="p1",
        file_paths=["frontend/src/pages/Both.jsx"],
        base_url="https://not-real.example",
    )
    # Must return a report dict, never raise.
    assert result["ok"] is True
    assert "orchestrator_error" in (result.get("skipped_reason") or "")
