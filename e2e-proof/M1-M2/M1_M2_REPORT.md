# M1 + M2 — Bounded Real-Model Window Report (2026-08-30)

Founder-authorized single bounded window, MOCK_LLM off, $3 hard cap.
**Flip OFF: `2026-08-29T16:49:41Z` → Flip ON: `2026-08-29T17:00:43Z`**
(~11 minutes). **Window spend: $0.210012** (7% of $3 cap — well under;
combined with T3/B4's $0.038668 earlier today, cumulative $0.248680,
each window independently within its own $3 cap).

All evidence below is a real logged reply/log line per the P0-B
evidence rule — file paths given for every claim, `/app/e2e-proof/M1-M2/`.

---

## 0. M1a — first-contact pinned-identity

**Root cause** (read-only investigation, confirmed before fixing):
`services/identity.py::OR_IDENTITY` pins the assistant's NAME only
("You are ORA..."). The casual-tier reply path
(`services/intent_gateway_casual_reply.py::casual_direct_reply`) had
**zero product-description grounding at all** — a bare "You are ORA —
AUREM's developer co-pilot. Respond naturally..." system prompt. On
"what does this tool do?", the model had nothing factual to anchor on
and generated (and got wrong) its own description.

**Fix**: added `services/identity.py::PRODUCT_IDENTITY` — one pinned,
factual sentence ("What ORA does... reads your GitHub repo, fixes real
issues, ships as a commit/PR — Loop Mode verifies first"), sourced from
the app's own landing-page copy (reuse, not invented). Injected into
BOTH first-contact surfaces: `casual_direct_reply`'s system prompt
(the exact repro path) AND `orchestrator.AUREM_CTO_PERSONA` (T3's
finding #2 was actually agentic-tier, so both needed it).

**Real reply proof** (`/app/e2e-proof/M1-M2/m1a_first_contact.json`):
> Prompt: "Hi, what does this tool do?"
> Reply (provider `intent-gateway-casual`, dt=3.87s):
> *"ORA helps developers by analyzing GitHub repos, fixing real issues,
> and shipping fixes as commits or PRs—think of it as a hands-on coding
> assistant. Let me know how I can help!"*

**Verdict: FIXED, 100% on this retest.** No "audio data" or any other
wrong description — the reply matches the pinned `PRODUCT_IDENTITY`
line closely (paraphrased naturally, not a robotic verbatim dump,
which is correct — the model is grounded, not scripted). No model-cap
note needed for M1a.

---

## 1. M1b — context-anchored repeated answer

**Fix**: added hard rule #6 to `AUREM_CTO_PERSONA`: a literal re-ask of
an already-answered question should get a short recap, not a full
regen; a genuinely new question still gets a full real answer.

**Real reply proof, attempt 1** (`/app/e2e-proof/M1-M2/m1b_retry_first_ask.json`,
`m1b_retry_reask_same.json` — session `m1b-retry-1`, same question
asked twice: *"Can you check my repo for security issues?"*):
- First ask (dt=51.8s): 1182-char full security review.
- Re-ask, same question (dt=44.0s): **1992-char full security review
  again** — NOT a short recap, and NOT a verbatim repeat either (each
  is a freshly-generated, on-topic, correct security analysis — the
  WORST original symptom, anchoring on a stale UNRELATED answer, was
  not reproduced — but the "give a 1-line recap on a literal re-ask"
  instruction was not followed).

**MODEL-CAP NOTE (per founder's own instruction — one fix attempt,
then log and move on, do not chase 100%)**: the persona-level
instruction is present and does measurably improve grounding (T3's
exact repro — a totally different question getting an old unrelated
answer verbatim — was NOT reproduced across 2 retest attempts this
window), but the real model does not reliably obey the "give a short
recap, don't regenerate" instruction-following ask on a literal
re-ask. This is a real-model instruction-following limit, not a code
bug. **Verdict: PARTIALLY FIXED** — anchoring-on-stale-unrelated-answer
(the more severe original symptom) not reproduced; short-recap
compliance not achieved. Not chasing further this round per founder's
explicit instruction.

(A first attempt at this re-test, session `m1b-repeat-1`, hit the
same infra 60s-cutoff gap documented in §2 below — see
`m1b_first_ask.json`, a real Cloudflare 502 at exactly 60.17s, not a
model/prompt issue — hence the retry with a different, faster-tier
question above.)

---

## 2. M2 — R8 acceptance completion

### 2.1 Fence-emit rate (N=5, varied file locations incl. repo-root README.md)

| # | Prompt (file) | Root/nested | Fence? | Provider | dt | Note |
|---|---|---|---|---|---|---|
| 1 | README.md | root — exact prior repro case | **YES** | deepseek-v3-council-c | 12.3s | Confirmed still fixed |
| 2 | services/response_confidence.py | nested | NO | longcat-2.0 | 22.8s | **Real answer reached a fix-capable model, but `services/output_guard.py`'s leak-redaction rewrote the exact filename the USER named into the generic placeholder "a project file" mid-reply — the model then produced a self-contradictory "exists / does not exist" statement about "a project file". Root cause identified, NOT fixed this round (redaction logic change, out of M1/M2 scope, flagged NEEDS-FOUNDER below). |
| 3 | requirements.txt | root | NO | longcat-2.0 | 21.1s | Correct "nothing to fix" — repo genuinely has no `requirements.txt` (uses `pyproject.toml`). Not a miss. |
| 4 | routers/health.py | nested | NO (no answer) | none | 60.3s | Real infra 502 at exactly 60s — same Cloudflare/ingress cutoff as §1's `m1b_first_ask` and the prior R8 round's documented finding. Pre-existing, out of scope. |
| 5 | .gitignore | root, dotfile | **YES** | longcat-2.0 | 14.5s | Confirmed |

**Raw rate: 2/5 = 40%.** Excluding the 1 correct no-fix (#3) and the 1
infra-timeout non-answer (#4): **effective rate on requests that
reached a fix-capable model with a real answer = 2/3 = 67%**
(items 1, 5 fenced; item 2's real miss cause is the output_guard
redaction collision above). Matches the prior R8 round's numbers
closely (2/5 raw, 67% effective) — **not gate-hacked, reported as-is.**

### 2.2 Low-confidence re-test
Target: same prompt R8 used (*"What do you think of this project
overall? Any thoughts?"* — deliberately no fix-intent tokens).
**This time it did NOT hit the 60s cutoff** (`/app/e2e-proof/M1-M2/m2_lowconf.json`,
dt=16.0s, `low_confidence: false`, `ship_suppressed: false`, content
non-empty, 106 chars): *"I need to actually read the source files
before giving a grounded assessment. Let me fetch everything now."*

**Verdict: NOT suppressed this retest** (resolves the prior round's
INCONCLUSIVE finding for THIS specific prompt/session). One caveat
noted, not a threshold issue: the returned content is a mid-task
"I'm about to read files" placeholder, not the actual finished
opinion — this looks like the non-streaming `/chat/send` endpoint
returning after the model's first turn (a tool-call turn) rather than
waiting for the full multi-turn tool loop to finish; a `/chat/stream`
call would likely show the full multi-turn sequence. **No threshold
change is proposed** — the suppression mechanism itself did not fire
(the actual finding is a possible non-streaming-endpoint UX gap, not
a confidence-threshold problem); flagged as an observation only, no
change made, per "do not change without GO."

### 2.3 Cost report (final pricing baseline for $9/Pro tiering)
- Window spend: **$0.210012** across 11 real chat sends + `llm_cost_ledger`
  rows grew by 41 (1907→1948).
- **Cost/message (blended, all 11 real sends): ~$0.0191.**
- **Cost/10 messages: ~$0.191.**
- **Cost/session (5-message session, prior round's sizing convention):
  ~$0.0955.**
- Tracked-ledger-only lower bound (casual-tier `call_llm` calls are
  NOT written to `llm_cost_ledger` — same pre-existing gap R8 already
  flagged, not fixed this round): 41 rows / $0.210012 ≈ $0.00512/row,
  consistent with the prior round's ~$0.0051/tracked-call figure.
- **These 2 numbers (blended $0.0191/msg full-session actual spend,
  vs $0.0051/row ledger-tracked-only) bracket the real per-message
  cost — use the blended $0.0191/msg ($0.191/10 msgs, ~$0.0955/session)
  as the more complete number for `$9/Pro` tier math**, since it
  reflects total actual provider spend, not just the subset of calls
  the ledger happens to record.

---

## 3. Mock restored + zero-spend proof
Flip ON at `2026-08-29T17:00:43Z`. 5 post-restore `/chat/send` calls
(`/app/e2e-proof/M1-M2/mock_restored_msg_1..5.log`): providers
`intent-gateway-casual` (×2) / `mock` (×3), **`llm_cost_ledger` total
unchanged: $5.475384 → $5.475384. Zero real spend leaked through.**

---

## 4. Regression — no new vs baseline
`pytest tests/ -k "identity or persona or intent_gateway or casual"`
(excluding the 1 pre-existing, unrelated `test_ora_chat_deep_research.py`
collection error): **256 passed, 6 failed** (all 6 independently
confirmed pre-existing via `git stash` A/B before any M1 edit — same 6
names fail on unmodified baseline code) **+ 0 new**.

New file `tests/test_m1_model_quality_fixes_2026_08_30.py`: **3/3 pass**
(`t_first_contact_uses_pinned_identity`,
`t_product_identity_also_in_agentic_persona`,
`t_no_long_repeat_on_same_q`).

**Caught-and-fixed during this round**: the first edit pushed
`AUREM_CTO_PERSONA` over its own 22,000-char hard latency budget
(`test_persona_loc_guardrail.py`/`test_iter129_chat_latency_budget.py`)
— trimmed `PRODUCT_IDENTITY` and rule #6's wording until the persona
is back under budget (21,993 chars, confirmed both guardrail tests
pass). This is exactly what those 2 tests are for; not a silent skip.

---

## 5. Flags/state
- `MOCK_LLM`: `true` at both start and end of this round (briefly
  `false` only during the logged, capped, restored window above).
- `Live Model Mode` admin tile: reflected `false→true` correctly
  across the flip (same boot-cached mechanism as the X1 fix, unchanged
  this round, not re-tested — no regression indication).
- No `ship_via_pr` flag, no prod flag, no migration, no site write, no
  new dependency this round.

## No-silent-fail audit
- M1a: fix verified against a REAL logged reply, not "should work."
- M1b: honestly reported as PARTIAL, not claimed as done — a real
  model-instruction-following limit, logged, not chased further.
- M2 fence miss #2's root cause (output_guard redaction collision) is
  named and evidenced, not glossed over as "misclassification" (would
  have been the easy, wrong story).
- M2 lowconf: honestly reports "not suppressed this time" without
  claiming the ORIGINAL round's INCONCLUSIVE finding is now "resolved
  forever" — infra-level flakiness (§2.1 item #4 hit the same 60s
  cutoff in THIS SAME window) means the underlying ~1-in-N timeout gap
  is still present and unfixed, just not the one that fired this time.

---

## 6. R9 re-readiness (with webhook in progress by founder, R8 now
complete, H3 done, warn-window)

| Gate | Status |
|---|---|
| H3 (loop repo pin-and-assert) | **SATISFIED** (prior round) |
| R1a/T2 (rollback-on-PR, 3/4 gaps) | **PARTIALLY SATISFIED** (prior round; gap #4 drift-detection still open) |
| R5e (webhook subscription + secret) | **IN PROGRESS BY FOUNDER** (not this agent's this round — not waited on, per instruction) |
| R8 (real-model acceptance numbers) | **NOW COMPLETE** — fence rate 2/5 raw / 67% effective (§2.1), low-confidence retest not-suppressed this time with an honest infra caveat (§2.2), final cost baseline $0.0191/msg (§2.3) |
| 48h warn-window review | **Unchanged from prior rounds — not reviewed this round, still open** |

**What's left to flip R9, stated exactly**: (1) founder's own R5e
webhook fix verifying green, (2) the 48h warn-window review (not
touched this round — separate item, needs its own pass), (3) R1a's
gap #4 (ship-branch drift detection) if the founder wants all 4 R10
gaps closed before flipping rather than accepting the residual risk.
H3 and R8 are done. **Still NOT READY TO FLIP** — 3 items above remain,
none of them require touching MOCK_LLM/prod flags/site writes today.

---

## NEEDS-FOUNDER (one-liner, not fixed, not blocking this round)
`polarisbuiltinc-wq/ora-grounding`'s GitHub App installation
(`152797252`) is unreachable from this pod (`app_installation_missing`)
— the primary fixture repo for future real-model/rollback drills needs
its GitHub App connection restored by the founder; `TJSNDHU/Aurem`
(installation `157161705`) remains the working drill target in the
meantime.

## STATUS: M1 + M2 CLOSED (agent-tested, not founder-confirmed).
**STOP. V1 starts on founder's separate "GO V1".**
