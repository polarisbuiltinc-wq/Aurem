# REGRESSION GUARDS CHARTER (founder-mandated, 2026-06-30)

**ARCHITECTURE RULE**: Sab guards EXISTING QA system ke andar integrate honge —
pre-deploy gate, /admin/qa page, qa_matrix, auto-qa-agent. Koi naya parallel
"guards" system NAHI. QA Health = single source of truth.
- Har guard = ek QA check registered in existing QA framework.
- /admin/qa page pe naya section: "REGRESSION GUARDS" — har guard ek row.
- Row: guard name · GREEN/RED/STALE · last run · next expected · one-line last
  result · click-through to run history.
- STALE = expected interval ke 2x tak koi run nahi. RED/STALE → existing
  critical-alerts banner on Overview.
- "GUARDS: X/N green" summary next to TEST COUNTS strip.
- Data from real backing collections (synthetic_checks, reconciliation log,
  integrity log, CI API). Dashboard READS, never fabricates.
- Lock tests: no-runs-in-2x-interval → STALE; failing-last-run → RED.
- No mocks, no TODOs, E2E tested, raw proofs after each deploy.

## SHIP ORDER (combined)
Wave 1: Guard 2 (DONE 2026-06-30) → 4 → 3 → 1,5,6,7 → 8 → QA page integration last
Wave 2 (9-16): 13 → 10 → 9 → 14 → 16 → 11 → 12 → 15
Wave 3 (17-21): 18 → 17 → 21 → 19 → 20

## STATUS
- Guard 2: ✅ SHIPPED (Iter 356): /usage/public/stats real_developers +
  commits_shipped (test-account excluded via services/test_accounts.py),
  Landing renders live-only (zero stats hidden), grep-lock in
  tests/test_iter356_nav_dedup_marketing.py. QA page row PENDING (ships last).
  PROD PROOF: real_developers=26, commits_shipped=88 live on auremcto.com.
- Guard 8 (partial): ✅ BUILT (Iter 357, preview — deploy pending): GitHub
  sync detection per founder correction — reuses EXISTING build badge on
  Admin Overview (services/github_sync.py = single check/data source;
  GET /admin/github-sync founder-gated; states not_wired/in_sync/behind/
  error; >48h gap → CRITICAL row in EXISTING topup_alerts banner,
  auto-resolves on sync; 10-min cache; commits-behind via local git on
  preview, hours-gap fallback on prod). Locks: test_iter357_github_sync.py
  (8). AWAITING FOUNDER: GITHUB_ACTIONS_TOKEN + GITHUB_REPO env vars
  (currently shows "not wired"). Full Guard 8 (CI wiring) still pending.
- Guard 16 (partial): ✅ router-level admin gate on all 4 admin routers
  (require_admin_dep) + live non-founder 112-endpoint 403 sweep lock
  (Iter 358b, deployed with SEO/chip). Remaining: revoked-key/
  expired-token/auth-endpoint-rate-limiter tests.
- Guards 1, 3-7, 9-15, 19, 20: NOT STARTED.
- Guard 21: ✅ SHIPPED (Iter 361, 2026-07-30): scripts/g21_security_scan.py
  — supply-chain (requirements.txt all pinned == : 0 unpinned; yarn.lock
  committed) + misconfig (no FastAPI/uvicorn debug=True; no default-cred
  patterns; every routers/admin*.py has router-level admin gate except
  admin_public.py; @app.exception_handler(Exception) present so no raw
  stack traces leak). Injection fuzz suite tests/test_iter361 (23): SQLi/
  NoSQL($ne operator auth-bypass)/XSS/command-injection payloads vs live
  signup + notify-interest + login — assert no 500, no stack-trace markers
  leaked, XSS inert (JSON content-type not text/html), NoSQL operator
  rejected. NOTE: login fuzz kept to 2 hits (NoSQL+nested) to avoid the
  brute-force IP-lockout polluting shared preview. Wired: ci.yml backend
  step + predeploy Lane 5 (build-fail on finding). Endpoint: GET
  /admin/qa/guard21-security-scan (founder-gated, live). Locks: static
  scan self-tests (detects unpinned dep + ungated router) + fuzz. QA row
  ships last.
- Guard 17: ✅ SHIPPED (Iter 360, 2026-07-30): services/retry_guard.py =
  THE central retry utility — CircuitBreaker (closed/open/half_open,
  threshold 5, cooldown 60s, single half-open probe w/ 30s stale expiry),
  call_with_retry (full-jitter exponential backoff), transition ring +
  best-effort Mongo breaker_events persist, trip_counts_7d. Deps
  pre-registered: openrouter/deepseek_direct/groq/github/stripe/tavily/
  firecrawl/vercel/resend/supabase. MIGRATED: llm.py _call_deepseek
  (openrouter breaker — open ⇒ skip chain, straight to groq/deepseek
  fallbacks), loop_safety.github_request_with_retry (fast-fail + 5xx/
  network recording; rate limits NOT counted), repo_heal._try_with_retries
  (→ call_with_retry), web_skills web_search+fetch_url (tavily, graceful
  "circuit open" tool errors). services/llm_circuit_breaker.py shim now
  real (admin_bin Bin panel reads central state). Endpoint: GET
  /admin/qa/guard17-breakers (founder-gated). UI: DEPENDENCIES strip on
  Admin Overview (green/amber/red dots, data-testid dependency-breaker-
  strip). NOTE: shared/resilience/* = pre-existing DEAD code (0 callers,
  other-product copy) — delete in Phase 4 dead-code removal. Locks:
  test_iter360_guard17_retry_breaker.py (17). QA page row ships last.
- Guard 18: ✅ SHIPPED (Iter 359, 2026-07-30): scripts/timeout_audit.py —
  static audit (Python AST: httpx/requests/aiohttp/urlopen need timeout=;
  JS regex: fetch needs signal/timeout, axios.create + direct axios.* need
  timeout). 23 violations found + fixed (6 backend AsyncClient ctors,
  17 frontend fetch/axios sites → AbortSignal.timeout). 179/179 covered.
  Escape hatch: `g18-exempt: reason` comment. Wired: ci.yml backend job
  step + predeploy_gate.sh Lane 4 (build-fail on violation). Endpoint:
  GET /admin/qa/guard18-timeout-audit (founder-gated, computed live).
  Locks: test_iter359_guard18_timeout_audit.py (11 — live-zero-violations
  + scanner self-tests + exempt + endpoint gate). QA page row ships last.

## GUARD SPECS

### G1 — Route smoke sweep
Playwright authenticated sweep of EVERY route (/admin/*, /dashboard, /settings
all tabs, /integrations, /projects, /deploy, /domain, /tokens, /analytics,
/automations, /wrapped, /wall, landing, /signup, /login). Fail per page:
non-200, "Cloudflare could not parse"/"Invalid Date"/"NaN"/"undefined"/raw
stack trace in text, or empty main content. Runs: pre-deploy gate + prod cron
30min. Results → synthetic_checks collection → QA row.

### G2 — Marketing truth gate ✅
Landing stats ONLY from /api public stats (real DB, test accounts excluded).
Lock: grep /\d{3,}\+/ near users/commits/developers in public pages → fail.
QA row: last grep-lock result + stats endpoint freshness.

### G3 — Scope-drift hard block
loop_integrity_guard: extras beyond frozen set = write BLOCKED + loop failed
(not just log). PROTECTED_PATHS (routers/admin*, payments.py, auth.py, mcp.py,
vault*, stripe_client.py, .github/workflows/*): loop writes only when task spec
explicitly names file AND trust >= L2 manual ship gate. Attempts logged.
Locks: out-of-scope reject; unnamed protected-path reject. QA row: blocked
writes this week + last attempt log.

### G4 — Rendered-page secret scanner
In G1 sweep + CI step: rendered HTML scan for sk-aurem-[A-Za-z0-9_-]{20,},
ghp_...{20,}, eyJ JWT — outside masked contexts. Full secret = fail/alert.
QA row: last scan pages count + findings.

### G5 — Data invariant tests
Financials: net_profit == mrr - total_burn exact; USD/CAD sign consistency;
no NaN/Invalid. Plans: exactly ONE "Current" tier == dev_users.tier. Date
helpers unit-tested vs dict/null/malformed. Runs in pre-deploy gate; QA page
"invariants" sub-count.

### G6 — DB dedup constraints
Chat sessions true dup key → MongoDB unique index → one-time cleanup migration
(report count) → graceful DuplicateKeyError. Same for duplicate
integration-alert rows (Stripe/Tavily). QA row: index present + dup count == 0.
NOTE: prod-e2e-* session debris already fixed (Iter 356) — filter + teardown +
cleanup endpoint POST /admin/qa/cleanup-e2e-sessions.

### G7 — Payment reconciliation cron
Hourly Stripe API vs local payments diff. Local pending >24h OR
Stripe-paid-missing-locally → critical alert + reconciliation log. Lock:
mocked stuck-pending fires alert. First run: classify existing 22 pendings.
QA row: last run, stuck count, reconciled count.

### G8 — External CI (GitHub Actions)
ci.yml push to main → pytest + vitest + G4 scan + G5 invariants. Founder gives
GITHUB_ACTIONS_TOKEN + GITHUB_REPO later — wire QA Health "CI STATUS" panel.
workflows/ dir in G3 PROTECTED_PATHS.

### G9 — External uptime monitor (dead man's switch)
UptimeRobot/BetterStack free tier pings /api/healthz 1-5min FROM OUTSIDE.
Founder banayega account — agent deta hai setup steps + exact config.
/api/healthz response mein last-cron-heartbeat field add karo. QA row:
external monitor last-seen heartbeat, STALE if >10min.

### G10 — Founder alert channel (email via Resend)
Har CRITICAL alert (banner + guards RED/STALE) → founder email via Resend.
Dedup: same alert max 1 email/6h. Daily digest option. Lock: forced critical
alert → email send call fires. QA row: last alert-email + delivery status.
PROOF: real received email screenshot (founder inbox).

### G11 — DB backup + restore verification
Daily MongoDB backup (Atlas steps if managed, ya mongodump→object storage).
WEEKLY automated restore-test into throwaway DB: collection counts + sample
integrity assert. Restore-test fail = RED. QA row: last backup time, size,
last restore-test result. PROOF: restore-test output with counts.

### G12 — One-click rollback
Admin founder-gated "Rollback to previous build": previous known-good SHA se
revert deploy trigger (deploy_logger events se SHA). Test: preview rollback
execute — build hash flip proof. QA row: last rollback test date + current vs
previous SHA.

### G13 — LLM cost circuit breaker
Hourly + daily spend caps (env: e.g. $2/hr, $10/day all providers). Cap hit →
new LLM calls blocked with clear user message + founder alert (G10). Loop
level: single loop > $X (default $0.50) → auto-kill + log. Lock: mocked
overspend → blocked + alert. QA row: current hour/day spend vs caps, live.
PROOF: blocked-call log with mocked overspend.

### G14 — Signup & free-tier abuse protection
Disposable-email domain blocklist (maintained package, not hardcoded). Per-IP
signup limit (3/day). Free-tier task rate limit per account + per IP.
Violations logged. Locks: disposable rejected; 4th same-IP signup rejected;
free burst throttled. QA row: blocked signups (7d) + throttle events.

### G15 — Dependency vulnerability scanning
CI (G8 workflow): pip-audit + npm audit --audit-level=high. HIGH/CRITICAL = CI
fail (allowlist with expiry dates — permanent ignores banned). QA row: last
scan + open high/critical count (must be 0).

### G16 — Auth & session hardening tests
Automated: (a) revoked sk-aurem key rejects on next request, (b) expired
session token rejects, (c) EVERY /admin/* endpoint 403 for non-founder
SERVER-SIDE (auto-enumerate routes from router), (d) rate limiter fires on
auth endpoints. QA row: last run + endpoints covered. PROOF: full endpoint
enumeration report.

### G17 — Retry & cascade protection
Central retry utility (all outbound: OpenRouter/LLM, Stripe, GitHub, Tavily,
Firecrawl, Vercel MCP): exponential backoff + jitter, max retries, circuit
breaker per dependency (open after N consecutive fails, half-open probe after
cooldown). No caller retries outside this utility — audit + migrate existing
direct-retry code. Lock: mocked failing dep → breaker opens, stops hammering,
half-opens on schedule. QA row: per-dependency breaker state + trip count (7d).
PROOF: breaker-state transition log from forced-failure test.

### G18 — Universal timeout budget
Every outbound network call has explicit timeout (no infinite waits). Audit +
fix missing. On timeout: graceful degraded response (cached/stale + "degraded"
flag, or clear "temporarily unavailable" — never hang/raw 504). Lock: static
grep/analysis in CI flags fetch/axios/httpx without timeout → build fail.
QA row: calls covered vs total outbound found. PROOF: CI timeout-audit output.

### G19 — Process-level auto-recovery (always active)
Supervisor auto-restart on crash/OOM (check Emergent platform capability).
/api/healthz gates traffic. Restart-loop detection: 3+ restarts in 10min →
STOP auto-restarting + founder alert (G10). Lock: forced crash → restart
confirmed + loop threshold trips alert. QA row: restarts (7d), last reason,
loop trips. PROOF: forced-crash-to-restart log + loop-trip proof.

### G20 — Automated postmortem log (always active, ships LAST in wave 3)
Every RED/critical alert from ANY guard (1-19) auto-creates postmortem entry:
what broke, when detected, which guard caught, root cause (filled on resolve),
resolution, follow-up. New /admin/qa sub-tab "Incident log" — chronological,
filterable, open vs resolved. Lock: forced alert → entry auto-created with
guard linkage. QA row/tab: open incidents, MTTR (30d). PROOF: screenshot of
Incident log with real auto-created entry.

### G21 — Broader OWASP/CWE coverage
Injection fuzz suite: SQL/XSS/command-injection payloads against every input
field/API param, assert safe handling. Misconfiguration check in CI: no
unauthed debug/admin routes, no default creds, no stack-trace leaks in prod
mode. Supply-chain: lockfiles committed + verified in CI, no unpinned deps.
Locks: bad payload rejected; missing-auth route fails scan; unpinned dep fails
CI. QA row: fuzz pass rate, misconfig findings, unpinned deps (must be 0).
PROOF: fuzz report + misconfig scan output.

## FOUNDER-SIDE ACTIONS NEEDED (batao exactly kab)
- G9: UptimeRobot account creation (agent gives config)
- G13: spend-cap env values confirm
- G11: Atlas backup settings (agar dashboard access chahiye)
- G8: GITHUB_ACTIONS_TOKEN + GITHUB_REPO env
