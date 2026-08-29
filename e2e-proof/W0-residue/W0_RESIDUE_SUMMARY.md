# W0-residue — GitHub read-only forensics (2026-08-30, founder follow-up GO)

Unblocked per founder's instruction: pod DB has no records for these repos (confirmed
last round), but GitHub has the actual writes — checked directly, read-only, via the
GitHub App's own installation-token minting (admin JWT via `create_token()`, no secrets
printed). Full raw output: `diagnostics_full.json`, `rerootsbeauty_repos.json`,
`rerootsbeauty_commits_branches.txt`, `tjsndhu_repos.json`, `tjsndhu_aurem_commits_branches.txt`.

## 0a. Identity — is RerootsBeauty a founder fixture or a real user?
**Real, active, distinct GitHub App installation**, not a founder fixture:
- `id 155986962`, `account_login: "RerootsBeauty"`, `account_type: "User"`,
  `repository_selection: "selected"` (a deliberately scoped grant — real customers do
  this; founder/test accounts in this same installation list are uniformly `"all"`),
  `suspended_at: null` (active), created `2026-08-23`.
- Repo: `RerootsBeauty/ReRoots-`, private, default branch `main`.
- Commit history shows two distinct human authors over time: `reroots` and
  `TjSandhu`/`Tejinder sandhu` (the same name that owns the `TJSNDHU` installation) —
  consistent with the founder (or someone on the founder's team) doing SEO/dev work
  for this real small-business customer as a service, explaining why both accounts
  legitimately co-exist and can plausibly be confused with each other in a UI.
- **Authority conclusion: this is a real user's project. READ-ONLY. Nothing was
  touched, nothing was cleaned (there was nothing stray to clean — see 0b).**

## 0b. Residue check on RerootsBeauty/ReRoots- — ZERO residue found
- Latest commit: `5267c0d` at **2026-08-21T02:39:31Z** ("chore(seo): aurem founder
  fix [via ORA by Aurem]").
- **No commit anywhere near 2026-08-29** (the incident date) — the most recent write
  is 8 days before the founder's regression test.
- Branches: exactly 3 — `main`, `conflict_280326_0057`, `conflict_300326_1857` (both
  `conflict_*` branches are from March 2026, unrelated). **No `auremcto/*` branch, no
  branch or commit matching loop IDs `89215749` or `9feafc45`.**
- **Conclusion: L1 (89215749) and L2 (9feafc45) made ZERO real GitHub writes to this
  repo.** The reported incident was a UI-only active-project mis-selection (already
  root-caused and fixed as W1/H1 last round) — it did not result in an actual
  cross-repo write. This is the single most reassuring fact in this whole
  investigation: the near-miss did not become an actual miss.

## 0c. Loop forensics for L1/L2 — binding over time
The pod's own loop/task DB collections have zero records for these loop IDs
(confirmed last round — Preview never had this data). GitHub-side, neither ID appears
as a commit SHA or a branch name on EITHER repo checked. Combined with 0b's zero
residue on RerootsBeauty and no matching artifact on TJSNDHU/Aurem either (see 0d),
the most consistent explanation across all available evidence: **L1 and L2 never
reached a real GitHub write on any repo** — most likely they were still at
propose/suggest stage (or aborted) when the active-project context was wrong, and
never got to `confirm_ship`. No orphaned partial-write artifacts exist anywhere this
agent can check.

## 0d. TJSNDHU/Aurem integrity — DATA-VERIFIED, not source-inferred
Commit history around the incident window, most-recent-first:
| SHA | Timestamp (UTC) | Message | Verdict |
|---|---|---|---|
| `6c0ef3f` | 2026-08-29T05:04:13Z | `feat(ora): Add a comment at the top of README... [via ORA by Aurem]` | **Expected test-ship** — this IS the founder's own "Loop L4 after the window = real commit 6c0ef3f" reference. Confirmed real, on `main`. |
| `a31f22b` | 2026-08-29T04:49:37Z | same pattern | Expected test-ship, part of the same regression series. |
| `7820a4a` | 2026-08-29T04:23:18Z | `AUREM: In README.md prepend the line "# Regression retest 2"...` | Expected test-ship (regression-battery harness commit, different message style from the ORA loop commits — looks like a direct/manual test-harness write, not a stray). |

**Important corroborating find (independent of, and consistent with, last round's
source-level X1 diagnosis):** `a31f22b` landed at **04:49:37**, which is *after* the
previous agent session restored `MOCK_LLM` back to `true` at `04:47:50` (see last
round's `.env` mtime evidence). A real commit landing while `MOCK_LLM=true` is exactly
what Finding A from last round's report predicts: the loop/task-execution path
(`services/llm/_meta.py::call_llm_with_meta`, used by `cto_projects.py`'s direct
task-submit ship path) had **zero MOCK_LLM awareness before this round's fix** — so it
made a real commit regardless of the mock flag. This is now **data-verified**, not
just source-inferred: the GitHub timeline itself lines up with the exact gap X1
fixed.

**Repo left in its pre-existing state** — no branches/commits were reverted or
deleted, because none were stray. `main`'s tip is unchanged from before this check.

**Incidental finding, NOT part of this incident, NOT touched:** the repo also carries
at least 100 branches named `aurem/fix-<vuln-class>-<epoch>` (e.g.
`fix-chain_eval_plus_secret-...`, `fix-dangerously_set_html-...`, `fix-eval_usage-...`)
— these look like leftover branches from a separate security-auto-fix feature,
unrelated to ORA/loop chat and unrelated to loop IDs 89215749/9feafc45. Flagged
NEEDS-FOUNDER as an observation only; out of scope for this round, not cleaned.

## 0e. Founder-required verify step — install 155986962 mapped in OUR db? (2026-08-30, follow-up)
Scanned all 182 collections in this pod's Mongo for any document referencing
`installation_id: 155986962` (int or string) or `github_login`/`account_login`
`"RerootsBeauty"`. **Zero hits anywhere.** This installation/account does not map
to any founder-owned test fixture record in this pod's DB.

**Per founder's own fallback rule ("if it maps to a regular user (or no mapping) ->
finalize W0 as real-user repo, read-only, no cleanup, cause fixed via H1+H3, founder
decides notification"): W0 IS FINALIZED as follows:**
- RerootsBeauty/ReRoots- = real user's repo, confirmed by both GitHub-side identity
  evidence (0a above) and this DB-mapping check (0e). Not a founder fixture.
- READ-ONLY throughout. No cleanup performed, none needed (0b: zero residue).
- Root cause fixed this round via H1 (ProjectSwitcher silent-switch removal, prior
  round) + H3 (loop repo pin-and-assert-before-write, this round).
- **Notification to the real user about the near-miss is the founder's own call** —
  not made by this agent (no user contact, per standing constraint).

**W0 STATUS: CLOSED.**
