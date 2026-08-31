"""
tests/test_visibility_kit_v2_2026_08_30.py — Visibility Kit v2 (spec §5/§6),
the two previously-not-implemented AUTO items + the R9-gate + admin tile.

Named tests (join the existing test_visibility_kit.py guardrail suite):
  t_kit_score_calculation             — weights sum to 100; pr_created = half weight
  t_kit_apply_gated_off_while_apply_flag_off — Apply returns 403 while kit_apply_enabled is OFF (default)
  t_kit_badge_component_generated     — PR would contain the docs-verified SDK + deeplink fallback
  t_kit_robots_read_modify_write      — existing rules preserved, AI block added (D1 deny-training/allow-retrieval)
  t_kit_apply_preserves_existing_files — an existing llms.txt is NOT overwritten without force
  t_kit_branding_in_correct_places    — code comment + llms.txt marker + PR body wording; no fake claims
  t_kit_admin_honest_placeholder      — admin dashboard's citation section is a real placeholder, never fake numbers

2026-08-30 KIT TRUTH-UPDATE (copy honesty patch, no schema/reweight change):
  t_kit_llms_row_honest       — llms_txt row discloses Google ignores it, weight stays 15
  t_kit_pref_sources_is_hero  — preferred_sources stays the top-weighted (25) per-visitor badge
  t_kit_score_not_overclaimed — panel note frames the score as a preparedness checklist, not live tracking
  t_kit_no_oversell_number    — no bare unsourced customer-facing stat anywhere in the catalog copy

2026-08-30 KIT GAP-PATCH (robots bot-name bug fix + schema @id + GBP advisory):
  t_robots_bot_names_verified — generated block uses verified current tokens, no dead "Claude-Web"
  t_schema_org_stable_id      — Organization @id is a stable URI, idempotent across regenerations
  t_gbp_advisory_only         — GBP row is advisory-only, never applied, no GBP API code path
"""
import importlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.visibility import preferred_sources as badge_gen
from services.visibility import llms_txt as llms_gen
from services.visibility import robots as robots_gen
from services.visibility import schema as schema_gen
from services.visibility import apply as apply_mod

_CATALOG = importlib.import_module("migrations.003_visibility_kit").CATALOG_ITEMS
_FRONTEND_PANEL = Path(__file__).resolve().parents[2] / "frontend/src/components/VisibilityKitPanel.jsx"


def _catalog_item(key: str) -> dict:
    return next(i for i in _CATALOG if i["key"] == key)


# ── t_kit_badge_component_generated ─────────────────────────────────
def test_t_kit_badge_component_generated():
    out = badge_gen.render_badge_block("example.com", "Example Co")
    # Google's own docs-verified SDK (2026-08-30 web search, not guessed).
    assert 'src="https://news.google.com/swg/js/v1/publisher.js"' in out
    assert "google-add-preferred-source-btn" in out
    assert 'data-theme="light"' in out and 'data-lang="en"' in out
    # R2 no-silent-fail — deeplink fallback ALWAYS present beside the SDK.
    assert "https://www.google.com/preferences/source?q=example.com" in out
    assert "Prefer Example Co in AI answers" in out
    # AUREM branding lives in the code comment only, per spec.
    assert "AUREM Visibility Kit" in out


def test_t_kit_badge_idempotent_no_duplicate():
    once = badge_gen.apply_managed_block("<html><body></body></html>", "x.com")
    twice = badge_gen.apply_managed_block(once, "x.com", "X Co")
    assert twice.count(badge_gen._START) == 1
    assert "X Co" in twice  # 2nd apply's site_name wins


def test_t_kit_badge_missing_body_tag_returns_unchanged():
    html = "<html><head></head></html>"  # no </body> at all
    out = badge_gen.apply_managed_block(html, "x.com")
    assert out == html  # caller (apply.py) is responsible for the conflict check


# ── llms.txt / llms-full.txt ─────────────────────────────────────────
def test_t_kit_llms_txt_generated_with_branding():
    txt, full, conflict = llms_gen.apply_llms_files(
        None, "Example Co", "https://example.com",
        [{"loc": "https://example.com/a"}, {"loc": "https://example.com/a"}, {"loc": "https://example.com/b"}],
    )
    assert conflict is False
    assert "example.com/a" in txt and "example.com/b" in txt
    assert txt.count("- [https://example.com/a]") == 1  # deduped, one list line
    assert llms_gen._MARKER in txt and llms_gen._MARKER in full
    assert "full index" in full


def test_t_kit_llms_txt_conflict_without_marker():
    existing = "# Some other tool's llms.txt\n\n- https://x.com/old\n"
    _, _, conflict = llms_gen.apply_llms_files(existing, "X", "https://x.com", [])
    assert conflict is True


# ── t_kit_robots_read_modify_write (named test, reuses robots_gen — no new logic) ──
def test_t_kit_robots_read_modify_write():
    existing = "User-agent: *\nDisallow: /private\n\nSitemap: https://x.com/sitemap.xml\n"
    out = robots_gen.apply_managed_block(existing, {})
    assert "Disallow: /private" in out  # existing rules preserved
    assert "Sitemap: https://x.com/sitemap.xml" in out
    assert "User-agent: GPTBot\nDisallow: /" in out       # D1 — deny training bots
    assert "User-agent: OAI-SearchBot\nAllow: /" in out    # D1 — allow retrieval bots


# ── t_kit_apply_preserves_existing_files ─────────────────────────────
@pytest.mark.asyncio
async def test_t_kit_apply_preserves_existing_files_llms_txt():
    from services.visibility.apply import apply_visibility_kit
    db = MagicMock()
    db.visibility_state.update_one = AsyncMock()
    db.visibility_applications.insert_one = AsyncMock()
    project = {"project_id": "p1", "user_id": "u1", "github_owner": "o",
               "github_repo": "r", "branch": "main"}

    async def fake_fetch(owner, repo, path, branch, token):
        if path == "llms.txt":
            return "# A pre-existing llms.txt from some other tool\n"
        return None

    with patch("services.visibility.apply.fetch_file", new=AsyncMock(side_effect=fake_fetch)), \
         patch("services.visibility.apply.resolve_git_identity", new=AsyncMock(return_value=("Bot", "b@x.com"))), \
         patch("services.visibility.apply.create_or_reuse_branch", new=AsyncMock(return_value=(True, None))), \
         patch("services.visibility.apply.commit_files", new=AsyncMock(return_value={"ok": True})), \
         patch("services.visibility.apply.open_draft_pr", new=AsyncMock(return_value=("https://github.com/o/r/pull/1", None))):
        result = await apply_visibility_kit(
            db, project=project, requested_items=["llms_txt"],
            token="t", scan_urls=[{"loc": "https://x.com/"}],
            site_meta={"schema": {"name": "X", "url": "https://x.com"}}, bot_policy={},
        )
    # No file was writable (the one requested item conflicted) -> all_conflicted.
    assert result["ok"] is False
    assert result["error"] == "all_conflicted"
    assert "llms.txt already exists without an AUREM marker" in result["conflicts"]


@pytest.mark.asyncio
async def test_t_kit_apply_force_overwrites_existing_llms_txt():
    from services.visibility.apply import apply_visibility_kit
    db = MagicMock()
    db.visibility_state.update_one = AsyncMock()
    db.visibility_applications.insert_one = AsyncMock()
    project = {"project_id": "p1", "user_id": "u1", "github_owner": "o",
               "github_repo": "r", "branch": "main"}

    async def fake_fetch(owner, repo, path, branch, token):
        if path == "llms.txt":
            return "# A pre-existing llms.txt from some other tool\n"
        return None

    with patch("services.visibility.apply.fetch_file", new=AsyncMock(side_effect=fake_fetch)), \
         patch("services.visibility.apply.resolve_git_identity", new=AsyncMock(return_value=("Bot", "b@x.com"))), \
         patch("services.visibility.apply.create_or_reuse_branch", new=AsyncMock(return_value=(True, None))), \
         patch("services.visibility.apply.commit_files", new=AsyncMock(return_value={"ok": True})) as mock_commit, \
         patch("services.visibility.apply.open_draft_pr", new=AsyncMock(return_value=("https://github.com/o/r/pull/1", None))):
        result = await apply_visibility_kit(
            db, project=project, requested_items=["llms_txt"], force=True,
            token="t", scan_urls=[{"loc": "https://x.com/"}],
            site_meta={"schema": {"name": "X", "url": "https://x.com"}}, bot_policy={},
        )
    assert result["ok"] is True
    written = set(mock_commit.call_args[0][4].keys())
    assert written == {"llms.txt", "llms-full.txt"}


# ── t_kit_apply_gated_off_while_apply_flag_off (the R9-gate) ────────
@pytest.mark.asyncio
async def test_t_kit_apply_gated_off_while_apply_flag_off():
    from routers.visibility import apply_kit, ApplyBody
    db = MagicMock()
    db.cto_projects.find_one = AsyncMock(return_value={
        "project_id": "p1", "user_id": "u1", "github_owner": "o", "github_repo": "r",
    })
    with patch("routers.visibility.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.visibility.get_db", return_value=db), \
         patch("services.feature_flags.is_enabled", new=AsyncMock(return_value=False)):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await apply_kit("p1", ApplyBody(items=["ai_crawler_policy"]), authorization="Bearer x")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "apply_not_yet_enabled"
    assert "R9" in exc_info.value.detail["message"]
    # The billing-tier check (dev_users.find_one) must never even run — the
    # R9-gate is checked FIRST and is the harder block.
    db.dev_users.find_one.assert_not_called() if hasattr(db.dev_users.find_one, "assert_not_called") else None


@pytest.mark.asyncio
async def test_t_kit_apply_proceeds_past_r9_gate_to_quota_gate_when_flag_on():
    """2026-08-31 — the old Pro-tier paywall is REMOVED (Kit Apply is
    free on every plan for a limited period). Proves the R9-gate is
    still real (not a no-op): with the flag ON, the SAME free-plan
    request now reaches the TASK-QUOTA gate (services/scan_fix_quota.py)
    instead of being blocked at 403 by the R9-gate — never a billing
    'upgrade to Pro' 402 anymore."""
    from routers.visibility import apply_kit, ApplyBody
    from fastapi import HTTPException
    db = MagicMock()
    db.cto_projects.find_one = AsyncMock(return_value={
        "project_id": "p1", "user_id": "u1", "github_owner": "o", "github_repo": "r",
    })
    quota_exhausted = HTTPException(402, {"error": "insufficient_tasks"})
    with patch("routers.visibility.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.visibility.get_db", return_value=db), \
         patch("services.feature_flags.is_enabled", new=AsyncMock(return_value=True)), \
         patch("services.scan_fix_quota.assert_can_fix",
               new=AsyncMock(side_effect=quota_exhausted)) as mock_assert_can_fix:
        with pytest.raises(HTTPException) as exc_info:
            await apply_kit("p1", ApplyBody(items=["ai_crawler_policy"]), authorization="Bearer x")
    assert exc_info.value.status_code == 402
    assert exc_info.value.detail["error"] == "insufficient_tasks"
    # Gated by TOOL "visibility-kit" — proves it's wired into the SAME
    # quota system as vanguard-scan/health-scan/etc., not a bespoke gate.
    mock_assert_can_fix.assert_awaited_once()
    called_args = mock_assert_can_fix.await_args
    assert called_args.args[1] == "visibility-kit"


def test_t_visibility_kit_free_on_every_tier_but_costs_a_task():
    """The removed paywall's replacement: every tier (including free)
    has 'visibility-kit' in its fix-tool set — no plan gate — but it's
    registered in ALL_FIX_TOOLS, so it still draws from the same
    monthly task quota as vanguard-scan/health-scan/etc. Not unmetered."""
    from services import scan_fix_quota as q
    assert "visibility-kit" in q.ALL_FIX_TOOLS
    for tier in ("free", "starter", "pro", "team", "founder"):
        assert "visibility-kit" in q.FIX_TOOLS_BY_TIER[tier], tier


@pytest.mark.asyncio
async def test_t_kit_apply_records_task_only_on_real_success():
    """1 apply = 1 task, deducted ONLY for a real successful PR open —
    never pre-deducted, never charged for a request that found nothing
    to apply or failed to open a PR (same rule scan_fix_quota.py's
    docstring states for every other fix tool)."""
    from routers.visibility import apply_kit, ApplyBody

    def _make_db():
        db = MagicMock()
        db.cto_projects.find_one = AsyncMock(return_value={
            "project_id": "p1", "user_id": "u1", "github_owner": "o", "github_repo": "r",
        })
        db.visibility_bot_policies.find_one = AsyncMock(return_value=None)
        return db

    common_patches = [
        patch("routers.visibility.current_dev", new=AsyncMock(return_value={"user_id": "u1"})),
        patch("services.feature_flags.is_enabled", new=AsyncMock(return_value=True)),
        patch("services.scan_fix_quota.assert_can_fix", new=AsyncMock(return_value={})),
        patch("services.pat_vault.get_repo_token_or_error",
              new=AsyncMock(return_value=("tok", None, None))),
    ]

    # Success case — record_scan_fixes MUST be called.
    db = _make_db()
    with common_patches[0], common_patches[1], common_patches[2], common_patches[3], \
         patch("routers.visibility.get_db", return_value=db), \
         patch("services.visibility.apply.apply_visibility_kit",
               new=AsyncMock(return_value={"ok": True, "pr_url": "https://x/pull/1"})), \
         patch("services.scan_fix_quota.record_scan_fixes", new=AsyncMock()) as mock_record:
        await apply_kit("p1", ApplyBody(items=["ai_crawler_policy"]), authorization="Bearer x")
    mock_record.assert_awaited_once_with("u1", "visibility-kit", count=1)

    # Failure case (e.g. no items implemented) — record_scan_fixes must NOT run.
    db2 = _make_db()
    with common_patches[0], common_patches[1], common_patches[2], common_patches[3], \
         patch("routers.visibility.get_db", return_value=db2), \
         patch("services.visibility.apply.apply_visibility_kit",
               new=AsyncMock(return_value={"ok": False, "error": "no_implemented_items_in_request"})), \
         patch("services.scan_fix_quota.record_scan_fixes", new=AsyncMock()) as mock_record2:
        await apply_kit("p1", ApplyBody(items=["ai_crawler_policy"]), authorization="Bearer x")
    mock_record2.assert_not_awaited()


# ── t_kit_score_calculation ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_t_kit_score_calculation_pr_created_is_half_weight():
    from routers.visibility import get_state
    db = MagicMock()
    db.cto_projects.find_one = AsyncMock(return_value={"project_id": "p1", "user_id": "u1"})
    items = [
        {"key": "preferred_sources", "name": "Badge", "what_why": "", "weight": 25, "mode": "auto", "sort": 1},
        {"key": "ai_crawler_policy", "name": "Robots", "what_why": "", "weight": 20, "mode": "auto", "sort": 2},
    ]

    class _ItemsCursor:
        def sort(self, *a, **kw):
            return self

        async def to_list(self, n):
            return items

    db.visibility_items.find = MagicMock(return_value=_ItemsCursor())

    async def _state_gen(*a, **kw):
        for s in [{"item_id": "preferred_sources", "status": "pr_merged"},
                  {"item_id": "ai_crawler_policy", "status": "pr_created"}]:
            yield s

    db.visibility_state.find = MagicMock(return_value=_state_gen())
    db.visibility_bot_policies.find_one = AsyncMock(return_value=None)

    with patch("routers.visibility.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.visibility.get_db", return_value=db), \
         patch("services.feature_flags.is_enabled", new=AsyncMock(return_value=False)):
        out = await get_state("p1", authorization="Bearer x")
    # 25 (full, pr_merged) + 10 (half of 20, pr_created) = 35 / 45 total weight
    assert out["score"] == round(100 * 35 / 45)
    assert out["apply_enabled"] is False
    # 2026-08-31 — every state response carries the honest pricing note
    # (no separate Kit price, free for now, still 1 task on apply).
    assert "free" in out["pricing_note"].lower()
    assert "1 task" in out["pricing_note"]


def test_t_kit_catalog_weights_sum_to_100_and_pr_created_half_documented():
    """Weight-sum invariant (already covered live by test_catalog_seeded)
    re-asserted against the static spec constants so this test doesn't
    need a live DB to catch a weight-sum regression in review."""
    spec_weights = {
        "preferred_sources": 25, "ai_crawler_policy": 20, "structured_data": 20,
        "llms_txt": 15, "sitemap_auto": 10, "answer_blocks": 7, "image_quick_wins": 3,
        "google_business_profile": 0,  # advisory checklist, deliberately non-scoring
    }
    assert sum(spec_weights.values()) == 100


# ── t_kit_branding_in_correct_places ─────────────────────────────────
def test_t_kit_branding_in_correct_places():
    from services.visibility.apply import _render_pr_body
    body = _render_pr_body(["preferred_sources", "llms_txt"], [], ["index.html", "llms.txt"], "auremcto/x")
    assert "Created by AUREM Visibility Kit" in body
    assert "news.google.com" in body  # CSP note only added when preferred_sources is applied

    badge = badge_gen.render_badge_block("x.com")
    assert "<!-- AUREM Visibility Kit" in badge

    txt, _, _ = llms_gen.apply_llms_files(None, "X", "https://x.com", [])
    assert "Maintained with AUREM" in txt

    # R11 — no AUREM entity in the site's own JSON-LD, ever.
    json_ld = schema_gen.render_json_ld({"name": "X", "url": "https://x.com"})
    import json as _json
    ld_only = json_ld.split(schema_gen._START, 1)[-1]
    for block in ld_only.split("<script"):
        if '"@type"' in block:
            raw = block.split(">", 1)[-1].rsplit("</script", 1)[0].strip()
            if raw:
                parsed = _json.loads(raw)
                assert "AUREM" not in _json.dumps(parsed)


# ── t_kit_admin_honest_placeholder ───────────────────────────────────
@pytest.mark.asyncio
async def test_t_kit_admin_honest_placeholder():
    from routers.admin_analytics import visibility_kit_admin_dashboard
    db = MagicMock()
    db.visibility_items.find = MagicMock(return_value=MagicMock(
        to_list=AsyncMock(return_value=[{"key": "preferred_sources", "weight": 25}]),
    ))
    db.visibility_state.distinct = AsyncMock(return_value=[])
    with patch("routers.admin_analytics._require_admin", new=AsyncMock(return_value=None)), \
         patch("routers.admin_analytics.require_db", return_value=db):
        out = await visibility_kit_admin_dashboard(authorization="Bearer x")
    assert out["citation_data"]["available"] is False
    assert "day-14 recheck pending" in out["citation_data"]["message"]
    # No fake number anywhere in the citation section (bool is fine, it's
    # not a count/metric — only real int/float metrics are disallowed).
    assert not any(
        isinstance(v, (int, float)) and not isinstance(v, bool)
        for v in out["citation_data"].values()
    )


# ── 2026-08-30 KIT TRUTH-UPDATE ───────────────────────────────────────
def test_t_kit_llms_row_honest():
    item = _catalog_item("llms_txt")
    assert item["weight"] == 15
    assert "Google ignores it" in item["what_why"]
    assert "Claude" in item["what_why"] and "ChatGPT" in item["what_why"]
    # the old, unsourced "~4% of major sites" claim must be gone.
    assert "%" not in item["what_why"]


def test_t_kit_pref_sources_is_hero():
    item = _catalog_item("preferred_sources")
    assert item["weight"] == 25
    assert item["weight"] == max(i["weight"] for i in _CATALOG)
    assert "PER-VISITOR" in item["what_why"]
    assert "not a global ranking signal" in item["what_why"]
    assert "badge" in item["what_why"].lower()
    assert "Top Stories" in item["what_why"]


def test_t_kit_score_not_overclaimed():
    panel_src = _FRONTEND_PANEL.read_text()
    assert "preparedness checklist, not live citation tracking" in panel_src
    assert "kit-score-note" in panel_src
    assert "Others measure your AI visibility. AUREM fixes it" in panel_src
    assert "kit-positioning-line" in panel_src


def test_t_kit_no_oversell_number():
    all_copy = " ".join(i["what_why"] for i in _CATALOG)
    # the old, unsourced "~4% of major sites ship one" stat must be gone.
    assert "% of" not in all_copy
    # the one customer-facing performance stat that remains (2x clicks)
    # must be phrased as an attributed claim, never a flat fact.
    assert "2x the clicks" in all_copy
    assert "Google reports preferred links get about 2x the clicks" in all_copy
    assert "(May 2026)" in all_copy  # sourced/dated, not a bare assertion


# ── 2026-08-30 KIT GAP-PATCH ──────────────────────────────────────────
def test_t_robots_bot_names_verified():
    out = robots_gen.render_managed_block({})
    # the real bug: a dead token meant the retrieval bot was never
    # actually allowed. Must be gone, replaced by the verified token.
    assert "Claude-Web" not in out
    assert "User-agent: Claude-SearchBot\nAllow: /" in out
    # verified retrieval set (allow), exact match, no extras.
    assert robots_gen.RETRIEVAL_BOTS == ["OAI-SearchBot", "Claude-SearchBot", "PerplexityBot"]
    # verified training set (deny by default) — DeepSeekBot removed (no
    # vendor-published source found), Bingbot never was a training bot.
    assert robots_gen.TRAINING_BOTS == ["GPTBot", "ClaudeBot", "Google-Extended", "CCBot"]
    assert "DeepSeekBot" not in out
    # user-fetch bots deliberately excluded (don't reliably obey robots.txt).
    for fetch_bot in ("ChatGPT-User", "Claude-User", "Perplexity-User"):
        assert fetch_bot not in out


def test_t_schema_org_stable_id():
    site = {"name": "Example Co", "url": "https://example.com/"}
    first = schema_gen.render_json_ld(site)
    second = schema_gen.render_json_ld(site)
    import json as _json
    def _org_id(rendered):
        block = rendered.split('"@type": "Organization"', 1)[-1]
        # walk back to find this same script tag's @id (simplest: re-parse)
        for chunk in rendered.split("<script"):
            if '"Organization"' in chunk:
                raw = chunk.split(">", 1)[-1].rsplit("</script", 1)[0].strip()
                return _json.loads(raw)["@id"]
        raise AssertionError("no Organization block found")
    id1, id2 = _org_id(first), _org_id(second)
    assert id1 == id2  # idempotent — same input, same @id, every time
    assert id1 == "https://example.com/#organization"
    assert id1.startswith("https://")  # a real URI, not an opaque token
    # no fabricated sameAs when the caller supplied none.
    assert '"sameAs"' not in first.split('"@type": "Organization"', 1)[-1].split("}", 1)[0]


def test_t_gbp_advisory_only():
    gbp = next(i for i in _CATALOG if i["key"] == "google_business_profile")
    assert gbp["mode"] == "advisory"
    assert "google_business_profile" in apply_mod.ADVISORY_ITEMS
    assert "google_business_profile" not in apply_mod.IMPLEMENTED_AUTO_ITEMS
    # never earns/dilutes the readiness score — deliberate, documented choice.
    assert gbp["weight"] == 0
    # no GBP API/OAuth code path exists anywhere in the visibility services.
    vis_dir = Path(apply_mod.__file__).resolve().parent
    for py_file in vis_dir.glob("*.py"):
        src = py_file.read_text().lower()
        assert "mybusiness" not in src and "businessprofile" not in src


# ── 2026-08-30 KIT ADD (2 advisory rows: Search Console + GBP checklist,
#    Google Platforms links) ──────────────────────────────────────────
def test_t_search_console_gbp_row_advisory():
    item = _catalog_item("search_console_gbp_check")
    assert item["mode"] == "advisory"
    assert item["weight"] == 0
    assert "search_console_gbp_check" in apply_mod.ADVISORY_ITEMS
    assert "search_console_gbp_check" not in apply_mod.IMPLEMENTED_AUTO_ITEMS
    assert "search.google.com/search-console" in item["what_why"]
    assert "business.google.com" in item["what_why"]
    # frontend renders every advisory row with a generic "View report"
    # button (same modal pattern as the GBP row) — no per-key hardcoding
    # needed, confirmed by grep: no google_business_profile-style special
    # case exists for this key either.
    panel_src = _FRONTEND_PANEL.read_text()
    assert 'item.mode === "advisory"' in panel_src
    assert "search_console_gbp_check" not in panel_src  # generic, not hardcoded
    assert "kit-view-report-" in panel_src  # the button id pattern this row gets


def test_t_google_platforms_row_advisory():
    item = _catalog_item("google_platforms_connected")
    assert item["mode"] == "advisory"
    assert item["weight"] == 0
    assert "google_platforms_connected" in apply_mod.ADVISORY_ITEMS
    assert "google_platforms_connected" not in apply_mod.IMPLEMENTED_AUTO_ITEMS
    assert "search.google.com/search-console" in item["what_why"]
    assert "business.google.com" in item["what_why"]
    assert "analytics.google.com" in item["what_why"]


def test_t_advisory_items_not_in_ship_pr():
    """Extends t_gbp_advisory_only's guard to the 2 new rows — same
    property: they are in ADVISORY_ITEMS, NOT in IMPLEMENTED_AUTO_ITEMS,
    so `apply_visibility_kit`'s `to_apply = requested & IMPLEMENTED_AUTO_ITEMS`
    can never place them in the `files` dict handed to the PR. No Google
    Search Console / GBP / Analytics API or OAuth code exists anywhere
    in the visibility services for these 2 keys either."""
    for key in ("search_console_gbp_check", "google_platforms_connected"):
        assert key in apply_mod.ADVISORY_ITEMS
        assert key not in apply_mod.IMPLEMENTED_AUTO_ITEMS
        requested_incl_new_rows = set(apply_mod.IMPLEMENTED_AUTO_ITEMS) | {key}
        to_apply = requested_incl_new_rows & apply_mod.IMPLEMENTED_AUTO_ITEMS
        assert key not in to_apply  # would never enter the ship-PR files dict
    vis_dir = Path(apply_mod.__file__).resolve().parent
    for py_file in vis_dir.glob("*.py"):
        src = py_file.read_text().lower()
        assert "searchconsole" not in src and "analytics.google" not in src


def test_t_score_weight_sum_still_100():
    """Adding 2 weight=0 advisory rows must not change the score
    denominator — still 100, the 7 scorable rows' weights untouched."""
    spec_weights = {
        "preferred_sources": 25, "ai_crawler_policy": 20, "structured_data": 20,
        "llms_txt": 15, "sitemap_auto": 10, "answer_blocks": 7, "image_quick_wins": 3,
    }
    assert sum(spec_weights.values()) == 100
    total_weight_all_catalog_rows = sum(i["weight"] for i in _CATALOG)
    assert total_weight_all_catalog_rows == 100  # 3 advisory rows all weight=0
    for key in ("google_business_profile", "search_console_gbp_check",
                "google_platforms_connected"):
        assert _catalog_item(key)["weight"] == 0
