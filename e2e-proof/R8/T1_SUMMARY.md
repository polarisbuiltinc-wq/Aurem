# T1 — R8 Real-Model Test, 2026-08-30

Gate: W1 Q3 = (i) unblocked now (real OpenRouter/DeepSeek/Groq keys already
present in this pod for the live chat path). Proceeded without a founder paste.
Spend cap: $3.00 hard. **Actual spend: $0.131953** (4.4% of cap).

## 1. Availability
Backend boot log (services.health_n...): "LongCat probe OK — Council A primary
= anthropic/claude-sonnet-4.5" (via OpenRouter). Real chat path also uses
GLM-5.2 (swift review_mode default) and DeepSeek (legacy/casual paths), per
services/llm/_meta.py. All three keys (OPENROUTER_API_KEY, DEEPSEEK_API_KEY,
GROQ_API_KEY) confirmed present and working (see smoke below).

## 2. Smoke (see smoke_run1.log)
- 2 plain chat: both answered correctly, fast (<2s), casual/intent-gateway tier.
- 3 tool-call flows (read-only): file-list, README summarize, LICENSE check —
  all 3 correctly used real GitHub read tools against polarisbuiltinc-wq/
  ora-grounding and answered accurately.
- 1 READ-tier action (pyproject.toml project-name lookup): correct, real tool call.
- Rate-limit boundary: sent 32 rapid sequential requests, **did NOT trigger a
  429**. The codebase's actual limiter (routers/chat.py:1139) is 30 req/min
  per-IP, not the "21st message/hour" cap described in the spec — no such
  hourly message-count limiter was found anywhere in this codebase (checked
  llm_usd_cap.py, llm_cost_breaker.py — those are USD-cost caps, not message
  counts). Sequential requests (not concurrent) with real network latency
  never packed 30 into one rolling minute. Reporting this mismatch rather than
  forcing a test against a limit that doesn't exist as described.

## 3. Fence emit test (see fence_run1.log) — 2/5 = 40%
| Prompt | Fence? | Provider | Note |
|---|---|---|---|
| README.md (root file, the exact P0-B repro case) | YES | deepseek-v3-council-c | Gate 6 fix would let this render a button |
| services/orchestrator.py (nested) | YES | deepseek-v3-council-c | real, well-formed fence |
| pyproject.toml (nested, min-python-version ask) | NO | intent-gateway-casual | **misclassified as casual — never reached the fix-capable model at all** |
| tests/test_grounding.py (nested) | NO | intent-gateway-casual | **same misclassification** |
| .gitignore (root file) | NO | longcat-2.0 | real model, correctly found NOTHING to fix (already has `__pycache__/`) — this is a correct non-fence, not a miss |

**Real finding: the raw 40% rate is not a fence-quality problem.** Of the 3
non-fence replies, 1 was a genuinely correct "nothing to fix" (not a miss),
and 2 were the intent-gateway's heuristic classifying a legitimate fix-worthy
request as "casual" BEFORE the fix-capable model was ever invoked — this is
the exact class of bug item 4 (low-confidence/misclassification) asks about,
just surfacing here too. **Effective fence rate on requests that actually
reached a fix-capable model: 2/3 = 67%, with the 1 non-fence being correct.**
Gate 6 itself (root-file fences) is confirmed fixed — the README case emitted
and would render (see MessageBubble.w2b_root_file_gate6_fix.test.jsx).
NOT touching intent-gateway classification thresholds tonight — flagged for
founder review, per the "no gate hacking without review" instruction.

## 4. REVERSIBLE action (propose → approve → execute → verify → rollback → verify-clean)
- Submitted via POST /cto/tasks/submit (task_3dae42d28d16): "add one HTML
  comment line to README.md".
- Real commit landed on ora-grounding main: `commit_sha=86017f7`.
- Verified live via raw.githubusercontent.com — comment WAS present.
- Rolled back via POST /cto/tasks/{id}/rollback {"confirm":"ROLLBACK"}:
  `rollback_sha=a717b0e`, `rollback_status=done`.
- Re-verified live — comment is GONE, README back to original. Repo left clean.
- Proof: task_final.json, rollback_response.json.

## 5. Low-confidence heuristic retest — INCONCLUSIVE, real infra finding instead
Target prompt ("What do you think of this project overall? Any thoughts?" —
deliberately contains none of response_confidence.py's _FIX_INTENT_TOKENS)
was sent twice. **Both attempts returned dt=60.1s, content_len=0, no
chat_sessions turn ever persisted.** This reproduces the EXACT pre-existing,
already-documented issue in routers/chat.py's own comment (2026-08-23,
founder-approved fix, HARD_TIMEOUT_S/SOFT_TIMEOUT_S): "~1/6 chat sends hitting
a raw... ~60s [ingress] cutoff" while the backend is still genuinely working.
Getting it 2/2 on the identical prompt suggests this specific request shape
(open-ended whole-project review, no fix keywords) reliably falls into the
"stuck on one slow round-trip with no interim progress frame" case the
SOFT_TIMEOUT_S=48s rescue is SUPPOSED to catch — but no rescue message
reached the client either time. **Could not confirm or deny the low-
confidence-suppression heuristic itself** because the request never
completed long enough to reach that check. This is a real, reproducible,
pre-existing infra-timing gap, not caused by tonight's work — out of scope to
fix (proxy/timeout tuning), flagged for founder.

## 6. Cost report
- Total tracked spend this test batch: **$0.131953** across ~26 llm_cost_ledger
  rows (the casual/intent-gateway tier's `call_llm` calls are NOT tracked in
  llm_cost_ledger at all — a separate pre-existing tracking gap, noted but not
  fixed tonight, out of scope).
- Approx cost/message (tracked calls only): ~$0.0051.
- Approx cost/10 messages: ~$0.051.
- Approx cost/session (5-message session): ~$0.025.
- These are LOWER BOUNDS (casual-tier cost is real but untracked) — useful as
  a floor for the $9/Pro tiering math, not the ceiling.

## 7. Mock back ON, zero-spend proof
- Flipped `MOCK_LLM=true` in backend/.env, restarted.
- 5 real `/chat/stream` calls (mock_back_on_msg_1..5.log): all returned the
  canned mock text, zero fence.
- `llm_cost_ledger` total unchanged: $5.226704 → $5.226704.

## Closures recorded
- Last night's P0-B "pending R8" sub-item: fence rate now measured (2/5 raw,
  67% on requests that reached a fix-capable model) — this closes the
  measurement gap; the intent-gateway misclassification found is a NEW,
  separate, named finding (not the same as Gate 6, not touched tonight).
- "Real model fence test" next-action item: done, see §3.
- "Verify at R8" notes from last night's mock work: MOCK_LLM short-circuit
  reconfirmed zero-spend after a real-spend session too (§7).
