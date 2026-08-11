# Deploy Verification Discipline

**Filed**: 2026-02-12, after 2 back-to-back deploy pipeline races
**Owner**: agent-side; enforced by process, not by the pipeline
**Status**: MANDATORY for every prod deploy going forward

---

## Why this exists

Between 18:14 and 19:31 on 2026-02-12, two prod deploys did NOT
ship the code the agent dispatched:

- **Incident 1 (no-op)**: Agent made local edits, dispatched
  deploy, verified `built_at` moved on prod, declared shipped.
  Reality: the local edits were uncommitted, deploy pipeline
  built the previous commit (survey-only, no code).
- **Incident 2 (pre-revert)**: Agent made edits, reverted a
  wave, committed the revert (`4982cfc`), dispatched. Reality:
  pipeline built commit `9b12d0a` (both waves) instead. The
  live SHA on prod after "landing" was NOT the commit HEAD was
  at dispatch time.

Both incidents happened because verification came AFTER
declaring "deploy landed". By then the mismatch was already
live.

**The fix is procedural, not technical.** The pipeline race is
outside agent control, but agent-side verification timing is
100% inside agent control.

---

## The mandatory checklist

### 🟢 PRE-DISPATCH (before calling `emergent__send_to_deployer`)

```
1. git status --short     → working tree should be clean (or
                             only intended changes staged)
2. git log --oneline -1   → confirm HEAD SHA matches the
                             commit you EXPECT to ship
3. git diff HEAD          → confirm no uncommitted changes
                             that would race the deploy
4. Run the full targeted pytest suite → all green
```

If ANY of the above are wrong: **do not dispatch**. Call
`finish` first (auto-commit runs) or explicitly commit changes,
then re-verify step 2's SHA.

### 🟡 DISPATCH (the `emergent__send_to_deployer` call itself)

Record 3 baselines in the dispatch message:
- Current prod SHA (from `curl .../version`)
- Current prod `built_at` timestamp
- Local git HEAD you EXPECT prod to reach

### 🔴 POST-DISPATCH (immediately, tight polling window)

**Do NOT wait for the deployer's "Deployment completed"
message before verifying.** Poll aggressively:

```bash
# Poll every 10-15 seconds until SHA changes OR built_at moves >5 min forward.
# Record BOTH values in every poll — a built_at move without a
# SHA change is a signal (see Incident 1 pattern).
BASELINE_SHA="<prev>"
for i in {1..20}; do
  sleep 15
  resp=$(curl -s https://auremcto.com/api/aurem-dev/version)
  sha=$(echo $resp | python3 -c "import sys,json; print(json.load(sys.stdin)['commit_sha'])")
  built=$(echo $resp | python3 -c "import sys,json; print(json.load(sys.stdin)['built_at'])")
  echo "poll $i · sha=$sha · built=$built"
  [ "$sha" != "$BASELINE_SHA" ] && break
done
```

### ✅ VERIFICATION (immediately after SHA changes)

Within 60 seconds of the new SHA landing, verify:

1. **SHA matches expectation**: Does prod's new `commit_sha`
   equal the local HEAD SHA you dispatched? If NOT: STOP and
   report to founder.  Do not proceed with more deploys until
   the mismatch is understood.

2. **Content signal**: Pick ONE observable behavior that the
   deployed change should produce. Verify it live.
   - HTTP wrapper migration → check that a migrated endpoint
     still responds (e.g. `/founder-offer/status` for anything
     that touches its call path).
   - Copy/UI change → verify the copy is visible via `curl` of
     the built asset or a headless page-check.
   - New endpoint → curl it and verify it responds.

3. **No latency regression**: 3x curl of the landing page +
   `/founder-offer/status`, confirm all under 1s.

4. **Auth gate integrity**: curl an admin-gated endpoint,
   confirm 401.

### 📋 REPORT

Only AFTER all 4 verification steps pass, tell the founder
"Batch X landed on prod". Include the actual prod SHA (not
the SHA you dispatched) so any mismatch is visible.

---

## Additional discipline — avoid Incident 2's specific race

Incident 2 was caused by the pipeline building a commit ahead
of the one at HEAD at dispatch time. Mitigation:

- **Never dispatch a "revert" deploy without a preceding
  finish-and-commit** that lands the revert as HEAD FIRST.
  Verify `git log --oneline -1` shows the revert commit BEFORE
  dispatching.
- **If a snapshot-and-revert is in play** (like Wave 7B), take
  the `/tmp/*_snapshot/` copies + `sha256sum` receipts BEFORE
  touching working tree.
- **After post-dispatch SHA lands**, verify the deployed
  commit's content matches what you intended to ship:

  ```bash
  # After prod shows new SHA "X", verify X contains the change
  # you meant to ship (or DOESN'T contain the change you meant
  # to hold back):
  git show X:backend/services/<file.py> | grep -c "ext_client"
  # (or whatever signal is diagnostic for the intent)
  ```

  This catches the "pipeline built the wrong ref" race
  within 60s of it landing, not in a status report later.

---

## What to do if the pipeline shipped the wrong thing

1. **Do NOT immediately try to "fix" with another deploy**.
   Another deploy could hit the same race.
2. **Verify what actually got shipped** — inspect the deployed
   commit's file contents (`git show <sha>:<path>`) against the
   intent.
3. **Assess safety of the deployed state**:
   - Is it a strict SUPERSET of what you intended? (like
     Incident 2 — both waves live instead of just 7A). Usually
     safe to leave.
   - Is it a strict SUBSET (like Incident 1 — nothing shipped)?
     Safe to leave, but the delivery is unmet.
   - Is it DIFFERENT (deployed something you didn't mean to
     ship, e.g. an old buggy commit)? URGENT — coordinate with
     founder on rollback strategy.
4. **Only after founder acknowledges the situation** should
   further deploys proceed. Otherwise you compound the race.
5. **Always align local working tree to prod** after such an
   incident, so a future accidental deploy doesn't roll back
   what's already live.

---

## Historical log

| Date | Incident | Type | Resolution |
|---|---|---|---|
| 2026-02-12 18:44 | Wave 7A "no-op" deploy | Uncommitted changes race | Auto-commit later captured; became Incident 2's precursor |
| 2026-02-12 19:03 | Wave 7B "no-op" deploy | Uncommitted changes race | Same as above; both waves became one commit `9b12d0a` |
| 2026-02-12 19:31 | Wave 7A "recovery" shipped both waves | Pipeline built wrong ref | Left as-is — live traffic verified both waves work; working tree re-aligned to prod |

---

**Rule of thumb**: If you ever say "the deploy landed" before
you've curled `/version` and confirmed the SHA matches what
you expected, you're skipping the checklist. Don't.
