# PRD — AUREM CTO / ORA (product requirements, living doc)

Last updated: 2026-06-30 (Iter 358). Every claim traces to code/DB.

## What ORA is today
Autonomous AI software engineer. Connects to a user's GitHub repository,
reads the codebase, writes production code, verifies it (syntax gates,
Vanguard security scan, optional second-model review), and delivers it
as a direct commit or Pull Request. Runs entirely from the browser
(mobile-friendly) — no IDE required.

## Target users
- **Developer track** (primary, live): solo devs / small teams with
  existing GitHub repos who want tasks delegated and shipped.
- **Personal track** (secondary, partially built): non-technical users
  building from scratch — onboarding wizard exists (`NewUserWizard`),
  dedicated track UX still evolving.

## Current feature set (source: Admin Overview "FEATURES — LIVE STATUS")
- Chat modes A/B/C/D/E/F (Swift / Pro / Maxx / Debug / Audit / Engage)
- Loop Mode: Plan → Execute → Verify → Security scan → Ship, with
  checkpoints + one-click rollback; SSE live phase stream
- Vanguard 2.0 pre-commit scan (25+ patterns, 13 deep rules, 3 chain
  rules), Bug Hunt (50+ Nuclei-style rules), Codebase Health dashboard
  (6 category scanners, 0-100 scores)
- Two-Agent Maxx (writer + Claude Sonnet reviewer)
- Project Brain per-repo memory + persistent correction rules (Phase 1)
- Parliament council logging, Citation Guard, loop intent gate
- GitHub OAuth + PAT (HKDF-Fernet encrypted at rest), direct-commit or
  PR delivery, one-click commit rollback
- F12 browser error capture, live preview split-pane, VS Code
  extension, MCP server (Cursor / Claude Desktop)
- Ship Wall (public), ORA Wrapped, "ships this week" streak chip
- Unified RailShell navigation (5 sections; Iter 356)
- Admin command centre: overview, financials, users, suggestions, API
  keys, QA health, system mapping, alerts banner + email (Resend)

## Pricing (SSOT: backend/services/subscription_tiers.py)
| Tier | Price/mo | Tasks/mo |
|---|---|---|
| Free | $0 | 10 |
| Starter | $9 | 50 |
| Pro | $19 | 300 |
| Team | $49 | 400 |

Stripe live; webhook-driven ledger. NOTE: founder must still set the
3 monthly price-ID env vars in prod (known pending action).

## Honest current gaps (as of 2026-06-30)
- **0 paying customers, $0 MRR.** Real adoption: 26 developers, 88
  production commits (live /usage/public/stats, test accounts excluded).
- Tavily credits exhausted + Firecrawl key issues → integration alerts
  active on admin Overview.
- ~270 legacy backend pytests quarantined (`@pytest.mark.legacy`).
- Regression Guards 1..21 charter accepted; only Guard 2 shipped and
  Guard 8 partial (see REGRESSION_GUARDS.md).
- CI not wired (needs founder GITHUB_ACTIONS_TOKEN + GITHUB_REPO).

## Public marketing truth policy (Guard 2)
All public adoption numbers render ONLY from
`/api/aurem-dev/usage/public/stats`. No hardcoded counts anywhere
public (grep-locked in tests).
