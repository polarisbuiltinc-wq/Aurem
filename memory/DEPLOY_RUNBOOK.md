# AUREM — Deployment Runbook

**Standing rule** (added 2026-02-09 · Session 4 · founder-issued as ads go live).
**Applies to**: every prod deploy from this point forward, including hotfixes.
**Non-negotiable**: rule 5 (rollback beats live-debug) and rule 6 (watch the ship step, not just the summary).

---

## The 6-step deploy gate

Every prod deploy MUST pass these six gates, in order. Skipping any = protocol violation.

### 1. Deploy window (traffic-aware)
- Chosen window: **TBD — founder input required** (see "Open Inputs" below)
- Convention: pick 2-5 AM in the majority-traffic timezone, NOT the operator's timezone
- Exception: rollback of a broken deploy (rule 5) — no window restriction, must ship the second the break is confirmed
- If ad campaign targets multiple geographies, use the largest-cohort's 2-5 AM

### 2. Pre-deploy: preview must be green
Automated pre-deploy check:
```bash
# Backend tests (existing)
cd /app/backend && python3 -m pytest tests/ -q --timeout=60

# Preview smoke test (curl the critical endpoints)
API=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d= -f2)
for path in /api/health /api/healthz /api/aurem-dev/admin/backups/status; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$API$path")
  echo "  $path → $code"
  # /api/health should be 200, /admin/* should be 401 (auth-gated, alive)
done

# Frontend lint (repo already has ESLint pinned to v8)
cd /app/frontend && yarn lint --max-warnings=0 || echo "frontend lint failed"
```
Any test failure or unexpected HTTP code → **do NOT deploy**.

### 3. Health-check-gated rollout (post-deploy verification)
Every deploy must be verified via `/api/health` before being declared live:
```bash
curl -s https://auremcto.com/api/health | python3 -c "
import sys, json
r = json.load(sys.stdin)
# Gate 3a: uptime_s low = fresh boot happened (not stale response)
assert r['uptime_s'] < 300, f'uptime {r[\"uptime_s\"]}s — deploy may not have restarted the pod'
# Gate 3b: no dead supervised tasks
dead = r['supervised_tasks'].get('dead', [])
assert not dead, f'dead supervised tasks: {dead}'
# Gate 3c: DB connected
assert r['db'] is True, 'DB not connected'
# Gate 3d: backup tools present (added 2026-02-09)
bt = r.get('backup_tools', {})
if bt:
    for name, info in bt.items():
        assert info is not None, f'{name} NOT FOUND on prod — deploy has regressed the backup tooling'
print('all 4 health gates passed')
"
```
Any assertion failure → treat as failed deploy, roll back per rule 5.

### 4. Rollback plan — pre-verified BEFORE deploy
- Mechanism: **Emergent deployer platform "Rollback" button** (per TC-09 test result from earlier session: "Rollback finished, 6 steps, all passed")
- **TBD — founder input required**: confirm this button is still present + functional in the current dashboard state, and give me the exact click path so I can document it here
- Rollback ETA: <2 min per TC-09
- Rollback smoke-test cadence: **quarterly minimum**, and before any campaign that pushes ads traffic above normal baseline

### 5. On 5xx during live traffic → rollback FIRST, debug in preview LATER
Non-negotiable behavioural rule for the agent:
- Never run a "quick fix" against prod while real users are hitting 5xx
- Trigger rollback the moment 5xx is confirmed on `/api/health` or in Sentry
- Once rolled back, reproduce the failure on preview, fix, run gates 1-4, redeploy
- The cost of an extra deploy cycle is 5 min; the cost of prolonged 5xx during ad-traffic is dollars of ad spend serving broken pages

### 6. Monitor the ship step itself (not just the "completed" summary)
Historical context: TC-10 has shown "Ship failed" scenarios where the deployer's summary said complete but the actual ship step silently failed partway.
Enforcement: after every deploy completion signal from the deployer, before declaring "done":
- Verify `uptime_s < 300` on `/api/health` (fresh boot)
- Verify `build_hash` or endpoint 404→401 signals that the intended code change actually landed (per the "endpoint discovery" pattern we used tonight)
- Read the deployer's build log for any warnings/errors — not just the ✅ symbol at the end

---

## What's programmatically enforceable RIGHT NOW

| Gate | Status | Notes |
|---|---|---|
| 1 (window) | ❌ needs founder input | Window not yet declared |
| 2 (preview green) | ✅ pytest exists, curl smoke test scripted above | Can wrap in a `pre_deploy_check.sh` script |
| 3 (health gates) | ✅ scripted above, all 4 sub-gates use existing endpoints | Includes new `backup_tools` diagnostic added tonight |
| 4 (rollback verified) | ⚠️ TC-09 passed once, no recent re-verification | Needs test-drill before next ad-spend cycle |
| 5 (rollback on 5xx) | ✅ behavioural — agent commits to it, logged here | No code change |
| 6 (ship-step monitoring) | ✅ pattern established tonight (uptime + endpoint-discovery) | Codify into post_deploy_check.sh |

---

## Open Inputs from Founder (blockers to campaign go-live)

1. **Deploy window** — What timezone/hours? Meta ads targeting which geography as primary?
2. **Rollback drill** — Should we do one dry-run rollback (roll back last deploy, wait 30 sec, roll forward) to prove the mechanism still works before ad-spend ramps? Would be ~4 min of prod downtime in the chosen window.
3. **Alerting on 5xx** — Currently Sentry catches exceptions. Do we also want an uptime-monitor pinger (item #21 in ledger, still awaiting your provider pick) firing pager alerts to your phone/email on `/api/health` failure? This IS the "immediate rollback" prerequisite.
4. **Confirm process ownership** — is the agent authorized to trigger rollback on its own if 5xx is detected during ad hours, or does it always wait for founder go-ahead? Rule 5's spirit implies auto-rollback is acceptable given the ad-spend risk, but you should say yes/no explicitly.

---

## Platform reality (support-agent confirmed 2026-02-09)

- **No blue/green / zero-downtime**: every deploy has a **30-60 sec stop-then-start window**. Health-gate curl must include sleep + retry loop, not fire immediately.
- **No canary / rolling / drain**: single-pod cutover only.
- **Rollback path (verified)**: Deployer dashboard → Home → deployed apps → **Rollback** button. Also available via chat clock-icon. No cost.
- **No deploy-failure webhook**: only manual dashboard checking or /api/health polling. Agent must actively verify after every deploy, no notification will arrive.

**Practical impact for ad traffic**: expect 30-60 sec of 503s per deploy. Every prod deploy = burning that many seconds of ad-spend on failed page loads. Minimize deploy frequency. Batch changes.

---

**Last updated**: 2026-02-09
**Owner**: agent + founder (dual sign-off on any deploy that crosses these gates)
