# AUREM — Future Builds Ledger

**Single source of truth for "save it for future" items.**
Founder-facing. Numbered. Cross-referenced with full design docs when they exist.

---

## How this file works (rules for future agents)

1. **When founder says "save it for future"** → append the item to `## 📋 Future Builds` section with the next available number, one-line summary, priority tag, and (if a detailed design doc exists) a link to it.
2. **When a build is completed and verified** → cut the item from `## 📋 Future Builds`, paste into `## ✅ Shipped Features` with the same number preserved (never re-use numbers), add ship date, add commit/iter ref.
3. **Never delete numbers.** Cancelled items go to `## ❌ Cancelled / Rejected` with a one-line reason, number preserved.
4. **Priority tags:** 🔴 P0 (revenue/stability blocker) · 🟠 P1 (soon) · 🟡 P2 (nice-to-have) · ⚪ P3 (someday).
5. **Trigger column** = the concrete signal that should promote this item from backlog to active build (e.g. "founder green-light", "first user complaint", "10+ paying users", etc.).

---

## 📋 Future Builds

| # | Feature | Priority | Trigger to start | Design doc |
|---|---|---|---|---|
| 1 | **Object-storage / CDN pattern for user media** — persist ORA-generated images + optional chat-upload archive. Two build routes specced (Emergent-managed vs GridFS-own). | 🟠 P1 | Founder green-light OR first user report that a generated-image link went dead in their gallery. | [`GRIDFS_MEDIA_STORAGE_DESIGN.md`](./GRIDFS_MEDIA_STORAGE_DESIGN.md) |
| 2 | **DB Schema Normalization Audit (A- → A)** — Full collection ownership map (`DB_SCHEMA_MAP.md`), flag denormalized fields (e.g. user email cached in multiple docs), write dedupe cleanup script. ~2h. | 🟡 P2 | After migration framework lands so schema changes can be tracked. | *TBD — no design doc yet* |
| 3 | **Index Registry Consolidation (A → A polish)** — Merge 71 scattered `create_index` calls into single declarative source-of-truth. Add `scripts/audit_indexes.py` using `$indexStats` to find dead/duplicate indexes. ~2h. | 🟡 P2 | After migration framework so index changes are versioned. | *TBD — no design doc yet* |
| 4 | **Query Optimization Observability (B- → A)** — Slow-query middleware (>500ms → Sentry breadcrumb), P95 latency logging on hot endpoints, `db.setProfilingLevel` toggle, weekly slow-query digest. ~4h. | 🟡 P2 | Founder notices perf degradation OR after 100+ paying users. | *TBD — no design doc yet* |
| 5 | **Backup Hardening (D+ → A) — 🔴 P1 SILENT DATA-LOSS RISK** — Current `mongodump` writes to `/tmp/backups/` which is **ephemeral pod disk** (wiped on pod restart / redeploy / OOM). Add offsite destination, weekly restore-drill script, Sentry alert on 2+ consecutive fails, encryption-at-rest. ~5h. **2026-02-08: Founder green-lit Cloudflare R2 (S3-compat), 30d retention, 03:00 UTC schedule. Implementation BLOCKED pending R2 keys landing in preview pod's `/app/backend/.env` (heredoc command given).** | 🟠 **P1 · ACTIVE** | Founder pastes 5 R2 keys via heredoc. | *TBD once shipped* |
| 6 | **CDN for user-media file serving** — Cache-layer for GridFS/object-store reads (Cloudflare / Vercel edge). Cuts DB read pressure once media store lives. Tied to #1 (GridFS design doc already flags CDN as a known con to solve). | 🟡 P2 | After #1 ships. | tied to `GRIDFS_MEDIA_STORAGE_DESIGN.md` |
| 7 | **Try/catch + retry-button wrap on every external call** — All external LLM/Stripe/GitHub/R2/Resend calls surfaced to user through a consistent error-shape with a visible "Retry" affordance. UI angle, complements existing backend retry logic. | 🟡 P2 | Founder green-light OR first user report of "silent hang" on external. | *TBD* |
| 8 | **4-state UI audit (loading / error / empty / success)** — Sweep every user-facing view; ensure all four states are explicitly rendered with distinct copy. Catches "spinner-forever" bugs and "blank screen means error" ambiguity. | 🟡 P2 | Post-Redis close, once cosmetic batch (Session 5) begins. | *TBD* |
| 9 | **Exponential-backoff retry policy (1s → 2s → 4s, capped 3 attempts)** — Codify in a shared `retry_with_backoff` helper for LLM/Stripe/GitHub client calls. Overlaps with #7 but is the backend implementation detail. | 🟡 P2 | Same window as #7. | *TBD* |
| 10 | **Accessibility audit (screen-readers, focus rings, ARIA)** — Full a11y sweep of ChatPanel, OraDirect, Dashboard. Never discussed before this session. | 🟡 P2 | Cosmetic-batch session. | *TBD* |
| 11 | **Offline handling** — Detect `navigator.onLine === false`, show non-scary offline banner, queue pending sends locally, replay on reconnect. Never designed before. | 🟡 P2 | Cosmetic-batch session OR first user report. | *TBD* |
| 12 | **Bundle-size optimization** — Baseline current `dist/` size, dynamic-import heavy routes (ORA-direct, Dashboard), tree-shake unused shadcn primitives. Never measured. | 🟡 P2 | Marketing push planned OR founder notices load-time complaint. | *TBD* |
| 13 | **Mobile-responsive testing sweep** — Currently untested breakpoints. Playwright suite across 375/768/1024 viewports, snapshot every critical page. | 🟡 P2 | Marketing push OR first mobile-user complaint. | *TBD* |
| 14 | **Mongo connection-pooling audit** — Confirm motor client `maxPoolSize` / `minPoolSize` / `maxIdleTimeMS` values are sane for expected concurrency; document rationale in `DB_SCHEMA_MAP.md` (once created per #2). Quick-check, ~30 min. | 🟡 P2 | Load-testing (#16) surfaces conn-limit errors OR post-Redis close audit sweep. | *TBD* |
| 15 | **Redis query-caching (reuse existing Upstash)** — Cache hot read-only Mongo queries in Redis (e.g. `dev_users` by-id, project-brain fetches). Matches DB-audit's B- grade gap. TTL config, invalidation-on-write. | 🟡 P2 | After DB observability (#4) surfaces which queries dominate. | *TBD* |
| 16 | **Pre-launch load testing (k6 or Artillery, 50-100 concurrent users)** — Ramp scripts against staging preview URL, prove rate-limiter + Mongo pool + LLM fallback all hold. Elevate to P1 the moment a marketing push is scheduled. | 🟠 **P1-conditional** | Marketing push scheduled OR before first paid-plan launch. | *TBD* |
| 17 | **Dependency audit — npm-audit (frontend) + pip-audit (backend)** — Elevated by founder from generic-P2 due to prior real credential-leak incident. Report high/critical CVEs, safe-fix what won't break, flag rest. | 🟠 **P1 elevated · next session** | Redis P0 already closed → runnable now. | *TBD* |
| 18 | **IDOR / Authorization-boundary self-audit** — 2 disposable test accounts, systematically try Account-A reading/writing Account-B resources across `/ora-chat/*`, `/cto-projects/*`, and every user-scoped endpoint. Founder rule: any success = **P0 immediate**, not backlog. Elevated because prior credential-leak incident on this project shows this category has already bitten us. | 🟠 **P1 elevated → 🔴 P0-if-found · next session** | Redis P0 already closed → runnable now. | *TBD — write methodology doc before running* |
| 19 | **Secrets sweep beyond the one known `test_credentials.md` incident** — Grep built frontend bundle for key-patterns (`sk-`, `rediss://`, `AKIA`, `AIza`, R2 access-ID shape), confirm `.env` is `.gitignore`'d and no fork of it slipped into `memory/` or `docs/`. Elevated for same reason as #18 (known-real-incident category). | 🟠 **P1 elevated · next session** | Redis P0 already closed → runnable now. | *TBD* |

---

## ✅ Shipped Features

Items numbered `S1+` were shipped directly (never sat in the Future
Builds list). Items that were originally numbered under Future Builds
keep their original number when moved here.

| # | Feature | Shipped on | Commit / Iter ref |
|---|---|---|---|
| S1 | **Redis P0 · Upstash-backed cross-pod rate-limiter** — Founder created free-tier Upstash Redis, rotated password, pasted `rediss://` URL into prod `REDIS_URL` env, redeployed. Health probe returns `{"backend":"redis","redis_active":true,"last_error":null}`. Single-IP burst test from preview → auremcto.com hit expected 299 × 401 + 101 × 429 split on the 300 req/min ceiling. Conflicting zero-429 result from founder's testing sandbox reconciled via the new `/health/echo-ip` diagnostic (proved 8-IP proxy-pool egress there). Options A (Emergent-managed) and C (stopgap ceilings) dropped in favour of B (Upstash). | 2026-02-08 | Session 2 close |
| S2 | **Migration Framework (DB grade C+ → A)** — `backend/migrations/{base,framework,cli}.py` + `__main__.py`. Versioned, tracked, reversible migration pipeline with `migration_history` collection (version, checksum, env, duration, status). Features: idempotent apply, `.down()` rollback with `irreversible` safety, checksum-drift detection, orphan detection, dev-only env-gating, dry-run, `mark-applied` for adopting existing state, `new <slug>` scaffolder. Existing `001_aurem_upgrade_indexes.py` + `002_encrypt_pats.py` converted to Migration-subclass pattern (legacy `python -m migrations.NNN_*` shims preserved). 18/18 pytest cases green. Full usage docs at `backend/migrations/README.md`. | 2026-02-08 | Deployed prod |
| S3 | **Migration Admin HTTP endpoint** — `routers/migrations_admin.py` mounted at `/api/aurem-dev/admin/migrations/*`. Endpoints: `GET /status`, `POST /mark-applied/{version}`, `POST /up`, `POST /down`, `POST /verify`. Same JWT admin gate as every other admin router. Preview E2E verified: `applied: 2, pending: 0, is_clean: True`. Prod adoption still awaits founder cookie-based curl call (bookkeeping only, no rush). | 2026-02-08 | Deployed prod |
| S4 | **Diagnostic `/api/aurem-dev/health/echo-ip` endpoint** — Uses the exact same `client_ip_from_request()` helper the rate-limiter keys on, so it can never drift from the real bucket logic. Returns caller's effective_ip + every IP-related header (X-Forwarded-For, CF-Connecting-IP, X-Real-IP, True-Client-IP, Forwarded, socket_client_host). Kept live for any future rate-limiter or CDN incident. | 2026-02-08 | Deployed prod |
| S5 | **TC-11 "New run" button fix** — `Dashboard.jsx:handleNewRun` now rotates `sessionId` via `crypto.randomUUID()` (was a bare event dispatch that never touched sessionId). `ChatPanel.jsx:374` useEffect on sessionId change now also calls `setInput("")`. All 5 contract points asserted in `TC11.newRunButton.test.jsx` (3/3 green). Preview E2E verified + prod-deployed 2026-02-08 (commit `8aaa759`). | 2026-02-08 | commit 8aaa759 |
| S6 | **Session 3 · 3.3 Intent-classifier calibration** — Added `INTENT_CASUAL = "CASUAL_CHAT"` as third label to the two-layer classifier. Rewrote `_LLM_SYSTEM_PROMPT` with 3-way schema + explicit CODE_CHANGE tie-break for short imperative confirmations ("fix it", "do it", "go ahead"). Baseline: 12/12 correct (was 3/12), 0 regressions on the 3 previously-correct cases. Adversarial set: 6/6 real code-change requests correctly held on CODE_CHANGE (no CASUAL_CHAT swallow). Frontend guard at `OraDirect.jsx:1285` updated so CASUAL_CHAT renders no chip and no Loop CTA — regression caught by the frontend test the founder insisted on. Coverage: `backend/tests/test_intent_router_casual_chat.py` (4/4) + `frontend/src/components/__tests__/OraDirect.intent_casual_chat.test.jsx` (6/6). Backend + frontend deployed to prod. | 2026-02-08 | commit 3d5c439 + one pending redeploy for frontend guard |
| S7 | **Session 3 · 3.2 TC-12 plan-content mismatch — investigation only, kept-open** *(logged in Shipped for audit-trail completeness, NOT marked fixed)* — Reconstructed 4-point backend request with "add endpoint" bait failed to reproduce the original failure signature across 3 runs (all runs covered 4/4 points, no auth/idempotency invention). Per founder rule, status = "could not reproduce with reconstructed input, kept open." Will resurface if hit organically; next occurrence must capture exact failing request text into memory/ before retest. | 2026-02-08 | no code change |

---

## ❌ Cancelled / Rejected

*(Empty.)*

| # | Feature | Rejected on | Reason |
|---|---|---|---|

---

**Last updated:** 2026-02-09 · Session 4 mid-flight (Backup Hardening blocked on R2 keys).
· Founder created this ledger via instruction "save all future builds in a file … with listing numbers".
· 7 items shipped this session (S1-S7). **19 items** now parked in Future Builds (numbers 1-19). 
· 2026-02-09 batch add: items 6-19 sourced from founder's Matt Murphy video-consolidated master list; dedupes honoured (Object-storage / URL-only-DB skipped as covered by #1; error boundaries + rate-limiting + backend-mediated + normalized/indexes/migrations/backups + legal skipped per founder's own annotations).
· Elevated-P1 batch: #17, #18, #19 (dependency-audit, IDOR, secrets-sweep) — flagged for next-session action, NOT generic-P2 backlog, because a prior credential-leak incident makes this category empirically-repeat-risk on this project.
