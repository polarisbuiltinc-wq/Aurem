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

### ✅ VERIFICATION (immediately after SHA changes) — HARD STOP if mismatch

**This is a MANDATORY PERMANENT STEP, not situational judgment.**
Every deploy dispatch MUST be followed by SHA verification
against the expected commit. Any mismatch is a HARD STOP —
no follow-up deploys, no "recovery" dispatches, no assumptions
that the mismatch is safe just because prod happens to be
responsive. Stop and report to founder.

**Rationale**: On 2026-02-12 the pipeline shipped a superset
commit (`9b12d0a` w/ both Wave 7A + 7B) when the dispatch was
from HEAD `4982cfc` (7A-only revert). Prod was healthy —
purely by luck — because the deployed code had already been
tested. If the deployed code had contained a bug the reverted
wave was hiding, we would not have known until the bug shipped.
Verification-by-luck is not verification. Every deploy must
confirm SHA-in = SHA-out, and any mismatch stops the train.

**Steps (in this order, no skipping):**

1. **Poll until prod SHA changes** (from the pre-dispatch
   baseline). Do NOT rely on the deployer's "Deployment
   completed" message — that comes AFTER the mismatch is
   already live. Aggressive poll every ~15s:

   ```bash
   BASELINE_SHA="$prev_prod_sha"
   EXPECTED_SHA="$(cd /app && git rev-parse HEAD)"
   for i in {1..25}; do
     sleep 15
     sha=$(curl -s https://auremcto.com/api/aurem-dev/version | \
           python3 -c "import sys,json;print(json.load(sys.stdin)['commit_sha'])")
     echo "poll $i sha=$sha"
     [ "$sha" != "$BASELINE_SHA" ] && break
   done
   ```

2. **HARD STOP CHECK — SHA in vs SHA out.** Compare the new
   prod SHA against the SHA you dispatched (local HEAD at
   dispatch time). Prod reports 12-char prefix; local HEAD is
   full — compare on prefix.

   ```bash
   PROD_SHA=$sha  # from poll above
   EXPECTED_PREFIX=${EXPECTED_SHA:0:12}
   if [ "$PROD_SHA" != "$EXPECTED_PREFIX" ]; then
     echo "🛑 HARD STOP: dispatched HEAD $EXPECTED_PREFIX but prod shows $PROD_SHA"
     echo "   Do NOT dispatch another deploy."
     echo "   Do NOT assume the mismatch is safe."
     echo "   Report to founder immediately with:"
     echo "     - what was dispatched (SHA + summary)"
     echo "     - what actually landed (SHA + git show <sha> --stat)"
     echo "     - live prod signals (curl /version, /founder-offer/status)"
     exit 1  # or equivalent — do not proceed to next step
   fi
   ```

   **If the SHAs match**: proceed to content signal check.
   **If they DO NOT match**: STOP. Report. Wait for founder.

3. **Content signal check** — pick ONE observable behavior
   the deployed change should produce, verify it live.
   Examples:
   - HTTP wrapper migration → `git show <prod_sha>:backend/services/<file>.py | grep -c ext_client` should equal expected count
   - Copy/UI change → curl asset, verify string present
   - New endpoint → curl it, verify it responds

4. **Latency + auth-gate integrity**:
   - 3x curl of landing + `/founder-offer/status`, all under 1s
   - curl an admin-gated endpoint, confirm 401

### 📋 REPORT

Only AFTER **all 4 verification steps pass, including the
SHA hard-stop check**, tell the founder "Batch X landed on
prod". Include the **actually-deployed SHA** in the report
(not the SHA you dispatched) so mismatches are visible in
history.

**If step 2 (SHA hard stop) triggered**, the report is:
"⚠️ deploy pipeline mismatch — dispatched X, prod shows Y,
hard-stopped per checklist, awaiting founder direction."

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
