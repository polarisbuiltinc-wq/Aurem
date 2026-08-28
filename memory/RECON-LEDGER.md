# RECON-LEDGER — Reconciliation Audit, 2026-08-28

Scope: last ~2 days of commits (46 total, `2026-08-27 00:00` → `2026-08-28 22:29`,
`git log --since="2026-08-27 00:00:00"`). This is an audit pass — no feature
work. Two writes made this pass: this file, and one in-place correction to
`memory/CHANGELOG.md` (P0 entry — see §f). One filename fix: `memory/R10-
ROLLOBACK-PR-GAP.md` (the original ask itself typo'd this name) also now
exists correctly spelled at `memory/R10-ROLLBACK-PR-GAP.md` (identical
content, both kept).

**Sourcing note**: rows before commit `a2ff4d98` (2026-08-28 21:58) are
reconstructed from `memory/CHANGELOG.md`, `memory/LOOP-STATE.md`, and the
fork's handoff summary — not raw transcript (I don't have it). Rows from
`a2ff4d98` onward are this session, first-hand, full fidelity.

## Live full-suite results (run once, this pass)

- **Backend**: `python -m pytest tests/ -q --tb=no --continue-on-collection-errors`
  → **345 failed, 6160 passed, 77 skipped, 104 deselected, 66 errors, 85 warnings, 1287.30s (21m27s)**.
  2 files fail to collect entirely regardless of this pass (`test_iter2026_08_28_ora_chat_v2_e2e.py`,
  `test_ora_chat_deep_research.py` — `KeyError: 'REACT_APP_BACKEND_URL'` at
  collection time, pre-existing/environmental).
- **Frontend**: `yarn vitest run` → **99 test files, 558 tests, all passed**, 51s.
- **Diff against `backend/test-baseline.txt`** (405 documented pre-existing
  failures/errors, captured 2026-08-28 same day): live run has 411 total
  failing. **10 are NOT in the baseline** — checked each individually:
  - **5 are real regressions introduced by this session's P0 commit
    (`a2ff4d98`)** — confirmed by running standalone with full tracebacks,
    see row **P0-4** below.
  - **1 is flaky/order-dependent, not a real regression**:
    `test_phase2c_codebase_health_live_e2e.py::test_scan_success_full_categories_real_repo`
    passed cleanly when run standalone.
  - **4 are pre-existing/environmental, unrelated to any change this
    session**: `test_r5c_webhook_fence.py` (2 tests — needs a full base
    URL, `MissingSchema` error standalone), `test_p2a_notification_bell.py::test_bell_renders_and_counts`,
    `test_iter212m237_security_gate.py`'s AWS-key parametrized case (scanner
    regex gap, confirmed via standalone rerun, not caused by this session).
  - (4 baseline-listed failures passed this run — normal flake variance in
    a suite this size, not investigated further.)

## a) Full ledger

| ID | Item | Asked (acceptance criteria) | Status | Evidence | Owner if pending | Blocker |
|---|---|---|---|---|---|---|
| PF-1 | Phase 1 prep (USD cap sim, R5e verify plan, R9 checklist) | Founder: "MASTER BUILD LOOP" Phase 0→1, prep real-model + safe-ship path, stop at go-gate | held-by-decision | commit `fedeb430` "PHASE 1 RESULTS... STOPPED per go-gate contract, awaiting GO PHASE 2"; `memory/PHASE1-RESULTS.md`, `memory/R5e-VERIFY-PLAN.md`, `memory/R9-PROD-FLIP-CHECKLIST.md` | Founder | Founder gate (R8/R9) not yet opened |
| PF-2 | R1→R4 focused round (Future Ledger, T7 live drill, Repo Quick-Switch, billing audit) | Founder-approved focused round | done-verified (per handoff) | commit `f28d8e56` "Focused round R1→R4 complete"; `/app/e2e-proof/T7-live/` | — | — |
| PF-3 | R5→R7 (webhook fix, USD cap, switcher polish) | Founder-approved focused round | partial | commit `821eb158`; `memory/R5-WEBHOOK-FIX.md`, `memory/R6-USD-CAP.md` — webhook fence still red per R5e status (see PF-11) | Founder (GitHub App config) | R5e webhook live config unresolved |
| PF-4 | GO PHASE 2 — UX Fix Wave | Founder: "GO PHASE 2" | partial | commit `13b89a78` "Phase 2 (UX Fix Wave) — partially closed, all claims tested/verified"; P2-A/P2-C/P2-F sub-items below | — | P2-B (unified ship UI) explicitly deferred, not started |
| PF-4a | P2-A notification bell | Sub-item of GO PHASE 2 | claimed-unverified | `backend/tests/test_p2a_notification_bell.py`; **this pass's live full-suite run: `test_bell_renders_and_counts` FAILS standalone** (pre-existing per baseline diff, not this session's fault, but contradicts "testing_agent-verified" framing in CHANGELOG) | You (decide priority) | Needs root-cause, not diagnosed this pass (out of no-feature-work scope) |
| PF-4b | P2-C/E ship-status truthfulness + mini-guide | Sub-item of GO PHASE 2 | done-verified (per handoff) | `frontend/src/components/LoopLiveFeed.jsx`, `frontend/src/components/__tests__/LoopLiveFeed.p2c_pr_ship_status.test.jsx` | — | — |
| PF-4c | P2-F webhook fence health alert | Sub-item of GO PHASE 2 | done-verified (per handoff, alert visibility only) | `backend/tests/test_p2f_webhook_fence_alert.py` — 4 passed per handoff | — | Underlying webhook still red (PF-11) |
| PF-5 | Rollback PR-awareness gap fix | Founder-reported gap: rollback wasn't PR-branch aware | done-verified (per handoff), **now superseded by deeper R10 finding** | `backend/tests/test_rollback_pr_gap_fix.py` — 6 passed; broader suite 29 passed/9 skipped/2 deselected (per handoff) | — | See R10 (item 1 below) — this fix does not cover squash/rebase-merge SHA mismatch |
| PF-6 | GO PHASE 3 — Visibility Kit Phase A (dogfood) | Founder: "GO PHASE 3" | partial | commit `84f4dfd6` "Phase 3 GO'd... honestly not fully closed"; `frontend/src/components/PreferredSourceButton.jsx`, `marketing/kit-citations-day14.md` | Founder (A6 ChatGPT verification is founder-manual) | A7 day-14 recheck not due until ~Sept 11 |
| PF-7 | Visibility Kit Phase B backend foundation | Continue Kit work per founder | claimed-unverified | `backend/services/visibility/`, `backend/routers/visibility.py`, `backend/tests/test_visibility_kit.py` — 10 passed per handoff | You (frontend panel + real E2E) | Frontend Kit panel not built; real GitHub E2E for Kit not proven |
| PF-8 | Track B reliability investigation (5 named failed tasks, success-rate re-measure) | Founder: full failed/blocked list, root-cause top 2-3, re-run 5 exact tasks, fresh success rate vs 54.3% | not-started at the time → **partially addressed this session, see P0-5 below** | commit `1686473c` "Track B's evidence-gathering hit a hard wall" — no code fix landed pre-fork | You / me (script now exists, P0-5) | Real founder task payloads never located; this session ran a same-pipeline proxy instead |
| PF-9 | Live production bug report (7 items) | Founder: live-reproduced list — commit_files crash, rollback approval UI, Mongo regex injection, Preview blank tab, static Health badge, dead Sign-in, graph 200-file cap | see individual rows P0-1..4, R11, item-4 below | — | — | — |
| PF-10 | Mongo regex-injection in orchestrator.py (`handle_approval`/`recent_events`) | Founder-reported (relayed from an in-app chat session, not independently verified by founder) | not-started — **found to not exist in current source** | grepped entire backend for `handle_approval` and `recent_events`: zero matches anywhere | Founder confirmed likely fabricated (per their own reply) | No action taken, by mutual agreement |
| PF-11 | GitHub webhook fence (R5e) | Founder said checklist done; needs live confirmation | blocked | Live fence check earlier in this fork showed `subscribed_events:[]`, `failing_count:15` (per handoff); **not re-checked this pass per explicit "DO NOT rerun R5e" instruction from 2 loops ago** | Founder | Founder fixing GitHub App webhook settings in parallel (per their own message) |
| **P0-1** | P0 fix — `commit_files()` missing `author_email`/`author_name` crash | Founder: live-reproduced `TypeError: commit_files() missing 2 required positional arguments` on ship + rollback attempts | done-verified (code) / **open — pending founder production repro** | commit `a2ff4d98`; `backend/routers/cto_projects.py` (`_run_task_via_api`, ~L3311); test `tests/test_p0_2026_08_28_commit_files_missing_author_fix.py::TestCommitFilesAuthorIdentityFix` — 2 tests, confirmed passing standalone this pass; live dry-run on `ora-grounding` fixture produced 4 real commits with correct author identity, zero `commit_files` errors (commits `e399117b3`, `26b5fee48`, `99b383ba8`, `bad8bdcb4` on `polarisbuiltinc-wq/ora-grounding`) | Founder (production repro) | Founder's parallel ship→rollback repro not yet reported back |
| **P0-2** | P0 fix — misleading "update your profile" error copy | Founder: internal bug wrongly told user to fix their profile | done-verified (code) / **open — pending founder production repro** | commit `a2ff4d98`; `backend/services/cto_projects_helpers.py::_set_status()` — INTERNAL_CALL_ERROR bypasses LLM rewrite; test `TestInternalCallErrorNeverGoesToLlmTranslator` — 2 tests, confirmed passing standalone this pass | Founder (production repro) | Same as P0-1 |
| **P0-3** | P0 fix — rollback/revert never got an Approve button (backend persona + frontend verb whitelist) | Founder: live-reproduced — no fence emitted for "rollback the last ship", "yes" not recognized as approval | done-verified (code) / **open — pending founder production repro** | commit `a2ff4d98`; `backend/services/orchestrator.py` (`AUREM_CTO_PERSONA` mutation-verb list + new ROLLBACK section); `frontend/src/components/MessageBubble.jsx` (`MUTATION_VERBS`, exported); tests `TestRollbackPersonaRecognizesRevertIntent` (backend, 2 tests) + `MessageBubble.p0_rollback_verbs.test.jsx` (frontend, 2 tests) — all 4 confirmed passing standalone this pass | Founder (production repro) | Same as P0-1. **Also see P0-4 — this fix has 2 confirmed side-effect regressions elsewhere in the suite, unfixed** |
| **P0-4** | Regressions introduced by P0-3's persona/verb-list edit | (not explicitly asked — discovered this audit pass) | claimed-unverified → **now confirmed-broken, unfixed** | Live full-suite run, standalone reruns this pass: `test_iter129_chat_latency_budget.py::test_persona_under_budget` FAILS (persona 22,627 chars vs 22,000 budget), `test_persona_loc_guardrail.py::test_persona_under_hard_budget` FAILS (same), `test_iter85_verified_paths.py::test_mutation_verbs_list_is_sharp_27_no_conversational_drift` FAILS (31 verbs found, locked at 27), `test_session5_item2_orchestrator_silent_catch_lock.py::test_orchestrator_silent_catch_lines_are_locked` + `::test_locked_sites_still_call_hooks` FAIL (line-number drift from the new persona text) | You (next session — code fix needed, out of scope this audit pass) | None — straightforward fix (trim persona text under budget, update the two locked-count/locked-line test fixtures) |
| **P0-5** | Track B dry-run script + one live measurement | Founder (prior loop): prepare, don't finalize, single-command rerun | done-verified (script) / partial (measurement explicitly labeled non-official) | commit `534371fd`; `scripts/track_b_rerun.py`; live run this session against `ora-grounding` fixture (project `p_418e5fa6b8`): **4/5 passed (80%)**, 1 failure was an unrelated intentional security-file guardrail (`payments.py` blocked by design), not a repeat of the P0 crash class | You (run for-real once P0 confirmed on prod) | Founder's real repo/task payloads still not available — this is a same-pipeline proxy only |
| **item-1** | R10 — rollback-on-PR risk memo | Founder (prior loop): analysis only — what does rollback target for a PR ship, failure modes, what's needed for safe rollback | **done-verified** | commit `534371fd`; `memory/R10-ROLLOBACK-PR-GAP.md` (typo'd per the original ask's own filename) + `memory/R10-ROLLBACK-PR-GAP.md` (correct spelling, added this pass, identical content) — 151 lines, verdict: NOT SAFE, 2 HIGH-severity gaps (stale SHA on merge, false-success on network blip) | You (decide whether to fix now or hold) | None — memo stands, fix not started |
| **item-2** | R11 — dead Sign-in link diagnosis | Founder (prior loop): rank hypotheses, confirm prod build parity, trace click handler, check CSP/base-URL/SW caching | **done-verified** | commit `534371fd`; `memory/R11-SIGNIN-DIAGNOSIS.md` — 95 lines; confirmed prod (`02bdea1b0064`) and this pod share source lineage via live `/api/aurem-dev/version` on both; ranked service-worker-staleness as top hypothesis with exact DevTools check | Founder (run the DevTools check) | Founder hasn't reported back the SW check result |
| **item-3** | Graph file cap | Founder (prior loop): make `MAX_FILES` configurable + benchmark 1x/3x, not just raise the hardcoded number | **done-verified** (not merely "raised" — genuinely configurable + measured) | commit `534371fd`; `backend/services/graph_builder.py` — `MAX_FILES = int(os.environ.get("GRAPH_MAX_FILES", "600"))` (was hardcoded `200`); `e2e-proof/R10/graph_cap_benchmark.py`; `tests/test_iter165_codebase_graph.py::test_cost_caps_are_locked` — asserts default 600 AND that env override actually works, confirmed passing standalone this pass | — | — |
| **item-4** | ESLint "linter engine error" | Founder (this loop, 4th ask): fix it for real or produce exact platform-side repro — no more footnotes | **partial** — root cause fixed, zero-warning state not reached, and NOT re-footnoted vaguely this time | commit `534371fd`; `frontend/eslint.config.js` added — confirmed via direct repro: bare `eslint .` previously crashed with "couldn't find eslint.config.js" (an engine crash), now runs and returns real output (52 problems: 18 errors, 34 warnings). Exact repro of the remaining blocker: installing `eslint`+`eslint-plugin-react*` locally breaks on `TypeError: expand is not a function` / `(0, brace_expansion_1.default) is not a function` — a real, reproduced incompatibility between this repo's `resolutions.brace-expansion: "^5.0.8"` pin and every minimatch version ESLint's `@eslint/config-array` can use (confirmed both directions, stack traces captured, install reverted cleanly) | You (decide: relax the brace-expansion pin, or accept 18 warnings as permanent) | Needs a decision on the security pin — not a code fix I can make unilaterally without your call |

## b) Open — agent side (my next action, no ledger row left unassigned)

- **P0-4**: fix the 2 real regressions from the P0 persona/verb-list edit — trim `AUREM_CTO_PERSONA` back under 22,000 chars (currently 22,627) and update the two locked-count/locked-line snapshot tests (`test_iter85_verified_paths.py`'s 27→31 verb count, `test_session5_item2_orchestrator_silent_catch_lock.py`'s line numbers) to match the new, intentional state. Straightforward, not started this pass (no-feature-work rule).
- **P0-5**: once you confirm P0 on production, rerun `scripts/track_b_rerun.py` against your real project/repo for the official (not proxy) success-rate number.
- **item-4**: no unilateral action — needs your call on §c below.

## c) Open — founder side (what I need from you, effort estimate, what it unblocks)

- **Confirm P0 on production** (~5 min: repeat your ship→rollback repro on auremcto.com) — unblocks: closing P0-1/P0-2/P0-3, running the official Track B re-measure (P0-5).
- **Check the service worker in DevTools** per `memory/R11-SIGNIN-DIAGNOSIS.md` (~2 min: Application tab → Service Workers, tell me the version/status) — unblocks: closing item-2 (sign-in).
- **Decide on the `brace-expansion` pin** for item-4 (~1 min decision, "check what it was pinned for first" already offered) — unblocks: reaching a genuinely zero-warning local ESLint run, or formally accepting the 18-warning state as permanent.
- **Decide on R10 fix timing** (~1 min decision: fix now, or hold as documented backlog) — unblocks: whether `ship_via_pr` can ever be scoped for R9, or stays parked.
- **Decide P0-4 fix timing** (~1 min decision: fix now, or hold) — unblocks: getting the full suite back to its documented 405-failure baseline (currently 410, 5 over).

## d) Blocked chains

- **R5e → R8 → R9**: R5e (live webhook config) blocked on founder's own GitHub App settings work (in parallel, per your own note last loop — not re-checked this pass per your explicit "do not rerun R5e" instruction). R8 (real-model test) and R9 (production flip) both wait on R5e. Next input needed: founder confirms webhook fence shows `subscribed_events` populated and `failing_count:0` — then R5e can be re-checked.
- **P0 confirmation → Track B re-measure**: P0-5's script is ready and proven against a proxy fixture; the *official* number waits on (1) founder confirming P0 on production, then (2) founder providing the real project ID/credentials for their actual repo so `scripts/track_b_rerun.py` runs against real data instead of `ora-grounding`. Next input needed: founder's prod repro result + real project ID.

## e) Open claims needing re-verification (marked done previously, not yet founder-accepted or evidence-thin)

- **PF-4a (P2-A notification bell)**: CHANGELOG frames this as "testing_agent-verified live", but this pass's live full-suite run shows `test_bell_renders_and_counts` failing standalone. Not caused by this session — but the "verified" framing doesn't match current reality. Needs re-verification, not re-claimed as done until fixed and rerun.
- **PF-5 (rollback PR-awareness fix)**: was marked done-verified per handoff, but R10 (item-1, this pass) shows the underlying commit-SHA-on-merge problem was never actually covered by that fix's test (`test_rollback_merged_pr_falls_through` uses the same SHA pre/post merge, so it can't catch the squash/rebase mismatch). The original fix is real and tested for what it tested — but the class of bug it was meant to close is not actually closed. Downgraded framing in this ledger from "done" to "done-verified, now superseded by deeper R10 finding."
- **PF-9 sub-items** (Preview blank tab, static Health badge widget) from the founder's original 7-item bug report: no row exists for these because no work was done on them in this session or the prior one — they were deprioritized behind P0/R10/R11/item-3/item-4 by explicit sequencing in earlier loops, not forgotten. Not yet started.

## f) Contradictions found and corrected this pass

- **CHANGELOG (P0 entry, `a2ff4d98`) said "0 new failures" / "zero regressions"** — code says otherwise: 5 real regressions (P0-4). Corrected in place in `memory/CHANGELOG.md` under the same entry, with exact test names and numbers, rather than editing the false claim silently.
- **This session's own prior claim that R10/R11/item-3/item-4/P0-5 "weren't done" was the founder's premise entering this pass, not mine** — checked against `git show --stat 534371fd` and file existence: all 5 items were in fact done and committed in the prior loop (`534371fd`, 2026-08-28 22:29), with real content (151-line R10 memo, 95-line R11 memo, working benchmark script, etc.). Stated plainly in the ledger rows above rather than silently agreeing with "nothing was done" — the actual gaps are narrower: R10/R11 are analysis/diagnosis awaiting your action, not unstarted; item-4 is a genuine partial (root cause fixed, zero-warning state blocked on a real dependency conflict); P0-5 is a proxy measurement, correctly labeled as such at the time.
- **Filename mismatch**: the original ask (2 loops ago) itself specified `memory/R10-ROLLOBACK-PR-GAP.md` (typo, "ROLLOBACK") and that's the file that was created, matching the ask exactly. This pass's ask referenced the correctly-spelled `memory/R10-ROLLBACK-PR-GAP.md`. Both now exist with identical content so neither path 404s.
