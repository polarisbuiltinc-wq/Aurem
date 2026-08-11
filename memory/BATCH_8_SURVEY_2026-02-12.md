# Phase 3 · Batch 8 — Pre-migration Survey Report

**Date**: 2026-02-12 (Fork session · post-deploy-discipline landing)
**Scope**: **Routers layer** — first foray into `/app/backend/routers/`
after 54 sites across 21 service files landed on prod. 9
router files with 12 total sites surveyed. Zero real breaker/
retry/limits signals (all grep hits were false positives —
mentions of `llm_cost_breaker`, `guard17_breakers` endpoint
name, `retry_after_seconds` for OUR own rate-limit responses,
none touching outbound httpx).

Also: **this is the first survey conducted under the tightened
deploy verification discipline** — every migration below will
be dispatched with a documented expected-SHA and post-dispatch
`git show <sha>:...` content check per
`memory/DEPLOY_VERIFICATION_CHECKLIST.md`.

---

## TL;DR — verdict per file

| # | File | Sites | Verdict | Dep name(s) |
|---|------|-------|---------|-------------|
| 1 | `routers/admin_qa.py` | 3 | ✅ **SAFE** | `github` (2) + **new** `vscode_marketplace` (1) |
| 2 | `routers/admin_bin.py` | 2 | ✅ **SAFE** | `github` + `openrouter` |
| 3 | `routers/admin_projects_brain.py` | 1 | ✅ **SAFE** | **new** `internal_probe` |
| 4 | `routers/admin_ops_config.py` | 1 | ✅ **SAFE** | **new** `cloudflare` |
| 5 | `routers/admin_users.py` | 1 | ✅ **SAFE** | `resend` |
| 6 | `routers/upload.py` | 1 | ✅ **SAFE** | `openrouter` — **45s timeout must preserve** |
| 7 | `routers/codebase_health.py` | 1 | ⚠️ **DEFERRED — supervised** | See writeup |
| 8 | `routers/fix_pipeline.py` | 1 | ✅ **SAFE** | `github` |
| 9 | `routers/github_oauth.py` | 1 | ⚠️ **AUTH-adjacent — safe, flag** | `github` — see writeup |

**Net migratable in this batch**: **11 sites across 8 files.**
**Deferred**: 1 site (`codebase_health.py`, custom
multi-value `httpx.Timeout` that needs a preserve-test to
guard against wrapper default drift).

---

## 🔍 Detailed writeup — the 2 files that need eyes-on

### ⚠️ `routers/codebase_health.py` (1 site) — DEFER to Batch 9 or supervised

**Site**: line 559 constructs the client with a *deliberately
tuned three-value timeout*:

```python
_timeout = httpx.Timeout(45.0, connect=6.0, read=15.0)
async with httpx.AsyncClient(timeout=_timeout) as client:
    blobs, tree_sha = await _list_repo_tree_with_sha(client, ...)
```

The `httpx.Timeout(45.0, connect=6.0, read=15.0)` signature is
subtle: the positional `45.0` sets the "overall" timeout (write
+ pool default to that), and `connect=6.0` / `read=15.0`
override two of the four slots. This is different from every
other migration in this session so far — those all used a
single scalar or the explicit 4-value form.

**Migration is straightforward** (just pass the same `_timeout`
object to `ext_client("github", timeout=_timeout)`), BUT it
needs a **preserve-timeout pinning test** so a future refactor
doesn't accidentally simplify it to `timeout=45.0` and lose
the 6s connect / 15s read discipline.

Also: `client` is passed to `_list_repo_tree_with_sha` which
does 20+ GitHub API calls per health scan (session-scoped
helper pattern like `repo_heal.py`). The type annotation on
`_list_repo_tree_with_sha` — if any — must survive.

**Recommendation**: Ship this in a mini-batch of its own after
Batch 8 lands and verifies. Not a blocker, just deserves the
same "one file, one commit, one deploy, verified" discipline
as `github_api_writer.py` will get.

---

### ⚠️ `routers/github_oauth.py` (1 site) — auth-adjacent, safe drop-in

**Site**: line 80 `_gh_primary_email(token)` — during OAuth
signup, if GitHub `/user` returns `email=null` (user marked
email private), we hit `/user/emails` to fetch the verified
primary. Called ONLY from the OAuth callback path — every
failure returns `None` and the caller falls back gracefully to
an empty email.

**Blast radius if migration breaks**: 🟡 Medium — auth-adjacent
but the caller already handles `_gh_primary_email → None`.
Worst case: some OAuth users get a signup with `email=""` and
we can't send them founder emails.

**Migration**: 5-line drop-in to `ext_client("github", ...)`.
Zero logic change.

**Pinning test to add**: verify the try/except still wraps the
call so an `ExternalCallError` from the wrapper still degrades
to `return None` rather than propagating and blowing up the
OAuth callback.

---

## 🆕 New dep names introduced in this batch

| Dep name | First site | Rationale |
|---|---|---|
| `vscode_marketplace` | `admin_qa.py::_vscode_marketplace_status` | We check whether our published extension is still listed. Different host than any existing dep. Falls back to `_default` timeout — 8s explicit override in the site is fine. |
| `internal_probe` | `admin_projects_brain.py::_probe_one` | 8 parallel service-health probes hit URLs on OUR own domain + a few third-parties. A GitHub outage tripping the `github` breaker shouldn't also fast-fail these internal probes; a distinct dep name keeps the breaker scopes separate. |
| `cloudflare` | `admin_ops_config.py::purge_cache` | Cloudflare API — separate SLA from GitHub. |

All three fall back to the wrapper's `_default` timeout when
not overridden — which is `read=20.0`, safe for all three
short probes. Individual sites still pass their own timeout
where they had one previously.

---

## 📋 Recommended Batch 8 scope + deploy plan

**Ship** (all 8 files, 11 sites) as **one Batch 8 deploy**
with the tightened checklist:

- 7 pure-utility sites (admin_qa GitHub, admin_bin, admin_ops_config,
  admin_users, upload, fix_pipeline, admin_projects_brain)
- 1 marketplace-check site (`admin_qa` VSCode)
- 1 auth-adjacent site (`github_oauth::_gh_primary_email`)
  — pinning test guards the try/except degradation

**Business-hours discipline**: 1 site is auth-adjacent, so
apply the same watch as Wave 7B — verify SHA lands, verify
`/founder-offer/status` continues to serve, then confirm
OAuth signup flows still complete cleanly (live traffic
signal like the 4 spots claimed after Wave 7B).

**Explicitly deferred to future batch/supervised**:
- `routers/codebase_health.py` — deserves its own preserve-timeout
  test + mini-batch.

**Confidence**: HIGH on all 8 shippable files. The founder's
deploy checklist means we'll catch any pipeline race within
60s of it landing this time.

---

## Post-survey action for main agent

**Hold in place**. Do NOT start migrating until founder says go.
Options for founder to consider:

- **A** (recommended): Ship the 8 files (11 sites) as one Batch
  8 during business hours, using the tightened deploy checklist.
- **B**: Split — 7 pure-utility sites first, verify prod, then
  the auth-adjacent `github_oauth::_gh_primary_email` +
  `admin_qa` VSCode marketplace check separately.
- **C**: Skip Batch 8 and go straight to the
  `ext_client(limits=...)` wrapper upgrade → `github_api_writer.py`
  supervised session — the highest-leverage remaining work.
- **D**: Batch 8 first, then wrapper upgrade + `github_api_writer.py`
  supervised.
