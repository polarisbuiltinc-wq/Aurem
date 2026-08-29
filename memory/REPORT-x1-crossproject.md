# REPORT — X1 (Mock-Mode Incident) + Cross-Project Data-Safety Round (W0-W4)
**Date:** 2026-08-30 · **Scope:** Preview only, no production flags/migrations, no new deps.

---

## 0. X1 — Session-Wide Mock-Mode Incident

### 0.1 Confirmed findings (evidence-based, not guessed)

**Finding A — MOCK_LLM only ever gated `chat.py`'s `chat_stream`, not the
loop/Council path.** Confirmed by direct source read: before this
round, `services/llm/_meta.py::call_llm_with_meta` — the ONE shared
gateway function every orchestrator call, every loop-plan call, and
every Council A/B/C member call goes through — had **zero** reference
to `MOCK_LLM` or `is_mock()` anywhere in the file. This fully explains
the reported "chat replied with the canned mock text while the loop
still made a real commit" pattern: `chat_stream`'s short-circuit
blocked the *chat reply* (and, by extension, blocked a loop from ever
being *dispatched* while mock was on), but any loop that WAS already
running, or started the instant mock flipped off, went straight
through `call_llm_with_meta` to a real provider with no gate at all.

**Finding B — `is_mock()` re-read `os.getenv` on every single call**,
so a bare `.env` edit + process restart (this pod runs exactly ONE
backend process for all traffic — there is no separate Preview/
Production process here) could change live-serving behaviour for
every in-flight and future request the instant the process came back
up, with zero warning, zero durable log trail, and zero operator-
visible signal beyond the chat bubble text itself.

**Finding C — this exact mechanism (Finding B) is directly evidenced
in this pod's own history**, independent of the founder's report:
`/app/backend/.env` was edited by the *previous agent session's* T1/R8
real-model-validation workstream — flipped `MOCK_LLM` `true → false`
to run real-model tests, then back `false → true` afterwards
(`.env` mtime `2026-08-29 04:47:50`, matching
`/app/e2e-proof/R8/*` proof-file timestamps spanning `04:31`–`04:48`).
That is a real, on-disk, timestamped example of exactly the failure
mode described: a **global, single-process config flip, made for
testing purposes, with no isolation from anything else using the same
running backend at the same time.**

**Finding D (honest limitation — NEEDS-FOUNDER, corroborated by prior
precedent):** this pod's MongoDB (`aurem_dev`) has **zero records**
for the project names in the founder's incident report
(`TJSNDHU/Aurem`, `RerootsBeauty/ReRoots-`) — see §1 (W0) below for the
full query proof. **This is not a new/uncertain gap** — `PRD.md`'s own
`2026-08-25` P0 entry ("raw Python error leak... ReRootsBeauty/
ReRoots-") independently documents the exact same standing fact for
the exact same org name: *"the reported Production project/task
aren't present in this Preview's local Mongo."* That confirms
`RerootsBeauty/ReRoots-` is very likely a **real production customer**
(the same one from that prior incident), and that this Preview pod's
database has **never** mirrored Production data — a known,
already-established architectural fact, not something newly
discovered tonight. That means **direct, data-level causal proof that
a specific past incident in this exact pod caused the founder's
specific reported incident is not obtainable from here** — the
founder's regression test necessarily ran against the real, separately
-hosted Production instance. What IS proven, with hard evidence, is
that the exact underlying **mechanism** (Findings A + B) exists in
this codebase and is fully capable of producing precisely the reported
symptom on whichever instance is running this same code.

### 0.2 The fix (F1 + F2 + F3, all shipped)

- **F1 — read-once-at-boot.** `services/ora_chat_v2/llm_client.py`:
  `is_mock()` now returns a module-level constant
  (`_MOCK_LLM_AT_BOOT`) captured exactly once at import time, logged
  at boot (`llm_client boot: MOCK_LLM=<bool> (read once, immutable for
  this process)`). A bare env mutation with no restart can no longer
  drift an already-running process's behaviour mid-flight — the ONLY
  way to change it is a full, deliberate, logged process restart.
  **Honest limitation, unresolved, flagged NEEDS-FOUNDER:** this pod
  genuinely runs one backend process for all traffic; true Preview-
  vs-Production *process* isolation is a deployment-topology decision
  (provisioning a second, separately-configured deployment), not
  something a code change alone can create. Recommend: any future
  real-model validation work should either (a) run against a
  dedicated, separately-deployed test instance, or (b) be scheduled
  with the founder's explicit awareness that it will briefly affect
  live traffic on this shared process.
- **F2 — mock-response protector, extended to the whole gateway.**
  `services/llm/_meta.py::call_llm_with_meta` now checks the same
  `is_mock()` FIRST, before any tracing/cost-cap/provider logic, and
  returns a `{"mock": True, "provider": "mock", ...}` response with
  zero real network calls. This is the fix for Finding A — loop
  planning and every Council member now inherit the same mock gate
  chat already had.
- **F3 — visible, durable signal.** Every mock resolution (both
  `chat_stream` and `call_llm_with_meta`) now fires a
  `mock_detected_in_live` event into the existing `trust_surface_events`
  collection (new event kind, reused collection, zero new
  infrastructure) plus a `logger.warning`. A brand-new, dedicated admin
  tile — **"Live Model Mode"** (`data-testid="card-live-model-mode"`,
  `GET /admin/live-model-mode`) — shows `REAL`/`MOCK` plus a 24h mock-
  served counter, sitting in `AdminSystemHealth.jsx` next to the
  Webhook Fence card.
  **Why a NEW tile, not the existing "100" Health Score widget:**
  `HealthScoreWidget.jsx` (`/admin/health-score`) measures **static
  code quality** (security/architecture/reliability from a code scan)
  — it has no knowledge of live LLM serving state and was never
  supposed to move during a mock window. The founder's observation
  that "the 100 badge didn't drop" is almost certainly this widget
  being correctly unrelated, not a detection failure in a signal that
  was ever meant to cover this. Conflating the two would have broken
  a working, useful signal to patch a gap that a new, small, honest
  tile now fills properly.
- **Belt-and-suspenders (W3, see §3):** `loop_engine.py`'s real-ship
  path now refuses outright (locked decision: default = refuse) if
  `is_mock()` is true, so even a mock-mode loop that somehow got this
  far makes literally zero real GitHub calls.

### 0.3 Tests (all green, isolated run — no order-dependency)
`/app/e2e-proof/X1/pytest_x1_w2_w3_all_green.log` — 56 passed, 0 failed:
`t_mock_isolation_preview_prod_read_once_at_import`,
`t_mock_response_sets_serving_flag`,
`t_mock_gate_covers_council_and_loop_path`,
`t_ship_refuses_on_mock_source_level`,
`t_mock_detect_alert_logged`,
`t_real_path_unchanged_when_mock_off`, plus the pre-existing
`test_iter212m18_glm_primary_claude_watchdog_sse_steps.py` (9),
`test_w2_step2_mock_short_circuit_chat_stream.py` (3, 2 updated for the
new caching design), `test_iter212m111_night_mode_focus_manual_ship.py`
and `test_iter212m177_prod_reliability.py` confirm-ship tests, all still
green.

### 0.4 Current live state (verified, this pod)
`MOCK_LLM=true` in `/app/backend/.env` — **unchanged from before this
round started**, confirmed by direct grep immediately before writing
this report. No production flag was touched. No real spend occurred
this round for X1 work itself (all new tests are unit-level, mocked
providers).

---

## 1. W0 — Forensics + Zero-Residue

### 1a. Target repo identity
`RerootsBeauty/ReRoots-` — **not found anywhere in this pod's
database.** Query: `db.cto_projects.find({github_owner: /reroot/i})` →
`[]`. `db.cto_projects.find({github_repo: /reroot/i})` → `[]`. Across
all 68 `cto_projects` documents and 183 total collections, there is no
record with this name in any field checked. See
`/app/e2e-proof/W0/db_forensics_query_output.json`.

`TJSNDHU/Aurem` — the account **"tjsandhu"** (lowercase) exists in
this pod's DB, bound to exactly 2 fixture projects
(`iter330-history-test-proj`, `iter330-harness-proj`, both
`user_id: test_admin_001`, both named after prior test-iteration work,
neither named "Aurem"). There is **no live `github_installations`
record** for `tjsandhu` in this pod (0 matches) — these are inert
fixture documents, not a connected, working installation.

**Cleanup authority (0b):** N/A — nothing to clean, because nothing
from this incident exists in this pod to begin with.

**Conclusion (headline, repeated from §0.1 Finding D):** this Preview
pod's database (`aurem_dev`) does not contain the projects referenced
in the founder's regression-test report — **confirming a known,
already-documented standing fact**, not a new gap: `PRD.md`'s
`2026-08-25` P0 entry independently records the identical situation
for the identical org name (`ReRootsBeauty/ReRoots-`), stating
"the reported Production project/task aren't present in this
Preview's local Mongo." This Preview pod has never mirrored Production
data. The founder's live incident happened on the real, separately-
hosted Production instance. **NEEDS-FOUNDER:** please pull the
Production logs/DB for the actual incident window (or grant this
agent read access to Production for forensics) so a future round can
verify the root cause against real data instead of inferring from
source code alone.

### 1c. Loop forensics for L1 `89215749` / L2 `9feafc45`
Searched every loop/task-related collection in this pod
(`loop_backups`, `loop_plans`, `loop_events`, `loop_sessions`,
`cto_tasks`, and 12 others — full list in the JSON proof) for either
ID as a `loop_id` or `_id` substring. **Zero matches anywhere.**
Consistent with §1a: this pod has no trace of the incident session at
all.

### 1d. Test-repo integrity (TJSNDHU/Aurem in THIS pod)
No live installation exists here to check GitHub-side commits/branches
against (confirmed 1a). The 2 fixture project *documents* that do
exist are untouched by this round's work — no writes were made to
them.

---

## 2. W1 — Root Cause With Evidence (cross-project silent switch)

**Confirmed via direct source read + reproducible unit test — not a
guess.** `frontend/src/components/dashboard/v2/ProjectSwitcher.jsx`
had a "login-landing guard" `useEffect` that automatically called
`onSelect(candidate.project_id)` — **with no user click, no
confirmation** — whenever the *currently active* project's polled
connection-status was in a set called `UNREACHABLE`, which was defined
as `new Set(["disconnected", "unreachable"])`.

The smoking gun: `backend/routers/repo_status.py` — the endpoint that
produces those exact status strings — has its OWN comment right next
to where it assigns `"unreachable"`, explicitly stating **"a network
failure is NOT a revocation."** The backend was deliberately designed
to distinguish a real revocation (`"disconnected"`) from a transient
timeout/network blip (`"unreachable"`). The frontend's auto-switch
effect ignored that distinction and treated both identically — so a
single transient network hiccup on the *correct* active project (for
example, exactly the kind of blip you'd expect during a burst of
concurrent GitHub API calls, like a rollback drill running at the same
time) was enough to silently redirect the user to a *different*
project in their list, with **zero action from them** — matching the
reported "active project context silently switched mid-run" and the
derived "Chats (20) → Chats (1)" symptom (chat history is scoped to
whichever project is active).

**Why "Chats (N) dropped" is not itself a separate bug:** confirmed it
derives directly from the active project (chat history is fetched per
`project_id`) — once the switch fires, the count legitimately reflects
the *new* project's own history. The switch is the bug; the count drop
is its correct, honest downstream symptom.

---

## 3. W2 — The Fix + Hardening

### H1 — Explicit-action-only project switch (DONE, tested)
Rewrote the guard effect: it now checks **only** `"disconnected"`
(never `"unreachable"`), and on a real disconnection it shows a
**non-navigating toast only** — the user must click the switcher
themselves. `onSelect` is never called automatically by this effect
again, for any reason.
Tests (`ProjectSwitcher.r3.test.jsx`, 6/6 pass, see
`/app/e2e-proof/W2/vitest_projectswitcher_h1.log`):
- `t_disconnected_active_project_shows_notice_no_auto_switch` — real
  revocation notifies, never switches.
- `t_unreachable_active_project_never_switches_or_notifies` — **the
  exact regression guard for the reported incident**: a transient
  status on the active project produces neither a switch nor a toast.
- `t_login_landing_noop_when_active_is_healthy`,
  `t_switch_repo_a_to_b`, `t_revoked_repo_non_selectable`,
  `t_r7_project_name_distinguishes_same_repo_projects` — all still
  pass (pre-existing behaviour unaffected).

### H2 — Loop state project-scoped (CONFIRMED already true by design, not newly built)
Read `services/loop_engine.py`: `project_id` is captured once as a
plain instance attribute at `LoopPipeline.__init__` and used from
`self.project_id` throughout that object's lifetime — it is never
re-read from any global "current active project" pointer mid-run. A
running loop was therefore already immune to a frontend switch
retargeting it *after* it started. **What this does NOT cover** (see
H3): if the user's active project was ALREADY wrong (because of the
W1 bug) at the moment they clicked "start", the loop faithfully — and
correctly, by its own logic — targets whatever `project_id` it was
given. H1 is the fix for that; H2 was already fine.

### H3 — Loop repo pinning (NOT DONE this round — honest gap, flagged P1)
The deeper defense-in-depth ask — capturing `{repo, branch,
installation_id}` at loop start and re-asserting the LIVE value
matches before every real write, aborting on mismatch — was **not
implemented**. Added as a new, explicit, NOT-yet-satisfied line to
`memory/R9-PROD-FLIP-CHECKLIST.md`'s stop-gate (now 5 required items,
was 4) so it cannot be missed before any production ship-via-PR flip
across multiple users/repos.

### H4 — Guardrail tests (PARTIAL)
Done: `t_disconnected_active_project_shows_notice_no_auto_switch` and
`t_unreachable_active_project_never_switches_or_notifies` (the two
tests above) ARE the cross-project-no-silent-switch guardrail for the
confirmed W1 bug. **Not done:** `t_loop_repos_pinned` (depends on H3,
not built), `t_breadcrumb_matches_active_project` (no dedicated
breadcrumb component found this round to test against). `t_chat_count_scoped`
— confirmed by design (chat history fetch is keyed by `project_id`
already, per H2's finding) but no new dedicated test was added for it
this round.

### B1 — "Not connected" banner stale-cache bug (DONE for the loop-ship path, PARTIAL overall)
`backend/routers/repo_status.py` gained an `invalidate(project_id)`
function that drops the short-TTL connection-status cache row for one
project. Called from `loop_engine.py` immediately after a successful
real commit (`SHIP RESULT` log line) — a landed commit is hard proof
the connection is fine, so the next poll re-checks instead of replaying
a stale "disconnected" reading for up to the old 8-second TTL.
Test: `test_t_commit_success_clears_not_connected` (passes, see
`/app/e2e-proof/X1/pytest_x1_w2_w3_all_green.log`… actually captured in
`test_b1_repo_status_invalidate.py`, run alongside it).
**Honest gap:** `routers/cto_projects.py`'s direct `/tasks/submit` →
`commit_files` path (used by the R8 drill, separate from
`loop_engine.py`'s pipeline) was **not** wired to call `invalidate()`
this round — flagged P1.

---

## 4. W3 — Mock/Real Consistency Across All LLM Paths

**Which paths were real during any test tonight, with the actual
cost:** per the T1 (prior round) cost ledger, real-model calls this
overnight window totalled **$0.131953** (well under the $3 cap) —
see `/app/e2e-proof/R8/T1_SUMMARY.md` (prior round's own accounting,
re-confirmed, not re-spent this round). This round's own X1/W2/W3 work
spent **$0** — all new tests use monkeypatched providers or the
already-mocked live backend (`MOCK_LLM=true` confirmed unchanged
before/after).

**Fix (locked decision: default = refuse-to-execute):**
1. `services/llm/_meta.py::call_llm_with_meta` — mock gate added (see
   §0.2 F2). Covers orchestrator + loop planning + Council A/B/C in
   ONE place, so no future caller of this shared gateway can bypass it.
2. `services/loop_engine.py` — the real-ship method now refuses
   outright when `is_mock()` is true, **before** minting a GitHub
   token, creating a branch, or calling `commit_files()` — zero real
   GitHub calls in mock mode, per the locked decision rule. User sees
   an explicit, honest failure message ("Mock mode is on... this loop
   refuses to write to your real repo...") rather than a silent no-op
   or a fake-looking success.
3. **Honest gap:** `routers/cto_projects.py`'s direct
   `/tasks/submit` execution path benefits from fix #1 (its content
   generation will be mock/placeholder, harmless) but was **not**
   given the same explicit "refuse before commit" guard as
   `loop_engine.py` — flagged P1, same file as the B1 gap above.

**Proof:** `test_t_mock_response_sets_serving_flag`,
`test_t_mock_gate_covers_council_and_loop_path`,
`test_t_ship_refuses_on_mock_source_level`,
`test_t_mock_detect_alert_logged`,
`test_t_real_path_unchanged_when_mock_off` — all pass, see §0.3.

---

## 5. W4 — Restart Conditions For The 5+5 Battery

**NOT MET — do not restart the battery yet.** Per the founder's own
gate: restarts only after (1) W0 clean/confirmed, (2) W2 ALL GREEN
(H1-H4), (3) W3 consistent. Status:
1. W0 — confirmed (nothing to clean; the incident's data doesn't
   exist in this pod at all — §1).
2. W2 — **NOT all green.** H1/B1(partial)/H4(partial) done; **H3 not
   done.** This is the literal reason to hold.
3. W3 — consistent for the two paths fixed this round (chat + loop
   planning/execution); the `cto_projects.py` direct-execute path has
   a smaller residual gap (§4).

**Context-pinning rule for whenever the battery does resume** (per the
founder's ask, documented now for that future run): every test in the
5+5 battery must assert the active project ID before starting AND
after finishing (one line each); any mid-test change = immediate test
failure + screenshot + stop that test.

---

## 6. Proof Ledger (appended in full — see `memory/PROOF-LEDGER.md` for the live, continuously-appended copy)
See the ledger file directly; the X1/W0-W3 entries added this round
are appended below the existing Overnight Loop entries, in the same
`{timestamp, workstream, task-id, status, proof path, note}` format.

## 7. Regression: no-new-vs-baseline
See `/app/e2e-proof/X1/regression_comparison.md` for the full method
and numbers. Headline: baseline had 410 FAILED/ERROR lines; this
round's final full run has 360 — **fewer, not more**. The ~34
"new-looking" lines from an intermediate run were tracked down to (a)
a real but immediately-fixed test-suite-only import-order issue (fixed
via one new autouse fixture in `tests/conftest.py`), and (b) pre-
existing order-dependent flakiness proven via `git stash` A/B on every
spot-checked case (both files fully reverted still failed/errored
identically or worse).

## 8. No-Silent-Fail Audit
- X1 F1 isolation: **partially satisfied, limitation stated out loud**
  (§0.2) — not silently claimed as "fixed" when the deeper
  single-process-topology issue is a founder/deployment decision.
- W0: **stated plainly** that the incident's data doesn't exist in
  this pod rather than fabricating a residue report against data that
  isn't there.
- W2 H3: **stated plainly as not done**, added as a new explicit
  stop-gate line in R9's checklist rather than marked GREEN.
- W2 B1 / W3 cto_projects.py gap: **stated plainly as not done**,
  flagged P1, not silently folded into "W3 done."
- W4: **explicitly NOT met**, battery restart withheld.
- Duplicate GitHub App installations (157161705/156023644): carried
  forward from the prior round's own investigation (already
  evidence-based — see `memory/PROOF-LEDGER.md`'s `[2026-08-30T00:25Z]`
  entry) — 157161705 is an active, real, unrelated installation
  (account "TJSNDHU") not referenced by any project in this DB;
  156023644 does not appear in the active-installations list at all
  (already gone on GitHub's side). **NEEDS-FOUNDER**, marked and
  moved on per the founder's own Q3 answer — nothing uninstalled from
  here.

## 9. Flag/State Readout
- `MOCK_LLM` = `true` in this pod's `.env` — **unchanged** before vs.
  after this round (verified by grep both times).
- No production flags were touched. No migrations ran. No new
  dependencies were added.
- New admin-visible flag/tile: `GET /admin/live-model-mode` +
  "Live Model Mode" card in `AdminSystemHealth.jsx`.
- `R9-PROD-FLIP-CHECKLIST.md` stop-gate: 4 items → **5 items** (H3
  added, NOT satisfied).

## 10. NEEDS-FOUNDER
1. **Duplicate GitHub App installations** (157161705 / 156023644) —
   10-minute founder action. 157161705 = active, account "TJSNDHU",
   not tied to any project in this pod's DB (likely a real,
   unrelated end-user of your GitHub App — do NOT uninstall from
   here without you confirming from your own GitHub account context).
   156023644 = not present in the active list at all (nothing to do).
2. **This Preview pod's database has never mirrored Production data**
   (confirmed standing fact, not new — see `PRD.md`'s `2026-08-25` P0
   entry for the identical situation with the identical org name,
   `ReRootsBeauty/ReRoots-`). This Preview pod's database has zero
   trace of `TJSNDHU/Aurem` or `RerootsBeauty/ReRoots-` as real,
   connected projects (§1). To get data-level (not just code-level)
   proof of the X1/W1 root causes for this specific incident, this
   agent would need read access to Production's logs/DB, or you'd
   need to pull them yourself for the incident window.
3. **Preview/Production process isolation** (X1 F1's honest
   limitation) — this pod runs one backend process for all traffic.
   If real-model validation work needs to happen again without any
   risk to live usage, it needs either a second, separately-deployed
   instance, or an explicit scheduled window.
4. **H3 (loop repo pinning) and the two smaller P1 gaps** (B1 +
   ship-refuse guard on `cto_projects.py`'s direct-execute path) are
   real, scoped, buildable follow-ups — not blockers, just not done
   tonight. Listed in the Next Action Items below.

---

**STOP. Awaiting founder.** T2-T5 (Overnight Master Loop 2) remain
paused per the founder's explicit instruction, to resume only after
X1 + W0 are green (this report) AND the founder's "resume" signal.
