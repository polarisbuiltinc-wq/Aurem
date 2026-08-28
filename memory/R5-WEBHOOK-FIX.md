# R5 — GitHub App Webhook Config Fix (2026-08-28)

## R5a — Forensics (read-only, live GitHub API evidence)

**1. Webhook mechanism** — GitHub-App-level webhook ONLY (App settings:
delivery URL + secret + event subscription). No per-repository webhooks
exist or are needed — an App-level webhook covers every installation
automatically. Live state on GitHub right now (App `aurem-devops`,
app_id 4541725):
  - `webhook_url` = `https://auremcto.com/api/aurem-dev/github/app/webhook`
    (confirmed via `GET /app/hook/config`) — **set, and correct** (see #2).
  - `events` (content-event subscriptions) = **`[]` — empty**. `pull_request`
    is NOT subscribed.
  - `installation`, `installation_repositories`, `github_app_authorization`,
    `meta` are sent to every GitHub App automatically regardless of the
    `events` subscription list — that's why deliveries for those exist
    at all despite `events: []`.

**2. Exact delivery URL AUREM's handler expects** — computed from code,
not guessed: `main.py` mounts `github_router` at prefix `/api/aurem-dev`
(`app.include_router(github_router, prefix="/api/aurem-dev")`);
`routers/github_app.py`'s router itself has `prefix="/github/app"`; the
route is `@router.post("/webhook")`. Full path:
`https://auremcto.com/api/aurem-dev/github/app/webhook`.
**This is EXACTLY what's already configured on GitHub** — the URL is
NOT the problem.

**3. Env var for the signing secret** — **there is none.** `webhook_secret`
lives ONLY in Mongo (`admin_settings._id="github_app_config"`,
`services/github_app_config.py`), set exclusively via the existing
admin UI (Admin → GitHub App Config card → `POST /admin/github-app-config`,
all 4 fields — `app_id`/`app_slug`/`private_key`/`webhook_secret` —
required together, all-or-nothing). It is never stored in `.env` and
never echoed back by the GET endpoint (security by design). This
changes the original framing — there's no "var name, set/unset" to
report; the real question is whether PRODUCTION's Mongo doc has the
correct value, which is unverifiable from here (see #5).

**4. Exact event set the handler dispatches on** (from
`routers/github_app.py::install_webhook`, read directly, not inferred):
`installation` (created/deleted/suspend/unsuspend), `installation_repositories`
(added/removed), `meta` (deleted), `pull_request` (all actions, further
routed by label inside `services/loop_safety.py::dispatch_pull_request_webhook`).
**Of these, only `pull_request` needs an explicit checkbox** on the
App's "Permissions & events" page — the other three are automatic.

**5. Why 401 — root cause** — `install_webhook()` returns 401 whenever
`_ga.verify_webhook_signature()` returns `False`, which happens on ANY
of: App not configured / missing signature header / malformed header /
**secret mismatch** (deliberately uniform 401 — "never leak reason...
to prevent probing", by design, a FOLLOWED guardrail). Evidence gathered:
  - URL is confirmed correct → rules out a bad/undeliverable URL.
  - **15/15 (100%) of the most recent real deliveries fail with 401**,
    spanning 2026-08-27→28, across multiple event types (`installation`,
    `installation_repositories`) — a uniform total failure, not an
    intermittent one.
  - Most likely cause: **the `webhook_secret` value in PRODUCTION's
    `admin_settings.github_app_config` does not match what GitHub is
    signing with** — either never set correctly there, or set once and
    since diverged. I cannot distinguish "never configured" from
    "configured with the wrong value" from this Preview pod (no
    production DB access, and GitHub never exposes the secret value
    back via API for either side to compare) — **both produce an
    identical symptom.** The founder checklist below (R5d) resolves
    this ambiguity as ITS OWN FIRST STEP before touching GitHub.

**6. Does the CURRENT prod ship flow depend on this webhook?** — Read
`services/loop_engine.py` directly: the ship path always calls
`commit_files()` (direct-push) regardless of the `ship_via_pr` flag;
the flag only ADDITIONALLY opens a PR (`services/loop_safety.py::open_draft_pr`)
when enabled. **Confirmed: NO, today's direct-push ship flow has zero
dependency on this webhook.** One secondary, smaller current-impact
nuance found: the `installation`/`installation_repositories` events
are what's SUPPOSED to keep `github_installations`/`cto_projects`
install-active/suspended/deleted state in sync in real time; with
those failing 401 today, that real-time sync isn't landing in
production — live-polling fallbacks (e.g. `GET /cto/projects/connection-status`,
which calls GitHub directly rather than trusting only the cached flag)
reduce the practical impact, but it is not literally zero. The
PR-webhook gap (item #22's real subject) is the true prod-blocker; this
secondary item is a bonus finding, not a new blocker.

## R5b — Verify AUREM's side (no GitHub changes)

- **Endpoint reachable**: YES — the configured GitHub URL matches our
  route's exact path (see #2).
- **Signature check wired**: YES — `verify_webhook_signature()`
  (`services/github_app.py`) is called FIRST, before any JSON parsing,
  raw-body HMAC-SHA256, constant-time compare (`hmac.compare_digest`),
  matches GitHub's `X-Hub-Signature-256: sha256=<hex>` format exactly.
- **Uniform 401 (never leaks reason)**: YES, confirmed in code — the
  route's own comment states this explicitly; already a FOLLOWED
  guardrail from the original audit.
- **Label dispatch wired correctly**: YES, confirmed in code
  (`services/loop_safety.py::dispatch_pull_request_webhook`) —
  `aura:ship` → `loop_outcomes` + `ship_pr_events`; `auremcto/visibility-kit-*`
  → its own `visibility_kit_pr_events` collection (deliberately kept
  separate so the two label families can't cross-write); anything else
  → log-only, no state write.
- **Nothing on AUREM's side needs to change before a GitHub-side config
  fix would work.** The code is correct and ready; this is purely a
  GitHub App settings + production-secret data problem.

## R5c — App Fence Tile (built, live-verified)

New live health check, wired into the existing AdminSystemHealth page:
- `services/github_app.py::webhook_fence_status()` — fetches `GET /app`
  (subscribed events) + `GET /app/hook/deliveries` (last 15, real) from
  GitHub, computes `ok`/`missing_subscriptions`/`failing_count`.
- `GET /admin/github-webhook-fence` (`routers/admin_ops_config.py`),
  admin-gated.
- New "GitHub Webhook Fence" card on `AdminSystemHealth.jsx` — shows
  subscribed events, a `⚠ missing: pull_request` badge, and an
  expandable list of the last 15 real deliveries with per-delivery
  success/fail.
- Tests: `tests/test_r5c_webhook_fence.py` (3, incl. a live integration
  test against the real endpoint) + `pages/__tests__/AdminSystemHealth.webhookFence.test.jsx`
  (3). Live E2E screenshot: tile renders WARMING, correctly shows
  `subscribed_events: []`, `missing: pull_request`, `15/15 failing` —
  the exact real broken state, proving the monitor works.

## R5d — Founder checklist (copy-paste executable, ~10 min)

**Step 0 — resolve the ambiguity from R5a #5 first (2 min).**
Open the AUREM Admin panel on **production** (auremcto.com) →
Settings → GitHub App Config card, and separately load
`GET https://auremcto.com/api/aurem-dev/admin/github-app-diagnostics`
(admin login required). Check the `"configured"` field.
  - If `"configured": false` → production's GitHub App credentials
    were never (fully) saved. Go to Step 1, but you'll need to
    re-paste `app_id` / `app_slug` / `private_key` there too (get
    those from your existing App settings page, `Private key` may
    need a NEW one generated if you don't have the .pem saved —
    "Generate a private key" button on the App settings page).
  - If `"configured": true` → credentials exist but the webhook
    secret is the likely mismatch. Go straight to Step 1.

**Step 1 — generate a fresh webhook secret (1 min).**
Generate any strong random string yourself right now (example:
run `openssl rand -hex 32` in any terminal, or use a password
manager's generator). Call this value `<NEW_SECRET>`. You do not
need to recall AUREM's old secret — this step REPLACES it.

**Step 2 — set it on GitHub (3 min).**
Go to `https://github.com/settings/apps/aurem-devops` → **Webhook**
section:
  1. Confirm **Webhook URL** = `https://auremcto.com/api/aurem-dev/github/app/webhook`
     (should already match — if it doesn't, fix it now).
  2. Paste `<NEW_SECRET>` into **Webhook secret**.
  3. Scroll to **Permissions & events** → find **Pull requests** under
     "Subscribe to events" → **check it**. (Nothing else needs
     checking — `installation`/`installation_repositories`/`meta` are
     automatic.)
  4. Click **Save changes**.

**Step 3 — set the SAME value in AUREM production (3 min).**
Go to production Admin → Settings → GitHub App Config card. Re-submit
ALL 4 fields together (the endpoint requires all-or-nothing):
`app_id`, `app_slug` (unchanged, just re-paste what's already there),
`private_key` (unchanged, re-paste your saved `.pem` — if you no
longer have it saved anywhere, go back to the App settings page and
generate + download a NEW private key first, then use that instead),
and **`webhook_secret` = `<NEW_SECRET>` (the exact same string from
Step 1)**. Submit.

**Step 4 — verify (1 min).**
On GitHub's App settings page → **Advanced** tab → find any recent
delivery → click **Redeliver**. Then open production's
`AdminSystemHealth` page → **GitHub Webhook Fence** card (refreshes
every 30s, or click "Refresh now") → confirm:
  - `subscribed_events` now includes `pull_request` (no more
    `⚠ missing` badge).
  - The redelivered event shows `OK` in the expanded delivery list
    (not `FAIL 401`).

That's it — R5e (the next round) re-runs a full live PR drill to
confirm `webhook_payload.json` is finally captured for real.

## R5e — deferred to next round (needs Step 0-4 above done first)

Also queued for that same round: confirm the R2 merged drill PR's
files (already reviewed now — see below) and, if anything beyond the
harmless marker landed, revert it so `ora-grounding`'s default branch
is exactly at its pre-drill state.

**Early check done now (no need to wait):** the R2 drill's merge
commit `9c676bb...` (squash-merged PR, see `/app/e2e-proof/T7-live/pr_merge.json`)
changed exactly one file, `.aurem/t7-drill-<timestamp>.md` (a harmless
marker doc) — **and that file was already deleted from `main` in the
same R2 round** (see LOOP-STATE.md R2 entry: "repo left clean"). Current
live branch list on `ora-grounding` = `["main"]` only. **No revert
needed** — already confirmed clean.
