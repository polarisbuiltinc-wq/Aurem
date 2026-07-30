# REGRESSION_GUARDS — bus-factor doc for the guards system

Last updated: 2026-07-30 (Iter 359 — G18 shipped). Full specs: /app/memory/GUARDS_CHARTER.md.
Live status: /admin/qa "REGRESSION GUARDS" section (planned — ships
last) + Admin Overview critical-alerts banner (RED/STALE escalation).
Rule: guards integrate into the EXISTING QA system — never a parallel
system. STALE = no run within 2× expected interval.

| # | Guard | Protects against | Status |
|---|---|---|---|
| 1 | Route smoke sweep | Any page shipping broken (non-200, "Invalid Date", NaN, empty main) | planned |
| 2 | Marketing truth gate | Fabricated public stats (legal risk) | ✅ SHIPPED — landing + llms.txt render/point to live /usage/public/stats; grep-locks |
| 3 | Scope-drift hard block | Loop writing files outside frozen task spec / protected paths | planned |
| 4 | Rendered-page secret scanner | Live keys/JWTs leaking into rendered HTML | planned (next) |
| 5 | Data invariant tests | Financial math drift, "Invalid Date"/NaN rendering | planned |
| 6 | DB dedup constraints | Duplicate sessions/alerts rows | partial — prod-e2e session debris fixed; unique indexes pending |
| 7 | Payment reconciliation cron | Stripe vs local ledger divergence, stuck pendings | planned |
| 8 | External CI + GitHub sync | Repo staleness + untested pushes | partial — sync badge + >48h RED alert built (services/github_sync.py); CI pending env |
| 9 | External uptime monitor | Site down with nobody watching | planned (founder: UptimeRobot account) |
| 10 | Founder alert emails | Alerts trapped inside the panel | planned (Resend; 1 email/6h dedup) |
| 11 | Backup + restore verify | Data loss / unrestorable backups | planned |
| 12 | One-click rollback | Bad deploy stuck live | planned |
| 13 | LLM cost circuit breaker | Runaway spend burning runway | planned (first in wave 2) |
| 14 | Signup/free-tier abuse | Disposable-email + IP farming | planned |
| 15 | Dependency vuln scanning | Known-CVE deps shipping | planned |
| 16 | Auth/session hardening tests | Admin endpoints reachable by non-founder; stale keys | partial ✅ — router-level admin gate on all 4 admin routers + live non-founder 112-endpoint sweep lock (test_iter358_admin_auth_hardening.py); revoked-key/expired-token/rate-limiter locks pending |
| 17 | Retry & circuit breakers | Hammering dead providers, cascade failures | planned |
| 18 | Universal timeout budget | Infinite-wait outbound calls / hangs | ✅ SHIPPED (Iter 359) — scripts/timeout_audit.py (AST py + regex js) 179/179 sites covered; CI step + predeploy Lane 4; GET /admin/qa/guard18-timeout-audit; locks test_iter359 (11) |
| 19 | Process auto-recovery | Crash loops hiding real bugs | planned |
| 20 | Automated postmortem log | Incidents without history/patterns | planned (ships last) |
| 21 | OWASP/CWE coverage | Injection, misconfig, supply chain | planned |

## If a guard goes RED
1. Read the row's one-line last result on /admin/qa (or the alert in
   the Overview banner — github_sync/integration rows live in
   `topup_alerts`).
2. RED = last run failed → fix the underlying system, re-run the
   guard's job, confirm GREEN.
3. STALE = the guard itself died (cron/CI not running) → treat as an
   incident too; a dead guard must never look green.
4. Every RED/critical should end up in the Incident log (Guard 20,
   once shipped) with root cause + resolution.
