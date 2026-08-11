# Phase 3 · Batch 5 — Pre-migration Survey Report

**Date**: 2026-02-12 (Fork session · Batch 4 hold)
**Scope**: 4 files originally proposed for Batch 5. Founder asked
for a survey BEFORE any migration to flag custom breakers /
retries / anything that could break under a naive `ext_client`
swap (same discipline that caught `ora_client.py` in Batch 3).

---

## TL;DR — verdict per file

| # | File | Sites | Verdict | Reasoning |
|---|------|-------|---------|-----------|
| 1 | `services/hosted_deploy.py` | — | ❌ **DOES NOT EXIST** | I named the file wrong in the Batch 4 wrap-up. Deploy logic is spread across `vercel_platform_deploy.py`, `vercel_skills.py`, `github_deploy_service.py`, `ftp_ssh_deploy.py`, `github_deploy_service.py`. Two of those (`vercel_platform_deploy.py`, `vercel_skills.py`) are ALREADY MIGRATED (8 sites total on `ext_client("vercel")`). Real candidates left: `github_deploy_service.py` (4 sites) — see line item below. |
| 2 | `services/supabase_provisioner.py` | 5 | ✅ **SAFE** | Pure external POST/GET to Supabase management API. Zero custom breakers, zero manual retry loops, zero rate-limit handling. Every callsite is a single `httpx.AsyncClient(timeout=_TIMEOUT)` block followed by status-code branching. Drop-in migration to `ext_client("supabase")` — the same dep name already used by `integration_health.py` probe. |
| 3 | `services/dev_skills.py` | 7 | ⚠️ **MOSTLY SAFE — 1 nuance** | 6/7 sites are pure GitHub reads (search, tree, contents, blob). 1 site (line 112 `github_search_code`) has a hardcoded fallback comment (`if GitHub code-search rate-limited or empty, grep the tree`) — but the fallback is a logical branch, NOT a manual breaker. Migration is safe; the retry_guard breaker will just fast-fail earlier on true 429/5xx bursts, which is what we want. |
| 4 | `services/github_api_writer.py` | **2 real** + 2 type annotations | ⚠️ **NEEDS CARE — 3 non-obvious wrinkles** | See detailed writeup below. NOT a simple drop-in. |
| 5 | `services/github_deploy_service.py` (bonus — replaces the missing hosted_deploy) | 4 | ✅ **LIKELY SAFE** | Need a per-site read before I stamp it as safe. Adding to Batch 5 scope. |

**Net**: 12 raw AsyncClient sites migratable across `supabase_provisioner` + `dev_skills` + `github_deploy_service` (needs quick per-site read). 2 sites in `github_api_writer.py` are migratable **only with care** — see wrinkles.

---

## 🔍 Detailed writeup — `github_api_writer.py`

**File**: `/app/backend/services/github_api_writer.py` (322 LOC)
**Purpose**: THE production-friendly GitHub write path — powers
`commit_files` (multi-file atomic commit via Git Data API) and
`revert_commit` (non-destructive revert on top of HEAD). Both
happen without a local `git` binary — pure REST. This is the
file that lets ORA actually ship code to your repo.

### Breaker / retry / cooldown signals
- `grep -nE "_breaker|_trip|get_breaker|call_with_retry|_cooldown|_retry"` → **zero matches**. No custom breaker logic, no manual retries. Naive migration will NOT double-track failures.
- `grep -n "record_failure|record_success"` → **zero matches**. Doesn't touch retry_guard directly.

### Non-obvious wrinkles (why this is NOT a Batch 4-style drop-in)

**Wrinkle 1 — Custom `_LIMITS` connection pool (line 32):**
```python
_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=20)
```
Both `commit_files` and `revert_commit` construct their
`httpx.AsyncClient` with `limits=_LIMITS`. This is because both
functions do **parallel `asyncio.gather`** over blob uploads
(`_upload` in commit_files line 120, `_build_spec` in
revert_commit line 260) — up to 20 concurrent POSTs to
`/git/blobs` in flight. The default httpx pool is 100
connections but only 20 keepalive; the raw `_LIMITS` here
pins the keepalive to 20 to match the parallel-fan-out size,
avoiding TIME_WAIT churn on the socket.

`ext_client()` doesn't currently accept a `limits=` kwarg —
looking at `services/http/client.py` it only takes `timeout`,
`headers`, and merges with per-dep defaults. **Migration
requires either:**
  - (a) Add `limits: httpx.Limits | None = None` parameter to
    `ext_client()` and pass through to the inner
    `httpx.AsyncClient(...)` — a real 3-line wrapper API change,
    NOT a per-site swap, OR
  - (b) Accept the wrapper's default pool for these 2 sites
    (probably fine — httpx default is 100/20 which is bigger than
    the pinned 20/20 here, so parallel fan-out won't be throttled).
    But this is a behaviour change from an explicit pool cap that
    was chosen deliberately.

**My recommendation**: option (a). It's a small, additive change
to the wrapper API that Batch 6+ files (github_sync.py, chat.py)
will also want. Do it as a supervised change to the wrapper
FIRST, then migrate this file.

**Wrinkle 2 — 60-second timeout is unusually long:**
```python
async with httpx.AsyncClient(timeout=60.0, limits=_LIMITS) as client:
```
The wrapper's `github` dep default is `read=20s` (from
`_DEP_TIMEOUTS`). 60s here is deliberate — a large commit with 50
files pushes a `git/trees` request whose body is 50 blob-sha
entries, and GitHub's tree endpoint sometimes takes 15-30s on
big monorepos. Migration MUST override with
`timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)`
or we'll start timing out big commits on prod.

**Wrinkle 3 — `fetch_file()` and `_get_branch_head()` take a
CLIENT parameter (lines 45, 65):**
```python
async def fetch_file(client: httpx.AsyncClient, owner: str, ...):
async def _get_branch_head(client: httpx.AsyncClient, owner: str, ...):
```
These are session-scoped helpers — they reuse the caller's client
so the connection pool + keepalive works across the 10+ REST hops
of a single commit. This is EXACTLY the pattern
`ext_client()` supports (yields an `httpx.AsyncClient`), so the
type annotations stay unchanged and callers just wrap their
`ext_client("github", ...)` block around the existing call chain.
Same pattern I already used for `repo_heal.py` in Batch 4.

### Migration plan for github_api_writer.py (2 sites)

1. **First**: Extend `ext_client()` in `services/http/client.py` to
   accept an optional `limits: httpx.Limits | None = None` kwarg.
   Add a pinning test. Ship this as a standalone wrapper-API
   change BEFORE touching github_api_writer.
2. **Then**: Migrate the 2 sites (line 112 `commit_files`,
   line 219 `revert_commit`) to
   `ext_client("github", timeout=httpx.Timeout(read=60.0, ...), limits=_LIMITS)`.
3. **Pinning test**: assert both `commit_files` and `revert_commit`
   still pass the 20-keepalive pool cap through the wrapper.

**Blast radius if we get this wrong**: 🔥 High. This is the
codepath ORA uses to actually push commits to customer repos.
Any regression breaks live deploys. **Deserves a supervised
session** — same care as ChatPanel / loop_engine, NOT overnight.

---

## 📋 Recommended Batch 5 scope

**Ship overnight (safe)**:
1. `supabase_provisioner.py` — 5 sites → `ext_client("supabase")`
2. `dev_skills.py` — 7 sites → `ext_client("github")`
3. `github_deploy_service.py` — 4 sites → per-site read first,
   then migrate (probably `github` dep)
**Total safe**: ~16 sites.

**Defer to supervised session (with the wrapper API upgrade)**:
4. `github_api_writer.py` — 2 sites (needs `ext_client(limits=...)` support first)

**Confidence level**: HIGH on 1-3, MEDIUM-HIGH on 4 (all 3
wrinkles are known and mitigable, none are blockers — but the
blast radius means I want you present when this lands).

---

## Post-survey action for main agent

**Hold in place**. Do NOT start migrating any of the above until
founder gives an explicit go on scope + timing. Options for
founder to consider:
- **A**: Ship items 1+2 (12 sites) overnight, defer 3 for another
  survey pass and 4 for supervised.
- **B**: Ship items 1+2+3 (~16 sites) overnight after a
  5-min per-site read of github_deploy_service.
- **C**: Ship items 1+2+3+4 together — needs the wrapper API
  upgrade first + all done supervised.
- **D**: Skip Batch 5 entirely for now and go to ChatPanel /
  loop_engine / custom-breaker reconciliation supervised.
