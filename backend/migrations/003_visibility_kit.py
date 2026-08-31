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
#
# 2026-08-30 KIT GAP-PATCH: added `google_business_profile` (advisory,
# weight=0 by deliberate choice — GBP automation is policy-blocked
# (see item's own copy), so this row is a pure checklist. weight=0
# means it never earns AND never dilutes the readiness score's
# denominator — no reweighting of the other 7 rows, matching the
# truth-update round's explicit "no score reweighting" rule.
CATALOG_ITEMS: list[dict] = [
    {
        "key": "preferred_sources", "weight": 25, "mode": "auto",
        "name": "Preferred Sources badge",
        "what_why": (
            "Your visitors can mark you a 'preferred source' on Google — a "
            "PER-VISITOR choice, not a global ranking signal. Once they do, "
            "they see a 'preferred' badge on your links in AI Mode, AI "
            "Overviews and Top Stories. Google reports preferred links get "
            "about 2x the clicks (May 2026). ~2 minutes to install."
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
            "A map of your site that helps Claude, ChatGPT and coding "
            "agents find you. Google ignores it for Search and AI "
            "Overviews. Cheap to add, low-risk, useful for the assistants "
            "that do read it."
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
    {
        "key": "google_business_profile", "weight": 0, "mode": "advisory",
        "name": "Google Business Profile",
        "what_why": (
            "Your Google Business Profile is a citation source for local AI "
            "answers. Complete it yourself in Google's own dashboard — we "
            "don't post on your behalf (GBP's API requires per-client OAuth "
            "+ your own manual sign-in per action, so no SaaS can bulk-"
            "automate this — Google's own policy). Checklist: 5+ photos, "
            "every category/service filled in, NAP (name/address/phone) "
            "matching everywhere online, hours kept current, a weekly post "
            "or photo, and a refresh within 30 days — Whitespark's 2026 "
            "report ties stale profiles to falling impressions."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 8,
    },
    # 2026-08-30 KIT ADD (2 advisory rows) — the #1 kit item
    # (preferred_sources, weight 25) only works well when the site is
    # also connected to Google's own platforms. These 2 rows make that
    # dependency visible + guide the user. ADVISORY ONLY, weight=0 —
    # same reasoning as google_business_profile: no automation exists
    # or is attempted, so no score reweighting of the 7 scorable rows.
    {
        "key": "search_console_gbp_check", "weight": 0, "mode": "advisory",
        "name": "Google Search + Business Profile",
        "what_why": (
            "The Preferred Sources button works best when your site is "
            "verified in Google Search Console and your Business Profile "
            "is fresh. We can't connect these for you (they're Google's, "
            "not ours) — but here's the 3-step checklist: 1) Verify your "
            "site in Google Search Console (search.google.com/search-"
            "console) — 5 min one-time. 2) Claim + complete your Google "
            "Business Profile (business.google.com) — 5+ photos, all "
            "categories, hours up to date. 3) Keep it fresh — profiles "
            "inactive 30+ days can drop in impressions. Do all 3 and the "
            "Preferred Sources badge + AI Overviews both work at full "
            "strength."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 9,
    },
    {
        "key": "google_platforms_connected", "weight": 0, "mode": "advisory",
        "name": "Google Platforms",
        "what_why": (
            "The more your business is connected across Google's products, "
            "the more Google trusts your content in AI answers. This is a "
            "one-time 10-min setup per platform: Search Console "
            "(search.google.com/search-console) — verify your site, get "
            "search insights; Google Business Profile (business.google.com) "
            "— local listings, reviews; Google Analytics "
            "(analytics.google.com) — traffic + behavior data. Connect all "
            "three and your site signals 'trusted source' to every AI "
            "system that reads Google's data on you."
        ),
        "frameworks": ["next", "react", "static", "unknown"], "sort": 10,
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
