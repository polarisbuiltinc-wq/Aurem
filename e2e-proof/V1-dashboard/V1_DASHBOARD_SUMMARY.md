# V1-dashboard — Verify Engine card on the Deploy panel (2026-08-30)

Agent-tested, NOT founder-confirmed. This is the USER-facing front for
the V1 deploy-verify engine (backend wiring landed in the M3+V1
round); the admin tile (`AdminSystemHealth`) keeps the full check
list/raw events — this is deliberately the compact summary only.

## What was built
- `GET /deploy/verify-summary?project_id=...` (`routers/deploy.py`) —
  scoped to the CURRENT user (+ optional project), last 30 days,
  reading the same `verify_engine` sub-document V1d already writes
  onto `aurem_cto_deploy_runs`. Returns exactly what the card needs
  and nothing more: `{has_any, total, passed, pass_pct, last_run_at,
  last_fail_what_happened, last_fail_run_id, last_fail_at}` — no raw
  checks, no console errors, no trace paths (those stay admin-only).
- `frontend/src/components/VerifyEngineCard.jsx` — new, standalone,
  <100 lines. Three states exactly per spec:
  1. **Pass rate + current state**: "Last 30d: 14/15 verifications
     passed" + "Last verified: 2h ago" (or a spinner "Verifying
     deployed site…" while a run's post-deploy verification is still
     in flight — driven by the SAME `verified === null` transitional
     signal `DeployPanel` already uses for its own `ReceiptCard`).
  2. **Last failure**: one-line "what happened" (e.g. "stale build
     detected on /pricing") + a "View evidence" link that selects that
     failed run in the existing history list (reuses `selectRun`, no
     new download/auth logic needed).
  3. **Honest empty state**: "Your first deployment will be verified
     automatically." — no fake stats, no raw metrics.
- Wired into `DeployPanel.jsx`: always-visible row between the
  toolbar and the log/history body, `projectId` + `verifying` +
  `refreshSignal={runs.length}` (re-fetches whenever the run history
  changes) + `onViewEvidence={selectRun}`.
- `initialSummary` escape-hatch prop (mirrors `LoopLiveFeed`'s `event`
  prop pattern already used by `/dev/visual` fixtures) — lets the card
  render hermetically for screenshots/tests without a live backend
  call, zero behavior change for real usage (prop is `undefined` in
  production).

## Tests — 5/5 pass (`VerifyEngineCard.test.jsx`, vitest)
- `t_dashboard_verify_card_renders` — pass rate ("14/15") + "Last
  verified: 2h ago" state, both sourced from the summary payload.
- verifying-spinner state (extra, not one of the 3 named but locks in
  the 4th spec'd state).
- `t_dashboard_verify_last_fail_shown` — failed verify shows its
  "what happened" line + evidence link fires `onViewEvidence` with the
  right run_id.
- `t_dashboard_verify_empty_state` — `has_any: false` → honest empty
  message, pass-rate row NOT rendered (no fake stats).
- Fetch-failure → renders nothing (not a broken card).

## Live proof
1. **Backend, live curl** (test@aurem.dev, project `p_demo_a`), 2
   synthetic realistic runs seeded then cleaned up after: `GET
   /deploy/verify-summary?project_id=p_demo_a` →
   `{"has_any": true, "total": 2, "passed": 1, "pass_pct": 50,
   "last_run_at": "...", "last_fail_what_happened": "stale build
   detected on /pricing", "last_fail_run_id": "run_demo_fail_v1dash",
   ...}` — exact shape the card consumes, confirmed end-to-end through
   the real endpoint. Docs left clean (2 synthetic
   `aurem_cto_deploy_runs` rows deleted after).
2. **Preview screenshot** of the actual rendered component, via the
   existing `/dev/visual` hermetic-fixture pattern (`?state=verify-engine-card-with-fail`
   and `?state=verify-engine-card-empty`) — confirms the real styled
   output: `"Last 30d: 14/15 verifications passed · Last verified: 2h
   ago · ⚠ stale build detected on /pricing · View evidence"` and the
   honest empty-state line, both rendering correctly at 1920×800.
3. **Not captured**: a screenshot of the card mounted live inside the
   full `DeployPanel` → `PreviewPanel` → chat-workspace nesting (that
   panel only mounts after a ship-dialog/deploy-tab trigger deep in
   `ChatPanel.jsx`'s live chat flow, not reachable via a quick scripted
   navigation without a live ship in progress). The `/dev/visual`
   fixture screenshot above renders the IDENTICAL component/styles/
   testids `DeployPanel` mounts — same proof value for this round's
   scope, flagged honestly rather than skipped silently.

## STATUS: V1-dashboard CLOSED (agent-tested, not founder-confirmed).
