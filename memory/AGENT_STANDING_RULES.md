# AGENT STANDING RULES

_Non-negotiable rules the E1 agent must follow across every session,
established after the founder repeatedly caught the same class of bug
("preview-verified" claims being falsified by real prod data)._

Language: **Hinglish** (founder's preference).

---

## Rule 1 · NO HALLUCINATION (already in effect)

Never claim a bug is `fixed`, `done`, `verified`, or `working` without
**real execution evidence** attached in the same turn.

**Definition of evidence** (must be one of):
- HTTP status + response body from a curl to the exact endpoint
- Pytest / vitest run output showing the test in question passing
- Screenshot of the actual UI state
- Direct DB query result

"Code inspection" alone is NOT evidence. "The logic reads correct" is
NOT evidence.

---

## Rule 2 · "preview-verified" vs "prod-verified" — separate labels

_Added 2026-02-14 after the #35 Admin Payments false-claim._

The preview environment (`REACT_APP_BACKEND_URL` in `frontend/.env`) and
production (`https://auremcto.com`) share **the same codebase after
deploy** but **DIFFERENT MongoDB databases**. A curl against preview
proves "the code is correct against the preview dataset" — it does NOT
prove "the same output will appear in prod".

Every claim MUST be tagged with which environment produced the
evidence:

| Label                | Meaning                                              |
|----------------------|------------------------------------------------------|
| `preview-verified`   | curl / test passed on preview only. Prod unknown.    |
| `prod-verified`      | curl / test passed against `https://auremcto.com`.   |
| `founder-confirmed`  | Founder reported success from real prod UI.          |

**Mandatory rules:**
1. Never write "verified" without a prefix (preview- / prod- / founder-).
2. If a claim depends on **data** (revenue, counts, aggregations),
   preview evidence is INSUFFICIENT — prod-verified or
   founder-confirmed is required before saying "fixed".
3. Public-endpoint fixes (health, SEO pages, static assets) can be
   `prod-verified` via curl.
4. Admin-endpoint fixes require `founder-confirmed` (main agent has
   no prod admin JWT).

---

## Rule 3 · Pre-deploy integration_health surface

_Added 2026-02-14 after the OpenRouter $0.20 surprise._

Before requesting a production deploy, the agent MUST run:

```bash
python3 /app/scripts/predeploy_integration_health.py
```

This is Lane 6 of `predeploy_gate.sh`. It reads the last cached
`integration_health.latest` snapshot from Mongo and surfaces any probe
in `warn` or `broken` state.

**Behaviour:**
- Exit 0 — clean, proceed.
- Exit 2 — WARN (e.g. low balance). Surface to founder in the deploy
  request message.
- Exit 3 — BROKEN. Surface to founder AND explicitly ask "proceed
  anyway?" before dispatching `emergent__send_to_deployer`.

Never dispatch a deploy silently when an integration is critical/broken.

---

## Rule 4 · Data-shape claims need real data

Any claim about "how many rows / how much revenue / what the count is"
must be backed by an aggregation against **the actual environment**
being claimed (preview OR prod). Copy-pasting the preview number into a
prod claim is a Rule 1 violation.

---

## Recurrence log

| Date         | Rule broken | Symptom                                                   |
|--------------|-------------|-----------------------------------------------------------|
| Bug #20 (~) | Rule 1      | Fix claimed "deployed" 3× — was never on prod             |
| #35 (2026-02-14) | Rule 2 | Preview curl (revenue=$9) claimed as "prod truth" ($0)   |

If a third violation appears in the log, the founder has authorised
the "stop shipping, do a systemic pipeline audit" protocol.
