"""
tests/test_visibility_kit.py — Visibility Kit Phase B (spec §10).

Named tests per spec + this app's own conventions:
  t_robot_preserve       — existing rules byte-identical, only managed block changes
  t_robot_idempotent     — second apply updates the block in place, no duplicate
  t_detect_frameworks    — next / react / static / unknown fixtures
  t_author_schema        — Person+sameAs emitted only when author data present
  t_sitemap_idempotent   — apply twice → one sitemap, no dupes
  t_catalog_seeded       — 7 rows, weights sum to 100
  t_billing_gate         — free plan → 402 with upgrade payload (R2)
  t_branding_present     — generated robots/schema blocks carry the AUREM comment
  t_apply_no_copy_edit   — apply() only ever writes robots.txt/sitemap.xml/html-head,
                           never a content/prose file
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.visibility import robots as robots_gen
from services.visibility import schema as schema_gen
from services.visibility import sitemap as sitemap_gen
from services.visibility.detect import detect_framework


def test_detect_frameworks():
    assert detect_framework([], {"dependencies": {"next": "14.0.0"}}) == ("next", False)
    assert detect_framework([], {"dependencies": {"react": "18.0.0"}}) == ("react", False)
    assert detect_framework(["index.html"], None) == ("static", False)
    assert detect_framework([], None) == ("static", True)  # unknown → static fallback


def test_robot_preserve():
    existing = "User-agent: *\nDisallow: /admin\n\nSitemap: https://x.com/sitemap.xml\n"
    out = robots_gen.apply_managed_block(existing, {})
    assert "Disallow: /admin" in out
    assert "Sitemap: https://x.com/sitemap.xml" in out
    assert robots_gen._START in out and robots_gen._END in out


def test_robot_idempotent():
    existing = "User-agent: *\nDisallow: /admin\n"
    once = robots_gen.apply_managed_block(existing, {"GPTBot": "allow"})
    twice = robots_gen.apply_managed_block(once, {"GPTBot": "deny"})
    assert twice.count(robots_gen._START) == 1
    assert "Disallow: /admin" in twice
    assert "User-agent: GPTBot\nDisallow: /" in twice  # 2nd apply's policy wins, no dupe


def test_robot_training_bot_default_deny():
    out = robots_gen.render_managed_block({})
    assert "User-agent: GPTBot\nDisallow: /" in out
    assert "User-agent: OAI-SearchBot\nAllow: /" in out  # retrieval always allowed


def test_author_schema():
    with_author = schema_gen.render_json_ld({
        "name": "X", "url": "https://x.com",
        "author": {"name": "Tej", "sameAs": ["https://linkedin.com/in/tej"]},
    })
    assert '"@type": "Person"' in with_author
    assert "linkedin.com/in/tej" in with_author

    no_author = schema_gen.render_json_ld({"name": "X", "url": "https://x.com"})
    assert '"@type": "Person"' not in no_author


def test_sitemap_idempotent():
    urls = [{"loc": "https://x.com/a"}, {"loc": "https://x.com/a"}, {"loc": "https://x.com/b"}]
    once = sitemap_gen.render_sitemap(urls, "2026-08-28")
    twice = sitemap_gen.merge_lastmod(once, urls, "2026-08-29")
    assert twice.count("<url>") == 2  # deduped, not 3
    assert "2026-08-29" in twice


@pytest.mark.asyncio
async def test_catalog_seeded():
    import os
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    items = await db.visibility_items.find({}, {"_id": 0}).to_list(20)
    client.close()
    assert len(items) == 7
    assert sum(i["weight"] for i in items) == 100


def test_branding_present():
    robots_block = robots_gen.render_managed_block({})
    assert robots_gen._START.startswith("# ---") and "AUREM" in robots_gen._START
    schema_block = schema_gen.render_json_ld({"name": "X", "url": "https://x.com"})
    assert "AUREM" in schema_gen._START and schema_gen._START in schema_block


@pytest.mark.asyncio
async def test_billing_gate_free_plan_returns_402():
    from routers.visibility import apply_kit, ApplyBody
    db = MagicMock()
    db.cto_projects.find_one = AsyncMock(return_value={
        "project_id": "p1", "user_id": "u1", "github_owner": "o", "github_repo": "r",
    })
    db.dev_users.find_one = AsyncMock(return_value={"tier": "free"})
    with patch("routers.visibility.current_dev", new=AsyncMock(return_value={"user_id": "u1"})), \
         patch("routers.visibility.get_db", return_value=db):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await apply_kit("p1", ApplyBody(items=["ai_crawler_policy"]), authorization="Bearer x")
    assert exc_info.value.status_code == 402
    assert exc_info.value.detail["error"] == "upgrade_required"


@pytest.mark.asyncio
async def test_apply_no_copy_edit_only_writes_managed_paths():
    """t_apply_no_copy_edit — apply() must only ever produce robots.txt,
    sitemap.xml, or the html-head path; never a prose/content file (R3)."""
    from services.visibility.apply import apply_visibility_kit
    db = MagicMock()
    db.visibility_state.update_one = AsyncMock()
    db.visibility_applications.insert_one = AsyncMock()
    project = {"project_id": "p1", "user_id": "u1", "github_owner": "o",
               "github_repo": "r", "branch": "main"}
    with patch("services.visibility.apply.fetch_file", new=AsyncMock(return_value=None)), \
         patch("services.visibility.apply.resolve_git_identity", new=AsyncMock(return_value=("Bot", "b@x.com"))), \
         patch("services.visibility.apply.create_or_reuse_branch", new=AsyncMock(return_value=(True, None))), \
         patch("services.visibility.apply.commit_files", new=AsyncMock(return_value={"ok": True})) as mock_commit, \
         patch("services.visibility.apply.open_draft_pr", new=AsyncMock(return_value=("https://github.com/o/r/pull/1", None))):
        result = await apply_visibility_kit(
            db, project=project, requested_items=["ai_crawler_policy", "sitemap_auto", "answer_blocks"],
            token="t", scan_urls=[{"loc": "https://x.com/"}],
            site_meta={"schema": {"name": "X", "url": "https://x.com"}}, bot_policy={},
        )
    written_paths = set(mock_commit.call_args[0][4].keys())
    assert written_paths <= {"robots.txt", "sitemap.xml"}
    assert result["advisory_skipped"] == ["answer_blocks"]
    assert result["ok"] is True
