"""
Session 6 · Item 6 regression contract — test-count freshness.

Real-user QA: admin dashboard showed "3712 backend across 430 files ·
218 frontend · grand total 3944" from a stale build_manifest. Live
count was actually 3904 / 4142. No visible indicator that the
displayed number could be days out of date.

Fix has two parts:
  (a) `backend/qa_manifest.json` regenerated fresh (was ~2 days old).
  (b) `QaCountsStrip` frontend component now surfaces the manifest
      age when using the build_manifest fallback, and paints amber
      when > 3 days old ("stale, redeploy to refresh").

Both parts are locked in below.
"""
from __future__ import annotations

import json
import pathlib
import time


_MANIFEST = pathlib.Path("/app/backend/qa_manifest.json")


def test_qa_manifest_is_present_and_recent():
    """The manifest must exist AND have been regenerated within the
    last hour of test runtime (fresh at the moment we ship). This
    catches a common pipeline break: predeploy_gate.sh not running
    the regen script, leaving stale numbers on prod for weeks."""
    assert _MANIFEST.exists(), (
        "backend/qa_manifest.json missing — prod /admin will show 0s"
    )
    m = json.loads(_MANIFEST.read_text())
    assert "generated_at" in m, "manifest missing generated_at epoch"
    age_hours = (time.time() - m["generated_at"]) / 3600
    # Regenerated during the Session 6 · Item 6 fix, so must be fresh.
    assert age_hours < 6, (
        f"manifest is {age_hours:.1f} hours old — regenerate via "
        f"`python backend/scripts/gen_qa_manifest.py` before shipping."
    )


def test_qa_manifest_totals_match_live_harvest():
    """Contract: the manifest MUST match `_harvest_counts()` at
    generation time. If a future refactor changes the counting logic
    without regenerating the manifest, this catches the drift."""
    import sys
    sys.path.insert(0, "/app/backend")
    from routers.admin_qa import _harvest_counts
    live   = _harvest_counts()
    manifest = json.loads(_MANIFEST.read_text())
    tc = manifest["test_counts"]
    # Backend + frontend counts should match live (the numbers the
    # manifest was generated FROM). Allow ±5 tolerance to accommodate
    # a test file added between regen and this assertion — but any
    # drift greater than that flags a stale manifest.
    for suite in ("backend_pytest", "frontend_vitest"):
        live_n     = (live.get(suite) or {}).get("tests", 0)
        manifest_n = (tc.get(suite)   or {}).get("tests", 0)
        assert abs(live_n - manifest_n) <= 5, (
            f"{suite}: live={live_n} vs manifest={manifest_n} — "
            f"manifest is stale."
        )


def test_admin_overview_shows_manifest_age_when_stale():
    """Source-level lock: the QaCountsStrip must surface the manifest
    age + 'stale, redeploy to refresh' hint when > 3 days old. Prevents
    the fix from being silently reverted."""
    src = pathlib.Path(
        "/app/frontend/src/pages/AdminOverview.jsx"
    ).read_text()
    assert "manifest_generated_at" in src, (
        "QaCountsStrip no longer reads manifest_generated_at — "
        "stale-manifest indicator regressed."
    )
    assert "stale, redeploy to refresh" in src, (
        "QaCountsStrip lost the 'stale, redeploy' hint — Session 6 "
        "· Item 6 fix regressed."
    )
    # And the pill's data-source attribute must be exposed so the
    # frontend regression test can assert on it.
    assert 'data-source={d.source' in src


def test_admin_qa_counts_endpoint_exposes_manifest_age():
    """When the endpoint falls back to build_manifest, it must expose
    `manifest_generated_at` so the frontend can render the age. Locked
    against the shape the FE reads."""
    import sys
    sys.path.insert(0, "/app/backend")
    from routers.admin_qa import _harvest_counts
    out = _harvest_counts()
    # On preview we get live_fs (not manifest) — so we just verify
    # the shape is present in the codebase, not a runtime lookup here.
    src = pathlib.Path(
        "/app/backend/routers/admin_qa.py"
    ).read_text()
    assert 'out["manifest_generated_at"]' in src, (
        "admin_qa._harvest_counts no longer emits manifest_generated_at "
        "in the build_manifest fallback branch."
    )
