# SESSION 5 DEEP AUDIT — Vanguard + MCP (Discovery Only, Zero Fixes)

**Date**: 2026-07-31 (post-redeploy build_hash `m1c61197`)
**Scope**: 8 files spanning the Vanguard security-scan family + the MCP protocol server. Every file was invoked (or curled on prod) — this is not a file-presence audit.
**Discipline**: READ-ONLY. Zero source edits during this session. Every finding gets a Severity (P0 / P1 / P2) + FIX DIRECTION only (no code).

---

## Executive summary

| Group | Files | LOC | FULLY BUILT | HALF BUILT | UNWIRED/DISABLED | UNCLEAR | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| Vanguard | 6 | 2,262 | 4 | 1 | 1 | 0 | Core scanner + verify + audit rock-solid; CI-ingest is disabled on prod (env not set); config store lives on disk only (Mongo persistence half-built). |
| MCP | 2 | 2,296 | 2 | 0 | 0 | 0 | Full 21-tool MCP server live at `/api/aurem-dev/mcp`, OAuth + JWT + API-key auth all wired. Scope-classifier silently degrades to "read+project" on any LLM failure — legit fail-open but observability-blind. |
| **TOTAL** | **8** | **4,558** | **6** | **1** | **1** | **0** | **1 real P1** (CI-ingest disabled on prod), **1 P2** (scoped-tools silent fallback), plus 4 minor P2 notes. |

---

## PART 1 — VANGUARD

### ✅ `services/vanguard_scanner.py` — FULLY BUILT (690 LOC, 18 imports)
**Real invoke evidence** — hit 7 canonical vulnerability patterns:

| Payload | Detected? | Rule fired |
|---|---|---|
| AWS access key `AKIA...` | ✅ | `aws_access_key` |
| Stripe live key `sk_live_...` | ✅ | `stripe_live_key` |
| Generic OpenAI `sk-proj-...` | ✅ | `generic_api_key` |
| `eval(user_input)` | ✅ | `eval_usage` |
| `exec(open("/etc/passwd").read())` | ✅ | `exec_usage` |
| `subprocess.run(cmd, shell=True)` | ✅ | `subprocess_shell_true` |
| `os.system(cmd)` | ✅ | `os_system` HIGH |
| Generic env-var-name secret (`JWT_SECRET = "supersecret..."`) | ❌ | (no rule — probably intentional, low entropy string) |

**Public API**: `scan_text(text, filepath, *, include_dangerous=True)`, `scan_file_blocks(blocks)`, `has_critical(findings)`, `run_two_round_scan(file_blocks, *, round1_budget=10.0, round2_budget=20.0)`. All working.

**Callers verified live**: `task_diff.py`, `loop_engine.py`, `scanner_utils.py`, `routers/cto_projects.py`, and 14 more via grep. Fully consumed.

**Minor** 🟢 P2: No detection for low-entropy env-name secrets (JWT_SECRET, DATABASE_PASSWORD without a real high-entropy value). Session 4 baseline acknowledged; a future session could add entropy-check rules — but MAY produce false positives on legit dev configs.

---

### ✅ `services/vanguard_verify_agent.py` — FULLY BUILT (646 LOC, 3 imports)
**Real invoke** with a two-file payload (one malicious, one safe):

```
verify_patch({"bad.py": "os.system(cmd)", "safe.py": "add(a,b)"}, mode="swift")
→ pass: False
→ findings_count: 2
   - os_system              severity=HIGH     (regex layer)
   - <llm-detected>         severity=CRITICAL (agent layer)
→ regex/agent/e2b layers all recorded in return dict
```
Multi-layer defence works: regex catches known patterns, LLM agent catches semantic issues, e2b sandbox available for runtime probes.

**Callers**: `task_diff.py`, `loop_engine.py`, `scanner_utils.py`, `routers/cto_projects.py` — fully wired into the ship pipeline.

**Env dependency**: `OPENROUTER_API_KEY` — set on prod ✅. If missing, agent layer skips (documented, not silent).

**Minor** 🟢 P2: The LLM agent finding sometimes returns with `name=None`, `filepath=None`, `line=None` — the raw LLM JSON isn't fully normalised into the shape regex findings use. **Direction**: add a small normaliser that falls back to `name="llm_detected"`, `filepath=first_key(blocks)`, `line=1` when the LLM output omits them, so downstream consumers can uniformly filter by field.

---

### ✅ `services/vanguard_audit.py` — FULLY BUILT (186 LOC, 2 imports)
Simple, focused. Three async DB-writer/reader functions:
- `log_blocked_commit(db, *, user_id, project, verify_result, project_id=None, task_id=None)` — writes to `vanguard_blocks` collection
- `recent_blocks(db, *, limit=25)`
- `weekly_stats(db, *, since_days=7)`

No env vars, one legit `except Exception: pass` inside a mongo write (idempotency guard) — reasonable.

**Verdict**: FULLY BUILT.

---

### ⚠️ `services/vanguard_config.py` — HALF BUILT (143 LOC, 2 imports)  🟡 **P2**
**What works**: `get_config()`, `save_config()`, `get_mode_settings()`, `default_config()` — all callable, all return dicts.

**Half-built aspect**: Config is stored in a **local JSON file** (`/app/backend/data/vanguard_config.json` or similar) — NOT in Mongo. On a multi-pod deploy, each pod has its own config file. Admin changes made via `POST /admin/vanguard/config` only affect the pod that handled the write.

**Env vars**: `VANGUARD_VERIFY_BLOCK_LEVEL`, `VANGUARD_VERIFY_ENABLED` — both used as env-first override with file fallback.

**Recommended-fix direction** (no code):
1. Move config storage to Mongo `vanguard_config` collection (single row keyed on `_id="active"`)
2. Or accept the per-pod state and add a `POST /admin/vanguard/config/broadcast` that forces all pods to refetch on the next request (via a short-TTL cache)

---

### ⛔ `routers/vanguard_ci.py` — UNWIRED on prod (327 LOC, 1 import)  🔴 **P1**
**Live prod check**:
```
$ curl -X POST https://auremcto.com/api/aurem-dev/vanguard/ci-findings ...
HTTP 503  {"detail":"CI ingest disabled — AUREM_CI_INGEST_TOKEN not configured"}
```
The router is registered, the endpoint exists, auth is enforced correctly — but the **feature itself is silently disabled on production because `AUREM_CI_INGEST_TOKEN` is not in the env**. Any external CI (GitHub Actions, GitLab, etc.) attempting to POST vanguard findings gets a 503.

**Three endpoints**:
| Method | Path | Prod status |
|---|---|---|
| POST | `/api/aurem-dev/vanguard/ci-findings` | 503 (disabled, no token) |
| GET | `/api/aurem-dev/vanguard/ci-findings` | 401 (needs admin JWT) — WORKING once auth'd |
| GET | `/api/aurem-dev/vanguard/ci-ingest-status` | 401 (needs admin JWT) — WORKING once auth'd |

**Impact**: The whole "external CI ingest" feature — designed to let GitHub Actions post Vanguard scan results back to Aurem — is dark on prod. No one is ingesting anything from external CI. The read-side (list findings, status) works for admins.

One `except Exception: pass` in the router (silent-catch — carry-forward to Item 2 orchestrator batch).

**Recommended-fix direction**:
1. **Founder task**: Generate an `AUREM_CI_INGEST_TOKEN` value, set it in prod env, share it with the external CI systems that need to POST.
2. **Or**: Delete the endpoint if external CI ingest isn't a live product feature — dead endpoints returning 503 are worse than no endpoints.
3. Either way, the CI-ingest-status endpoint should surface the "token not configured" state to admins (currently only surfaces in the 503 body).

---

### ✅ `routers/admin_vanguard.py` — FULLY BUILT (70 LOC, 1 import)
Two admin endpoints for reading/writing the vanguard config (which is the per-pod JSON file from `vanguard_config.py` above):

| Method | Path | Auth |
|---|---|---|
| GET | `/api/aurem-dev/admin/vanguard/config` | admin |
| POST | `/api/aurem-dev/admin/vanguard/config` | admin |

Both wire directly into `vanguard_config.get_config()` / `save_config()`. Works — but inherits the "per-pod state" limitation from the underlying config module.

---

## PART 2 — MCP

### ✅ `routers/mcp.py` — FULLY BUILT (1,941 LOC, 6 imports, 7 endpoints)
**Real prod invoke**:

```
$ curl https://auremcto.com/api/aurem-dev/mcp
HTTP 200 (large JSON)
→ 21 MCP tools registered:
  list_projects, ship_code, get_task_status, get_recent_commits,
  read_repo_file, list_repo_files, search_repo, write_repo_file,
  run_vanguard_scan, get_scan_status, get_repo_health,
  get_repo_structure, get_project_info, qa_traceability_matrix,
  qa_open_gaps, qa_regression_index, qa_coverage_summary,
  run_canary_e2e, qa_mock_reality_check, qa_static_vs_behavioural_ratio,
  (+ 1 more)
→ auth: JWT (from auremcto.com login) OR API key (prefix sk-aurem-)
→ oauth: authorization_code + PKCE-S256, discovery URL provided
→ transport: streamable-http
→ protocolVersion: 2025-03-26 (matches spec)
```

**7 endpoints all live on prod**:
| Method | Path | Prod status |
|---|---|---|
| GET | `/api/aurem-dev/mcp` | 200 (discovery + tool list) |
| POST | `/api/aurem-dev/mcp` | (needs Bearer auth — JSON-RPC over HTTP) |
| GET | `/api/aurem-dev/mcp/.well-known/mcp` | 200 (public discovery) |
| POST | `/api/aurem-dev/mcp/keys` | (admin — mint API key) |
| GET | `/api/aurem-dev/mcp/keys` | (admin — list keys) |
| DELETE | `/api/aurem-dev/mcp/keys/{key_tail}` | (admin — revoke) |
| GET | `/api/aurem-dev/mcp/install-links` | 401 (needs auth — returns config for Claude/Cursor/Windsurf install) |

**Env dependency**: `AUREM_PUBLIC_BASE_URL` — used for the OAuth redirect URLs. Verified live on prod (`https://auremcto.com`).

**Two silent-catch sites** in the file (per AST scan) — carry-forward to the deferred silent-catch cleanup batch.

**Minor** 🟢 P2 — File is 1,941 LOC. Similar to `llm.py`, would benefit from a future split (`mcp/rpc.py`, `mcp/tools.py`, `mcp/oauth.py`, `mcp/keys.py`) — but LESS urgent than llm.py because the surface area is narrower and the callers (external MCP clients) don't care about internal structure.

---

### ✅ `services/mcp_scoped_tools.py` — FULLY BUILT (355 LOC, 4 imports) — with one **P2 observability gap** 🟡
**Real invoke evidence**:

```
group_for_tool("list_projects")  → "project"
group_for_tool("ship_code")      → "write"
group_for_tool("read_repo_file") → "read"
group_for_tool("nonexistent")    → None

classify_tool_groups("show me my projects")  → ['read','project']  (LLM)
classify_tool_groups("ship a fix")           → ['read','project']  (LLM fallback ← concern)
classify_tool_groups("run security scan")    → ['read','project']  (LLM fallback ← concern)
```

**The gap**: When `OPENROUTER_API_KEY` isn't set OR the LLM classifier call fails for ANY reason (timeout, rate-limit, upstream 5xx), `classify_tool_groups()` **silently falls back to `["read", "project"]`** — the same 3 tools regardless of user intent (line 190).

The fallback is **documented** in the docstring (line 163-166 — "Falls back to `["read", "project"]` on: LLM timeout / error / import failure / LLM returned non-JSON or unknown groups") — so it's fail-open by design. BUT there is ZERO log line when the fallback kicks in.

**Impact**: If OpenRouter has a bad day, every MCP session gets the wrong tool scope (missing `ship_code`, `run_vanguard_scan`, `write_repo_file`, etc.) — silently. The user experience degrades from "AI has 21 tools" to "AI has 3 tools" with no indication why.

**Recommended-fix direction** (Item to add to Session 5 backlog, aligned with Session 4 P0 pattern for ORA breaker):
1. Add a `logger.debug("[mcp-scoped] classify LLM fallback: %r", reason)` line inside the `except` at line 174 AND after the `if isinstance(parsed, list): ...` block at line 190
2. Optionally track a Mongo counter `mcp_classify_fallback_count_daily` so ops can alert if the fallback rate exceeds a threshold
3. Consider a heuristic keyword-based classifier as a secondary fallback (before jumping straight to the safe default) — e.g. "ship", "deploy", "commit" → include `write` group

**Verdict**: FULLY BUILT + working, but observability-blind on the LLM-degraded path.

---

## PART 3 — Cross-cutting observations

### Silent fail-open remains the recurring pattern
- Vanguard CI-ingest is silently disabled on prod (503, no alert).
- MCP scoped-tools silently degrades to a fixed 3-tool scope when LLM fails (no log).
- Both mirror the Session 4 P0 ORA-breaker pattern — features that work locally but degrade invisibly in production.

### Prod-first verification worked
Every finding above was captured against `https://auremcto.com` (or `launch-pad-237.emergent.host`), not the preview pod. This session's process-note (`prod-URL first`) has already prevented one false alarm — earlier I flagged `/mcp` bare as returning HTML, but that was a routing gotcha (correct prefix is `/api/aurem-dev/mcp`), not a real bug.

### Env-var configuration debt
Missing on prod (from what I could verify without admin auth):
- `AUREM_CI_INGEST_TOKEN` — makes vanguard-ci ingest a dead endpoint
- (all others verified present via indirect checks: `OPENROUTER_API_KEY`, `ORA_API_KEY`, `AUREM_PUBLIC_BASE_URL`)

Recommend: an `/api/health/env-required` endpoint that reports which non-secret env vars are set vs missing — same "surface it" spirit as the ORA breaker endpoint from Session 4 P0.

---

## PART 4 — Session 6 shortlist (report only, not a commitment)

Ordered by real-world impact:

1. **🔴 P1 · Set `AUREM_CI_INGEST_TOKEN` on prod OR delete the endpoint** — dark ingest feature.
2. **🟡 P2 · Log MCP scoped-tools LLM fallbacks** — 2-line change, matches ORA-breaker observability pattern.
3. **🟡 P2 · Normalise LLM findings in `vanguard_verify_agent`** — populate `name` / `filepath` / `line` when the LLM omits them.
4. **🟢 P2 · Move `vanguard_config` to Mongo** — remove the per-pod-JSON-file limitation.
5. **🟢 P2 · Consider entropy-check rule in `vanguard_scanner`** for env-name secrets — but only if false-positive rate can be kept < 5%.
6. **🟢 P2 · Long-term `routers/mcp.py` split** — 1,941 LOC — after `llm.py` split lands.

---

**End of Session 5 Discovery — 8 files deep-audited via real invoke/curl, 1 P1 + 5 P2 findings, ZERO source edits during discovery.**
