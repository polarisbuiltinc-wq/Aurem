# Overnight T5 — Parts D/E/F Synthesis (2026-08-28)

Lens: **developer-founder Maya** — knows repo/commit/branch/PR/env var.
`repo`/`commit`/`branch`/`PR` are NEVER jargon under this lens. The bar
is: no raw stack traces, no silent nulls, ONE assistant name + ONE verb
set, every reply parseable in 5 seconds, a concrete next action.

Built from source inspection + testing_agent's live pass
(`/app/test_reports/iteration_386_session2_pass2_t4.json`) on this pod
(MOCK_LLM=true — anything requiring the model's actual generated text
is marked NEEDS REAL-MODEL RE-TEST, not guessed).

---

## Part D — Jargon / copy / prompt sweep

| Where | Phrase | Why a technical user is lost | Plain replacement | Status |
|---|---|---|---|---|
| `MessageBubble`/`api.js` error path | `⚠ HTTP 403: {"detail":"..."}` | Raw status-code prefix + JSON braces stacked on top of an already-good themed panel — visual noise, breaks "parse in 5s" | Show only the `detail` string; drop the `HTTP {code}:` + braces wrapper | **FIXED this run** (T6/P1d, `frontend/src/lib/api.js`) |
| `ShipConfirmModal` shipping-phase button | "Run in background" | Implies the app keeps tracking/will notify — it actually just stops polling; only the server task itself survives | "Close (task keeps running)" | **FIXED this run** (T6/P1b) |
| orchestrator/loop_engine system prompts | (identity) | Prior audits found inconsistent "AUREM" vs "ORA" self-reference | Shared `OR_IDENTITY` constant, single voice | **Already fixed (Round-2/N1)** — guardrail tests green; actual model wording is `NEEDS REAL-MODEL RE-TEST` (MOCK_LLM=true this pod) |
| K9 — stray "via ORA" at bubble end | (model output) | Fragment reads like a cut-off sentence | n/a — needs real-model observation | `NEEDS REAL-MODEL RE-TEST` |
| K2/K3/K4/K5/K6/K7 (see K-table below) | (model output) | Conversational-quality gaps (filler replies, over-explaining, confidence-gating contradictions) | n/a — needs real-model observation | `NEEDS REAL-MODEL RE-TEST` |

**Root prompt check** (source-verified, not model-output-verified):
`services/identity.py::OR_IDENTITY` is imported by BOTH
`orchestrator.py` and `loop_engine.py` — one assistant name, one voice
source of truth exists at the prompt layer. Whether the *rendered*
text is plain, direct English with a concrete next action can only be
confirmed with a real model (this pod can't).

## Part E — Ranked issue table

| ID | Surface | Wrong | Severity | Evidence | Mock-or-real | Effort |
|---|---|---|---|---|---|---|
| E1 | `api.js` streaming error path | Raw `HTTP {status}: {json}` wrapper shown even when a themed panel already renders the same info below it | P1 | `frontend/src/lib/api.js` (pre-fix); live-caught by testing_agent iter386 on a revoked-access project | Real (live-caught) | S — **FIXED this run** |
| E2 | `ShipConfirmModal` | "Run in background" over-promised tracking that doesn't happen | P2 | `frontend/src/components/ShipConfirmModal.jsx:526` (pre-fix) | Real | S — **FIXED this run** |
| E3 | `FixProgressDrawer` close icon | Ambiguous cancel-vs-hide semantics | P2 | Already fixed pre-overnight (Iter 212m-148); this run added a matching `title` tooltip on the legacy X icon | Real | XS — **FIXED this run** |
| E4 | `Projects.jsx` / `Integrations.jsx` | Native `window.confirm()` on non-ship-flow user actions (remove project, reveal API key) | P2 | Lines 1758 / 427 (pre-fix) | Real | S — **FIXED this run** (new `ConfirmModal.jsx`) |
| E5 | `/ora-chat/pin-login` | Lockout was per-IP only — attacker rotating IPs had unlimited attempts against the one shared PIN | P1 | `routers/ora_chat.py::pin_login` (pre-fix) | Real | S — **FIXED this run**, live E2E-proved (5 IPs → 429 on 6th new IP) |
| E6 | Day-1 onboarding | Fresh signup can't connect a repo without completing external GitHub OAuth first — zero product value seen pre-OAuth | P1 | Testing-agent finding, iter386; ROADMAP F16 | Real | M — parked (F16), founder design call needed |
| E7 | `funnel-repro` GitHub App installation | `get_repo_token_or_error` returns `app_installation_missing` for installation 152797252 despite an `active:true` Mongo row | P1 | This run's T7-live-drill attempt (see T7 status below) | Real | Needs founder — re-install the App or refresh the fixture |
| E8 | `/wall` | Opt-in, sanitized before render | — (not a leak) | Prior audit, unchanged this run | Real | n/a |
| E9 | K1 ship-CTA silent fallback | Was: prose describes a ship button that never renders | P0 (fixed) | Round-2 P0-1; guardrail + unit tests green; real live-fence-pass render `NEEDS REAL-MODEL RE-TEST` (mock fence can't be organically triggered) | Mixed | Done, pending real-model confirmation |
| E10 | Session delete (both surfaces) | Was: one-click hard-delete, no confirm | P0 (fixed) | Round-2 P0-2; live-confirmed both SessionSwitcher + Shell legacy sidebar (iter385, re-confirmed this run's regression pass) | Real | Done |
| E11 | Countdown 0:00 | Was: stale action buttons stayed clickable past expiry | P0 (fixed) | Round-2 P0-3; unit-tested, live trigger not organically reproduced this run | Real | Done |
| E12 | Personal Track | Hidden from Settings, routes/pages intact | — (as-designed, founder ruling) | Round-2 P0-4 | Real | n/a |
| E13 | OraDirect (`/ora`) | Single shared PIN, per-IP lockout only | P1 (partially fixed) | This run added per-account lockout (E5); per-user PIN still needs schema+prod migration | Real | Per-account done; per-user PIN = DECISION NEEDED |

## Part F — Should-be canon (proposal, founder rules)

- **One "approve a change" pattern** — for the upcoming Wave-2 PR flow,
  present a ship as: *"ORA opened a change on your repo — approve it
  on GitHub like you'd approve a doc, or Approve the fix here and
  ORA merges it for you."* Ship/rollback confirms across all 3
  surfaces already share one verb ("Approve the fix"); the 3
  components themselves stay separate for now (parked — ROADMAP F17).
- **One canonical status set (≤5 words)**: `missing → ready → PR open →
  merged → live → skipped → error`. All 3 ship surfaces + the new PR
  flow should map their internal states onto exactly these 7 words,
  nothing else.
- **Notification center** — proposed, not built this run (out of T1-T8
  scope): a persistent-until-acted-on list for errors + a home for
  "Kit: Live ✅"-style status once the Kit engine exists (it doesn't
  yet — T2 confirmed zero admin surface, zero backend model).
- **"What happens in GitHub" mini-guide** — proposed 3 steps: *(1) ORA
  opens a Pull Request — a proposed change, nothing is live yet. (2)
  You review the diff on GitHub (or trust ORA and click Approve here).
  (3) Merging it makes it live on your branch.* This is now wired as a
  first-ship-only permanent asset in the T7 build (flag-gated,
  Preview-only).
- **First-10-minutes ideal** — sign up → connect a repo → see a real
  finding/fix in <2 minutes → ship it. Current reality: OAuth popup
  before any value is shown (E6/F16) is the biggest blocker to this
  ideal; not fixed this run (founder design call needed).
- Part F does **not** recommend Personal Track activation — it stays
  hidden/as-designed per founder ruling (E12).

## K1–K10 status table (canonical IDs, live pass this run)

| K# | Status | Evidence |
|---|---|---|
| K1 | Fixed (code+test); live real-fence-pass render `NEEDS REAL-MODEL RE-TEST` | `MessageBubble.k1_ship_fallback.test.jsx` 4/4 pass |
| K2 | `NEEDS REAL-MODEL RE-TEST` | requires observing model's actual complaint-handling reply |
| K3 | `NEEDS REAL-MODEL RE-TEST` | same |
| K4 | `NEEDS REAL-MODEL RE-TEST` | same |
| K5 | `NEEDS REAL-MODEL RE-TEST` | same |
| K6 | `NEEDS REAL-MODEL RE-TEST` | same |
| K7 | `NEEDS REAL-MODEL RE-TEST` | same |
| K8 | **Code-verified**: `ChatPanel.jsx` routes a brand-new user's first message through the LEGACY `/chat/*` engine (`routers/chat.py`), NOT `ora_chat_v2` | testing_agent iter386 source read |
| K9 | `NEEDS REAL-MODEL RE-TEST` | same |
| K10 | Not observed this pass (no long-content first message triggered) | — |

## T7 status note (referenced by Part E7)

The Wave-2 ship-via-PR **build** (branch/PR/label/webhook-dispatch/
revert plumbing) is done and unit/guardrail-tested (12/12 pass,
`test_overnight_t7_ship_via_pr.py`). The `ship_via_pr` feature flag is
now live **ON** in this Preview environment (verified via
`POST/GET /admin/feature-flags`). The **live drill** (open a real PR
end-to-end) is `CREDENTIALS-PENDING`: minting a real GitHub App
installation token for the pre-seeded fixture (installation 152797252,
`polarisbuiltinc-wq/ora-grounding`) fails with `app_installation_missing`
even though its Mongo row says `active: true` — the underlying GitHub
App installation is not actually reachable from this pod. See T8 report
`DECISION NEEDED` section for the exact founder action required.
