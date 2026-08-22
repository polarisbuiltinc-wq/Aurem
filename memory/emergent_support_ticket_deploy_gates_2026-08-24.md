# DRAFT — für Founder-Review, NICHT gesendet (2026-08-24)

To: support@emergent.sh
From: Tejinder Sandhu <teji.ss1986@gmail.com>
Subject: Feature request: conditional production deploys (CI-gated Deploy button)

---

Hi Emergent Support team,

Tejinder here, founder of Aurem CTO (https://auremcto.com), running on Emergent.

Job ID: 73df9f0d-7149-4a95-89d4-c9972e2b0c6d
Deployed app: https://auremcto.com

Filing this as a platform feature request. Not urgent — I have a documented
process rule as a workaround — but this is a real safety gap for any team
shipping to production from Emergent.

## Problem

The production "Deploy" button packages and ships the CURRENT workspace
state, unconditionally. Your support team confirmed (2026-08-24) that there
is currently:

- no deploy webhook or API,
- no way to attach a pre-deploy check or external CI gate,
- no way to require a green GitHub Actions status before deploying.

My repo has a full CI + Quality Gate pipeline on GitHub Actions
(tests, security scan, lockfile checks, auto-deploy blocking on red).
On GitHub, that gate works: red CI blocks the downstream deploy workflow.
But it cannot protect the Emergent Deploy button at all — anyone with
workspace access can ship an unverified state to production with one click.
The gate is a signal, not an interlock.

## What I've already done (workaround)

A documented deploy discipline rule in the project PRD: deploy only when
(a) the latest pushed SHA is CI + Quality Gate green, OR (b) a full
independent verification report exists for the exact workspace state.
Plus a planned admin "Deploy Readiness" card that compares workspace vs.
remote SHA and shows the CI verdict — advisory only, it cannot block.

Process discipline works for a solo founder but does not scale and does
not survive a bad day.

## Feature request (any ONE of these)

1. **Conditional Deploy button**: let me register a GitHub repo + required
   check names (e.g. "CI", "Quality Gate"); the Deploy button is disabled
   (with an override-with-confirmation escape hatch) unless the workspace
   matches a pushed SHA whose required checks are green.

2. **OR: Pre-deploy hook**: a `deploy-hooks/pre-deploy.sh` in the repo that
   runs before packaging; non-zero exit aborts the deploy. I would call the
   GitHub Checks API myself from that script.

3. **OR: Deploy API/webhook with confirmation token**: an API to trigger or
   confirm deploys programmatically, so my GitHub Actions pipeline can be
   the single path to production instead of a parallel manual button.

## Why this matters platform-wide

Every Emergent team that grows past one person needs "you cannot ship red
to production" as a mechanical guarantee, not a memo. This is standard in
Vercel/Railway/Render (required checks, protected deployments) and is the
main gap I'd flag for enterprise readiness.

Happy to jump on a call if useful.

Best,
Tejinder Sandhu
Founder, Aurem CTO
teji.ss1986@gmail.com
https://auremcto.com
