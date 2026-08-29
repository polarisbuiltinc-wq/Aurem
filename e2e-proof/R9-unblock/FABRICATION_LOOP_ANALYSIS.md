# P2 — Fabrication Learning Loop, recurring pattern analysis (2026-08-30)

Agent-analyzed, NOT founder-confirmed. Read-only — no code changed.

## 1. Confirmed: the infra exists
- **Write side**: `services/ora_fix_learning.py::record_fabrication_incident()`
  — called from `routers/chat.py:3446` (fire-and-forget `asyncio.create_task`)
  whenever the CitationGuard corrects a model reply that cited an
  unverified file path. Persists to Mongo collection
  `ora_fabrication_incidents`: `{incident_id, source, project_id,
  route, user_id, user_prompt, unverified_paths, signature, corrected,
  created_at}`. `signature` = the sorted, joined unverified paths —
  the de-dup/grouping key.
- **Read/aggregate side**: `services/ora_fix_learning.py::get_recurring_fabrication_patterns()`
  — groups by `(source, project_id, route, signature)`, last 30 days,
  exposed at `GET /admin/qa/fabrication-patterns` (`routers/admin_qa.py:270`).
- **Self-correction side**: `services/ora_fix_learning.py::recall_fabrication_caution()`
  — if a `(source, project_id, route)` scope crossed 3 incidents in
  30 days, returns a compact caution string; wired into the LIVE
  prompt pipeline at `services/orchestrator.py:1893-1903`, injected
  silently (never surfaced to the user) before every `customer_chat` +
  `chat_stream` turn. Fail-open on any error.

## 2. Live data (this pod, 2026-08-30) — with an honest correction
Live query: **31 unique signatures** in the trailing 30 days (the
founder's figure of 34 may reflect a slightly earlier moment or a
different window edge — reporting today's actual number, not
adjusting to match). **1 signature is recurring (count >= 3)**:

```
signature:    services/billing/definitely_fake_invoice_engine.py
source:       customer_chat
project_id:   home
route:        chat_stream
count:        96
corrected:    96/96 (100%)
sample_prompt: "who handles billing?"
```

**Important finding — this is a TEST ARTIFACT, not organic traffic.**
The specific incident doc's `user_id` is `"test-customer-1"`, and the
filename `definitely_fake_invoice_engine.py` is a literal string
planted by `tests/test_citation_guard_persist_ordering.py` (also
present in `ora_training_data/latest.jsonl`, a synthetic eval set).
Every time that pytest file runs in this pod, it re-seeds an identical
incident — the count of 96 reflects ~96 dev-cycle test runs across
this pod's history, not 96 real customer conversations asking about
billing.

**Everything else in the 31 signatures is also test/dev-session
traffic in this Preview pod**, not paying-customer data: the next
highest signatures (`src/components/userlist.tsx`,
`src/components/dashboard.tsx`, `backend/auth.py`, `test.js` — all at
count=2, below the recurring threshold) trace to `user_id:
"test_admin_001"` (this pod's own demo/QA account, used throughout
prior agent-testing rounds) or one hashed admin-chat test session with
Hinglish QA prompts ("kya tum sure ho ye files real exist karti
hain?"). This Preview pod does not appear to carry distinguishable
real external-customer fabrication data at all right now.

## 3. Classification
**SAME class as the M2 fence-miss** — structurally identical
mechanism: the model asserts a specific file's identity/existence
with unwarranted confidence, and CitationGuard is the exact
grounding-check built to catch that class (confident-but-wrong
file-path citation). The `definitely_fake_invoice_engine.py` incident
is CitationGuard correctly catching an intentionally-planted test
fabrication, every single time (100% corrected) — which is actually
a **positive signal that the guard itself works**, not a sign of an
uncaught product bug.

## 4. Pin-ability
**Not pin-able this round — and a static per-filename pin would be
the wrong fix even if it were.** A hardcoded system-prompt line
telling the model "the file is not `definitely_fake_invoice_engine.py`"
would only ever matter for this one test fixture string; it teaches
nothing generalizable and would never fire in real usage. Two
separate, real findings instead:

1. **The self-correction mechanism for this exact class ALREADY
   EXISTS, is ALREADY WIRED, and is ALREADY ACTIVE** —
   `recall_fabrication_caution()` is a dynamic, data-driven
   equivalent of a "pin": once any `(source, project_id, route)` scope
   crosses 3 incidents/30d, it injects a corrective caution
   automatically, no manual prompt edit needed, self-updating as new
   patterns emerge. For `home`/`chat_stream`/`customer_chat` (the one
   currently over threshold), this caution IS firing on every turn
   right now. Nothing to propose here — it's already doing the
   PRODUCT_IDENTITY-style job, generically.
2. **A real, unproposed-fix-needed gap**: `recall_fabrication_caution()`'s
   call site is HARDCODED to `source="customer_chat", route="chat_stream"`
   only (`orchestrator.py:1897-1898`). The `admin_ora_chat`/`general`
   route (where the `test.js` incident above actually occurred) is
   LOGGED on the write side but has NO caution recall wired for it at
   all — an asymmetric gap. This is a real, generalizable fix
   candidate for a FUTURE round (wire the same recall call into
   whichever prompt-assembly path serves `admin_ora_chat`, with its
   own `source`/`route` values) — **not proposed as a concrete pin
   line this round**, since it's a wiring gap, not a pinnable prompt
   line, and needs its own scoping/verification pass.
3. Separately, worth flagging (not fixing): this pod's fabrication
   data is currently indistinguishable from test noise. A future
   data-hygiene pass (excluding known test/demo `user_id`s such as
   `test_admin_001`/`test-customer-1` from the admin dashboard's
   counts, or tagging test-seeded incidents) would make the "34
   signatures / 1 recurring" figure the founder sees actually
   reflect real customer behavior once real traffic exists.

## Output (per the requested format)
**Recurring pattern: `services/billing/definitely_fake_invoice_engine.py`
(customer_chat/home/chat_stream, 96/96 corrected). Class: SAME as the
M2 fence-miss (confident false file-path citation). Proposed pin: NOT
APPLICABLE — this instance is a self-seeding pytest fixture, not
organic traffic, and the generic self-correction mechanism for this
class (`recall_fabrication_caution`) is already live and already
firing for this exact scope. The one real gap found is a wiring
asymmetry (admin_ora_chat/general has no caution recall at all) —
flagged for a future round, not a pin, and not applied this round.**
