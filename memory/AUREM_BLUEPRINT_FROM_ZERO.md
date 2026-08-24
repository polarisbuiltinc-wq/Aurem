# Building AUREM's Category of Product, Done Right, From Zero

**Purpose:** a step-0-to-production reference for an AI coding-agent product for non-technical
founders. Every recommendation below is either (a) a **SESSION LESSON** — grounded in a real bug,
fix, or test result observed in AUREM's own codebase this session, cited with file/line/date, or
(b) a **GENERAL PRACTICE** — a standard industry pattern, labeled as such, not dressed up as
something we proved. Where effort/cost trade-offs matter, they're stated, not glossed over.

---

## Phase 0 — Foundations, before writing any product code

### 0.1 Auth architecture: decide the identity/repo-access model on day one

**SESSION LESSON.** AUREM went through three real auth models for GitHub access, in this order,
and each transition caused a customer-visible bug:

1. **OAuth-only** (`Continue with GitHub`) → gives an identity token, not a repo-scoped
   installation. AUREM's `NewUserWizard.jsx` bug (found 2026-08-20): the wizard decided its UI
   state from `/github/oauth/status` alone, so any user who had OAuth-linked their identity but
   never installed the GitHub App got dumped into a **required-PAT** repo picker — a broken
   first-run experience for what the founder estimated was "a large share of users." Root cause
   was a pure frontend state-machine bug: checking the wrong signal ("is identity linked") instead
   of the right one ("can this user actually list repos").
2. **PAT (Personal Access Token)** — asks the user to generate and paste a long-lived token with
   broad scope. Real, observed problems: (a) it's a genuinely bad first-run UX for a
   non-technical founder (paste a cryptic token vs one OAuth click); (b) it grants broad,
   long-lived, un-auditable access — no per-repo scoping, no install/uninstall lifecycle, no
   webhook-driven revocation signal; (c) removing it later (2026-08-20, "PAT removed from
   connect-repo UI") required auditing and touching **~20 call sites across `loop_engine.py`,
   `rollback_manager.py`, `chat.py`, `mcp`, `local_tools.py`, `security_scan.py`,
   `finding_fix_applier.py`, `admin_projects_brain.py`, `repo_heal.py`, `repo_status.py`** — every
   one of them had grown its own PAT-vs-OAuth-vs-App fallback branch over time. Retrofitting a
   single auth model onto a codebase with 20 divergent fallback branches is a multi-day, high-risk
   migration; deciding the model on day 0 avoids all of it.
3. **GitHub App (installation-token based)** — the model AUREM landed on. Scoped per-repo,
   webhook-driven install/uninstall/suspend events, short-lived tokens minted on demand. This is
   the correct end-state, confirmed by the fact that removing PAT support broke nothing once the
   App path was solid.

**Even the App path had a real, deployed bug from getting the HTTP mechanics wrong**: GitHub App
installation tokens must be passed as the HTTPS **password** with username `x-access-token`
(`https://x-access-token:{token}@github.com/...`). AUREM's git-clone code built the URL as
`https://{token}@github.com/...` (token as bare username, no password) — this is a real,
production-relevant bug that existed **since 2026-05-30** (confirmed via `git log -L`, introduced
in commit `c21078a`, ~3 months before it was caught), and was only found by a **mandatory
real end-to-end drill** (signup → connect → ship), not by unit tests, because the unit tests
mocked the subprocess call that would have executed the broken URL. Every ship/rollback attempt
authenticated by a GitHub App token, on any host with `git` on PATH, silently failed with a
misleading "could not read Password... No such device or address" error — which AUREM's own
error-translator then turned into a plausible-sounding but **wrong** "your GitHub token expired"
message shown to the user. This is a two-layer lesson: (1) get the exact wire format right for
your chosen auth model and verify it with a real subprocess call, not a mock; (2) a good error
translator can actively make a wrong diagnosis sound MORE convincing to the user, which is worse
than no translation if the underlying cause is misclassified.

**Recommendation for day 0:** commit to GitHub App as the sole repo-auth model from the start.
Never build a PAT fallback "just in case" — it becomes the SPOF-of-fallback-branches problem
above. Test the exact wire-level auth mechanics (clone URL format, header format) with one real,
live call against a real disposable test repo before writing a single line of the higher-level
onboarding UI. This is cheap (minutes) on day 0, expensive (days) three months in.

**Auth broker decision (identity, not repo access):** AUREM also carries this same lesson at the
identity layer — it currently uses Emergent's shared Google OAuth broker for login, with a
parallel AUREM-owned direct Google OAuth path already built and Preview-verified but not yet
switched live (pending founder go/no-go on branding + Production OAuth app setup). **GENERAL
PRACTICE:** a shared/managed auth broker is the right choice to ship fast pre-launch (zero OAuth
app review, zero redirect-URI management); owning your own OAuth client is the right choice once
brand consistency and consent-screen ownership start to matter. Decide which era you're in before
building — but build the code so the swap is a config/flag change, not a rewrite (AUREM's
parallel-path approach, not yet flipped, is the right shape for this).

### 0.2 Data model foundations: avoid the "one shared db.py" SPOF from day one

**SESSION LESSON.** AUREM's `cto_services/db.py` (the single shared Mongo-access helper) has
**117 confirmed importers**; `cto_services/auth.py` (the shared auth-dependency helper) has **78
confirmed importers** (both counts from a direct grep-based architecture audit this session,
flagged as real single-points-of-failure). This isn't a hypothetical risk: any breaking change to
either file's function signature ripples through 100+ call sites with no compiler to catch it
(Python), and any bug in either file has a blast radius touching nearly the entire backend. This
is exactly the kind of coupling that turns a 10-minute fix into a multi-hour regression hunt.

**GENERAL PRACTICE + how it should have been designed from day one:** a lightweight repository
pattern — one repository class per collection/domain (`ProjectRepository`, `TaskRepository`,
`UserRepository`), each depending on the shared low-level Mongo client but exposing typed,
domain-specific methods (`get_active_project(project_id)` not `db.cto_projects.find_one(...)`
scattered 100 places). This doesn't eliminate the shared low-level client (you still need one Mongo
connection), but it means a breaking change to "how we fetch a project" touches one file, not
every router that happens to know Mongo's query shape. **Trade-off, stated honestly:** this is
more upfront boilerplate (one repository class per collection vs. calling `db.collection.find()`
directly), and for a true zero-to-one MVP with 3 collections, it may be over-engineering — the
right trigger point is "when you have more than ~10 collections or more than ~3 engineers," not
day 1 literally. What SHOULD happen on day 1 regardless of team size: never let a router file
reach into another domain's collection directly — that's the actual coupling that compounds.

### 0.3 Test infrastructure: decide the test-writing convention before file #1

**SESSION LESSON — this is the single most expensive lesson of this category found this session.**
AUREM has **~40 existing backend test files** that hit a live server over real HTTP
(`requests.get/post` against a `BASE_URL`/preview URL) instead of FastAPI's in-process
`TestClient`. This style gives genuine end-to-end confidence — but Python's coverage tooling
(`pytest-cov` / `coverage.json`) can only see code executed **inside the pytest process itself**.
A `requests.post()` call executes the actual backend code in a **separate OS process** (the running
uvicorn server) — so every line that style of test exercises is systematically invisible to the
coverage report, making the codebase's tracked code-quality/coverage score look worse than the
real behavioral coverage, and making it easy to "improve coverage" by migrating tests without
having fixed anything — the tests were already passing, they just weren't counted. This was found
mid-way through a multi-week coverage-improvement initiative (Phase 2c), after ~170 files had
already been written using the correct in-process pattern, meaning the wrong-pattern files were
identified late rather than never — costly to retrofit at scale (the migration was explicitly
"queued, not started" because it's "repo-wide effort, not a quick fix" per the founder's own
note this session).

**Recommendation — write this down as a one-page team rule BEFORE test file #1**:
- Unit/integration tests: FastAPI's `TestClient(app)` in-process, with `with TestClient(app) as c:`
  so the lifespan context manager runs (AUREM separately found bugs from tests instantiating
  `TestClient(app)` without the context manager, missing startup-time wiring). Mock only at the
  true I/O boundary (real GitHub/LLM calls), never mock your own business logic.
- A small, clearly-labeled minority of tests are genuine E2E-over-HTTP against a live/preview
  environment (real GitHub App, real LLM, real commit) — these are gold for catching the class of
  bug that unit tests structurally cannot catch (see 0.1's git-clone-URL-format bug, caught ONLY
  by a real E2E drill because the unit tests mocked the subprocess call). Put these in a clearly
  separate directory/marker (AUREM's `live_env_quarantine.txt` convention) and document in the
  file's own docstring that they don't count toward the coverage metric — so nobody is confused
  later about why "60% coverage" and "a live-only critical bug" coexisted (which is expected, not
  contradictory, once you understand what each test type can and cannot see).
- Tiered coverage targets from day one (see Phase 4.2) — decided once, not re-litigated per file.

---

## Phase 1 — Core agent architecture

### 1.1 The ReAct loop (Perceive→Reason→Act→Observe), built in from the start

**GENERAL PRACTICE, validated against a real gap found in AUREM this session.** AUREM's
`loop_engine.py` today runs PLAN→EXECUTE→VERIFY→SHIP, but the VERIFY step, until this session,
either (a) didn't exist at all on one of the two code-execution worker paths (`_run_task_with_git`
had **zero** Vanguard-verify call — confirmed by grep, meaning the security/quality gate that
existed on the OTHER worker path, `_run_task_via_api`, was silently absent on the path every real
production host actually uses, since `git` is installed on virtually every host), or (b) treated a
VERIFY failure as a dead end (fail immediately) rather than feeding the failure back into a new
REASON pass.

The correctly-built version — validated this session by building it and having it independently
tested (testing_agent, 14/14 pass across two rounds): on VERIFY failure, the loop must feed the
**exact evidence** (file:line, severity, rule, message — not a vague "it failed") back into a new
ACT step, then re-run the SAME OBSERVE (re-verify) before either looping again or giving up. The
mechanical detail that makes this a real loop and not a disguised retry: **attempt N+1 must
receive information attempt N did not have.** A loop that just re-calls the same LLM with the same
input on failure is not a ReAct loop, it's a coin flip with retries.

**Build this in from day 0 as an explicit state machine** with named phases (not implicit control
flow inside one giant function — AUREM's `_run_task_via_api`/`_run_task_with_git` are each
3,000+ lines partly because PLAN/EXECUTE/VERIFY/SHIP logic is interleaved with retry logic,
logging, and auth handling in one function body). A cleaner target shape:
```
Task -> Perceive(gather context: files, brain, issues, skills)
     -> Reason(LLM call -> proposed edit)
     -> Act(apply edit, in-memory or on a branch)
     -> Observe(verify: security scan + smoke test + diff review)
     -> if Observe.pass: Ship
     -> if Observe.fail and attempts < N: Reason(context + Observe.evidence) -> Act -> Observe
     -> if still fail: translate error, offer retry/escalate (see Phase 4/6)
```
Each phase should be a separately callable, separately testable function — not inlined — so that
(a) unit tests can exercise Reason/Act/Observe independently with mocked boundaries, and (b) a
SECOND worker implementation (like AUREM's git-vs-API-path split) can share the SAME phase
functions instead of two divergent 3,000-line copies that can (and did) drift out of parity.

### 1.2 Checkpointing / state persistence, designed in from day one

**SESSION LESSON (honest, not yet built in AUREM — proposed only).** AUREM's current retry
button creates a brand-new task and restarts PULL→READ→THINK→WRITE→VERIFY→COMMIT from zero,
confirmed by code read (`retry_task()` in `cto_projects.py`). The INNER self-correction loop added
this session (1.1 above) does NOT have this problem — because it's additional LLM calls inside
the SAME task execution, not a new task — but the OUTER user-facing retry button does.

**What the schema should look like, decided on day 0** (not retrofitted): a task execution
record with a **step ledger**, not just a final status:
```
task_execution {
  task_id, status,
  steps: [
    { step: "pulled_files", completed_at, data_ref: {...} },
    { step: "generated_edit", completed_at, data_ref: {edit_hash, files: [...]} },
    { step: "verify_attempt_1", completed_at, result: "blocked", findings: [...] },
    { step: "verify_attempt_2", completed_at, result: "pass" },
  ],
  resumable_from: "generated_edit"   # last step whose output is still valid
}
```
A retry then means: re-enter the state machine at `resumable_from`, not at step 1 — reusing the
already-pulled files and (if the failure was VERIFY, not the edit itself) even the already-
generated edit, only regenerating what's actually implicated by the failure. This is meaningfully
more engineering than "just create a new task" (you need cache-invalidation logic — is a
checkpoint still valid if the underlying repo changed since it was taken?) — flag this as real
complexity, not a free win. **Effort estimate, stated honestly:** for a small team, this is a
multi-day feature, not an afternoon; the ROI shows up specifically on tasks with expensive early
steps (large repo reads, multi-file context building) that are wasteful to redo on every retry of
a late-stage (e.g. commit-time) failure.

### 1.3 The ambiguity-gate and reachability-scope boundary, as first-class components

**SESSION LESSON, directly from a real investigation this session.** AUREM's "Mode D" deep-
diagnosis capability has a real, confirmed gap: its own prompt instructs the model to "prefer a
probing answer over a refusal" even when the available signal is weak — meaning it will guess
rather than say "I don't have enough information," which is a trust problem independent of any
specific bug. Separately, and more structurally: Mode D's `read_file()` and the task-execution
agent's commit path both operate ONLY on the customer's own connected repo, by design — they have
no visibility into AUREM's own backend source. When a real customer-facing bug (`'str' object has
no attribute 'get'`, this session's P0 incident) turned out to live in AUREM's OWN backend code
(an OpenRouter response-parsing bug, not anything in the customer's repo), no version of "let the
agent try harder to diagnose it" could have worked, because the evidence needed to diagnose it was
categorically outside what the agent could see.

**These should be two explicit, separate architectural components, not implicit model behavior:**

1. **Ambiguity gate** (before Reason): given the task description + available context, does it
   resolve to ONE unambiguous target (specific file/function/behavior)? If the task is
   under-specified in a way that would materially change what gets written, stop and ask — don't
   let the model guess-and-write. Implementable as a cheap pre-check (does the task mention a
   file path or clearly identifiable feature name; if not, and the repo has >N plausible
   candidates, ask) before spending an expensive LLM codegen call on a guess.
2. **Reachability-scope check** (before accepting a diagnosis as final): does the evidence (stack
   trace, error text, file reference) resolve to a path INSIDE the agent's actual jurisdiction
   (the customer's repo, for a task-execution agent)? If the evidence points outside that scope —
   or there's no evidence at all — the correct output is an honest "this doesn't look like it's in
   your repo" / "I don't have enough information," not a plausible-sounding guess. This is a
   structural check on the EVIDENCE's origin, not a confidence score on the model's own
   self-assessment — models are unreliable at knowing what they don't know, but a hard rule
   ("if the file reference isn't in the repo I can see, say so") is checkable in code.

Building these as first-class, testable functions (not prose instructions buried in a system
prompt) means you can unit-test the boundary directly — e.g. "given an error with no file
reference and a repo with 500 files, assert the agent's output is a clarifying question, not a
guess at one of the 500 files" — which is exactly the kind of regression AUREM cannot currently
test for, because the current version is prompt-only.

---

## Phase 2 — Trust and safety layer

### 2.1 Approval gates (plan-approve, ship-confirm) — what AUREM got right, generalized

**SESSION LESSON — this is a real, working pattern, confirmed by founder-witnessed live
Production use this session ("Trust Layer two-phase approval gates ... working as designed via
real chat + real ship").** The pattern: the agent never silently commits an irreversible action.
A generated plan is shown before execution (Plan→Approve), and a generated commit is shown with
its actual diff before it's pushed (Ship→manual confirm with commit preview). Generalized as a
reusable rule: **any action that is expensive to undo (a real commit, a real payment, a real
delete) gets a preview-then-confirm step; anything cheap to undo (a draft, an in-memory edit) can
proceed without one.** This is the right default for a non-technical-founder audience specifically
— they cannot review a diff for correctness the way an engineer can, but they CAN recognize "does
this look like what I asked for" if shown a plain-language summary + the actual changed files.

### 2.2 Rollback — the real, proven mechanism, as reference design

**SESSION LESSON, this is the most thoroughly validated piece of infrastructure surfaced this
session — build to this exact shape, not a simpler version.** AUREM's Rollback v2 (built and
independently tested this session, 25/25 pass, plus a live drill against a real disposable GitHub
repo with byte-exact restore verification) has four components, and all four are necessary — a
simpler version (e.g. "just `git revert`") was tried first (that's `loop_rollback.py`'s original
mechanism) and found insufficient on its own:

1. **Pre-change snapshot** — byte-exact file contents (present/absent tracked) + relevant DB state,
   captured BEFORE every ship, stored to durable object storage (Cloudflare R2 in AUREM's case)
   with a Mongo index row. Why this matters beyond git history: git revert only undoes the file
   diff; it doesn't restore DB-side state (e.g. project config that changed alongside the commit).
2. **Two-phase preview-then-execute** — Phase 1 computes and shows the actual revert diff (a
   single-use, time-boxed preview token); Phase 2 requires that exact token + explicit confirm to
   execute. Mirrors the Ship-side preview-then-commit pattern from 2.1 — rollback is just as
   destructive as shipping and deserves the same UX respect.
3. **An attempts ledger, independent of the rollback mechanism itself** — every attempt (success
   or failure) is recorded with `{attempt_id, snapshot_id, mechanism, result, verified,
   restored_commit_sha}`. This is what let AUREM's own health-scoring honestly report "zero
   positive-path evidence exists" for rollback before this session's drill — you cannot claim a
   safety mechanism works if there's no record of it ever having been exercised.
4. **A synthetic drill harness** — a disposable test repo, seeded fresh per run, that ships a
   known-good commit, deliberately breaks it, triggers rollback, and reads back via the real
   GitHub API to CONFIRM the break is actually reverted (not just "the rollback endpoint returned
   200"). This is what caught the git-clone-URL-format bug (0.1 above) — a bug that had been live
   for ~3 months and that no unit test caught, because unit tests mocked the exact call that was
   broken. **Build this harness in Phase 0/1, not as an afterthought** — its cost is one disposable
   repo + a scheduled CI job; its value is catching exactly the class of bug that silently corrupts
   real user actions in production while every dashboard shows green.

### 2.3 Error translation — never show raw exceptions, built in from day one

**SESSION LESSON — this is the clearest "should have been day-0, was retrofitted" case in this
whole document.** AUREM built a founder-language error-translation layer (`error_translator.py`)
weeks before this session — but it was wired into only ONE of several places an error could reach
the user, and this session found and fixed **three separate raw-error leaks of the same underlying
class**, discovered only because a real paying customer saw `'str' object has no attribute 'get'`
verbatim in their chat:
1. The live task-progress "steps" feed (`_log()`/`_emit()` calls) embedded `str(exception)`
   directly — visible in real time, DURING task execution, before the task even reached a final
   "failed" status where the translator would have run.
2. The per-attempt internal retry warning log did the same.
3. A completely separate code path (Mode D's own exception handler) had the identical bug,
   independently, because it was never told about the existing translator convention.

**The lesson generalizes cleanly: "never show raw exceptions" cannot be a single call site you
remember to wire once — it has to be a hard rule enforced at every place an exception can reach a
user-visible surface** (chat replies, live progress feeds, final status fields, retry messages).
Concretely, from day 0: (a) build a single `classify_error(exc) -> {category, user_message}`
helper FIRST, before any user-facing logging exists; (b) make it structurally impossible to call
your own logging/emit functions with a raw exception object or `str(exception)` — e.g. a lint
rule or a typed wrapper that only accepts a `SafeMessage` type, not a bare string built from
`f"{e}"`. AUREM's fix this session was reactive (find every leak after a customer hit one); the
day-0 version should be a type-level guardrail that makes the leak a compile-time/lint-time error,
not a code-review hope.

### 2.4 Security scanning — Vanguard's real architecture as the target

**SESSION LESSON, from directly reading `vanguard_verify_agent.py`/`vanguard_scanner.py` this
session.** AUREM's real (not aspirational) security-scan architecture on generated code is a
three-layer pipeline: (1) a **regex floor** — fast, deterministic pattern matches for known-bad
constructs (e.g. `eval()` on user input) that block a commit outright, no LLM involved, sub-second;
(2) an **LLM-based review pass** — given the diff, the model is asked to find real issues,
returning structured findings (file/line/severity/rule/message); (3) a **sandboxed dynamic smoke
test** (E2B) — actually imports/executes the generated code in an isolated sandbox to catch
syntax/import errors the static passes can't.

**The honest gap, confirmed by reading the code, not assumed:** step (3) is a smoke test (does it
import/run without crashing), not a DAST (Dynamic Application Security Testing) pass in the
standard security sense — it does not send adversarial inputs to a running instance of the
generated application and observe its behavior (SQL injection probes, auth-bypass attempts, XSS
payloads against actually-rendered output). For a coding-agent product where the "customer" is
often shipping to their own real users, this is a real, current limitation worth being honest
about, not silently implying "we run DAST" when the sandbox step is closer to "does the code at
least run." **Recommendation:** build the three-layer pipeline (regex floor → LLM static review →
sandboxed execution) as the Phase 2 baseline from day one — it's genuinely good and this session
validated it works (the self-correction loop in Phase 1.1 feeds real findings from exactly this
pipeline back into a fix attempt). Layer in real DAST (e.g. OWASP ZAP baseline scan against a
sandboxed, ephemeral deploy of the generated app, before it ships to the customer's real
environment) as a Phase 2+ addition once the product has traffic that makes the extra latency/cost
per ship worth it — this is genuinely more infrastructure (an ephemeral deploy target, not just a
Python import), so sequencing it after the cheaper static layers is a reasonable, stated trade-off,
not a corner being cut silently.

---

## Phase 3 — Onboarding, done right from the start

### 3.1 The three-path pattern, designed in from day one

**SESSION LESSON (partially real, partially a confirmed gap).** AUREM's onboarding actually has
two of three paths solid today: "Connect existing repo" (GitHub App, the primary path, confirmed
reliable for 46 of 46 zero-project users this session's production segmentation) and "Skip for
now" (confirmed reliable by construction — a pure "0 projects" check with no per-user flag that
could selectively fail). The **third path — "start a new project from scratch" (describe an idea,
get a scaffolded repo)** — has real, non-stub backing infrastructure already built
(`scaffold.py`, ~1,400 lines: brief→LLM file tree→QA gate→security-scan gate→real GitHub repo
creation→optional auto-deploy) but is **wired into zero onboarding screens** and has **zero real
usage** (confirmed via live DB query this session: 0 users on this track, 0 materialized projects).
This is the textbook version of "we built the capability but never connected it to where users
actually decide," and it sat that way long enough that the founder had to re-decide basic product
questions (should the new repo live in the user's own GitHub account or AUREM's shared org? should
it drop the user into normal chat, or a separate guided/safety-railed UI?) MONTHS after the
backend was built, because nobody had made those calls when the backend was designed.

**Recommendation: decide and wire the three-path decision tree on day 0**, even if "start from
scratch" is the last one you actually build:
```
First-run screen: "Connect an existing project" | "Start something new" | "Just looking, skip for now"
                          |                            |                         |
                   GitHub App install         idea -> scaffold ->      dashboard with a
                   (existing flow)             land in normal chat      persistent, low-pressure
                                                (NOT a separate          "connect when ready" banner
                                                 guided/safety-railed
                                                 UI — decide this
                                                 ONCE, before building)
```
The specific trap to avoid (AUREM hit it): don't build "scratch" mode as a separate, parallel
product experience (its own guided screens, its own safety rails) unless you've deliberately
decided your non-technical audience needs that — AUREM's founder ultimately decided the opposite
(drop them into the SAME chat window everyone else uses), which means the earlier guided-UI
version was wasted build effort that a day-0 decision would have avoided.

### 3.2 The 60-second time-to-value principle

**GENERAL PRACTICE.** For a non-technical founder, the first automated action after connecting a
repo should require zero typing and produce a visible, understandable result within ~60 seconds —
not "now write your first task." Concretely, informed by what AUREM already has: on repo connect,
immediately run the existing scan pipeline (Vanguard's regex + LLM review, already built, see 2.4)
against the connected repo in read-only mode and surface 1-3 REAL, plain-language findings ("your
signup form doesn't validate email format" / "3 dependencies have known security issues") as the
very first thing the user sees post-connect — proving value before asking for a single instruction.
AUREM has the scanning capability to do this today; whether it's wired to fire automatically on
first connect (vs. requiring the user to ask for a scan) is the kind of day-0 decision that avoids
a repeat of the scaffold-mode-built-but-never-wired trap in 3.1.

### 3.3 Real analytics on the funnel from day one

**SESSION LESSON — a genuine partial success worth learning FROM, not just a gap.** AUREM's
activation funnel tracking (`_compute_activation_funnel`, `github_funnel_events`,
`funnel_nudge_cron.py`) is real, per-stage, and was used this session to segment all 59 real
production users into three honestly-labeled groups (46 healthy-by-construction, 10 healthy
GitHub-connections, 3 genuinely stuck) and resolve the stuck group with a targeted email — this
is the funnel infrastructure DOING ITS JOB. The lesson isn't "AUREM has no analytics"; it's that
this capability was built **incrementally, after the fact**, and had real, found-late bugs along
the way worth avoiding on day 0: (a) a step-2 funnel stage was found under-counting due to a
query-logic bug (`_compute_activation_funnel`, 2026-08-20); (b) a `funnel_stage_nudge` cron had a
real check-then-act race condition that could send duplicate emails before a unique DB index was
added to prevent it. **Recommendation:** design the funnel schema (stages, the events that move a
user between them, a uniqueness constraint on any "send once per stage" action) as a first-class
data model on day 0, alongside the user model — not as a later aggregation query bolted onto
whatever fields happened to exist. Emit a real event on every meaningful state transition (signup,
repo-connect-started, repo-connect-completed, first-task-sent, first-ship-completed) from the
first version of the product, even before you have a dashboard to look at them — replaying
historical events into a new dashboard is easy; you cannot retroactively generate events you never
recorded.

---

## Phase 4 — Code quality discipline, enforced from commit #1

### 4.1 CI guardrails as day-one policy

**SESSION LESSON.** AUREM built two real, working, CI-wired guardrails this session — a new-file
"bloat" guard and a coverage ratchet — both live-reproduced (deliberately triggered a block,
fixed it, confirmed it then passes) before being trusted. One specific wiring detail matters and
is easy to get wrong: the new-bloat guard was deliberately wired to run on **every push**, not
gated to pull-requests-only, because "Emergent's Save-to-GitHub pushes directly with no PR, so a
PR-only gate would never fire on the real deploy path." **The general lesson: a CI gate is only as
good as its trigger condition matching your ACTUAL deploy mechanism — audit how code really
reaches production (direct push? PR merge? a separate deploy button?) before wiring any gate, or
it will pass every review while doing nothing on the path that matters** (see 5.1 for a much more
serious version of this same mistake). Concretely, from commit #1: a coverage ratchet (this
commit's coverage must not be lower than the last known-good baseline) and a diff-coverage check
(80-90% of CHANGED/NEW lines must be covered, distinct from total-file coverage) are both cheap to
add on day 1 and expensive to retrofit onto a codebase that's already accumulated years of
untested code (AUREM's own coverage-improvement initiative this session took multiple weeks across
several large router files specifically because it started late).

### 4.2 Tiered coverage targets, written down from the start

**SESSION LESSON.** AUREM's founder set an explicit, written tiered standard mid-session: **80%+
coverage for auth/ship/payment code** (the files where a bug directly causes money loss, broken
security, or a corrupted customer repo — `chat.py`, `cto_projects.py`) **and 60-70% for everything
else**. This is a genuine, sensible policy — not every line of code carries equal blast radius, and
demanding uniform 90%+ coverage everywhere is a common overcorrection that burns engineering time
on low-risk code (admin dashboards, cosmetic routes) instead of the code that actually hurts users
when it breaks. **Write this down as a one-paragraph team standard before any test file exists**,
naming the specific tiers for YOUR product (for an AI coding agent: auth, ship/commit, rollback,
payments = tier 1; everything customer-repo-scanning-and-reporting = tier 2; internal admin
tooling = tier 3) — deciding this once avoids the multi-week "which files need how much coverage"
negotiation AUREM had to do mid-flight.

---

## Phase 5 — Deploy and operational maturity

### 5.1 A genuinely closed-loop deploy gate

**SESSION LESSON — this is a serious, real, confirmed bug worth over-indexing on, because it's
the kind of thing that looks fine in every dashboard while being completely broken.** AUREM's
deploy-gate CI job (`auto_deploy.yml`'s `gate-on-ci`) was designed to block a deploy if the test
suite hadn't passed — but it compared the running CI workflow's name against a hardcoded string
(`CI_NAME="AUREM CI — Test Suite"`) that **never matched the real workflow's actual name**
(`"AUREM CI — Build + Test Guard"`). Because the comparison always failed to find a match, the gate
fell through to its "allow deploy" branch **every single time, regardless of the real CI result** —
a textbook fail-open bug. This had been silently true for an unknown period before this session
found it (confirmed this session, fixed to fail-closed on timeout/mismatch). **The general lesson:
a deploy gate that "exists" in your workflow YAML is not the same claim as "a deploy gate that
works" — the only way to know the difference is to deliberately push a build you know should fail
and confirm it actually gets blocked in real GitHub Actions, not reason about the YAML.** AUREM's
own fix, as of this session, is explicitly **NOT yet trusted** for exactly this reason — the
founder's own standing instruction is "gate remains UNPROVEN in real GitHub Actions until a
deliberate failing build is blocked," and it has not yet had that live-fire test. Build this
discipline in from day 1: after writing ANY deploy gate, its very next test is "make it fail on
purpose and confirm the block actually happens," before trusting it for a single real deploy.

### 5.2 Monitoring that reads real, live state

**SESSION LESSON, two distinct real bugs.** (1) AUREM's `/api/health` endpoint reported a
**stale build hash lagging real deploys by 24h+**, because `deploy_logger.py`'s commit-resolution
cascade fell back to a cached/legacy source (`BUILD_INFO.txt` / a `.build_info` file mtime) instead
of the real, fresh `git rev-parse HEAD` captured at actual boot time — fixed by recording a real
`deploy_events` row from `git rev-parse HEAD` + a real UTC timestamp at every boot, and having the
health endpoint prefer that over the legacy cascade. (2) A guard (`G18`) that health-checked the
codebase was re-running a **full codebase scan on every 45-second poll under an 8-second hard
timeout** — under real production load, occasionally crossing 8 seconds, flipping to "red," then
finishing fine on the very next poll and flipping back to "green" — firing two contradictory
alerts for what was really one transient blip, not a real outage. Fixed with (a) a 5-minute result
cache so the expensive scan doesn't re-run on every poll, and (b) a general flap-dampening rule
requiring 2 CONSECUTIVE confirmed ticks before treating any status change as real, for every guard,
not just this one. **The compounding general lesson: an accurate-looking health dashboard can be
lying in two different ways at once — showing STALE data (bug 1) or showing REAL but
NOISY/self-contradictory data from measuring something too expensive too often (bug 2).**
Build monitoring from day 1 with both failure modes explicitly guarded against: (a) always
timestamp and version-stamp what you're displaying, and always ask "when was this actually
computed" not just "is a number showing"; (b) never run an expensive check more often than its
own execution time comfortably allows, and always require multiple consecutive confirmations
before surfacing a status CHANGE (not a status value) to a human, to avoid alert fatigue on
transient noise.

### 5.3 DORA metrics, SLI/SLO — real infrastructure or an honest "not yet"

**HONEST STATUS, not built.** AUREM does not have DORA metrics (deployment frequency, lead time
for changes, change failure rate, MTTR) or formal SLI/SLO tracking as real, queryable
infrastructure today — this session's health-scoring work found real, adjacent building blocks
(a `restore_drill_history`/`backup_history` with genuine timestamped pass/fail data; a
`rollback_attempts` ledger; real deploy-event timestamps from 5.2) that COULD feed a DORA
dashboard, but no one has assembled them into the four standard metrics, and no SLO has been
declared for any endpoint. **GENERAL PRACTICE for a new build:** decide your SLOs (e.g. "chat
response starts streaming within 3s p95," "ship completes within 60s p95") before you need them
for an incident review, not after — an SLO declared retroactively during a postmortem is really
just "we noticed it was slow," not a real operating target. DORA metrics are cheap to start
tracking from day 1 if your deploy and incident events are already being recorded as structured
data (which they should be, per 5.2/5.1) — the four numbers are just aggregations over event
streams you already need for other reasons; the mistake is treating them as a separate initiative
requiring new infrastructure, when they should be a downstream query over infrastructure you built
for other purposes anyway.

---

## Phase 6 — What "100% user-friendly" actually requires (honest limits)

**Be direct: 100% reliability is not achievable for any AI system that generates code, and
claiming otherwise would be dishonest.** LLM-based code generation is probabilistic; a model can
misunderstand an ambiguous instruction, generate a subtly wrong fix, or hit a failure mode nobody
anticipated (this session's own root-cause incident — a malformed third-party API response shape —
is a good example: not something the product's own code choices could have fully prevented, only
handled better once it happened). **The honest, achievable target is not "never fails" — it's
"fails gracefully, transparently, with a real path to resolution every time":**
- The user is never shown a raw internal error (Phase 2.3) — CONFIRMED buildable, has been built.
- The system genuinely attempts a real fix before giving up, using real evidence, not a blind
  retry (Phase 1.1) — CONFIRMED buildable for failures inside the agent's own jurisdiction (the
  customer's repo); CONFIRMED NOT possible for failures outside that jurisdiction (the vendor's
  own infrastructure) — and the product should say so honestly when that's the case, rather than
  implying the AI "tried and failed" when it structurally could not have tried at all.
- When the AI genuinely cannot resolve something autonomously, there is a real, low-friction path
  to a human — this should be RARE (not the default response to failure, which would defeat the
  product's core value proposition of "fixes code itself"), but it must exist and must be
  reachable in one click, not buried.

### 6.1 Human escalation as a first-class, planned last-resort feature

**GENERAL PRACTICE, informed by this session's course-correction.** The first design pass this
session initially over-weighted escalation (a circuit-breaker that could have become the default
response to any repeated failure) — correctly identified and corrected as wrong: for a product
whose entire value proposition is "fixes code itself," making "contact support" the primary
response to failure would make the product indistinguishable from every generic AI wrapper that
already does that. **The correct design, decided this session:** escalation triggers only after
the agent has made a genuine, evidence-based fix attempt (not just a retry) and that attempt
demonstrably failed AGAIN with the same signature — detected via a failure-signature mechanism
(hash of project + normalized task + normalized error, tracked with a repeat counter) rather than
a raw retry count, so it's specifically "this exact thing keeps happening" not "this task took more
than N tries." Build the signature-detection infrastructure from day one (cheap — a hash and a
counter) even before building the escalation UI itself (more product-design work — deciding what
"reach a human" actually looks like) — the detection is the harder-to-retrofit part.

### 6.2 Real support-ticket integration, wired from day one

**HONEST STATUS.** AUREM does not yet have this wired — the escalation path proposed this session
explicitly reuses the EXISTING support surface (routing a repeat-failure banner to the existing
`/support` flow with task context pre-filled) rather than building a parallel system, which is the
right instinct or generalizes as one: **before building a new support/escalation surface, check
whether one already exists that can be extended — a second, parallel "contact us" system is a
classic sign of not checking first.** For a from-zero build: decide on day 0 which support channel
is canonical (a ticketing tool, a shared inbox, an in-app thread) and make sure the "AI genuinely
can't fix this" escalation path always routes there with the FULL context already attached (task
id, repo, the exact failure signature, what the AI already tried) — a human picking up a ticket
with zero context re-does the diagnosis work the AI already did, defeating half the point of
having tried autonomous diagnosis first.

---

## Summary — what to actually do differently if starting over

1. **Decide GitHub App as the sole repo-auth model on day 0.** Test the exact wire-level mechanics
   with one real call before building any onboarding UI on top of it.
2. **Write the test-writing convention (in-process TestClient, mocked I/O boundary, tiered
   coverage targets) down before test file #1.** Treat live-E2E tests as a small, clearly-labeled,
   deliberately-not-coverage-counted minority.
3. **Build the ReAct loop as an explicit, named-phase state machine with a step ledger from day 1**
   — not interleaved retry/logging/auth logic inside one giant function, and not two divergent
   copies for two worker paths.
4. **Build the ambiguity-gate and reachability-scope boundary as testable functions, not prompt
   text.** A model cannot reliably self-report what it doesn't know; a structural check on
   evidence-origin can.
5. **Copy AUREM's rollback design exactly (snapshot + two-phase preview + independent attempts
   ledger + synthetic drill harness) — a simpler version was tried and found insufficient.**
6. **Make "never show a raw exception" a type-level guardrail, not a convention people have to
   remember at every call site.**
7. **Audit your REAL deploy path (direct push? PR? separate button?) before trusting any CI gate,
   and deliberately push a build you know should fail to prove the gate actually blocks it — don't
   reason about the YAML.**
8. **Wire onboarding's three paths and the funnel event schema together from day 0** — a scaffolded
   capability that isn't wired into where users actually decide gets built for nothing (AUREM's
   scratch-mode: real code, zero usage).
9. **Escalation is a rare last resort, gated on a real failure-signature repeat, not the default
   response to any failure** — the product's differentiation depends on this ordering.
