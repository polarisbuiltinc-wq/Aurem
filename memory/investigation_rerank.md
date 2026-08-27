# Investigation A — Reranker-Based Cross-Session Recall (Phase 0)
Date: 2026-08-27 · Preview only · Budget used: ~$0.002 of $0.10 cap · 3 source files read (ora_council_retriever.py, chat_helpers.py, routers/chat.py, all read once) · 0 product-code changes made

## 5-LINE SUMMARY
- CONFIRMED (major, previously undocumented): a mode-taxonomy mismatch (`_detect_mode()` returns "code"/"chat"; the retrieval index is keyed by "A"/"B"/"C"/"D"/"E" from `classify_intent()`) means `_candidate_indices()` **always** returns `([], "below-threshold")` on the real call sites today — cross-session recall is currently **inert for 100% of live traffic**, independent of `_MIN_SCORE`.
- CONFIRMED (live-reproduced, 2 calls): reranker endpoint works great — `qwen/qwen3-reranker-8b` returns 0-1 normalized scores, p50 latency 0.276s (5 runs), ~$0.0002/call; `qwen/qwen3-reranker-0.6b` is **unavailable on our account** (404 "No endpoints found", reproduced twice) — no 8B-vs-0.6B choice exists, 8B is the only option by availability, not preference.
- CONFIRMED: candidate scoping is strictly user-only (no cross-user tier exists in code — removed 2026-08-20 per a founder security directive) — **no scoping gap found**, L4 clears clean.
- LIKELY/UNCERTAIN: the founder's literal "plain question → Ship via CTO example" pair, reconstructed from real DB logs, does **not** reproduce a TF-IDF false-positive at either 0.25 or 0.42 (measured score 0.067, ~40x below threshold) — and a full-corpus scan found **zero naturally-occurring cross-topic pairs** scoring ≥0.42. A synthetic, keyword-dense adversarial query does trigger it (0.68). This means the original incident's mechanism is likely NOT proven to be TF-IDF weak-matching (a prior agent's Aug-21 commit already admitted this exact uncertainty), and it may instead be explained by the mode-bug above.
- Recommendation signal: the reranker itself works and is fast/cheap enough to deploy — but wiring it on top of a call site that never produces candidates fixes nothing until the mode-bug is fixed first (that fix is outside this task's stated scope, but is the actual precondition for this whole feature to matter).

## I1 — Real availability + latency with OUR key (PREVIEW, real OpenRouter calls)
**Endpoint:** `POST https://openrouter.ai/api/v1/rerank`, body `{model, query, documents[], top_n}`. Confirmed working, distinct from the chat-completions shape.

**Control (2-doc), qwen/qwen3-reranker-8b:**
```
status=200 time=0.558s
{"model":"accounts/fireworks/models/qwen3-reranker-8b",
 "results":[{"index":0,"relevance_score":0.9740425944328308,"document":{"text":"Paris is the capital of France."}},
            {"index":1,"relevance_score":0.000004092911694897339,"document":{"text":"Bananas are yellow fruit."}}],
 "usage":{"total_tokens":171,"cost":0.0000342}, "provider":"Fireworks"}
```
Score shape = **0–1 normalized relevance_score** (NOT raw logits) — this is what L2's threshold math should be built on directly (no re-normalization needed).

**Control (2-doc), qwen/qwen3-reranker-0.6b — reproduced twice:**
```
status=404  {"error":{"message":"No endpoints found for qwen/qwen3-reranker-0.6b.","code":404}}
status=404  {"error":{"message":"No endpoints found for qwen/qwen3-reranker-0.6b.","code":404}}
```
CONFIRMED (contradicts the founder-supplied "verified model facts" — flagging honestly per SR-5): 0.6b is not currently reachable on our OpenRouter account/key. This is a live, twice-reproduced 404, not a transient blip. **8B is the only real option; the "8B vs 0.6B" decision in the spec is moot — there is no choice to make.**

**Realistic load (query + 8 real corpus-length docs), qwen/qwen3-reranker-8b, 5 runs, top_n=8:**
```
run 0: 0.301s   run 1: 0.372s   run 2: 0.276s   run 3: 0.202s   run 4: 0.234s
min=0.202s  p50=0.276s  max-of-5("p95")=0.372s   cost=$0.0001858 (929 tokens)
```
Well inside any live-chat latency budget (the existing retriever's own asyncio.wait_for pattern in `chat.py` uses a 10s cap on a comparable operation — this reranker call would use <4% of that).

**Bonus — this same real call is direct supporting evidence for the reranker's efficacy** (belongs formally to Phase 1, noted here since it fell out of the I1 latency test for free): query = the real "I'm not a coder…" plain question; among the 8 real docs was the real "fix the deployment error and ship it via CTO" text. Reranker scored the two genuinely-relevant "this site helps…" replies at **0.9988 / 0.9982**, and the Ship-via-CTO doc at **0.0011** — correctly rejected. This is a clean, real, positive signal for the reranker's discriminative power (see I5/Phase-1 note below on whether this exact pair is the right test).

## I2 — Exact trace of the current path (ora_council_retriever.py + routers/chat.py, each read once)
- TF-IDF score computed: `ora_council_retriever.py:_score()` (cosine-ish TF-IDF, `_index["doc_freq"]` built once per `_REFRESH_TTL`).
- Threshold check + candidate selection: `ora_council_retriever.py:_candidate_indices()` — **only two tiers exist**:
  - Tier 1: `user_id ∩ project_id ∩ mode` intersection, needs ≥ `_MIN_BUCKET`(20)
  - Tier 2: `user_id ∩ mode` intersection, needs ≥ `_MIN_BUCKET`(20)
  - No fallback tier (the old cross-user/global tier was intentionally removed 2026-08-20 for security — see I3).
- Candidate becomes injected context: `chat.py:386-388` (chat_send) and the equivalent block in the streaming handler (`chat.py` ~1298-1300) — `extra_sys = _council_block + ("\n\n"+extra_sys if extra_sys else "")`.
- **Execution frequency: EVERY chat message** on both `/chat/send` (`chat.py:376-390`) and the SSE streaming path (`chat.py:1291-...`), for every mode EXCEPT Ask-Advisor turns (`body.ora_panel==True`, explicitly excluded — consistent with the "OUT OF SCOPE: Ask Advisor" note in the brief). This is NOT "only when the council decides to recall" — it's unconditional, so the reranker's added latency would land on literally every non-advisor chat turn once a user clears the bucket-size threshold.

### CRITICAL FINDING — mode-taxonomy mismatch (CONFIRMED, live-reproduced)
Both call sites pass `mode=_detect_mode(body.prompt or "")` (`chat.py:381`, `chat.py:1293`). `_detect_mode()` (`chat_helpers.py:30-32`) is a **binary** classifier: returns only `"code"` or `"chat"` (substring-matches a small hint list — note: it also false-positive-matches "code" inside "coder", a minor separate bug worth flagging).
Meanwhile the retrieval index (`_index["by_mode"]`, built in `_rebuild_index()`) is keyed by the **real** council mode taxonomy `"A"/"B"/"C"/"D"/"E"` — written by `classify_intent()` (`chat_helpers.py:222-291`) at LOG-WRITE time (`ora_council_logger.py:23,68`), a completely different function.
Live proof (real DB, real function calls, no mocks):
```
DETECTED MODE (used at call site): 'code'
AS-WIRED TODAY -> recalled_count= 0
IF MODE WERE "A" INSTEAD (the real stored mode) -> recalled_count= 2
```
Since `"code"`/`"chat"` are **never** keys in `_index["by_mode"]` (confirmed: the only keys present in the real corpus are `"A"`, `"D"`, `"E"`), `mode_pool` is always the empty set, so **both tiers always intersect to empty, for every user, every message, regardless of corpus size.** `get_council_few_shot()` has been returning `("", 0)` unconditionally on every real call for as long as this taxonomy split has existed.
This is independently corroborated by a prior agent's own commit (`8d67a4a`, 2026-08-21): 10 live reproduction attempts on Preview all showed `council_recalled=0`, and the agent explicitly wrote "root cause remains unidentified" — consistent with what I found, just not connected to this specific cause at the time.

### Real corpus size (I2, requested)
`ora_council_logs` total = 1350 rows (near the 1500 cap). By user: `test_admin_001`=1067 (founder's own test account, dominant), `pytest_user`=180, `test-customer-1`=74 (synthetic), one real anonymized user id=21 (just over the 20-row personalization floor), most other real ids=1-3 rows. By mode: A=1251, D=87, E=12, **B=0, C=0** (no rows at all in those buckets currently). **At the founder's stated ~72 users/797 dev_users scale, the overwhelming majority of real users are still below the 20-row bucket floor today — recall (once the mode-bug is fixed) would activate for only a small minority of real accounts at current usage levels.**

## I3 — Scope confirmation (L4)
CONFIRMED CLEAN, no gap. `_candidate_indices()` (`ora_council_retriever.py:234-267`) restricts every tier to `_index["by_user"].get(user_id)` — there is **no code path** that returns another user's rows. The prior cross-user/global fallback tier was **removed** 2026-08-20 specifically because of a cross-tenant recall risk (documented in the function's own docstring, citing "the founder's own cold-start mismatch bug" as the reason — see the Engineering Gaps note below on whether that attribution is itself solid). Project-level scoping is *preferential*, not mandatory: Tier 2 pools across ALL of a user's own projects if Tier 1 (same-project) doesn't clear the bucket floor — this is same-user, cross-project, which is within the stated tolerance ("(+project, if the current design is project-scoped)" — it isn't strictly project-scoped by design, and that's an existing, intentional choice, not a new gap). **No PARENT-LEVEL P3 finding needed for this module.**

## I4 — recall@8 / recall@10 (real pairs, real scoring, no mocks)
5 real (query, known-relevant-but-not-identical) pairs sampled from the real corpus:
```
Q: 'team decision note karo: hum saare naye components...' -> in top8=True  in top10=True | top1=0.924
Q: 'Security audit. For each critical issue found...'      -> in top8=True  in top10=True | top1=0.414
Q: 'Review this snippet for security issues: SECRET_KEY...'-> in top8=True  in top10=True | top1=0.851
Q: 'what is 2+2'                                            -> in top8=False in top10=False| top1=1.000 (exact near-dup ranked #1, "what is 7+7?" pushed out — different numbers share zero real tokens, a legitimately-different-content miss, not a system failure)
Q: 'Hi, what can you do?'                                    -> in top8=False in top10=False| top1=0.439 (same weak-overlap-but-same-category pattern)
recall@8 = 3/5 (60%)   recall@10 = 3/5 (60%) — identical, because the 2 misses are far outside top-10 (not a borderline K cutoff issue)
```
Honest read: TF-IDF's pre-filter reliably surfaces genuinely paraphrased/near-duplicate content (recall found the Vitest decision-note pair, the security-snippet-template pairs). It misses same-*category*-different-*specifics* pairs (different arithmetic operands, generic "what can you do" phrasing) because those share almost no real lexical overlap after tokenization — this is a **pre-filter weakness**, not something a reranker fixes (the reranker can only re-score what TF-IDF already surfaced). At the current small/homogeneous test corpus this is a minor concern; it should be re-measured once real customer volume exists.

## I5 — Eval-set assembly (real texts, honest reproduction attempt)
- **Mismatch pair, sourced (RECONSTRUCTED-VERBATIM-FROM-LOGS, not paraphrased — both halves found as real stored text):**
  - Plain question (test_admin_001, mode A, DB-verbatim): *"I'm not a coder. Can you tell me in simple words what this website does and if it's working okay right now?"*
  - Unrelated past example (test_admin_001, mode D, DB-verbatim): *"fix the deployment error and ship it via CTO"* → reply *"Let me check your **deployment** integration first — reading the repo before I diagnose so I'm not guessing. _Routing this through Mode A (read-then-plan)…"*
  - **Measured TF-IDF score between these two real texts = 0.067** (self-score sanity check = 1.0, confirming the scoring pipeline is working correctly) — this is ~6x below the OLD 0.25 threshold and ~40x below the current 0.42. **This specific real pair does not reproduce the bug at any threshold that has ever been in production, and is additionally blocked by the mode-mismatch (D≠A/code).** Per the founder's own instruction ("if a reconstruction is too cleanly-unrelated to trigger today's bug, rebuild it until it triggers"), I searched further:
  - Full-corpus brute-force scan (51 unique real messages, all pairs): the highest-scoring genuinely cross-topic pair found anywhere in the real corpus was **0.246** (a generic-capability-question pair that is arguably still same-topic) — **zero real pairs in this corpus cross 0.42, and zero cross 0.25 either.** The corpus is small and dominated by the founder's own repetitive test probes, which are either near-duplicates (score >0.85) or genuinely unrelated (score <0.25) — there is no natural "weak-but-wrong" middle ground here to find.
  - **SYNTHETIC-ADVERSARIAL fallback (clearly labeled, not from logs):** constructed query *"ship via cto deployment error status"* (keyword-dense, unnatural phrasing) scores **0.678** against the same real "ship it via CTO" doc — clears 0.42 easily. A more natural phrasing, *"CTO deployment ship error status update please"*, scores **0.4135** — just under the current threshold, right at the boundary. This demonstrates the underlying vulnerability CLASS is real (keyword-echo can beat 0.42) even though the literal historical pair does not.
- **Positives (≥10 required, real):** decision-note Hindi/English pair (0.924), 3 security-snippet-template pairs (0.85-0.88), the 2 "this site helps..." near-duplicate answer pairs, 2 "say hello briefly" case-variant pairs, 2 "ye error dekho..." near-duplicate paste pairs, "what is 2+2"/"What is 2+2?" case-variant pair — **12 real positive pairs collected**, scores 0.85-1.0.
- **Negatives (≥19 required, real, cross-topic):** collected via the full-corpus scan above — every pair among the 51 unique real messages that is NOT a template-variant of the same message; **≈2,400 real cross-topic pairs available, all scoring <0.42, the large majority <0.1.** Sampled 20 for the record; median score 0.02, max 0.246.

## ENGINEERING GAPS (mandatory)
1. **The premise may not hold.** The reranker task is framed as fixing "the traced root cause" of a real incident. On direct measurement, (a) the literal historical pair doesn't reproduce at any real threshold, (b) no natural pair in the real corpus does either, and (c) a prior agent's own commit already flagged this exact uncertainty ("no new evidence contradicts this... stated explicitly, not assumed"). The most likely REAL root cause of "council_recalled always seemed to fire strangely" is the **mode-taxonomy mismatch**, which — as coded — means recall has been silently OFF for 100% of production traffic the whole time. That is arguably a bigger, more clear-cut bug than a TF-IDF weak-match, and it sits in the exact same file this task is scoped to touch, but fixing it is a plain-code-bug fix, not a reranker feature — **it is not authorized by this task's scope**, so I have not fixed it. Flagging it explicitly for the founder's separate decision.
2. **0.6B is unavailable.** The founder-supplied "verified facts" said 0.6B was a viable low-latency sibling; live testing (twice) shows a 404 on our account. This isn't a latency/quality tradeoff decision anymore — it's a hard availability constraint. 8B is fine anyway (p50 0.276s, cheap), so this doesn't block Phase 2, but the spec's framing of "test both, 8B is default unless 0.6B is clearly better" no longer applies — there is nothing to compare.
3. **recall@K has a real, minor gap** for same-category/different-specifics queries (2/5 misses in my small sample) — a pre-filter limitation the reranker cannot fix by itself, since it only re-scores what TF-IDF already surfaced. Not a blocker at current corpus size; worth re-measuring at real customer scale.
4. **If the mode-bug is fixed separately** (out of scope here) and recall starts actually firing, the corpus composition (I2) means it will initially only affect `test_admin_001`-like heavy users; most real customers are still under the 20-row floor, so the reranker's real-world exposure would ramp slowly and naturally with usage — a mitigating factor for rollout risk.

## The single fact that would flip the recommendation
If the founder can supply the ACTUAL verbatim historical incident texts (not my DB reconstruction) and they turn out to score ≥0.42 under the CURRENT scoring — that would restore confidence that TF-IDF weak-matching (not the mode-bug) is the real, suf003icient mechanism, and the reranker fix would be squarely justified on its own. Absent that, the honest recommendation is: **the reranker is a good, cheap, fast, well-behaved fix for a REAL vulnerability class (proven via the synthetic adversarial pair) — but it should ship alongside (or after) a fix for the mode-taxonomy bug, otherwise it fixes a path that currently never runs.**

## Founder decision needed
PROCEED to Phase 1 (T1-T5 replay) / ADJUST (fix mode-bug first, in a separate scoped task, then replay) / NO-GO on this specific incident-pair framing (while still valuing the reranker as future-proofing once mode-bug is fixed).

---

# A0 — Mode-taxonomy bug FIX (2026-08-27, separate authorized scope)
Founder explicitly authorized fixing the mode-bug (Engineering Gap #1 above) as its own scoped task, BEFORE any A1 false-positive re-scan. Budget used: $0 (no reranker/LLM calls, pure code fix + real-DB proof against the existing Preview corpus).

## 5-LINE SUMMARY
- CONFIRMED (fixed): both call sites in `backend/routers/chat.py` (`chat_send` line ~388, `chat_stream` line ~1301, plus the `_mode`-broadcast reuse at ~1544-1549) now pass `classify_intent(prompt, f12_payload)` — the real "A"-"F" taxonomy the retriever's index is keyed by — instead of `_detect_mode(prompt)` ("code"/"chat", never a real index key). `chat_stream` computes it once (`_recall_mode`) and reuses it for the existing A-F broadcast, avoiding a duplicate classification call.
- CONFIRMED (regression test added, real call-path boundary): `backend/tests/test_iter2026_08_27_council_mode_taxonomy_fix.py` — (1) spies the real `/chat/send` endpoint and asserts the `mode` kwarg reaching `get_council_few_shot` is the real taxonomy value (`"B"` for "should I pivot or persevere"), never `"chat"`/`"code"`; (2) end-to-end, with NO mocking of the retriever, seeds a 25-row mode-A bucket and proves the real `/chat/send` response's `council_recalled` is `>=1`, while re-running the same corpus/query with the pre-fix `_detect_mode()` value in the same test proves it would have been `0`. Both pass.
- CONFIRMED (live Preview, real DB, real corpus, no mocks): logged in as `test_admin_001`, sent a REAL `/chat/send` call with prompt `"hi"` (a real, frequently-occurring message from this user's own history in `project_id="home"`) → **`council_recalled = 2`** (post-fix). Independently re-ran the retriever against the SAME live DB/query with the OLD `_detect_mode("hi")` value (`"chat"`) → confirmed **`0`**. This is the exact `0 → >=1` proof requested, on the live system, not a synthetic fixture.
- CONFIRMED (no regression): targeted suite run — `test_iter212m77_council_retriever.py`, `test_council_retriever_weak_match_filter.py`, `test_phase2_ora_council_retriever_coverage.py`, `test_phase2c_chat_router.py`, `test_phase2c_chat_router_wave3.py`, + the new file — same 5 pre-existing baseline failures (confirmed via `git stash` to exist identically BEFORE this fix, unrelated order/state issues in those specific tests) and 0 NEW failures; the 2 new regression tests pass.
- HONEST caveat: the earlier greeting-style query `"hi there, what can you do?"` against the SAME live `test_admin_001` corpus still recalled `0` — this is the `_MIN_SCORE=0.42` threshold correctly rejecting a weak match, working exactly as the founder's 2026-08-25 fix intended; it does not indicate the taxonomy fix is incomplete. Recall is now genuinely gated by score, not by an always-empty candidate pool.

## Fix detail (file:line)
- `backend/routers/chat.py:379-392` (chat_send, non-streaming): `mode=classify_intent(body.prompt or "", body.f12_payload)` replaces `mode=_detect_mode(body.prompt or "")`.
- `backend/routers/chat.py:1285-1312` (chat_stream, SSE): added `_recall_mode = classify_intent(...)` computed once, passed into `get_council_few_shot(mode=_recall_mode, ...)`.
- `backend/routers/chat.py:~1544-1549`: the pre-existing `_mode = classify_intent(...)` broadcast (A-F pill) now reuses `_recall_mode` if already computed (`_recall_mode if _recall_mode is not None else classify_intent(...)`) instead of classifying the same prompt twice.
- Compile check: `python -m py_compile routers/chat.py` → `SYNTAX_OK`.

## Live proof transcript (Preview, real DB `test_admin_001`)
```
POST /api/aurem-dev/chat/send  prompt="hi"  project_id="home"
-> council_recalled = 2   (POST-FIX, real corpus, real call)

Same corpus/query, retriever called directly with pre-fix mode value:
pre-fix _detect_mode("hi") = 'chat'
pre-fix (old mode) recalled_count = 0
```

## Founder decision needed (A0 → A1 gate)
A0 is complete and verified (code fix + regression tests + live 0→2 proof + no new regressions). **Requesting approval to proceed to A1** (re-run the natural false-positive re-scan from I5, now that real recall is actually active, since the original I4/I5 measurements in Phase 0 above were taken while this bug was silently zeroing all real recall).

---

# A1 — Natural false-positive re-scan on the REAL live-fixed path (2026-08-27)
Founder-approved. Budget: $0 (pure TF-IDF, local Mongo reads — no reranker/LLM calls needed for this re-scan). Retriever source file NOT re-read (already read exactly once in Phase 0, per cap).

## Method
Sampled 22 unique real `mode="A"` messages from `test_admin_001`'s OWN real corpus (all their projects, not synthetic), then ran each through the REAL, now-fixed `get_council_few_shot(mode='A', user_id='test_admin_001', project_id=<real pid>, k=2)` — i.e. the actual production candidate-pool + scoring path, not an isolated retriever-module call with a hand-picked mode.

## Result
```
22 real queries sampled -> 14 total candidates recalled (8 queries recalled 0, 14 recalled 1-2)
Naturally-occurring cross-topic (false-positive) recalls: 0
```
The only 4 rows my automated overlap-check initially flagged as "zero-lexical-overlap" were `"hi"` recalling a past `"hi"` — an exact self-match, mis-flagged only because my own heuristic excluded ≤3-character tokens (so "hi" wasn't counted as overlap with itself). Manually inspected: **not a real false positive** — it is the single most correct possible recall (identical text). Corrected count: **0 genuine false positives** in this live-fixed-path sample.

## CONFIRMED conclusion
This corroborates and EXTENDS the Phase 0 I5 finding ("zero naturally-occurring cross-topic pairs ≥0.42 in a full-corpus scan") to the real, per-user, per-project candidate-pool path now that the mode-bug fix makes it actually run in production. Across both the unscoped full-corpus scan (Phase 0) and this real-user/real-bucket re-scan (A1), **no evidence of a naturally-occurring TF-IDF false-positive recall exists in the current Preview corpus** — the only reproduction of the vulnerability class remains the SYNTHETIC adversarial query from Phase 0 I5 (0.678, keyword-dense, deliberately unnatural).

## Updated recommendation
- The mode-bug fix (A0) alone already closes the "recall was completely inert" gap and is now live-verified working correctly with zero false positives on real traffic patterns sampled.
- The reranker (Phase 1, T1-T5) remains a good, cheap, fast SECOND layer of defense against the SYNTHETIC-adversarial vulnerability class (a real, if currently unobserved-in-the-wild, risk) — but is no longer blocking on an already-broken recall path (A0 fixed that).
- **Recommendation: reranker Phase 1 (T1-T5) is now founder's call on priority/timing** — the emergency justification (recall totally inert) is resolved; the remaining case is future-proofing against a vulnerability class proven only synthetically so far.
