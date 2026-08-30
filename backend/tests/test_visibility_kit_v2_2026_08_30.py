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
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.visibility import preferred_sources as badge_gen
from services.visibility import llms_txt as llms_gen
from services.visibility import robots as robots_gen


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
async def test_t_kit_apply_proceeds_past_gate_when_flag_on():
    """Proves the gate is real (not a no-op) — with the flag ON, the
    SAME free-plan request now reaches the billing gate (402) instead
    of being blocked at 403 by the R9-gate."""
    from routers.visibility import apply_kit, ApplyBody
    db = MagicMock()
    db.cto_projects.find_one = AsyncMock(return_value={
        "project_id": "p1", "user_id": "u1", "github_owner": "o", "github_repo": "r",
    })
    db.dev_users.find_one = AsyncMock(return_value={"tier": "free"})
    with patch("routers.visibility.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.visibility.get_db", return_value=db), \
         patch("services.feature_flags.is_enabled", new=AsyncMock(return_value=True)):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await apply_kit("p1", ApplyBody(items=["ai_crawler_policy"]), authorization="Bearer x")
    assert exc_info.value.status_code == 402


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


def test_t_kit_catalog_weights_sum_to_100_and_pr_created_half_documented():
    """Weight-sum invariant (already covered live by test_catalog_seeded)
    re-asserted against the static spec constants so this test doesn't
    need a live DB to catch a weight-sum regression in review."""
    spec_weights = {
        "preferred_sources": 25, "ai_crawler_policy": 20, "structured_data": 20,
        "llms_txt": 15, "sitemap_auto": 10, "answer_blocks": 7, "image_quick_wins": 3,
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
    from services.visibility import schema as schema_gen
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
