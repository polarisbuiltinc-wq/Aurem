# T4 — Deployed-build verification (read-only, 2026-08-30, T2-T5 GO chain)

Read-only, no production writes, no login attempted on production
(no safe non-founder authenticated production credential available in
`memory/test_credentials.md` — founder's own password is redacted per
this repo's own security policy; not attempted).

## 1. Version/SHA drift check
- Production `GET /api/aurem-dev/version`: `commit_sha: "f1c73be8a706"`,
  `built_at: 2026-08-29T15:16:55Z`, `environment: production`.
- Production `GET /api/health`: `build_hash: "f1c73be8a706"`, `ok: true`,
  `db: true`, `env: production`, 26/26 supervised background tasks
  alive, 0 dead.
- This pod's local `git log -1`: `f1c73be8a70647f1512c3295c9e803281262c85e`
  (2026-08-29T14:53:34Z).
- **Match: production's deployed SHA (`f1c73be8a706`) IS this pod's
  current git HEAD (first 12 chars identical). Zero drift.** Note: all
  of this round's H3/B1-extend/T2 code changes are UNCOMMITTED working-
  tree diffs in this Preview pod (by design — Preview-only per
  constraints) and are therefore correctly NOT yet reflected in
  production's deployed SHA. This is expected, not a gap.

## 2. Landing page load
`https://auremcto.com` — loaded cleanly (screenshot:
`/app/e2e-proof/T4/production_landing.png`), no fatal console errors,
cookie-consent banner rendered normally, hero copy/CTA/pricing strip/
"Watch ORA ship real code" section all present.

## 3. Authenticated screen load
**NOT PERFORMED** — honest limitation, not silently skipped. No safe
non-founder production account exists in `test_credentials.md` (the
one production account listed is the founder's own, password
redacted per this file's own security policy; a synthetic QA account
`qa-scan-bot@aurem.dev` exists but is scoped for secret-leak scans, not
general UI verification, and using it for a UI screen-load check would
be a scope expansion beyond this task's read-only mandate without
explicit founder sign-off). T3's full authenticated journey (12/12
flows) was already verified on Preview, which runs the same codebase.

## 4. S-surfaces presence (code-level, since prod login wasn't
performed)
Confirmed present in the currently-deployed commit (`f1c73be8a706` —
same as this pod's git HEAD, checked via `git show`/`grep`, not guessed):
- `PreviewPanel.jsx` (preview/code/deploy 3-tab surface) — present.
- `DeployPanel.jsx` (BYOH SSH deploy) — present.
- `AdminSystemHealth.jsx` webhook-fence + preview-deploy-monitor cards
  — present (also independently confirmed live via production's own
  `/api/health` supervised-tasks list above, which is the same process
  serving that page).
- `UserNotificationBell.jsx` — present.
- `services/loop_engine.py`'s H3 pin-and-assert guard — **NOT yet in
  production** (this round's uncommitted work, Preview-only by design;
  correctly not claimed as deployed).

## Verdict
Production is healthy, live, and running exactly the last committed
Preview state (zero unexpected drift). This round's new H3/B1/T2 work
is intentionally Preview-only and has not been deployed — reported
honestly, not conflated with "production verified."

## T4 STATUS: CLOSED (read-only, honest limitation on item 3 documented)
