# Phase 3 · Batch 7 — Pre-migration Survey Report

**Date**: 2026-02-12 (Fork session · post-Batch 6 hold)
**Scope**: 6 files with a total of **21 raw `httpx.AsyncClient`
sites**. Zero breaker/retry/limits signals across all of them.
Founder greenlit read-only survey; no migration until they say go.

---

## TL;DR — verdict per file

| # | File | Sites | Verdict | Notes |
|---|------|-------|---------|-------|
| 1 | `services/codebase_indexer.py` | 2 (**1 real** + 1 type annotation) | ✅ **SAFE** | Same `client: httpx.AsyncClient` type-annotation pattern as `repo_heal.py` (kept as-is; annotation stays). 1 real site is a pooled tree walk. Drop-in `ext_client("github")`. |
| 2 | `services/personal_track_smoke.py` | 2 | ✅ **SAFE** | Two smoke-test probes with `timeout=20.0`. No auth flow, no state. Drop-in. |
| 3 | `services/github_oauth.py` | 3 | ⚠️ **AUTH-CRITICAL — safe but flag** | See writeup below. |
| 4 | `services/project_brain.py` | 3 | ⚠️ **1 tight-timeout override needed** | See writeup below. |
| 5 | `services/github_org_client.py` | 5 | ✅ **SAFE** | All 5 use a shared `_TIMEOUT` module constant. Simple POST/GET to GitHub org endpoints. Drop-in. |
| 6 | `services/github_app.py` | 6 | ⚠️ **AUTH-CRITICAL — safe but flag** | See writeup below. |

**Net**: 21 sites migratable. All are technically drop-in swaps —
**no** custom `httpx.Limits` pool, **no** manual retry loops, **no**
custom breakers. But 3 files carry non-trivial blast radius and
1 file has a tight-timeout override that must be preserved.

---

## 🔍 Detailed writeup — the 3 files that need eyes-on

### ⚠️ `github_oauth.py` (3 sites) — AUTH-CRITICAL, safe migration

**Sites**:
- Line 53 `exchange(code)` — POSTs to `github.com/login/oauth/access_token` (the OAuth code exchange endpoint, **NOT** `api.github.com`).
- Line 72 `gh_user(token)` — GET `api.github.com/user`.
- Line 85 `gh_repos(token)` — GET `api.github.com/user/repos`.

**Nuance**: `exchange()` hits `github.com` (the web host), not
`api.github.com`. Both belong logically to the same provider and
usually fail together, so using dep name `"github"` for all 3
sites is correct — a wrapper breaker tripping on api.github.com
5xx will also fast-fail the OAuth exchange path, which is the
behavior we want (if github.com's OAuth server is having an
outage, api.github.com is almost certainly having it too).

**Blast radius if it breaks**: 🔥 High — no user can log in via
GitHub OAuth. Migration is a pure client-swap with zero logic
change, but I want the founder aware before it ships.

**Migration plan**: All 3 sites → `ext_client("github", ...)`.
No custom timeout override needed (15s matches wrapper default
comfortably).

---

### ⚠️ `project_brain.py` (3 sites) — 1 tight-timeout override

**Sites**:
- Line 115 `_recent_commits_context` — `timeout=4` (four seconds!)
- Line 474 `_gh_list_files` — `timeout=8.0`
- Line 510 `_gh_read_small` — `timeout=8.0`

**Nuance**: Line 115's `timeout=4` is a **deliberately tight
budget**. This path is called from the chat/brain enrichment
flow and MUST not block the chat response beyond the enrichment
budget. The wrapper's `"github"` dep default is `read=20s` —
5x too long. **Migration MUST override to
`timeout=httpx.Timeout(connect=3.0, read=4.0, write=3.0, pool=3.0)`**
or chat responses will get 4x slower on cold-cache GitHub
lookups.

**Blast radius if we drop the override**: Chat responses slow
by ~15s on brain-enrichment-cache-miss. Not fatal, but
noticeable and reported-bug-worthy within a day.

---

### ⚠️ `github_app.py` (6 sites) — AUTH-CRITICAL, safe migration

**Sites**: all 6 use `httpx.AsyncClient(timeout=15.0)` — no
custom limits, no custom pool tuning, no retries. Sites cover:
- Line 217: **installation token minting** (mint access_token
  from JWT for a specific installation)
- Line 269: `list_installations` (App-JWT auth)
- Line 294: `list_installations_for_user` (OAuth-token auth)
- Line 309: `list_installation_repositories`
- Line 333: `get_installation`
- Line 347: `delete_installation`

**Nuance**: This is the GitHub App backbone — the file that
powers the entire connect-repo-via-GitHub-App flow. `_INSTALL_TOKEN_CACHE`
is a per-installation TTL cache (line 210) but it's a plain dict
keyed on `installation_id` — **orthogonal to httpx**, so migration
doesn't touch the cache logic. Pagination via `Link` header at
line 276 works transparently with `ext_client` (yielded
`httpx.AsyncClient` supports response headers normally).

**Blast radius if it breaks**: 🔥 High — GitHub App users can't
push commits, can't list installs, can't complete the connect
flow.

**Migration plan**: All 6 sites → `ext_client("github", ...)`.
15s timeout matches wrapper default; no override needed.

**Why this is safe unlike `github_api_writer.py`**: The writer
uses `httpx.Limits(max_connections=20, ...)` because it fans out
20 concurrent blob-upload POSTs — the wrapper needs a
`limits=` upgrade to support that. Nothing in `github_app.py`
does parallel `asyncio.gather` — every callsite is a single
sequential POST/GET, so the wrapper's default pool is fine.

---

## 📋 Recommended Batch 7 scope

**Ship (all 6 files, 21 sites)** with the following per-file
handling:

| File | Dep | Timeout override | Notes |
|---|---|---|---|
| `codebase_indexer.py` | `github` | none (30s → 30s explicit) | type-annotation preserved |
| `personal_track_smoke.py` | `github` | 20s explicit | 2 probes |
| `github_oauth.py` | `github` | none (15s default) | 3 sites, auth-critical |
| `project_brain.py` (site 1) | `github` | **`read=4.0` MUST override** | 4s tight budget |
| `project_brain.py` (sites 2,3) | `github` | 8s explicit | |
| `github_org_client.py` | `github` | `_TIMEOUT` explicit | 5 sites |
| `github_app.py` | `github` | none (15s default) | 6 sites, auth-critical |

**Total**: 21 sites, **20 real migrations + 1 preserved type
annotation** (`codebase_indexer.py` line 74).

**Confidence**: HIGH on 4 files (`codebase_indexer`,
`personal_track_smoke`, `github_org_client`, `project_brain`
with the timeout preserved). HIGH-with-attention on the 2
auth-critical files (`github_oauth`, `github_app`) — no logic
change, but blast radius means I want the founder aware and
would recommend deploying Batch 7 during business hours rather
than late-night unattended, so that a rollback (if ever needed)
can happen fast.

---

## Post-survey action for main agent

**Hold in place**. Do NOT start migrating until founder says go.
Options for founder to consider:

- **A** (recommended): Ship all 6 files (21 sites) as one Batch 7,
  during business hours, with pinning tests that specifically
  guard: (a) the 4-second timeout override in
  `project_brain.py`, (b) the `_INSTALL_TOKEN_CACHE` still
  intact, (c) OAuth `exchange` still hits the correct
  `github.com` host.
- **B**: Split — ship the 4 non-auth-critical files
  (`codebase_indexer` + `personal_track_smoke` +
  `github_org_client` + `project_brain`, 12 sites) first as
  "Batch 7A", verify prod for a few hours, then ship
  `github_oauth` + `github_app` (9 sites) as "Batch 7B" with
  a supervised watch.
- **C**: Skip Batch 7 entirely and move on to the
  `ext_client(limits=)` wrapper upgrade → `github_api_writer.py`
  supervised session next.
