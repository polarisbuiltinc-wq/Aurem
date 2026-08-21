# Prod Deploy Verification — 2026-07-31 (Session 6 + Session 7)

## Deploy Summary
- Live URL: https://auremcto.com
- build_hash (prod): `m1c61197` (mtime-fingerprint — prod container has no git binary; expected)
- Latest local commit shipped: `1cfe485 auto-commit for ed4f6490-3da5-4efc-b5bd-89ec3c809fa4`
- env: production
- longcat_live: true (Council A primary)

## Prod Public Endpoints (real data)
- `GET /api/health` → 200 ✅ (build_hash + env + longcat)
- `GET /api/aurem-dev/usage/public/stats` → 200 ✅
  - users: 41 · real_developers: 30 · commits_shipped: 88 · tasks_shipped: 82

## /admin Banner Verification (via preview — same code as prod)
Preview URL: https://bin-context-pat.preview.emergentagent.com/admin
- ✅ "1 critical integration alert (total active: 1)" — Session 6 Item 2 (Tavily dedup)
- ✅ "74 Developers" — Session 6 Item 5 (`stats?.real_developers ?? stats?.users` fallback)
- ✅ "Built — not yet published (aurem-cto-0.1.0.vsix ready; needs `vsce publish` + PAT)" — Session 6 Item 1

Prod has same commit deployed; founder should visually confirm on https://auremcto.com/admin.

## Session 6 Local Tests (post-manifest-regen)
`pytest tests/test_session6_item1..6` → **47/47 pass** ✅

## Session 7 Loop UI-State Reliability Tests
`vitest src/components/__tests__/Session7_Item{1,2,3}` → **17/17 pass** ✅
- Item 1 (Cancel loop UI stuck chip): 6/6 pass
- Item 2 (Duplicate plan-bubble + Approval Panel Cancel): 5/5 pass
- Item 3 (Rapid-send race lock): 6/6 pass

## Notes
- Prod `real_developers` is 30 (real prod DB), preview is 74 (preview DB).
  User expected "74" but that was preview number — prod is legit real data.
- QA manifest regenerated: grand_total_tests=4163.
