"""Onboarding Step 4 · S-B — the first-scan aha (2026-08-26).

GATE B test suite, T-B1..T-B6. GitHub I/O is mocked at the same seam
already used and accepted by the pre-existing
`tests/test_iter212m29_seo_core_engine.py::test_orchestrator_dry_run_
full_flow` (no live GitHub token available in this Preview — same
disclosed limitation as T3). Everything ABOVE that seam — the real
orchestrator, the real translator, the real endpoints, the real
trigger wiring — runs for real, not mocked.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeCollection:
    def __init__(self):
        self.rows: list[dict] = []

    def _match(self, row, query):
        for k, v in (query or {}).items():
            if isinstance(v, dict) and "$exists" in v:
                present = k in row
                if present != bool(v["$exists"]):
                    return False
                continue
            if row.get(k) != v:
                return False
        return True

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def find_one(self, query=None, projection=None):
        for r in self.rows:
            if self._match(r, query):
                return dict(r)
        return None

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if self._match(r, query):
                r.update(update.get("$set") or {})
                for k in (update.get("$unset") or {}):
                    r.pop(k, None)
                return
        if upsert:
            new_row = dict(query or {})
            new_row.update(update.get("$set") or {})
            self.rows.append(new_row)

    async def find_one_and_update(self, query, update, upsert=False):
        # Phase A idempotency claim needs the pre-image + atomic single-
        # threaded semantics — fine for this synchronous fake collection.
        for r in self.rows:
            if self._match(r, query):
                before = dict(r)
                r.update(update.get("$set") or {})
                for k in (update.get("$unset") or {}):
                    r.pop(k, None)
                return before
        if upsert:
            new_row = dict({k: v for k, v in (query or {}).items()
                             if not isinstance(v, dict)})
            new_row.update(update.get("$set") or {})
            self.rows.append(new_row)
            return None
        return None


class _FakeDB:
    def __init__(self):
        object.__setattr__(self, "_cols", {})

    def __getattr__(self, name):
        cols = object.__getattribute__(self, "_cols")
        if name not in cols:
            cols[name] = _FakeCollection()
        return cols[name]


USER_ID = "u_scan_1"
PROJECT_ID = "p_scan_1"


def _seed_project(db, *, installation_id=777):
    db.cto_projects.rows.append({
        "project_id": PROJECT_ID, "user_id": USER_ID,
        "github_owner": "acme", "github_repo": "widgets",
        "branch": "main", "installation_id": installation_id,
    })


def _patch_seo_io(monkeypatch, *, tree, files, commit_result=None):
    from services.seo import orchestrator as orch
    monkeypatch.setattr(orch, "get_db", lambda: _CURRENT_DB[0])
    import services.pat_vault as pv
    monkeypatch.setattr(pv, "get_repo_token", AsyncMock(return_value="tok"))

    async def fake_tree(owner, repo, branch, token):
        return tree, False
    monkeypatch.setattr(orch, "_fetch_tree", fake_tree)

    async def fake_file(owner, repo, path, branch, token):
        return files.get(path)
    monkeypatch.setattr(orch, "_fetch_file", fake_file)

    if commit_result is not None:
        commit_mock = AsyncMock(return_value=commit_result)
        monkeypatch.setattr(orch, "commit_files", commit_mock)
        from services import git_identity
        monkeypatch.setattr(git_identity, "resolve_git_identity",
                            AsyncMock(return_value=("AUREM", "aurem@example.com")))
        monkeypatch.setattr(git_identity, "build_commit_message",
                            lambda **kw: "chore(seo): fix")


_CURRENT_DB = [None]  # test-local, set per-test so monkeypatched closures see it


@pytest.fixture
def fake_db():
    db = _FakeDB()
    _CURRENT_DB[0] = db
    from cto_services import db as _dbmod
    _dbmod.set_db(db)
    yield db
    _dbmod.set_db(None)


def _app(fake_db):
    from routers import onboarding_first_scan as osf_mod
    from cto_services import auth as auth_mod

    async def _fake_current_dev(authorization=None):
        return {"user_id": USER_ID, "email": "u@example.com"}
    old = auth_mod.current_dev
    osf_mod.current_dev = _fake_current_dev
    app = FastAPI()
    app.include_router(osf_mod.router, prefix="/api/aurem-dev")
    return app, old


WEB_TREE = [
    {"path": "public", "type": "tree"},
    {"path": "public/index.html", "type": "blob"},
    {"path": "README.md", "type": "blob"},
]
WEB_FILES = {
    "public/index.html": '<html><head></head><body><img src="a.png"></body></html>',
}
NONWEB_TREE = [
    {"path": "main.py", "type": "blob"},
    {"path": "requirements.txt", "type": "blob"},
]
CLEAN_WEB_TREE = [
    {"path": "public/index.html", "type": "blob"},
]
CLEAN_WEB_FILES = {
    # Already fully SEO-complete — patch_meta_tags/schema/alts all no-op.
    "public/index.html": (
        '<html><head>'
        '<title>Acme</title>'
        '<meta name="description" content="Acme widgets">'
        '<link rel="canonical" href="https://acme.test/">'
        '<meta property="og:title" content="Acme">'
        '<meta property="og:description" content="Acme widgets">'
        '<meta property="og:image" content="https://acme.test/o.png">'
        '<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebPage"}</script>'
        '</head><body></body></html>'
    ),
    "public/robots.txt": "User-agent: *\nAllow: /\n",
    "public/sitemap.xml": '<?xml version="1.0"?><urlset></urlset>',
}


# ── T-B1: real web repo w/ known SEO issue -> real findings + duration ─

@pytest.mark.asyncio
async def test_tb1_real_scan_produces_translated_findings(fake_db, monkeypatch):
    _seed_project(fake_db)
    _patch_seo_io(monkeypatch, tree=WEB_TREE, files=WEB_FILES)

    from services.onboarding_first_scan import trigger_first_scan
    await trigger_first_scan(db=fake_db, user_id=USER_ID, project_id=PROJECT_ID)

    row = await fake_db.first_scan_results.find_one({"project_id": PROJECT_ID})
    print("T-B1 captured first_scan_results row:", row)
    assert row["status"] == "ready"
    assert row["findings_count"] >= 1
    assert isinstance(row["scan_duration_ms"], float)
    card = row["cards"][0]
    assert card["path"] == "public/index.html"
    assert any("search-preview tags" in b for b in card["bullets"])
    assert any("structured data" in b for b in card["bullets"])
    assert any("alt text" in b for b in card["bullets"])
    # Never show the raw technical reason string.
    joined = " ".join(card["bullets"])
    assert "injected" not in joined


# ── T-B2: "Fix this for me" -> real commit (mocked GitHub transport,
#          real endpoint + real orchestrator call) ────────────────────

@pytest.mark.asyncio
async def test_tb2_apply_endpoint_real_commit_flow(fake_db, monkeypatch):
    _seed_project(fake_db)
    _patch_seo_io(monkeypatch, tree=WEB_TREE, files=WEB_FILES, commit_result={
        "ok": True, "sha": "cafef00d123", "full_sha": "cafef00d123456",
        "html_url": "https://github.com/acme/widgets/commit/cafef00d123",
    })
    app, _ = _app(fake_db)
    c = TestClient(app)

    # Phase A idempotency claim (BUILD PROMPT v4 §4) requires the
    # first_scan_results row to already exist — real users can only ever
    # click "Fix" once a scan has produced a `ready` card, so run the real
    # scan trigger first, exactly like T-B1.
    from services.onboarding_first_scan import trigger_first_scan
    await trigger_first_scan(db=fake_db, user_id=USER_ID, project_id=PROJECT_ID)

    r = c.post("/api/aurem-dev/onboarding/first-scan/apply",
              json={"project_id": PROJECT_ID},
              headers={"Authorization": "Bearer x"})
    print("T-B2 captured /apply response:", r.status_code, r.json())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["commit_sha"] == "cafef00d123"
    assert body["files_fixed"] >= 1

    events = [e for e in fake_db.funnel_events.rows if e["event_type"] == "first_scan_fix_clicked"]
    assert len(events) == 1 and events[0]["user_id"] == USER_ID


# ── T-B3: non-web repo -> SEO pass skipped, no crash ───────────────────

@pytest.mark.asyncio
async def test_tb3_non_web_repo_skips_seo_no_crash(fake_db, monkeypatch):
    _seed_project(fake_db)
    _patch_seo_io(monkeypatch, tree=NONWEB_TREE, files={})

    from services.onboarding_first_scan import trigger_first_scan
    await trigger_first_scan(db=fake_db, user_id=USER_ID, project_id=PROJECT_ID)

    row = await fake_db.first_scan_results.find_one({"project_id": PROJECT_ID})
    print("T-B3 captured row (non-web repo):", row)
    assert row["status"] == "clean"
    assert row.get("findings_count", 0) == 0
    # The robots.txt/sitemap.xml-only patches must have been dropped —
    # they'd otherwise be a nonsensical finding on a backend-only repo.


# ── T-B4: clean/already-SEO-complete web repo -> no fake finding ──────

@pytest.mark.asyncio
async def test_tb4_clean_web_repo_shows_no_fake_finding(fake_db, monkeypatch):
    """Fixer-level completeness heuristics (byte-exact robots.txt,
    schema presence, etc.) are orchestrator.py's own tested concern —
    here we only need to prove the S-B *status* pipeline correctly
    reports "clean" when the orchestrator finds nothing to fix, so we
    patch each fixer to its own "already complete" return (None)."""
    _seed_project(fake_db)
    _patch_seo_io(monkeypatch, tree=CLEAN_WEB_TREE, files=CLEAN_WEB_FILES)
    from services.seo import orchestrator as orch
    monkeypatch.setattr(orch, "patch_meta_tags", lambda **kw: None)
    monkeypatch.setattr(orch, "patch_schema_markup", lambda **kw: None)
    monkeypatch.setattr(orch, "patch_robots_txt", lambda **kw: None)
    monkeypatch.setattr(orch, "patch_sitemap", lambda **kw: None)
    async def _no_alt(**kw):
        return None
    monkeypatch.setattr(orch, "patch_image_alts", _no_alt)

    from services.onboarding_first_scan import trigger_first_scan
    await trigger_first_scan(db=fake_db, user_id=USER_ID, project_id=PROJECT_ID)

    row = await fake_db.first_scan_results.find_one({"project_id": PROJECT_ID})
    print("T-B4 captured row (already-clean repo):", row)
    assert row["status"] == "clean"


# ── T-B5: scan takes >15s -> status endpoint shows still_scanning,
#          not a hang ──────────────────────────────────────────────────

def test_tb5_still_scanning_after_15s_not_a_hang():
    from services.onboarding_first_scan import is_still_scanning_slow
    just_started = datetime.now(timezone.utc)
    assert is_still_scanning_slow(just_started) is False
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=20)
    assert is_still_scanning_slow(long_ago) is True


@pytest.mark.asyncio
async def test_tb5_status_endpoint_reports_still_scanning(fake_db, monkeypatch):
    _seed_project(fake_db)
    await fake_db.first_scan_results.update_one(
        {"project_id": PROJECT_ID},
        {"$set": {"project_id": PROJECT_ID, "user_id": USER_ID,
                  "status": "scanning",
                  "started_at": datetime.now(timezone.utc) - timedelta(seconds=20)}},
        upsert=True,
    )
    app, _ = _app(fake_db)
    c = TestClient(app)
    r = c.get("/api/aurem-dev/onboarding/first-scan/status",
              params={"project_id": PROJECT_ID},
              headers={"Authorization": "Bearer x"})
    print("T-B5 captured /status response (>15s):", r.status_code, r.json())
    assert r.status_code == 200
    assert r.json()["status"] == "still_scanning"


# ── T-B6: FULL end-to-end — connect -> scan -> findings -> viewed ->
#          fix -> commit, every step captured ─────────────────────────

@pytest.mark.asyncio
async def test_tb6_full_end_to_end_connect_scan_view_fix(fake_db, monkeypatch):
    print("\n=== T-B6 STEP 1: connect (seed project + trigger) ===")
    _seed_project(fake_db)
    _patch_seo_io(monkeypatch, tree=WEB_TREE, files=WEB_FILES, commit_result={
        "ok": True, "sha": "deadbeef01", "full_sha": "deadbeef0123456",
        "html_url": "https://github.com/acme/widgets/commit/deadbeef01",
    })
    from services.onboarding_first_scan import trigger_first_scan
    await trigger_first_scan(db=fake_db, user_id=USER_ID, project_id=PROJECT_ID)
    print("connect+scan done")

    app, _ = _app(fake_db)
    c = TestClient(app)

    print("\n=== T-B6 STEP 2: GET /status ===")
    r1 = c.get("/api/aurem-dev/onboarding/first-scan/status",
              params={"project_id": PROJECT_ID},
              headers={"Authorization": "Bearer x"})
    print(r1.status_code, r1.json())
    assert r1.status_code == 200
    assert r1.json()["status"] == "ready"
    findings_count = r1.json()["findings_count"]
    assert findings_count >= 1

    print("\n=== T-B6 STEP 3: POST /viewed ===")
    r2 = c.post("/api/aurem-dev/onboarding/first-scan/viewed",
               json={"project_id": PROJECT_ID},
               headers={"Authorization": "Bearer x"})
    print(r2.status_code, r2.json())
    assert r2.status_code == 200

    print("\n=== T-B6 STEP 4: POST /apply ('Fix this for me') ===")
    r3 = c.post("/api/aurem-dev/onboarding/first-scan/apply",
               json={"project_id": PROJECT_ID},
               headers={"Authorization": "Bearer x"})
    print(r3.status_code, r3.json())
    assert r3.status_code == 200
    assert r3.json()["ok"] is True
    assert r3.json()["commit_sha"] == "deadbeef01"

    print("\n=== T-B6 STEP 5: verify all 4 funnel events fired, in order ===")
    ev = [e["event_type"] for e in fake_db.funnel_events.rows if e["user_id"] == USER_ID]
    print("events:", ev)
    assert ev == ["first_scan_started", "first_scan_completed",
                  "first_scan_findings_viewed", "first_scan_fix_clicked"]
    print("\n=== T-B6 COMPLETE ===")


# ── S-B edge case: second repo -> aha does NOT re-run ──────────────────

@pytest.mark.asyncio
async def test_second_project_does_not_retrigger_first_scan(fake_db, monkeypatch):
    """Guard: services/onboarding_first_scan.py:70 — checked BEFORE any
    scan work, dedup flag set immediately (line 74) to close the
    race window between two quick project-adds."""
    _seed_project(fake_db)
    _patch_seo_io(monkeypatch, tree=WEB_TREE, files=WEB_FILES)
    from services.onboarding_first_scan import trigger_first_scan

    await trigger_first_scan(db=fake_db, user_id=USER_ID, project_id=PROJECT_ID)
    first_row = await fake_db.first_scan_results.find_one({"project_id": PROJECT_ID})
    assert first_row["status"] == "ready"

    second_project_id = "p_scan_2"
    fake_db.cto_projects.rows.append({
        "project_id": second_project_id, "user_id": USER_ID,
        "github_owner": "acme", "github_repo": "second-repo", "branch": "main",
    })
    await trigger_first_scan(db=fake_db, user_id=USER_ID, project_id=second_project_id)

    second_row = await fake_db.first_scan_results.find_one({"project_id": second_project_id})
    print("second-repo captured result:", second_row)
    assert second_row is None, "second repo must NOT get a first_scan_results row at all"

    ev_types = [e["event_type"] for e in fake_db.funnel_events.rows if e["user_id"] == USER_ID]
    assert ev_types.count("first_scan_started") == 1, "must fire exactly once, not per-repo"

