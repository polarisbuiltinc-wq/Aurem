# P3 — Webhook channel confirmation (2026-08-30, read-only)

No secret requested, accepted, or discussed by value. Answers are
mechanics only, sourced from `/app/memory/R5-WEBHOOK-FIX.md` (the
existing, already-verified forensics doc) — nothing new investigated
in code this round.

## 1. What channel sets the prod `WEBHOOK_SECRET`?
**There is no env var at all** — `verify_webhook_signature()`
(`services/github_app.py`) reads the secret ONLY from Mongo, at
`admin_settings.github_app_config.webhook_secret`. The channel to set
it in production is the **production Admin UI's "GitHub App Config"
card**, which submits `POST /admin/github-app-config` with all 4
fields (`app_id`, `private_key`, `webhook_secret`, `client_secret`)
in one call. Access = whoever has admin/founder login on the
PRODUCTION site (auremcto.com) — that's the founder's own account (or
anyone the founder has separately granted production admin rights
to). This dev/Preview pod has **zero write access** to that
production Mongo document — it's a different database entirely
(Preview's `MONGO_URL` is not production's).

## 2. Does the secret value ever transit this chat?
**No, and it doesn't need to.** The mechanism in #1 is a direct
browser-to-production-API form submission — the founder (or whoever
has prod admin access) opens the production Admin UI in their own
browser, types the secret directly into that form's field, submits.
Nothing about this flow ever requires typing the secret into this
chat, a message to the agent, or any intermediary at all. If "founder
pastes it here" were the ONLY available channel, that path is
correctly OFF per the standing policy — but it isn't the only
channel: the production form already exists and is the real one.
**"Dev's job" is executable exactly as stated, with zero secret
exposure to chat, using the channel that already exists.** The
agent's role in this is confirmation-only (step 3), never entry.

## 3. Once set, how is it confirmed + how is R5e closed?
- **Confirm the fix (any event type)**: open GitHub's own "Recent
  Deliveries" list for the App (Settings → Developer settings →
  GitHub Apps → the app → Advanced tab) — a non-`pull_request` event
  (`installation`, `installation_repositories`, `meta`, etc.) will
  already show `200` once the secret matches, without needing a new
  PR. The AdminSystemHealth "GitHub Webhook Fence" tile is the
  equivalent read-only in-app view of the same signal.
- **Close R5e specifically (`pull_request` delivery)**: a
  `pull_request` delivery only ever appears after a REAL pull request
  event happens on a connected repo — "no `pull_request` delivery
  yet" is not evidence of a continuing 401, it just means no PR has
  fired since the fix. To trigger one on-demand: open ONE real PR on
  a reachable, connected repo. `TJSNDHU/Aurem` (installation
  `157161705`) is the repo already confirmed reachable across the
  T2/R1a drift drills this round and last; `ora-grounding`
  (installation `152797252`) remains flagged unreachable per repeated
  prior findings, unrelated to this fix. Confirm that PR's delivery
  lands `200` in Recent Deliveries → **R5e CLOSED**.

This round did not check whether the founder has changed the
production value yet — no signal of that was given, so nothing to
report as "post-fix 200" this round. Nothing else webhook-related was
touched, viewed, or requested.
