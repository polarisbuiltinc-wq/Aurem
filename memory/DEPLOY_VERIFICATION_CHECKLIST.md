# Deploy Verification Discipline

**Filed**: 2026-02-12, after 3 back-to-back deploy pipeline races
**Revised**: 2026-02-12 (post Emergent Support confirmation of pipeline model)
**Owner**: agent-side; enforced by process, not by the pipeline
**Status**: MANDATORY for every prod deploy going forward

---

## Pipeline model (per Emergent Support, 2026-02-12)

**The deploy pipeline is "snapshot at build-start", NOT commit-pinning.**

- When a dispatch fires (`emergent__send_to_deployer` OR platform UI
  "Deploy" button), the pipeline resolves the buildable ref at
  **build-start time**, not dispatch time.
- If commits land between dispatch and build-start — or if the
  platform's out-of-band sync between the local `/app/.git` and the
  build-source ref lags — the pipeline will build a different SHA
  than the local HEAD at dispatch.
- **This is expected behavior**, not a bug. There is no SHA-pinning
  API today. Design deploy discipline around it.

## Source of truth for "what actually shipped"

**Do NOT rely solely on the `/api/aurem-dev/version` endpoint.** Its
`commit_sha` is read from `backend/BUILD_INFO.txt`, which historically
lagged actual HEAD (see 2026-02-12 SHA-ambiguity incident + fix in
`scripts/git_hooks/post-commit`).

**Primary source of truth**: **Manage Publishes → Overview** in the
Emergent platform UI. That panel reports the actually-built commit
and the pipeline's own view of the last-shipped ref.

**Secondary signal**: `/version` endpoint, but only after confirming
`backend/BUILD_INFO.txt` is being stamped correctly by the
post-commit hook (see Iter 314 fix).

---

## Three channels that mutate HEAD or trigger a deploy

Any of these can move HEAD or cause a build. Discipline must account
for all three.

| Channel | Trigger | Content shipped | Message pattern |
|---|---|---|---|
| **A — Agent `finish` auto-commit** | Every `finish` tool call | Substantive: whatever code/docs the agent touched | Agent's finish summary |
| **B — Platform session-fork bookkeeping** | New agent session spawned | Metadata-only: `.emergent/emergent.yml` `created_at` bump | `"Auto-generated changes"` |
| **C — Founder manual UI deploy** | Founder clicks Deploy in platform UI | (deploy channel, not a commit channel) — fires a build against current buildable ref | N/A |

**Implications for pre-dispatch checks:**

- HEAD can move between check-ins via Channel A (agent's own finish
  commits) or Channel B (session-fork bookkeeping). B is safe — it
  never adds code. A is bounded — agent controls `finish` timing.
- Channel C can fire any time. If a UI deploy is already building
  when the agent dispatches, the agent's dispatch may either be
  queued behind, coalesced with, or race the in-flight build.
- **Rule**: before dispatching, verify no build is currently
  in-flight (via Manage Publishes → Overview). If one is, wait for
  it to complete.

---

## The mandatory checklist

### 🟢 PRE-DISPATCH — before calling `emergent__send_to_deployer` or clicking Deploy

```
1. git status --short
   → Working tree should be clean (or only intended staged changes)

2. git log --oneline -5
   → Confirm the last 5 commits match what you EXPECT to ship.
     Look for Channel B "Auto-generated changes" commits that
     landed since your last check — they're safe but change HEAD.

3. Confirm all INTENDED commits exist on HEAD
   → Every code/doc change you meant to include must be committed
     locally. Uncommitted changes will NOT ship (pipeline snapshots
     the tracked git state, not the working tree).

4. Confirm no deploy is currently in-flight
   → Open Manage Publishes → Overview. If a build is running,
     WAIT for it to complete. Do NOT fire a second dispatch —
     concurrent dispatches produce race conditions.

5. Run targeted pytest suite → all green
```

If any of the above is wrong: **do not dispatch**. Fix it first
(commit outstanding work via `finish`, wait for in-flight build,
re-run tests).

### 🟡 DISPATCH — the `emergent__send_to_deployer` call itself

Record baselines in the dispatch message:
- Current prod SHA (from `curl .../version`, treat as informational
  only — see reliability note below)
- Current prod `built_at` timestamp
- Local git HEAD (informational — pipeline may build a different SHA
  per the snapshot-at-build-start model)

**Do NOT expect prod's post-deploy SHA to exactly match local HEAD.**
The pipeline may build a slightly older or slightly newer commit
depending on when the ref resolves. If the actually-built SHA is a
strict SUBSET of your intent (missing some of your latest commits),
that's the snapshot race — not a pipeline defect. See "Recovery"
section below.

### 🔴 POST-DISPATCH — Manage Publishes → Overview is the primary signal

**Do NOT wait for the deployer's "Deployment completed" message
before verifying — that message can arrive after the wrapper's
event-await window times out, even when the build succeeded.**

**Step 1** — Open Manage Publishes → Overview in the platform UI.
Look for the newly-started build entry. Confirm:
- Build status transitions: `wakeup → build → deploy → health_check`
- No red steps
- The commit hash the panel shows for THIS build

**Step 2** — Content signal check on the actually-built commit
(from Manage Publishes, not `/version`):

```bash
# If the Manage Publishes panel shows commit X was built:
git show X:backend/services/<file>.py | grep -c "<expected-signal>"
```

**Step 3** — Confirm live behavior:
```bash
# Latency + auth-gate integrity:
curl -sw "%{http_code} %{time_total}s\n" -o /dev/null \
  https://auremcto.com/founder-offer/status  # <1s
curl -sw "%{http_code}\n" -o /dev/null \
  https://auremcto.com/api/aurem-dev/admin/observability/breakers  # 401
```

**Step 4** — `/version` cross-check (informational):
```bash
curl -s https://auremcto.com/api/aurem-dev/version
```
- If `commit_sha` matches Manage Publishes: ✅ ideal, `BUILD_INFO.txt`
  traveled to prod as untracked file
- If `commit_sha` differs from Manage Publishes: acceptable — means
  `BUILD_INFO.txt` did NOT travel as an untracked file, and prod
  fell back to `.emergent/emergent.yml` `job_id`. Still no signal
  of pipeline mismatch — trust Manage Publishes.

### 📋 REPORT

Only AFTER Manage Publishes shows the build succeeded and all step-2
and step-3 signals pass, tell the founder "the change landed on prod".
Include the **actually-built SHA from Manage Publishes** in the
report (not the SHA you dispatched, and not the `/version` SHA).

---

## Recovery — when the pipeline shipped a SHA behind your intent

Sometimes the snapshot-at-build-start race means the shipped SHA is
1-2 commits behind your local HEAD (as happened in Incident 3 on
2026-02-12). This is NOT a pipeline defect; it's the ref-resolution
timing.

1. **Verify what actually shipped** via Manage Publishes → Overview.
   Cross-reference the shipped SHA against local git log.
2. **Assess safety**:
   - **Strict SUBSET of intent** (missing your latest commits):
     Usually safe if the missing commits were purely additive. If
     they were a critical fix (like today's middleware fix), the
     fix is not live and needs another dispatch.
   - **Strict SUPERSET of intent** (built commits ahead of yours):
     This can happen if Channel A auto-commits landed between
     dispatch and build-start. Safety depends on the extra commits.
3. **Redispatch procedure** (if fix must land):
   - Wait for in-flight build to complete
   - Verify no other dispatch is queued (Manage Publishes)
   - Confirm your intended commits are still on HEAD
   - Redispatch; expect the same snapshot behavior
4. **If pipeline consistently ships behind HEAD**:
   - Escalate to Emergent Support (support@emergent.sh) with job
     ID `73df9f0d-7149-4a95-89d4-c9972e2b0c6d` + timing details

---

## Historical log

| Date | Incident | Type | Resolution |
|---|---|---|---|
| 2026-02-12 18:44 | Wave 7A "no-op" deploy | Uncommitted changes race | Auto-commit later captured; became Incident 2's precursor |
| 2026-02-12 19:03 | Wave 7B "no-op" deploy | Uncommitted changes race | Same as above; both waves became one commit `9b12d0a` |
| 2026-02-12 19:31 | Wave 7A "recovery" shipped both waves | Pipeline built commit AHEAD of dispatch HEAD | Left as-is — live traffic verified both waves work; working tree re-aligned to prod |
| 2026-02-12 20:21 | Middleware fix dispatch shipped 2 commits behind HEAD | Snapshot-at-build-start race | Emergent Support confirmed fix IS live; `/version` was reporting lagging SHA per BUILD_INFO.txt stamping bug. Fix: BUILD_INFO.txt now untracked + post-commit hook stamps HEAD SHA. Checklist rewritten to remove SHA-pinning assumption. |

---

**Rule of thumb**: If you ever say "the deploy landed" before you've
opened **Manage Publishes → Overview** and confirmed the build
succeeded + noted the actually-built SHA, you're skipping the
checklist. Don't.
