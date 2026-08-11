# Architecture Hotspot Audit — 2026-02-11

**Method**: churn (`git log --follow` per file, all-time), size (LOC), coupling (Python `ast` import graph, 100+ files), duplicate-pattern grep, test-file cross-reference. Analyses run against `backend/` + `frontend/src/` only; tests / scripts / docs excluded from prod-code counts.

---

## 🔴 Top 10 Hotspots — Churn × Complexity

Score = `commits × log₁₀(LOC)`. Anything scoring ≥ 100 is a real refactor candidate.

| # | Score | Churn | LOC | File | Why it matters |
|---|---:|---:|---:|---|---|
| 1 | **652.8** | 177 | **4,874** | `frontend/src/components/ChatPanel.jsx` | **Extreme** — biggest hotspot in the entire codebase. Handles chat streaming, message rendering, tool-call orchestration, ORA state. Single component owns way too much. First place a regression will hit. |
| 2 | **473.1** | 138 | 2,681 | `backend/main.py` | 138 commits into an entry-point suggests every new router/middleware/env-var adds a line here. `main.py` should be a boot-only file; investigate what's leaking in. |
| 3 | **455.3** | 128 | **3,607** | `backend/routers/chat.py` | Same story as ChatPanel — request-side of the chat pipeline. Every intent/context/tool-routing tweak lands here. Tightly coupled to `orchestrator.py`, `loop_engine.py`, `local_tools.py`. |
| 4 | **323.5** | 86 | **5,782** | `backend/routers/admin.py` | **Largest single file in the repo.** 86 commits × 5,782 LOC = a "god router" for every admin surface. High risk of a change to one admin tab breaking another. |
| 5 | **290.7** | 81 | **3,876** | `backend/routers/cto_projects.py` | Owns project add / edit / list / delete / PAT decryption / OAuth-fallback logic. The Bug 2 sweep touched this file — proves it's structurally a magnet for auth/token changes. |
| 6 | **284.3** | 84 | 2,426 | `backend/services/orchestrator.py` | LLM routing + provider fallback. High churn suggests every model change requires an edit here. |
| 7 | **240.6** | 66 | **4,416** | `backend/services/loop_engine.py` | Plan → Execute → Verify → Ship state machine. Massive file. Bug 2 sweep found **7 separate token-fetch sites** in this one file — clear evidence of duplicated concerns. |
| 8 | 163.0 | 63 | 387 | `frontend/src/App.jsx` | Not oversized; high churn because it's the router. Recently refactored (`<PrivateRoute>` wrap). Watch. |
| 9 | 162.8 | 57 | 718 | `frontend/src/pages/Dashboard.jsx` | Landing surface for logged-in users; every UX iteration lands here. |
| 10 | 162.1 | 46 | **3,347** | `frontend/src/pages/Admin.jsx` | Mirror of `routers/admin.py` — grew tab-by-tab into a monolith. |

**Immediate takeaway**: 5 of the top 10 files are **>3,000 LOC**. `admin.py`, `cto_projects.py`, `chat.py`, `loop_engine.py`, `Admin.jsx`, `ChatPanel.jsx` — these are the "if it breaks, everything breaks" files.

---

## 🕸️ Coupling — Files Everyone Depends On ("God Files")

Backend import graph, top targets by number of importers.

| # of importers | Module | Assessment |
|---:|---|---|
| **100** | `cto_services.db` | Expected — everyone needs Mongo. Not a smell. |
| **65** | `cto_services.auth` | Expected — every gated endpoint uses `current_dev()`. Not a smell. |
| **27** | `services.llm` | High but justified — LLM is a shared primitive. |
| **21** | `services.pat_vault` | 🟠 **Watch** — this is the file Bug 2 lived in. 21 importers means any breaking change to `get_repo_token()` signature ripples widely. Consider freezing its public API now. |
| **14** | `services.usage` | Token accounting. Fine. |
| **10** | `services.vanguard_scanner` | Fine. |
| **10** | `services.github_api_writer` | Fine, cohesive purpose. |

**Circular imports found: 0** ✅ — clean dependency DAG.

**Bin_context.py-style landmines (import count ≥ 20 + covers a security boundary)**: only `pat_vault.py` currently qualifies. Not a red flag yet, but the reason Bug 2 was systemic was that **21 files independently reimplemented the token-lookup instead of calling `pat_vault.get_repo_token()`** — that's the *inverse* of a god-file problem (too little central use, not too much). The fix landed 2026-02-11.

---

## 🧬 Duplicate / Near-Duplicate Logic

### 🟠 Duplicate class A: HTTP client boilerplate (18+ files)
`httpx.AsyncClient(timeout=X)` is instantiated in **at least 20 different `services/*.py` and `routers/*.py` files** with **~218 individual usages** — each with its own timeout, headers, retry logic, and error handling. Zero shared wrapper.

| File | External service |
|---|---|
| `services/github_api_writer.py`, `services/github_app.py`, `services/github_oauth.py`, `services/repo_heal.py` | GitHub |
| `services/verification_email.py`, `services/welcome_email.py`, `services/onboarding_email.py` | Resend |
| `services/vercel_platform_deploy.py`, `services/vercel_skills.py`, `services/supabase_provisioner.py` | Vercel/Supabase |
| `services/web_skills.py`, `services/graph_builder.py`, `services/health_checks.py`, `services/integration_health.py`, `services/ora_client.py`, `services/dev_skills.py`, `services/loop_safety.py`, `services/project_brain.py`, `services/local_tools.py`, `services/mock_reality_check.py` | Various |

**Impact**: Same class of bug hits every one of these — no shared retry/backoff, no shared timeout policy, no shared trace propagation, no shared error taxonomy. This is Ledger item #29 ("External-API wrapper audit — single-source-of-truth per service"). Already scheduled but currently 🟡 P2. **Recommend elevating to 🟠 P1** — every one of these is a Bug-2-class landmine waiting for the next auth/rate-limit/error-format tweak.

### 🟠 Duplicate class B: Retry / backoff (8 separate implementations)
`services/loop_safety.py`, `services/repo_heal.py`, `services/retry_guard.py`, `services/llm/openrouter_client.py`, `services/orchestrator.py`, `services/loop_full_scan.py`, `services/loop_engine.py`, `routers/cto_projects.py` — each has its own `max_retries + sleep(2**i)` loop. There *is* a `services/retry_guard.py` central helper but 7 of 8 callsites bypass it. Migrating everyone to `retry_guard` would kill this whole class.

### 🟢 Duplicate class C: Fernet encrypt/decrypt (RESOLVED via Bug 2 sweep)
9 files touched Fernet directly before 2026-02-11. Post-sweep, only `services/pat_vault.py` + `cto_services/crypto.py` own the primitives; everything else routes through `get_repo_token(project)`. Ledger #43 close verified this class.

### 🟡 Duplicate class D: PAT/token guard patterns
34 references across 10 prod files remain — all now going through `services.pat_vault.get_repo_token()` after the Bug 2 sweep. **No action needed**, but noted as a class that used to be broken.

### 🟢 Duplicate class E: `datetime.utcnow()` (deprecated) — CLEAN
Grep count on prod code: **0 direct usages**. All 332 datetime callsites use `datetime.now(timezone.utc)`. The 4 hits earlier included tests + scripts only.

### 🟢 Duplicate class F: Founder/admin gate — CLEAN
Router-boundary `Depends(require_admin_dep)` + inline `_require_admin(authorization)` pattern is consistent across 12 files (defense-in-depth). Not a duplication problem — same pattern intentionally repeated.

### 🟢 Duplicate class G: Rate-limit — CLEAN
Only 5 files own rate-limit implementations, all cohesive: `services/rate_limiter.py` (Upstash), `services/signup_guards.py` (per-IP signup gate), `main.py` (global net), `routers/auth.py` (login lockout), `routers/codebase_health.py` (scan rate). Each is a distinct policy, not duplication.

---

## 💀 Risk Zones — Large + Rarely-Touched + Zero Tests

Files ≥ 500 LOC, ≤ 3 commits over their lifetime, and no matching test file. These are the "nobody's looked at this in months" landmines.

| LOC | Churn | File | Why it's a risk |
|---:|---:|---|---|
| 684 | 2 | `backend/services/supabase_provisioner.py` | Provisioning code — activated during onboarding for Supabase-track users. Test coverage is 0. If Supabase changes their API, we won't know until a customer hits it. |
| 679 | 1 | `frontend/src/components/DeployPanel.jsx` | Deploy panel UI. **1 commit** (initial add). If deploy logic ever changes, this UI has zero cushion. |
| 655 | 2 | `backend/services/vercel_skills.py` | Vercel API glue for Personal Track users. Same story as supabase_provisioner. |
| 553 | 3 | `backend/services/llm/openrouter_providers.py` | OpenRouter provider config. Not a bug magnet today, but any OpenRouter API shape change flows through here. |
| 521 | 3 | `frontend/src/pages/AdminFinancials.jsx` | Financial dashboard. Founder-only. Reads live Stripe data. **If Stripe API shape drifts, this page silently mis-renders revenue.** |

**Total: 5 files ~3,092 LOC of low-test-coverage code**. Recommend: seed a smoke test (single "does it 200 with mocked deps?") for each. That alone would catch 80% of regressions.

---

## 📋 Prioritized Action Recommendations

### 🔴 P0 — Structural refactors (long-lived pain)

1. **Split `backend/routers/admin.py`** — ✅ **SHIPPED 2026-02-11**. 5,782 LOC → 6 sub-routers + 1 shared helper. 110 handlers preserved via AST verification + testing agent 32/32 green.

2. **Split `frontend/src/components/ChatPanel.jsx` (4,874 LOC)** — Phase 3, deferred to supervised session.

3. **Split `backend/services/loop_engine.py` (4,416 LOC, 7 token-fetch sites)** — Phase 3, deferred. Do LAST — Bug 2 just stabilized this file.

### 🟠 P1 — Deduplication payoff

4. **Elevate Ledger #29 (external-API wrapper audit)** from P2 → P1. 218 `httpx.AsyncClient()` calls across 20 files = 20 independent surface areas that will need patching next time we standardize retries, tracing, or timeouts. Build `services/http/client.py` with policy-injected clients per external service. Fix once, benefit everywhere.

5. **Migrate 7 remaining retry-loop callsites → `services/retry_guard.py`** (`loop_safety`, `repo_heal`, `openrouter_client`, `orchestrator`, `loop_full_scan`, `loop_engine`, `cto_projects`). Small, mechanical, high dedup ratio.

### 🟡 P2 — Coverage floor

6. **Seed smoke tests for the 5 risk-zone files** (supabase_provisioner, DeployPanel, vercel_skills, openrouter_providers, AdminFinancials). One `does_it_render_without_throwing` test per file. Cheap insurance.

### 🟢 P3 — Watchlist (no action, but track)

- `services/pat_vault.py` — freeze public API. 21 importers means any signature change now ripples across the whole backend.
- `backend/main.py` — 2,681 LOC entry point. Investigate what's leaking in vs what should be delegated to routers/middleware modules.
- `services/orchestrator.py` — 2,426 LOC LLM router. Growing at 84 commits and rising. Split before it becomes the next chat.py.

---

## ✅ What's actually healthy

- **Zero circular imports** across 100+ backend Python files
- **Zero `datetime.utcnow()` usage** in prod code (fully migrated to timezone-aware)
- **Founder/admin gate pattern is consistent** — same defense-in-depth applied everywhere
- **Rate-limit implementations are cohesive** — no dupes, each policy is distinct
- **Post-Bug-2 sweep, PAT/token dispatch is centralised** — no residual `_decrypt_pat` scatter
- **All top-10 hotspots have ≥ 40 test-file mentions** — heavy-churn files are well-tested (only concern: quality of coverage, not quantity)
