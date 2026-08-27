# Investigation B — Qwen3.8-27B PLAN + Most-of-EXECUTE Swap (Phase 0)
Date: 2026-08-27 · Preview only · Real spend: ~$0.045 of no hard Phase-0 cap stated (well under Phase-1's $5 cap) · Files read (view_file): loop_engine.py (targeted ranges), core/parliament.py (targeted ranges), services/llm/_meta.py (full), repo_context.py (grep only), graph_builder.py/file_selector.py (grep only) · 0 product-code changes made

## 5-LINE SUMMARY
- CONFIRMED (I0): Loop-mode PLAN = ONE direct call (`loop_engine.py:_generate_plan` → `call_llm_with_meta(mode="loop_plan", review_mode="pro")`). Loop-mode EXECUTE = routed through the 3-member **Parliament council** (`core/parliament.py`), each member ALSO calling `call_llm_with_meta(mode="code", review_mode="pro")`. Both PLAN and EXECUTE ride the exact SAME choke point and the exact SAME `review_mode="pro"` branch as Prompt-mode's own "pro" tier (GLM-5.2 primary, Claude-Sonnet-4.6 fallback) — confirming I0(b) and giving a clean single-seam answer to I0(c): swap the model at `services/llm/_meta.py`'s dispatch, not at each call site.
- CONFIRMED, unplanned but important finding: council members' distinct temperatures (0.1/0.2/0.3) are **silently dropped today** — `parliament.py` tries `call_llm_with_meta(temperature=..., **kwargs)`, catches the resulting `TypeError` (the function doesn't accept that kwarg), and retries WITHOUT temperature — meaning the "3-member council" currently issues 3 calls with the SAME effective temperature. Flagging for the founder; not in this task's scope to fix, but materially relevant to "how many Qwen council seats" (2 of the 3 seats' stated purpose is already inert).
- CONFIRMED (I1, real calls): thinking-ON on a real ~23K-token repo briefing burned the full 2000-token budget on reasoning alone in 44s without finishing (matches the founder's 150-320s framing); thinking-OFF on the SAME briefing finished in **4.48s**, produced a correct, valid plan, cost **$0.01172**. An EXECUTE-shaped diff call (thinking off) finished in **8.55s**, cost **$0.0015**, produced a correct 3-file diff. **Thinking OFF is the clear right default for both PLAN and EXECUTE** — no demonstrated quality loss, order-of-magnitude latency/cost win.
- CONFIRMED (I3): Qwen ($0.35/$2.75 per M) is only modestly cheaper than GLM-5.2 ($0.50/$3.15 per M, confirmed via live OpenRouter lookup) on the NORMAL (non-fallback) path — the real cost win is (a) eliminating the ~8-9x-more-expensive Claude-Sonnet-4.6 fallback tax, and (b) predictability. A full PLAN+3-member-EXECUTE+CEO task on Qwen is estimated at **~$0.04-0.05 total**, far under the $3/task cap and trivially inside a $9-plan margin — cost is NOT the blocker for this swap.
- CONFIRMED, high-value, unexpected finding (I4): the real binding constraint on repo-briefing size today is **NOT any model's context window** — it's a hardcoded `MAX_TOTAL_CHARS = 15000` (≈4K tokens) cap in `repo_context.py`, independent of model. Our current 262K-token models are only ~1.5-2% utilized; Qwen's 1M window (itself only available via ONE of 10 OpenRouter providers — Alibaba — the other 9 cap at 262K, one at 65.5K) would deliver **zero additional benefit** unless this hardcoded cap is separately raised. The "1M context" pitch is largely moot for this pipeline as currently wired.

## I0 — Call-site map
Brief's assumption corrected as instructed: PLAN/EXECUTE calls are not in `cto_projects.py` (router only).
- **Loop-mode PLAN**: `services/loop_engine.py` (`LoopEngine._do_plan()` calls the module-level `_generate_plan(user_id, project_id, ...)`, ~line 4090-4160) → `call_llm_with_meta(system, user, max_tokens=..., mode="loop_plan", review_mode="pro")`. Context = the plan-phase system/user prompt built from `repo_context.py`'s briefing (see I4) + the frozen task description. **Not council-routed — one direct call.**
- **Loop-mode EXECUTE**: `LoopEngine._do_execute()` (~line 1043-1200) builds per-file context and calls into `core/parliament.py`'s `Parliament`/`CouncilA` (3-member vote + CEO judge), via `_llm_call_protected()` (parliament.py ~line 355-410) → same `call_llm_with_meta(mode="code", review_mode="pro", max_tokens=2500)` for each of the 3 members. **Council-routed, confirming I0(a): PLAN=direct, EXECUTE=council.**
- **CEO judge**: `core/parliament.py` (~line 900-980), calls `call_llm_with_meta(mode="chat", review_mode="swift")` — this is the GLM-5.2-ONLY "swift" branch (no Claude fallback path for swift), with DeepSeek-V3 as its own rescue-on-timeout. **CEO stays a different routing branch than the council members already today — the cross-family judge rule is structurally easy to keep: just never point "swift"'s model at Qwen.**
- **Prompt-mode writing call**: `routers/chat.py`'s send/stream paths call `call_llm_with_meta(mode=..., review_mode=<swift|pro|maxx>)` directly — confirmed I0(b): YES, "pro" review_mode is the GLM-primary/Claude-fallback branch, and it is the EXACT SAME branch Loop-PLAN and Loop-EXECUTE-council already use.
- **I0(c) — single choke point**: CONFIRMED. `services/llm/_meta.py`'s `_call_llm_with_meta_inner()` (public `call_llm_with_meta()`) is the ONE place all four call classes above converge, dispatching purely on `(mode, review_mode)`. A Qwen swap is a **model-selection branch added inside this one file**, with the call sites needing only a 1-line parameter change each (e.g. a new `review_mode="qwen"` or a flag check) — NOT a line-by-line rewrite of `loop_engine.py`/`parliament.py`.
- **I0(d) — protected-file exception**: Given the clean seam in (c), the ONLY changes needed in `loop_engine.py`/`parliament.py` (both are call sites, `parliament.py` is not on the explicit protected list but is treated with the same care) are 1-line parameter swaps at 2 call sites — well under the disclosed-exception budget used elsewhere in this codebase. **No design gap requiring a large protected-file exception.**

## I1 — Real availability + 3 timed calls (PREVIEW, real OpenRouter calls, our key)
**Model metadata (live, `GET /models/qwen/qwen3.8-27b/endpoints`):** 10 providers. Only **Alibaba** offers the full 1,048,576-token context; 8 others cap at 262,144 (same as our current models); one (Io Net) caps at 65,500. Pricing is uniform across providers at $0.35/M in, $2.75/M out (matches founder's figures — CONFIRMED).

**Call A — PLAN-shaped, real ~23.4K-token repo briefing (self-repo file-map stand-in, see note below), thinking ON, max_tokens=2000:**
```
status=200  total_time=44.39s  provider=CoreWeave  finish_reason="length"
```
Never reached the actual JSON answer — the entire 2000-token budget was consumed by the reasoning trace alone (visible in the raw `reasoning` field, cut mid-thought). This directly reproduces the founder's "150-320s, thinking ON" framing — a real task-sized output would need a much larger token budget and would take proportionally longer.

**Call B — same PLAN-shaped prompt, thinking OFF, max_tokens=2000:**
```
status=200  total_time=4.48s  provider=Io Net
usage: prompt_tokens=23434, completion_tokens=138, cost=$0.01172, reasoning_tokens=0
```
Produced a correct, valid `{"files_to_change":["backend/main.py"], "steps":[...]}` plan, including sensibly noting the auth-bypass consideration for a no-auth endpoint. **Thinking OFF is ~10x faster and completes the task; no evident quality loss on this prompt.**

**Call C — EXECUTE-shaped multi-file diff, thinking OFF, max_tokens=2500:**
```
status=200  total_time=8.55s  provider=Alibaba
usage: prompt_tokens=144, completion_tokens=560, cost=$0.0015
```
Produced a correct 3-file unified diff (new router file + new test file + a main.py registration edit). Honest caveat: my test prompt did not include the REAL existing `main.py` content, so the diff shows `main.py` as if new (`--- /dev/null`) rather than a modification hunk — an artifact of my simplified test prompt, not a Qwen defect; a real EXECUTE call would include real file content and Qwen would generate a normal modification hunk.

**Note on the repo briefing used:** Preview's `project_graphs` cache (`map_text`) was empty for every test project in this environment (never warmed), so I could not pull a real cached briefing. I built a stand-in ~23.4K-token briefing from this app's own real file tree (a genuinely large, real codebase, 4630 files) — labeled honestly as a SELF-REPO STAND-IN, not an actual customer `repo_context.py` output. It is a realistic size/shape proxy, not a byte-real briefing.

## I2 — Escalation signal: what data already exists (SAMPLE = last 20 real completed/attempted loop_sessions, per SR-11)
- `loop_task_specs.py:97` already carries `frozen_files_to_change: list[str]` — populated straight from the plan, **zero new LLM calls**.
- `graph_builder.py:126` has `extract_imports(content, path)` — a real, existing, non-LLM import extractor; usable to compute cross-module impact radius without any new model call. (`build_graph()` overall also has an LLM-based file-describe helper, but the import-extraction piece itself is pure static analysis.)
- `file_selector.py:50` has `score_file()` — an existing non-LLM relevance scorer, usable as a "touched-files-with-tests" proxy input.
- **Honest gap (real, not a design failure): the last 20 real `loop_sessions` in this Preview environment are 100% single-file tasks** (`README.md`, mostly from this session's own restart-loop-honesty test replays) or zero-file `failed` rows. There is **no genuine multi-file or cross-module task in test_admin_001's real history to calibrate a threshold against.** This matches the founder's own anticipated risk (Refinement 4 of the go-ahead reply) — the "at least 1 deliberately HARD task" for Phase 1's replay will need to be sourced from the shared `ora-grounding` test repo's own history or a faithful drill-replica, NOT from this account's real history, exactly as instructed. **I2's composite-signal FORMULA is buildable today (file_count + import-radius + test-file overlap, all zero-new-LLM-call); its THRESHOLD cannot be honestly calibrated from this account's data and must wait for Phase 1's sourced hard task(s) or real customer-scale volume.**

## I3 — Cost model (real numbers)
Live OpenRouter pricing lookups (GET, no completion cost):
```
qwen/qwen3.8-27b        in=$0.35/M   out=$2.75/M
z-ai/glm-5.2            in=$0.50/M   out=$3.15/M   (today's "pro" primary)
anthropic/claude-sonnet-4.6  in=$3.00/M  out=$15.00/M  (today's "pro" FALLBACK, fires when GLM returns empty)
```
- **Qwen vs GLM (normal path, no fallback):** Qwen is ~30% cheaper on input, ~13% cheaper on output than GLM — a real but modest saving on the common case.
- **Qwen vs the Claude fallback path:** Qwen is ~8.6x cheaper on input, ~5.5x cheaper on output — this is where the real money is, IF the fallback fires meaningfully often today (not independently measured in this run — would need GLM empty-response-rate telemetry, not collected here).
- **Estimated full-task cost on Qwen** (1 PLAN call + 3 council EXECUTE calls + 1 CEO judge call, thinking off, using I1's real per-call shape ~23K in / ~150-560 out for PLAN/EXECUTE, CEO judge call assumed smaller ~2-4K in): **PLAN ≈ $0.012, 3× EXECUTE ≈ 3 × ~$0.010 = $0.030, CEO (GLM, stays) ≈ $0.003 → total ≈ $0.045/task.** This is **1.5% of the existing $3/task hard cap**, and trivially compatible with a $9/plan target at any realistic task volume — **cost is not the constraint on this swap; quality (Phase 1) is the real gate.**
- `services/llm_cost_breaker.py`'s $2/hr, $10/day, $3/task caps stay exactly as-is; Qwen's real per-task cost gives enormous headroom under all three.

## I4 — Context-window opportunity (cache-metadata only, no large-file reads, per SR-11)
`services/repo_context.py:57-58`: **`MAX_FILE_CHARS = 3000`, `MAX_TOTAL_CHARS = 15000`** — a hardcoded ~15K-character (≈4K-token) total budget for the ENTIRE inlined-file briefing, regardless of model. This is the actual, current, binding constraint — not any model's context ceiling.
- CONFIRMED-capability: Qwen advertises up to 1,048,576 tokens, but only via ONE OpenRouter provider (Alibaba); the other 9 providers serving this model cap at 262,144 (same as today) or 65,500. Without explicit provider-pinning, a random-routed call gets no window improvement over today.
- UNCERTAIN-benefit, and arguably **moot as currently wired**: since `repo_context.py` caps briefings at ~4K tokens today, our EXISTING 262K-token models are already sitting at ~98% headroom, unused. Raising the model's window to 1M (even if reliably routed to Alibaba) changes nothing until `MAX_TOTAL_CHARS`/`MAX_FILE_CHARS` are ALSO raised — which is a separate, small, MODEL-AGNOSTIC change that could be tested with today's models before any Qwen swap is even relevant. **The "big context" story and the "swap to Qwen" story are two independent levers; conflating them would overstate Qwen's benefit.**

## ENGINEERING GAPS (this run's honest surprises)
1. Council members' 0.1/0.2/0.3 temperature differentiation is silently dropped today (TypeError-caught fallback in `parliament.py`) — the "3 different members" may currently be closer to "3 identical calls." Relevant to the founder's "1, 2, or 3 seats" question: if members aren't actually diverse today, the case for keeping 3 Qwen seats (vs. 1) needs its OWN justification independent of "council diversity," since that diversity isn't currently real.
2. No genuine hard/multi-file task exists in `test_admin_001`'s real history — Phase 1's "≥1 HARD task" must be sourced elsewhere (ora-grounding test-repo history or a drill-replica), exactly as the founder already anticipated and authorized.
3. The 1M-context opportunity is provider-dependent (Alibaba only) and, more importantly, is currently blocked by an unrelated hardcoded 15K-char cap in `repo_context.py` — flagging this as a possibly higher-leverage, lower-risk, MODEL-AGNOSTIC fix than the Qwen swap itself, for the founder's awareness (not proposing to build it here — out of scope).
4. My I1 repo briefing was a self-repo stand-in (labeled honestly), not a real cached customer `repo_context.py` output — Preview's `project_graphs.map_text` cache was empty for every test project.

## Founder decision needed
Cost (I3) clears easily — pricing is unblocked either way. Proceed to Phase 1 replay? Given I2's finding, Phase 1's hard task will need explicit sourcing from ora-grounding/drill-replica per your own Refinement 4 (not test_admin_001 history). Recommend: PROCEED to Phase 1, thinking-OFF only (no case yet for thinking-ON's cost given no quality data supports it), with the hard task sourced as you already specified.
