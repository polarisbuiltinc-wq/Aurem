"""
migrations/003_visibility_kit.py — Visibility Kit (SEO+GEO+AEO) Phase B
data model (spec §5). Idempotent — safe to re-run.

Seeds `visibility_items` (7-row catalog, §3) and creates indexes for
the 3 remaining collections. `visibility_items` is upserted by `key`
so re-running after a copy/weight edit updates rows, never duplicates.
"""
from __future__ import annotations

import time

from .base import Migration

# §3 catalog — single source of truth for copy + weights. AUTO items
# apply directly in the PR; ADVISORY items are report-only (R3/§9).
CATALOG_ITEMS: list[dict] = [
    {
        "key": "preferred_sources", "weight": 25, "mode": "auto",
        "name": "Preferred Sources badge",
        "what_why": (
            "Let your visitors make your site a 'preferred source' on Google. "
            "For them, your links then get a 'preferred' badge in AI Mode & AI "
            "Overviews, and you appear more in Top Stories. Google reports "
            "these links are clicked 2x more. ~2 minutes to install."
        ),
        "frameworks": ["next", "react", "static"], "sort": 1,
    },
    {
        "key": "ai_crawler_policy", "weight": 20, "mode": "auto",
        "name": "robots.txt AI policy",
        "what_why": (
            "Tell ChatGPT, Claude & Perplexity exactly which of their bots "
            "may read your site — so AI answers are grounded in your "
            "content, and you control who trains on it."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 2,
    },
    {
        "key": "structured_data", "weight": 20, "mode": "auto",
        "name": "JSON-LD + meta/OG",
        "what_why": (
            "Machine-readable facts about your business, pages, and authors "
            "— plus the meta/OG tags answer engines read when deciding what "
            "to quote."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 3,
    },
    {
        "key": "llms_txt", "weight": 15, "mode": "auto",
        "name": "/llms.txt + /llms-full.txt",
        "what_why": (
            "A curated map of your site that AI assistants fetch. Claude & "
            "Perplexity confirm they use it; ~4% of major sites ship one — "
            "your competitors haven't. No downside if you're wrong."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 4,
    },
    {
        "key": "sitemap_auto", "weight": 10, "mode": "auto",
        "name": "sitemap.xml generate / lastmod / dupe cleanup",
        "what_why": "Deterministic, 0 tokens — fast 'Live' win.",
        "frameworks": ["next", "react", "static", "unknown"], "sort": 5,
    },
    {
        "key": "answer_blocks", "weight": 7, "mode": "advisory",
        "name": "Answer-block gaps",
        "what_why": (
            "Pages that answer a question directly in the first 40–60 words "
            "under a question-style heading are the ones AI cites. We list "
            "your gaps + suggested copy — you decide."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 6,
    },
    {
        "key": "image_quick_wins", "weight": 3, "mode": "advisory",
        "name": "Image quick wins",
        "what_why": (
            "Missing alt / missing lazy-loading / heavy hero images — file "
            "list + advice. Auto alt-fill from filenames is forbidden."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 7,
    },
]


class VisibilityKitMigration(Migration):
    version = "003"
    name = "visibility_kit"
    description = "Seed visibility_items catalog + indexes for Visibility Kit Phase B."
    dev_only = False
    irreversible = False

    async def up(self, db) -> None:
        now = time.time()
        for item in CATALOG_ITEMS:
            await db.visibility_items.update_one(
                {"key": item["key"]},
                {"$set": {**item, "updated_at": now},
                 "$setOnInsert": {"created_at": now}},
                upsert=True,
            )
        await db.visibility_items.create_index("key", unique=True)
        await db.visibility_bot_policies.create_index("project_id", unique=True)
        await db.visibility_state.create_index(
            [("project_id", 1), ("item_id", 1)], unique=True,
        )
        await db.visibility_applications.create_index("project_id")
        await db.visibility_applications.create_index("pr_number")

    async def down(self, db) -> None:
        from pymongo.errors import OperationFailure
        try:
            await db.visibility_items.drop_index("key_1")
        except OperationFailure:
            pass
        try:
            await db.visibility_bot_policies.drop_index("project_id_1")
        except OperationFailure:
            pass
        try:
            await db.visibility_state.drop_index("project_id_1_item_id_1")
        except OperationFailure:
            pass
