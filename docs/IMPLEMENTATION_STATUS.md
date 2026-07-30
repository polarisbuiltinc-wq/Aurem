# IMPLEMENTATION_STATUS — living status (replaces static plans)

Last updated: 2026-06-30 (Iter 358). Keep this current — stale
"next actions" caused a real bug once (Iter 123 stale plan incident).

## Shipped (verified, in production unless noted)
- P0 Loop engine hang fixes + lightweight intent gate (loop_intent.py)
- Phase 1: persistent correction rules + Auto-QA active
- Admin panel audit fixes (financials signs, API-key masking, health
  badge bands, Stripe webhook ledger sync)
- Iter 356: **Unified RailShell nav** (deployed), prod-e2e chat-session
  debris root fix (filter + cleanup endpoint + E2E teardown),
  /chat/sessions double-fetch fix, RouteErrorBoundary,
  /integrations back button
- **Guard 2** marketing truth gate (deployed — landing renders live
  stats: 26 devs / 88 commits on prod)
- **Guard 8 (partial)**: GitHub sync detection on Admin Overview build
  badge + >48h RED alert (preview; needs env vars + next deploy)
- Iter 358 (preview, deploy pending):
  - "ships this week" chip real-data fix (this_week period existed
    nowhere; loop ships now counted — routers/wrapped.py)
  - SEO/GEO/AEO refresh: /vs/cursor, /vs/github-copilot,
    /vs/replit-agent, /vs/windsurf real pages + /compare hub, single
    data source src/data/competitors.js, FAQPage JSON-LD 1:1,
    build-time static snapshots (scripts/seo-prerender.mjs), sitemap +
    llms.txt truth fixes (fake 500+/12k+/4.9★/498-spots removed,
    pricing aligned to subscription_tiers SSOT)
  - Internal docs set (/app/docs/*.md, 7 docs + index)

## In progress
- Regression Guards rollout (charter: /app/memory/GUARDS_CHARTER.md).
  Ship order wave 1: G4 secret scanner → G3 scope-drift block →
  G1 route sweep, G5 invariants, G6 dedup constraints, G7 payment
  reconciliation → G8 CI wiring → /admin/qa GUARDS section (last).
  Wave 2: 13→10→9→14→16→11→12→15. Wave 3: 18→17→21→19→20.

## Deferred (explicit, with reason)
- **Phase 4 dead-code removal** (legacy Shell/sidebars): waits for
  founder's PROD verification of unified nav — do not delete early.
- **~270 legacy pytests** (@pytest.mark.legacy): fix/purge in batches.
- **/admin/qa REGRESSION GUARDS section**: intentionally ships LAST
  (reads all guards).
- **Personal track UX**: after guards waves.

## Blocked on founder
- GITHUB_ACTIONS_TOKEN + GITHUB_REPO env (Guard 8 full + CI)
- Stripe 3 monthly price-ID envs (prod revenue)
- Tavily credits / Firecrawl key (33 integration alerts active)
- UptimeRobot account (Guard 9), spend caps confirm (Guard 13)
- Click "Save to GitHub" after each session (repo goes stale otherwise)
