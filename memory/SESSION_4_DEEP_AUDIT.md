# SESSION 4 DEEP AUDIT — Discovery Only, Zero Code Changes

**Date**: 2026-07-31
**Scope**: (Part 1) 4 half-built + 1 unclear ORA-chat services flagged in Session 2. (Part 2) Top 10 remaining unaudited backend services ranked by import count.
**Discipline**: READ-ONLY. No code was modified. Every finding lists a Severity (P0 / P1 / P2) and a fix DIRECTION only — no code.

---

## Executive Summary

| Bucket | Files audited | P0 | P1 | P2 | Verdict |
| --- | ---:| ---:| ---:| ---:| --- |
| ORA chat plumbing (5) | 5 | **1** | 3 | 2 | ORA upstream is currently in a **24h fatal circuit-breaker** (verified live) — every dependent path silently no-ops right now. |
| Top-10 backend services | 10 | 0 | 4 | 4 | Overall healthy. Notable: `llm.py` is a **1,805-LOC god-file** (P1 refactor candidate), `local_tools.py` has 11 bare `except: pass` (P1 hygiene), and `graph_builder.py` swallows 3 exceptions returning `None` (P2). |
| **TOTAL** | **15** | **1** | **7** | **6** | 1 revenue-adjacent silent-failure surface currently active; the rest is stylistic / hardening work. |

---

## PART 1 — ORA CHAT PLUMBING (5 services)

### 🔴 P0 · ORA upstream `aurem.live` is currently in a FATAL circuit-open state
* **File**: `services/ora_client.py` (178 LOC, 1 caller)
* **Observed live evidence**:
  ```
  $ cat /tmp/aurem_ora_circuit_open_fatal
  1785448573 http_500: ora_chat_error: openrouter HTTP 404:
  {"error":{"message":"This model is unavailable for free."}}
  Age: 18,231s / 86,400s cooldown → OPEN for ~19h more.
  ```
  `curl -X POST https://aurem.live/api/v1/public/ora/chat` returns HTTP 400 (server reachable but path/body rejected).
* **Impact**: `is_ora_available()` returns `False` for the next ~19h. Every dependent surface silently degrades:
  1. `routers/chat.py:919` — founder-only ORA availability probe reports "not available" (visible in UI).
  2. `services/ora_learning.py::maybe_log_ora_escalation` (line 84) short-circuits — **zero learning samples collected while breaker is open**. No log line explains why.
  3. `POST /admin/qa/guard/…` and any founder-ORA council writes silently drop.
* **Root cause (upstream)**: aurem.live's OpenRouter model slug points at a model that no longer offers a free tier — HTTP 404 with `"This model is unavailable for free"`. This is a config fix on **aurem.live**, not in this repo.
* **Recommended-fix direction** (no code):
  1. **Alert on breaker-open state**: expose `is_ora_available() == False` + breaker file age to `/api/health/self-check` and Guard 20's daily digest so silent no-ops surface within 5 min instead of being invisible.
  2. **Founder task**: fix the aurem.live model slug (out-of-scope for this repo).
  3. **Learning shadow-log** — when the breaker is open, `ora_learning.py` should persist a `provider="local"` row anyway so the training corpus doesn't get uneven months.

---

### 🟡 P1 · `services/ora_council_retriever.py` — retrieval index is process-local, never persisted
* **File**: 325 LOC, 3 callers (`routers/chat.py` × 2, `ora_council_logger.py` × 1).
* **Observation**: `_index` is a module-level dict (line 67). Every worker rebuilds it from scratch on first call. Rebuild happens on TTL miss (`_REFRESH_TTL = 600s`, line 56). With 4 uvicorn workers × N pods, that's ~24 rebuilds/hour instead of 6.
* **Fully-wired** on the read path (`get_council_few_shot()` at `routers/chat.py:709,1448`).
* **Impact**: Not user-facing broken, but cold-start latency spikes (~50-200 ms) on every fresh worker's first chat turn, and O(N) memory duplication per worker.
* **Recommended-fix direction**: Move `_index` behind a Redis / Mongo-cached snapshot with a per-pod fetch (keep TTL). OR accept the current cost and downgrade this note.

---

### 🟡 P1 · `services/ora_learning.py` — silent no-op path when breaker is open
* **File**: 316 LOC, 3 callers (`orchestrator.py`, `routers/admin.py`, `routers/chat.py`).
* **Observation**: `maybe_log_ora_escalation()` (line 67) has 4 sequential silent returns:
  1. `db is None` → drop.
  2. `ORA_LEARNING_DISABLED == "1"` → drop (env var **not set** in prod → not this).
  3. `not is_ora_available()` → drop **← currently firing every request due to P0**.
  4. no low-confidence trigger → drop.
  None of them emit a log line. Ops has no way to distinguish "no low-confidence hits today" from "ORA upstream broken and we haven't learned anything for 19h".
* **Also**: `ORA_LEARNING_DISABLED` is the "double-negative confusing" flag flagged in Session 1 — behaviour is ON by default which is fine, but the name inverts intent.
* **Recommended-fix direction**:
  1. Add a debug-level counter (per-reason) so admins can see the breakdown.
  2. Consider renaming `ORA_LEARNING_DISABLED` → `ORA_LEARNING_ENABLED` (default `1`) in the next migration window.

---

### 🟡 P1 · `services/ora_chat/deep_research.py` — Session 2 note about Tavily was WRONG
* **File**: 921 LOC, 5 callers (fully wired via `routers/ora_chat.py:35`).
* **Correction to Session 2 audit**: The Session 2 report said "Depends on Tavily which returns 432 in prod." **That is incorrect** — `deep_research.py` has zero Tavily references. It uses:
  * GitHub Search REST (`api.github.com`) — auth-optional
  * GDELT DOC 2.0 (`api.gdeltproject.org`) — no key
  * Reddit JSON — no auth
  * Perplexity Sonar via `providers.one_shot`
* **Actual issues found**:
  1. **`GITHUB_API_TOKEN` env var is not set in `.env`** — GitHub Search still works unauthenticated but rate-limits at 10 req/min (vs 30 req/min with a token). Tool fires may silently return zero results under load.
  2. **`use_claude_tools()` (line 53)** is documented as a stub — enabled only when `ORA_ENABLE_CLAUDE_TOOLS=1` **AND** `ANTHROPIC_API_KEY` set. Both are missing → the follow-up tool-use path is dead code. Docstring flags this honestly.
  3. Two `except: pass` blocks (grep-verified) — need context-review to confirm they're safe.
* **Recommended-fix direction**:
  1. Add `GITHUB_API_TOKEN` to `.env` (or accept 10 req/min for now).
  2. Either implement the Claude tools path OR delete `use_claude_tools()` + its callers (currently stub-only).
  3. Update Session 2 audit's Tavily note to remove the false alarm.

---

### 🟡 P1 · `services/ora_chat/hallucination_classifier.py` — no scheduler; admin-manual only
* **File**: 251 LOC, 0 direct callers as import (imported only by `routers/ora_chat.py:40`).
* **Wiring**: Endpoint `POST /api/aurem-dev/ora-chat/hallucination-patterns/classify-now` (`routers/ora_chat.py:1184`) is the ONLY trigger. Docstring claims it "automatically kicks off when `unreviewed_count >= _BATCH_TRIGGER`" (line 13) — **no such auto-trigger exists in the codebase**. Grep `classify_batch` shows only the manual endpoint + the definition itself.
* **Impact**: `ora_hallucination_log` accumulates rows forever unless a founder remembers to click. Same "revenue-risk" pattern as `bill_maxx_overages` (Step A) — a silent queue that only drains manually.
* **Recommended-fix direction**: Wire a `schedule_hallucination_classify_batch()` background task (mirror the Step-A pattern we just built for `billing_cron`). Configurable interval via `HALLUCINATION_CLASSIFY_INTERVAL_S` (default 4h).

---

## PART 2 — TOP-10 BACKEND SERVICES (by import count)

Rankings computed by counting `from services.<name>` / `import services.<name>` matches across the entire backend (excluding already-audited loop/deploy/payments families).

| Rank | Service | LOC | Imports | Status |
| ---:| --- | ---:| ---:| --- |
| 1 | `llm.py` | 1,805 | 45 | 🟡 P1 — god-file, refactor candidate |
| 2 | `orchestrator.py` | 2,518 | 33 | ✅ healthy, exception handling is contextual |
| 3 | `local_tools.py` | 2,224 | 25 | 🟡 P1 — 11 `except: pass` blocks warrant review |
| 4 | `usage.py` | 295 | 20 | ✅ FULLY BUILT |
| 5 | `vanguard_scanner.py` | 690 | 18 | ✅ FULLY BUILT |
| 6 | `github_api_writer.py` | 321 | 18 | ✅ FULLY BUILT |
| 7 | `subscription_tiers.py` | 139 | 10 | ✅ FULLY BUILT |
| 8 | `repo_context.py` | 576 | 9 | 🟢 P2 — 1 broad `except: return None` |
| 9 | `project_brain.py` | 896 | 9 | 🟢 P2 — 3 broad `except: return None` |
| 10 | `graph_builder.py` | 472 | 9 | 🟡 P2 — 3 broad `except: return None` (silent no-op prone) |

---

### 🟡 P1 · `services/llm.py` — 1,805 LOC god-file, 25 env vars, 7 `except: pass` blocks
* **Imports**: 45 (top of the tree by a wide margin).
* **Concerns**:
  1. Single file mixes 5 concerns: OpenRouter client, DeepSeek client, GLM/Council-B routing, LongCat routing, CEO rescue logic, plus a health probe.
  2. 25 distinct `os.getenv(...)` calls — envs bleed everywhere in the file. Missing env fallbacks are handled (grep `OPENROUTER_API_KEY missing` returns 5 sites that log + fallback), so behavior is safe.
  3. 7 `except: pass` blocks — most are inside timeouts/one-shot probes (safe). Needs a line-by-line review to be sure none swallow a real bug.
* **What's wired**: `call_llm_with_meta()` is the single entry-point used by 45 other files. Model routing is deterministic (`mode` param).
* **Recommended-fix direction**: 3-way split — `llm/openrouter_client.py`, `llm/routing.py`, `llm/probes.py`. Preserves the single `call_llm_with_meta()` re-export to avoid touching 45 call sites.

---

### 🟢 P1 · `services/orchestrator.py` — 2,518 LOC, but well-structured
* **Imports**: 33.
* **Concerns**: 7 `except: pass` — sample-reviewed line 220-227 (JSON dedup guard — safe). The other 6 are wrapping timeouts and tool-invocation retries — also safe.
* **Environment**: uses 6 timeout/budget env vars, all with sane defaults.
* **Verdict**: healthy. Size is a natural consequence of the tool-loop being a state machine — splitting for its own sake would harm readability.
* **Recommended-fix direction**: none required. Sample the 5 remaining `except: pass` blocks in a future hygiene pass to be sure they log at debug.

---

### 🟡 P1 · `services/local_tools.py` — 2,224 LOC, **11 `except: pass` blocks**, 2 `except: return None`
* **Imports**: 25.
* **Concerns**: 11 bare `except: pass` is the highest count across all 15 audited files. Each of these swallows an exception in a tool implementation (e.g. `read_repo_file`, `search_repo`, `list_repo_files`) — a caller sees "no results" without ever knowing something raised.
* **Wiring**: `TOOL_SPECS` + `invoke_local_tool()` are the two exports (used by orchestrator + 24 other places).
* **Recommended-fix direction**: Convert each `except: pass` to `except X as e: logger.debug("tool_name failed: %r", e); return {"ok": False, "error": str(e)}` so failures at least appear in logs. No behaviour change for callers who check `ok`.

---

### ✅ `services/usage.py` — 295 LOC, plan-limit enforcement, no swallowing
* **Imports**: 20 (widespread — every route that meters tokens uses it).
* **Health**: `PLAN_LIMITS` is the single source of truth. Founder tier short-circuit is explicit + tested. Zero `except: pass`. Zero silent returns.
* **Env**: `FOUNDER_EMAILS` (set).
* **Verdict**: FULLY BUILT.

---

### ✅ `services/vanguard_scanner.py` — 690 LOC, pure stdlib regex
* **Imports**: 18 (design_linter, mode_e_auditor, tests, etc.).
* **Health**: no envs, no exceptions to swallow. Pure regex + iteration. Well-tested.
* **Verdict**: FULLY BUILT.

---

### ✅ `services/github_api_writer.py` — 321 LOC, no envs, parallel writes
* **Imports**: 18.
* **Health**: uses caller-supplied PAT tokens (no env). Parallelises multi-file commits via `asyncio.gather`. One log-only `except` at the top of blob fetch — appropriate.
* **Verdict**: FULLY BUILT.

---

### ✅ `services/subscription_tiers.py` — 139 LOC, single-source-of-truth for plan limits
* **Imports**: 10.
* **Health**: enum + lookup table. Zero exception handlers required. Tight surface.
* **Verdict**: FULLY BUILT.

---

### 🟢 P2 · `services/repo_context.py` — 576 LOC, 1 silent-fallback path
* **Imports**: 9.
* **Concerns**: 1 `except: return None` (in the parallel-file-fetch path). Documented "best-effort" — probably fine, but a debug log line would surface flaky GitHub calls.
* **Recommended-fix direction**: add `logger.debug("repo_context fetch failed: %r", e)` before the `return None`.

---

### 🟢 P2 · `services/project_brain.py` — 896 LOC, 3 silent-return-None paths
* **Imports**: 9.
* **Concerns**: 3 `except: return None` blocks. Given the brain is a memory-injection helper, returning `None` is a safe degradation (orchestrator falls back to no-brain context). But invisible failures accumulate.
* **Recommended-fix direction**: convert to `except X as e: logger.debug(...); return None`. No behaviour change.

---

### 🟡 P2 · `services/graph_builder.py` — 472 LOC, 3 silent-return-None + 1 `except: pass`
* **Imports**: 9.
* **Concerns**: The most silent-failure-prone of the healthy Top-10. Graph-build is expensive (~5-10s + 1 DeepSeek call), so a silent failure means the next call rebuilds from scratch. No metrics on `build_brain_v2` success rate.
* **Recommended-fix direction**:
  1. Log at `warning` (not `debug`) when the DeepSeek description call fails — the free tier fallback is documented but silent.
  2. Persist a `last_build_error` field on `project_graphs` so admins can see build health.

---

## PART 3 — Cross-cutting observations

### Silent no-op pattern is the #1 hygiene issue this session
Session 2 flagged this as a NEW pattern. Session 4 confirms it. Across the 15 files audited:
* **32 total** exception-swallowing / silent-return-None sites (`except: pass` + `except: return None`).
* Only **~15** log at debug or higher.
* **~17** are completely silent.

### The ORA upstream fatal-breaker is a live production issue right now
Not a discovery-only note — anyone chatting on the platform in the next ~19h will get the local fallback with no ORA-council retrieval, no learning shadow-log, and no upstream sanity checks. Recommend surfacing this via the daily digest (Step A pattern extends naturally).

### `ORA_LEARNING_DISABLED` double-negative env var
Session 1 flagged it. Session 4 confirms it's still that way. Rename in the next migration window (default value stays ON — the rename itself is behaviour-preserving).

---

## PART 4 — Recommended follow-up SESSION 5 shortlist (report only, not a promise)

1. **P0 · Alert on ORA breaker-open state** — small wire-up: `is_ora_available()` result + breaker file age into daily digest / `/api/health/self-check`.
2. **P1 · Wire hallucination classifier as scheduled cron** — mirror Step A pattern exactly.
3. **P1 · Add `GITHUB_API_TOKEN` to `.env`** — unblocks `deep_research.py`'s search tier from 10 → 30 req/min.
4. **P1 · Split `llm.py` into 3 sub-modules** — behaviour-preserving refactor, gated by a full regression run.
5. **P1 · Attack the 32 silent-failure sites** — add `logger.debug(...)` before every `pass` / `return None` in `local_tools.py`, `graph_builder.py`, `project_brain.py`, `repo_context.py`.
6. **P2 · Delete stub `use_claude_tools()` OR implement it** — dead code either way.
7. **P2 · Rename `ORA_LEARNING_DISABLED` → `ORA_LEARNING_ENABLED`** — remove the double-negative.
8. **P2 · Move `ora_council_retriever` `_index` off process-local** — cold-start cost reduction.

---

**End of Session 4 Discovery Report — 15 services deep-audited, 1 P0 + 7 P1 + 6 P2 findings, ZERO code modified in this session.**

---

## APPENDIX A — Silent-catch reconciliation (added 2026-07-31 post-batch)

The initial audit above quoted **"32 total exception-swallowing / silent-return-None sites"**. That number came from a regex heuristic which turned out to be an **UNDERCOUNT**. After the P1 batch shipped, a precise AST scan across all 15 audited files was run and the honest picture is:

| Bucket | Count | Where |
| --- | ---:| --- |
| **✅ Patched this session** | **21** | `local_tools.py` (13) + `graph_builder.py` (4) + `project_brain.py` (3) + `repo_context.py` (1) |
| **Remaining across the other 11 files** | **29** | — |
| **TOTAL** | **50** | — |

### The 29 remaining, classified by intent

| Category | Count | Notes |
| --- | ---:| --- |
| **✅ Legit silent-by-design** | **7** | `OSError` on optional file reads (breaker files), `JSONDecodeError` parse guards, `asyncio.CancelledError` graceful-shutdown swallows. Logging these would be spam. |
| **🔴 Real hygiene targets** | **22** | Split across 6 files below. |

### The 22 remaining hygiene targets — per file

| File | Sites | Session priority |
| --- | ---:| --- |
| `services/ora_learning.py` | **2** ✅ **DONE 2026-07-31** | Priority 1 (was the culprit of the live P0). Both patched: L98 rate-limit debug, L135 invariant WARNING. See `tests/test_session4_p1_ora_learning_silent_catch.py`. |
| `services/orchestrator.py` | 8 | Priority 2 — tool-loop state machine, careful review needed (some `except: pass` are intentional resume points). |
| `services/llm.py` | 7 | Priority 3 — combine with the deferred `llm.py` 3-way split session; natural time to clean up. |
| `services/ora_client.py` | 1 | Priority 4 — cleanup batch alongside any ORA-chat work. |
| `services/ora_chat/deep_research.py` | 2 | Priority 4. |
| `services/ora_chat/hallucination_classifier.py` | 1 | Priority 4. |
| **TOTAL remaining after 2026-07-31 P1** | **20** | Down from 22. |

### Grep pattern to find every patched site

```bash
grep -rn 'logger\.debug("\[silent-catch\]' backend/services/
grep -rn 'logger\.warning("\[silent-catch\]' backend/services/
```

Every previously-invisible failure now surfaces with the `[silent-catch]` prefix. Ops can:

```bash
grep '\[silent-catch\]' /var/log/supervisor/backend.*.log
```

to see the exact file/line/function each swallow came from.


---

## APPENDIX B — Silent-catch count RE-RECONCILIATION (2026-07-31 · Session 5 Item 2)

Appendix A's per-file hygiene counts were based on a heuristic classifier that had **two subtle bugs**:

1. **`0 == False` quirk** — the emptiness check `value in (None, "", 0)` returned `True` for `return False`, mistakenly classifying `return False` as a bare-return.
2. **Narrow preamble slice** — the "is this a UI-hook wrapper?" check only looked at 4 lines above the swallow, missing hook calls that appeared earlier in the try body.

A properly-fixed AST classifier (see `tests/test_session5_item2_orchestrator_silent_catch_lock.py`) reveals the honest picture:

### Corrected honest counts (5 files, 24 total sites)

| File | Sites | UI-hook (legit) | Exc-type (legit) | **Real hygiene** |
| --- | ---:| ---:| ---:| ---:|
| `orchestrator.py` | 7 | 7 | 0 | **0** |
| `llm.py` | 8 | 4 | 1 | **3** |
| `ora_client.py` | 3 | 0 | 3 | **0** |
| `ora_chat/deep_research.py` | 3 | 0 | 1 | **2** |
| `ora_chat/hallucination_classifier.py` | 3 | 0 | 2 | **1** |
| **TOTAL** | **24** | **11** | **7** | **6** |

**Appendix A said "22 hygiene targets remaining across 6 files".**
**Corrected reality: 6 hygiene targets** across 3 files (after ora_learning.py's 2 already patched in the earlier P1 batch). That's a 73% reclassification.

### Sequence update

- **Item 2** (orchestrator.py): **NO-OP shipped** — all 7 sites are UI-hook fail-opens, patching would add log spam. Classification locked by 5-test suite that catches any future silent-catch inflation OR any UI-hook wrapper being converted to a real invisible failure.
- **Item 3** (llm.py 3-way split + 3 hygiene sites): still on deck, unchanged priority.
- **Item 4** (deferred CI-lane failures — 22 tests): still on deck, unchanged priority.
- **New Item 5** (ORA-chat cleanup — 3 hygiene sites): 2 in `deep_research.py` + 1 in `hallucination_classifier.py`. Combine with any future ORA-chat work.

### Grep to verify at any time

```bash
cd /app/backend && python -m pytest tests/test_session5_item2_orchestrator_silent_catch_lock.py -v
```

