# R10 — Rollback-on-PR Gap: Risk Memo (analysis only, no code changed)

**Scope**: `services/loop_rollback.py` + `routers/loop.py::rollback_loop` +
`services/loop_engine.py`'s ship-via-PR path (`ship_via_pr` feature flag,
Preview-only today). Written per the founder's Loop N item 1 ask.

**Verdict: NOT SAFE as-is for real users.** Concrete, evidence-based gaps
below. Ship-via-PR must stay gated (flag OFF in prod, which it already is)
until either this memo can say "safe" or a fix lands — this memo does
**not** clear that gate.

---

## 1. What does a rollback target for a PR-based ship?

At ship time (`loop_engine.py:3654-3777`), AUREM:
1. Commits to a throwaway branch `auremcto/ship-{loop_id}-{ts}` (NOT the
   base branch) via `commit_files()`.
2. Opens a draft PR head→base, labels it `aura:ship`.
3. Persists `context["commit"] = {sha, full_sha, pr_url, pr_number,
   pr_branch, ...}` **once, at ship time** — `full_sha` is the commit on
   the *throwaway branch*, never the base branch.

At rollback time (`routers/loop.py:1327-1395`), the code branches on live
PR state:
- **PR never merged** → `close_and_retract()`: closes the PR, deletes the
  throwaway branch. Nothing to revert on the base branch (correct — the
  commit never landed there).
- **PR merged** → falls through to the **unchanged, pre-ship-via-PR**
  path: `run_rollback_bg(commit_sha=full_sha, ...)`, i.e. it reverts the
  **stored, pre-merge `full_sha`** — the SHA from the throwaway branch,
  captured before the PR ever merged.

**This is the core problem: the rollback target for a merged PR is stale
by construction.** `full_sha` is never updated when the PR actually
merges. The webhook handler that DOES fire on merge
(`services.loop_safety.dispatch_pull_request_webhook`) only updates
`loop_outcomes.pr_status` (a different collection) — it never touches
`loop_sessions.context.commit.sha`/`full_sha`, and `get_pr_status()`
(the live check `rollback_loop` calls) discards the one field that
actually matters here: GitHub's own `merge_commit_sha` on the PR object.
It only extracts `merged` (bool) and `state`.

## 2. Failure modes, ranked by severity

### HIGH — a network blip on the PR check can make rollback silently no-op while reporting "done"
`get_pr_status()` fails closed to `{"merged": False}` on **any** exception
(timeout, DNS blip, GitHub 5xx). `rollback_loop` treats `merged: False`
as "never merged" and runs `close_and_retract()` — which, for an
**already-merged** PR, is a harmless no-op (`close_pr` on an
already-closed PR is idempotent; `delete_ship_branch` no-ops if GitHub
already auto-deleted the head branch on merge, a very common repo
setting). The response returned to the user is
`{"ok": true, "rollback_status": "done", "detail": "PR was never merged —
closed the PR and deleted the ship branch instead of reverting a
commit."}` — **a false, confidently-worded success message.** The actual
shipped change is never reverted and stays live on the base branch. This
is worse than an honest failure: the user is told the rollback succeeded.

### HIGH — squash/rebase merges break the revert's file-diff assumption
`revert_commit()` (`github_api_writer.py:301-436`) reverts by: fetch the
target commit's file list + its single parent's version of each file,
then rebuild those files on top of **current branch HEAD**. This is a
content-diff revert, not a true `git revert` — it works only if the
commit object it's given (`full_sha`) represents the *actual* diff that
landed on the base branch.

- **Squash merge** (GitHub's default merge-button setting on many repos):
  produces a brand-new commit on the base branch with a different SHA and
  a diff that reflects the actual squashed result — which can differ from
  the pre-merge throwaway commit if the base branch moved and GitHub
  needed to resolve that during merge, or if the repo squash-edits the
  commit message/content. Reverting the STALE pre-merge `full_sha`
  instead of the real squash commit means the revert is computed from
  the wrong diff — it can miss files, restore the wrong "before" content,
  or leave the actual applied change partially in place.
- **Rebase merge**: GitHub's `merge_commit_sha` for a rebase-merged PR
  points to the *last* of the (possibly multiple) rebased commits, not a
  single commit representing the whole PR. AUREM only ever pushes ONE
  commit itself, but the ship branch is a **draft PR meant for human
  review** — a reviewer pushing a fixup commit before merging would make
  this materially wrong: reverting only the last rebased commit would not
  undo AUREM's own original commit at all if a reviewer's edit landed on
  top of it.
- **True merge commit** ("Create a merge commit" strategy): the resulting
  merge commit has TWO parents; `revert_commit()`'s
  `commit["parents"][0]["sha"]` assumption (first parent = "the version
  before") happens to align with GitHub's own combined-diff convention
  for merge commits, so this specific strategy is *likely* fine — but
  this is inferred from GitHub's API behavior, not verified by a test in
  this codebase.

**Net effect: even in the "PR was merged, fall through to revert" branch,
today's code has never actually reverted a squash- or rebase-merged
result correctly, because it never fetches or uses the real
`merge_commit_sha`.**

### MEDIUM — object retention after branch deletion
If the repo (or the org) auto-deletes head branches on merge (a common
GitHub setting, and the exact behavior `close_and_retract()` already
treats as expected in the unmerged path), the throwaway branch ref
disappears. The original commit object can still usually be resolved by
SHA via the GitHub API for some time after becoming unreachable, but this
isn't guaranteed indefinitely (loose-object retention windows vary). A
rollback attempted long after merge risks `_get_commit_details()` 404ing
on a SHA that's since been garbage-collected — an outright failure this
time, at least an honest one (not silently wrong), but still an
availability gap unique to the PR path (the always-on direct-commit path
has no equivalent branch-deletion step, so this risk doesn't exist there).

### LOW — human-added commits on the ship branch aren't tracked at all
Nothing compares the ship branch's tip SHA at rollback time to what
AUREM recorded at ship time. If a reviewer pushed anything to
`auremcto/ship-*` before merging, AUREM has no signal that the "shipped"
state differs from what it originally pushed.

## 3. What would safe rollback require

1. **Re-fetch live PR state at rollback time, including
   `merge_commit_sha`** — not just `merged`/`state`. `get_pr_status()`
   already makes the right API call (`GET /pulls/{number}`, which
   includes `merge_commit_sha` in the response); it just discards the
   one field that matters. Revert **that** SHA, not the stale
   pre-merge `full_sha`.
2. **Self-heal the stored SHA via the merge webhook.** When
   `dispatch_pull_request_webhook` sees `action=="closed" and merged`,
   it should also write the real `merge_commit_sha` onto
   `loop_sessions.context.commit.sha`/`full_sha` for the matching
   `ship_branch`, so the session's own record of "what actually shipped"
   isn't permanently stale even before a rollback is ever requested.
3. **Do not let a lookup failure drive a "done" response.** Distinguish
   "confirmed unmerged" from "couldn't confirm" — the latter must return
   an honest "couldn't verify PR state, try again" instead of silently
   taking the close+retract branch and reporting success.
4. **Detect drift on the ship branch before reverting.** Compare the ship
   branch's current tip SHA (or the PR's `commits` count) against what
   AUREM pushed at ship time; if they differ, refuse automatic rollback
   and require manual review rather than reverting only part of what's
   actually on the base branch.
5. A named regression test per fix, using a *real* squash-merge-shaped
   `merge_commit_sha` distinct from the pre-merge SHA (the current test
   suite's `test_rollback_merged_pr_falls_through` uses the SAME sha for
   both, so it cannot catch this class of bug — it passes today because
   it isn't testing the thing that's actually broken).

## Hard gate status
`ship_via_pr` remains Preview-only (Mongo-backed flag, no prod row = OFF,
no env var, no code-level prod flip). **No real user has been exposed to
this gap yet.** Per the founder's own gate: this feature must not reach
R9 / real users until this memo says safe, or the fix above lands. This
memo says: **not safe yet** — items 1-4 above are the minimum scope.
