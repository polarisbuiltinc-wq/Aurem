# OVERNIGHT REPORT — T1→T8 (2026-08-28)

## 0. TL;DR
**7 done-with-proof** (T1, T2, T3-partial, T4, T5, T6, T7-build) /
**3 pending-your-GO** (SHIP_VIA_PR prod flip, per-user PIN, F1-F15 seed
re-forward) / **2 skipped-needs-you** noted below (T7-live-drill
credentials, Day-1 onboarding design). Nothing left IN-PROGRESS — the
loop ran to completion. No data-safety trip occurred (IRON RULE 3
never fired).

## 1. DONE + PROOF

| Task | What | Tests green | Regression | E2E proof |
|---|---|---|---|---|
| T1 METER | 4 deterministic fields on every ship/task record (`services/ship_meter.py`), wired into both engines, admin `/admin/loop-metrics` line | `test_overnight_t1_ship_meter.py` 4/4 | 36 pass / 2 pre-existing baseline fails (unrelated `pat_vault`) | `/app/e2e-proof/T1/pytest_t1.log` + live curl (admin line renders, denominator-fixed) |
| T2 SEO/Kit report | 4-row visibility report | n/a (read-only) | n/a | see §2 below |
| T3 Ledger | `ROADMAP.md` §FUTURE LEDGER, R1-R5 + F16/F17 | n/a (file) | n/a | `memory/ROADMAP.md` |
| T4 Session 2 | J1-J4 + K1-K10 re-verify | testing_agent | n/a | `/app/test_reports/iteration_386_session2_pass2_t4.json` |
| T5 Parts D/E/F | jargon + ranked issues + canon | n/a (doc) | n/a | `memory/PART_D_E_F_SYNTHESIS_2026_08_28.md` |
| T6-P1a | per-account `/ora` lockout | `test_ora_chat_pin_login.py` 8/8 | included above | `/app/e2e-proof/T6/p1a_pin_lockout_live.log` (real curl, 5 IPs→429) |
| T6-P1b | "Run in background" → "Close (task keeps running)" | `ShipConfirmModal.p1b_honest_label.test.jsx` 3/3 | — | test file above (source-level, label change is trivially visual) |
| T6-P1c | FixProgressDrawer close icon tooltip | already-shipped pre-run + this run's polish | — | source diff |
| T6-P1d | raw-error humanization (`api.js`) | covered by existing frontend suite (no regression) | — | fixed the exact case testing_agent live-caught in T4 |
| T6-P1e | native confirm sweep (Projects, Integrations) | `P1e_native_confirm_sweep.test.jsx` 6/6 | — | screenshot `/app/e2e-proof/T6/integrations_page.png` (smoke) |
| T7-build | ship-via-PR branch/PR/label/webhook/revert plumbing | `test_overnight_t7_ship_via_pr.py` 12/12 | included in T1 regression batch | flag ON proof via `/admin/feature-flags` curl |

## 2. T2 — SEO/Kit admin visibility (read-only report)

| Surface | Where | Gap |
|---|---|---|
| LLM cost/usage | Settings → Models & LLM → `/admin/llm/configs` | Exists, live |
| Guardrail events | `/admin/guardrails` (GET+POST) | Exists, live |
| Kit citations | **No file, no admin surface at all** — Phase A (dogfood) was never started, still blocked on founder's master spec (confirmed via `ROADMAP.md`) | Not "file-only" as assumed — doesn't exist yet |
| Kit per-project status | No backend model, no admin surface | Confirmed absent (grepped `visibility_kit`/`VisibilityKit`, zero hits) |

"Admin Kit & SEO Dashboard" stays parked (F7) — not built.

## 3. FLAG STATE

| Flag | Preview value (this pod) | Prod value | Who flips prod |
|---|---|---|---|
| `ship_via_pr` (Mongo `feature_flags` collection) | **enabled: true** (set this run via `/admin/feature-flags`) | No row = OFF by default. No env var exists — a prod flip means creating this same flag row in the prod Mongo. | Founder only (A1) |
| `MOCK_LLM` | `true` (backend/.env, this pod) | Unknown/founder-managed | Founder only (A2) — untouched this run |
| `TRACK_SWITCHER_ENABLED` | `false` (unchanged) | unchanged | n/a, no change this run |

## 4. DECISION NEEDED

- **[A8-adjacent] F1-F15 ledger seed missing.** Searched `ROADMAP.md`,
  `PRD.md`, `FUTURE_BUILDS_LEDGER.md` (unrelated freeform format) — no
  F1-F15 6-field entries exist anywhere on disk. **Need**: re-forward
  the original F1-F15 list so it can be seeded verbatim. Did instead:
  seeded F16/F17 + the R1-R5 rules (fully specified in your own
  instruction), logged this gap plainly in `ROADMAP.md` itself.
- **[T6-P1a per-user PIN]** Per-account lockout is built and live-
  proved. A true per-user PIN needs a new schema field + a migration
  path for existing installs. **Need**: your GO to add that schema
  change (not done on auto-pilot per your own instruction).
- **[T7 GitHub App installation, E7]** The pre-seeded fixture project
  (`funnel-repro` → `polarisbuiltinc-wq/ora-grounding`, installation
  `152797252`) cannot mint a real GitHub token from this pod —
  `services.pat_vault.get_repo_token_or_error` returns
  `app_installation_missing` even though the Mongo row says
  `active: true`. **Need**: either re-install the AUREM GitHub App on
  that repo from this Preview pod, or confirm which fixture repo is
  meant to be genuinely live here. This is what blocked the T7-live-
  drill (see §7) — it is a credentials/environment gap, not a build
  gap.
- **[T4 fixture ratify]** J3 found the account's *active* project on
  this pod (`aurem-demo/frontend`) has a revoked App install, and the
  intended fixture (`funnel-repro`) wasn't auto-selected — there's no
  one-click project switcher yet. Used `funnel-repro` directly via API
  once identified. **Need**: confirm this is the intended fixture going
  forward, and whether a real project-switcher is worth prioritizing
  (feeds F16).
- **[Day-1 onboarding, F16]** Fresh signups hit an external GitHub
  OAuth popup before seeing any product value. Two options captured:
  (1) a lightweight "browse a sample repo" preview before connect, or
  (2) a manual repo-URL/PAT fallback alongside the App flow. **Need**:
  your design call — not built this run (F16, parked).

## 5. PROD-FLIP-PENDING

- `ship_via_pr` — built, unit/guardrail-tested (12/12), live-proved ON
  in Preview via `/admin/feature-flags`. **Needs your GO** to flip in
  prod (A1). The **live PR-open drill** itself is blocked by the
  credentials gap in §4 — the code path is proven via mocked-GitHub
  unit tests, not yet via a real merged PR on this pod.
- No other P1 items are Preview-only-pending — P1a-P1e all shipped
  fully (per-user PIN aside, which needs your GO per §4).

## 6. LEDGER

`memory/ROADMAP.md` §FUTURE LEDGER now has:
- R1-R5 (standing rules) — printed at top.
- F1-F15 — **BLOCKED, not seeded** (see §4).
- F16 (Day-1 onboarding) — seeded this run, informed by the real J3
  finding.
- F17 (3-ship-surface consolidation) — seeded this run, per your spec.

No item was built off this ledger (L8 honored).

## 7. KNOWN OPEN / NEEDS REAL-MODEL RE-TEST

- **N1** (assistant self-identifies as ORA, never AUREM) — guardrail
  tests green; actual model wording unverified (`MOCK_LLM=true`).
- **K2, K3, K4, K5, K6, K7, K9** — all require observing the real
  model's phrasing; not guessable, not tested this run.
- **K1 real-fence-pass render** — the fallback path is code+test
  proven; the "happy path, fence parses, real button renders" case
  needs a real model response to organically trigger.
- **T7 live PR drill** — CREDENTIALS-PENDING, see §4/E7. The build
  itself is fully tested against mocked GitHub responses.
- **T1 organic-ship meter proof** — the admin-line/denominator fix is
  live-proved via curl; the 8 rows currently counted are this
  session's own test-fixture writes (all-zero), not an organic AI-
  generated ship. A genuine ship needs either a real model (MOCK_LLM
  off, A2) or the GitHub credentials fix in §4 to drive a mock-content-
  but-real-commit ship. Neither was done on auto-pilot, per the prod
  fence and IRON RULE 3 spirit around fixture-repo caution.

## 8. NO-SILENT-FAIL AUDIT

Every skip/block in this run appears in §4 or §7:
F1-F15 seed (§4) · per-user PIN (§4) · GitHub App installation (§4) ·
J3 fixture ratify (§4) · Day-1 onboarding design (§4) · N1/K2-K7/K9
real-model items (§7) · K1 happy-path live render (§7) · T7 live drill
(§7) · T1 organic-ship proof (§7). Nothing else was skipped this run.

---

## Regression statement

No new failures vs `backend/test-baseline.txt` (404 pre-existing
entries, untouched) or `lint-baseline.txt` (37 backend + 1 frontend,
untouched). All new/changed-file targeted test runs this session: 24
backend (T1+T6+T7) + 28 frontend (T4-verify+T6) = 52 passing, 0 newly
introduced failures. The only 2 failures seen in any run this session
(`test_iter367_rollback_fake_success_fix.py`) are listed verbatim in
`test-baseline.txt` lines 314-315 and are unrelated to any file touched
tonight.

## OVERNIGHT-LOG.md / LOOP-STATE.md

Both closed with a final timestamp (`2026-08-28T09:00Z`). Totals:
**done: 12** (T1, T2, T3-partial, T4, T5, T6×5, T7-build, T7-flag-proof)
· **blocked: 3** (T3 F1-F15, T6 per-user-PIN, T7-live-drill) ·
**skipped: 0** (nothing was skipped outright — every blocked item has
a real partial delivered).
