# REPORT — Loop 2 (T2-T5), 2026-08-30

Founder's explicit GO chain: **H3 → B1-extend → W0-residue(GitHub) → T2
→ T3 → T4 → T5 (+5+5 inside T5's validation)**. Checkpoint +
PROOF-LEDGER after each step (see `/app/memory/PROOF-LEDGER.md`,
2026-08-30 entries). All of the below is **agent-tested, NOT
founder-confirmed** unless explicitly marked otherwise.

---

## §1 — H3 → B1-extend → W0-residue chain

**H3 (loop repo pin-and-assert-before-write)**: `services/loop_engine.py::confirm_ship`
and `routers/cto_projects.py::_run_task_via_api` both now pin
`{owner, repo, branch, installation_id}` at ship-stage time and
re-assert the LIVE binding matches immediately before the real GitHub
write, aborting with an explicit user-visible error on mismatch (never
silently re-targeting). Tests: `tests/test_h3_loop_repo_pin_2026_08_30.py`
(3/3), `tests/test_h3_b1_direct_task_pin_2026_08_30.py` (3/3).
`R9-PROD-FLIP-CHECKLIST.md` line 5 marked SATISFIED.

**B1-extend**: `repo_status.invalidate()` now also fires on the direct
task-submit ship path (was loop-pipeline-only from the prior round).

**W0-residue (GitHub read-only forensics)**: `RerootsBeauty/ReRoots-`
confirmed real, active, distinct GitHub App installation (`155986962`)
— NOT a founder fixture (0 hits scanning all 182 collections in this
pod's DB for the installation ID or account login). Zero residue: last
commit 8 days before the incident, no `auremcto/*` branch, no match
for loop IDs `89215749`/`9feafc45`. **W0 CLOSED**: real-user repo,
read-only throughout, no cleanup performed or needed, root cause fixed
via H1 (prior round) + H3 (this round), notification to the affected
user is the founder's own call. Full detail:
`/app/e2e-proof/W0-residue/W0_RESIDUE_SUMMARY.md`.

**Broader test result at this checkpoint**: 41 passed, 16 warnings
(H3/B1 focused + related loop suite).

---

## §2 — W1–W4 / X1 recap (prior round, unchanged this round)

- **X1** (session-wide mock-mode incident): root-caused —
  `services/llm/_meta.py`'s orchestrator/loop/council gateway had zero
  `MOCK_LLM` awareness before the prior round's fix; `is_mock()` now
  cached once at boot (not re-read per-call). Fixed: F1 (boot-cached
  mock flag), F2 (mock gate on the whole LLM gateway), F3 (`Live Model
  Mode` admin tile + durable `mock_detected_in_live` trust event).
- **W1** (cross-project silent-switch root cause): `ProjectSwitcher.jsx`
  conflated "unreachable" (transient) with real "disconnected",
  silently switching the active project. Fixed via H1 (removed the
  silent switch; a real revocation now only shows a non-navigating
  toast).
- **W2/W3**: mock/real consistency extended across chat_stream,
  `call_llm_with_meta`, and loop-ship (belt-and-suspenders refuse-guard
  before any real GitHub call while `MOCK_LLM=true`).
- **W4 (battery restart conditions)**: was NOT MET at the end of the
  prior round (H3 not done). **NOW MET this round** — H3 green (§1),
  W0-residue data-verified (§1), fresh session, context-pinning applied
  per test (§6). This is what unblocked T2-T5 + the 5+5 battery.

Full detail: `/app/memory/REPORT-x1-crossproject.md`.

---

## §3 — T2 (rollback-on-merged-PR fix) proof

Full detail: `/app/e2e-proof/T2/T2_SUMMARY.md`.

Closed 3 of R10's 4 documented gaps:
1. **SHA truth** — `get_pr_status()` now returns the real
   `merge_commit_sha` (was fetched but discarded); the merge webhook
   self-heals `loop_sessions.context.commit.sha/full_sha` to it.
2. **No false success** — an unconfirmed PR-lookup (`ok: false`) now
   returns an honest `rollback_status: "failed"` immediately, never
   silently falling into `close_and_retract()`.
3. **Squash/rebase-safe revert** — the merged-PR rollback path now
   reverts the REAL landed `merge_commit_sha`, not the stale pre-merge
   throwaway-branch SHA.
4. **New**: a bounded `verify_branch_head` poll (10×6s≈60s) confirms a
   revert push actually landed before reporting "done"; a timeout
   reports `rollback_status: "failed"` + `rollback_candidate_sha` + a
   new `ship_rollback_failed` trust event — never a false "done". Retry
   is gated by `force: true` when there's an unconfirmed candidate
   (avoids a blind duplicate revert).

**Explicitly NOT done**: ship-branch drift detection (R10 gap #4) —
outside this round's literal scope, flagged open in
`R10-ROLLBACK-PR-GAP.md`.

**Tests**: 17/17 pass across `test_t2_rollback_pr_gap_hardening_2026_08_30.py`
(7 new, all founder-named IDs present) + 2 updated pre-existing files.

**Live drill (real GitHub, not mocked)**: `TJSNDHU/Aurem` (installation
`157161705` — `ora-grounding`'s installation `152797252` is currently
unreachable, `app_installation_missing`, a pre-existing infra gap, not
caused by T2). Real commit `a317362b...` → `verify_branch_head`
confirmed → real revert `4cb2c0bc...` → `verify_branch_head` confirmed
→ zero orphan branches → repo left clean.

**Regression**: 667 passed / 22 pre-existing (baseline+git-stash-A/B
confirmed) / 0 new, on `pytest -k "loop or rollback or ship"`.

`ship_via_pr` remains Preview-only / prod flag OFF — T2 makes the path
safer for whenever it's flipped, does not flip it.

---

## §4 — T3 (First-Experience Wave) journey

Full detail: `/app/e2e-proof/T3/T3_SUMMARY.md`.

**B4 real-model window** (main agent, founder-authorized $3 cap):
flip OFF `2026-08-29T16:07:22Z` → flip ON `2026-08-29T16:10:43Z`
(~3m21s). Spend: `$5.226704` → `$5.265372` (**$0.038668**, 1.3% of
cap). Post-restore: 3 more real-call attempts, spend unchanged —
mock gate holds. **2 new P1 findings**: (1) casual-tier first message
can give a factually wrong product description ("audio data" instead
of the actual repo-connected coding assistant); (2) an agentic-tier
follow-up can return a verbatim-repeated answer from an earlier,
unrelated turn (context-anchoring). Neither fixed this round (prompt-
engineering scope, out of T2-T5's literal ask) — carried to §9/backlog.

**Journey verification** (`testing_agent`, `MOCK_LLM=true`): **12/12
required flows PASS** — signup + empty-state, admin login + existing
project, Preview/Code/Deploy tabs, chat casual/agentic/ship-intent (all
honest mock, ship correctly refused, zero real GitHub writes), bell,
ProjectSwitcher no-silent-auto-switch (W1/H1 regression-checked),
AdminSystemHealth. Zero crashes, zero fake-success paths.

**Top 3 bounce moments** (advisory/design, not code bugs):
1. Returning-admin dashboard stacks 4 warning banners/badges on first
   paint — reads as "broken," not normal.
2. ORA-GUIDE tooltip copy ("Connect GitHub") mismatches the actual
   button label ("Connect repo →").
3. "SEND TO ORA →" console-error badge visually outcompetes the real
   chat-send button in the composer.

---

## §5 — T4 (deployed-build verification) result

Full detail: `/app/e2e-proof/T4/T4_SUMMARY.md`.

- Production `/version` + `/health`: `commit_sha`/`build_hash`
  `f1c73be8a706`, matches this pod's git HEAD exactly. **Zero drift.**
  This round's H3/B1/T2 changes are intentionally uncommitted/Preview-
  only and correctly not yet reflected in production.
- Landing page: loads cleanly (screenshot captured), no fatal console
  errors.
- Authenticated screen: **NOT performed** — honest limitation, no safe
  non-founder production credential available; not fabricated.
- S-surfaces: confirmed present in the deployed commit via source
  check + production's own live `/health` supervised-tasks list.

---

## §6 — 5+5 battery results (context-pinning per test)

Script: `/app/e2e-proof/T5/battery/run_battery.py`. Result:
`/app/e2e-proof/T5/battery/battery_result.json`. `MOCK_LLM=true`
throughout — zero real spend, zero real GitHub writes.

**5 chat prompts** (varied intent tiers: casual / complaint / ship-intent
/ long-explain / close-out), all pinned to `p_6d0be78cdd`
(`polarisbuiltinc-wq/ora-grounding`): **5/5 responses correctly report
the pinned repo** (`repo_owner`/`repo_name` fields match on every call).

**5 loop-mode starts** on the same pinned project (each polled via
`GET /loop/{id}/status` and explicitly rejected via `confirm
approved:false` before the next iteration, to respect the one-active-
loop-per-project lock — this rejection is itself a real, working
control path, not a workaround): **5/5 confirm `project_id ==
p_6d0be78cdd`** in their live status doc. Zero cross-project drift
across all 10 calls.

**10/10 pinned correctly.**

---

## §7 — Full proof ledger

See `/app/memory/PROOF-LEDGER.md` (chronological, append-only). This
round's entries (2026-08-30, `# H3 + B1-EXTEND + W0-RESIDUE(GitHub)
ROUND` section onward): H3, B1-extend, W0-residue ×2, T2, T3. This
report is the T5 checkpoint entry.

---

## §8 — Regression comparison

| Scope | Result |
|---|---|
| H3/B1 focused + related loop suite | 41 passed, 16 warnings |
| T2 dedicated tests | 17/17 pass |
| Targeted `-k "loop or rollback or ship"` | 667 passed, 22 pre-existing (baseline+stash-A/B confirmed), 0 new |
| 5+5 battery | 10/10 correctly pinned |

No full 6500+-test sweep was run this round (impractical wall-clock
cost observed in prior rounds, ~2+ min and still <10% through); the
targeted sweep directly covers every file this round touched
(`loop.py`, `loop_safety.py`, `loop_rollback.py`, `github_api_writer.py`,
`trust_surface_events.py`, `cto_projects.py`) and is the same discipline
prior rounds used. `MOCK_LLM` confirmed `true` in `.env` at the start
and end of this entire round (briefly `false` only during the
explicitly-authorized, logged, restored T3/B4 window, §4).

---

## §9 — No-silent-fail audit

- Rollback: an unconfirmed PR-merge lookup or a verify-timeout now
  ALWAYS reports `rollback_status: "failed"` with an explicit,
  actionable reason — never a false "done" (§3).
- Loop/direct-ship: a live-binding mismatch at write time aborts with
  an explicit user-visible error (H3, §1) — never a silent re-target.
- Mock mode: loop-ship makes zero real GitHub calls when
  `MOCK_LLM=true` (belt-and-suspenders refuse-guard, §2/§6) — never a
  fake success.
- T3's 2 new model-quality findings (wrong product description;
  context-anchored repeat) are reported here, not silently dropped —
  carried to backlog, not fixed this round (see §4, out of literal
  scope).
- T4's authenticated-screen gap is reported as a genuine limitation
  (no safe credential), not glossed over.
- R10 gap #4 (ship-branch drift detection) remains explicitly open —
  not claimed as done.

---

## §10 — Flags/state and R9 readiness

- `MOCK_LLM=true` (confirmed, `.env`, both before and after this round).
- `ship_via_pr`: Preview-only, prod flag OFF. Unchanged this round.
- **`R9-PROD-FLIP-CHECKLIST.md` status**: item 5 (H3) SATISFIED. Item 4
  (R1a/rollback-on-PR) now PARTIALLY SATISFIED (3/4 gaps closed via T2;
  drift-detection gap #4 still open — do not flip until it lands or the
  founder explicitly accepts the residual risk). Items 1-3 (R5e webhook
  delivery, R8 real-model N≥5/rate-cap re-test, 48h warn-window review)
  **unchanged from prior rounds — still not met.**
- **R9 verdict: NOT READY TO FLIP.** 2 of 5 stop-gate items still open
  (webhook delivery + R8's full acceptance numbers), independent of
  this round's work.

---

**STOPPING here per founder's own explicit "On T5: STOP + full
report." Awaiting founder review before PART B (V1 — server-side
browser deploy-verify) begins, per the founder's own explicit
sequencing rule (PART A fully first, V1 starts clean afterward).**
