# TIER-1 SECURITY AUDIT — Iter 388-aa (2026-02-14)

Read-only audit executed while founder investigates the Payments $0
mystery. Covers Tier-1 items #17 (CVE audit) + #18 (IDOR self-audit).
Item #19 (frontend-bundle secrets sweep) queued for the next slice.

Evidence-only. No fixes applied yet — founder decides which lines to
patch based on the severity + patch-risk table below.

---

## Item #17 · Dependency CVE Audit

### Frontend (yarn audit) — 9 vulns / 930 deps

| Severity  | Count | Notes                                          |
|-----------|-------|------------------------------------------------|
| Critical  | 0     | —                                              |
| High      | 2     | `extract-zip` (via `@lhci/cli` → DEV ONLY)     |
| Moderate  | 7     | `uuid` (dev), `react-router-dom` × 5 (PROD!)   |

**Actionable in prod runtime:**
- `react-router-dom` **6.30.3 → 6.30.4** — CVE-2026-40181 open redirect
  via protocol-relative URL (`//evil.com` in `<Link to>`) plus 3
  related open-redirect/XSS advisories on the same pkg. Only exploitable
  in Framework/Data mode — we use Declarative (`<BrowserRouter>`) per
  App.jsx, so blast radius is smaller, but **cheap patch (0.0.3 bump)**
  and closes the whole family. **Recommended.**

**Safe to defer (dev-only):**
- `@lhci/cli` → `extract-zip` symlink traversal + `uuid` bounds-check —
  DEV chain, doesn't ship in prod bundle. Fix requires `@lhci/cli`
  upstream release. **Defer.**

**No fix available:**
- None on frontend.

### Backend (pip-audit) — 95 vulns / 15 packages

**High-impact production bumps** (fix version → upgrade path):

| Package         | Cur    | Fix   | Why (paraphrased CVE cluster)                       |
|-----------------|--------|-------|-----------------------------------------------------|
| aiohttp         | 3.13.5 | 3.14.3 | 14 CVEs — request smuggling, digest cross-origin leak, DoS via pipelined requests, memory-bomb decompress, TLS SNI check bypass on connection reuse |
| cryptography    | 44.0.0 | 50.0.0 | 7 CVEs — PKCS7 Bleichenbacher oracle (44+), name-constraint bypass, wildcard-SAN escape, ECC public-key subgroup missing validation, RFC5280 DoS |
| litellm         | 1.80.0 | 1.84.0 | 10 CVEs — proxy prompt-injection, prompt-log leak, credential exfil paths (this is the heavy hitter for our LLM stack) |
| pyjwt           | 2.10.0 | 2.13.0 | 11 CVEs — algorithm confusion + audience bypass         |
| starlette       | 0.37.2 | 1.3.1  | 8 CVEs — request smuggling, form parsing DoS. **fastapi 0.115.0 pins starlette<0.38, so bumping starlette needs a fastapi bump too — coupled upgrade.** |
| pillow          | 12.2.0 | 12.3.0 | 12 CVEs — image decoders CVE cluster; **trivial patch**. |
| python-multipart | 0.0.18 | 0.0.31 | 6 CVEs — form-parse DoS. **fastapi dependency.**       |
| h2              | 4.3.0  | 4.4.1  | 1 CVE — dup Host header smuggling                       |
| httplib2        | 0.31.2 | 0.32.0 | 1 CVE — unbounded gzip decompression (zip-bomb DoS)     |
| python-dotenv   | 1.0.1  | 1.2.2  | 1 CVE — parser gadget                                   |
| pytest          | 8.3.0  | 9.0.3  | 1 CVE — DEV ONLY, low urgency                           |
| pip             | 26.1.1 | 26.1.2 | 1 CVE — DEV ONLY                                        |
| pyasn1          | 0.6.3  | 0.6.4  | 2 CVEs — parser edge cases                              |

**No fix available (accept + document):**
- `ecdsa 0.19.2` — Minerva timing attack on P-256 (CVE-2024-23342). The
  project explicitly says "side-channel attacks are out of scope, no
  fix planned". Mitigation: audit whether we use `ecdsa.SigningKey.sign_digest`
  in high-frequency paths. Only used indirectly via `python-jose` /
  `google-auth`. **Low real-world impact for us — accept.**
- `paramiko 3.5.0` PYSEC-2026-2858 — no fix version listed in advisory
  yet. **Watch for release.**

**Patch-risk classification:**

- **Zero-risk (semver patch bumps):** pillow, httplib2, pip, pyasn1,
  python-dotenv, h2. Ship in one PR.
- **Minor-risk (semver minor bumps):** aiohttp (3.13→3.14, small API
  changes on cookies handling), pyjwt (2.10→2.13, algorithms allowlist
  tightened — sanity-check any JWT-verify call that omits `algorithms=`),
  cryptography (44→46/50 — need to test our TLS + PKI paths, especially
  scaffold GH signature verification).
- **Coupled-risk (major bumps + coupling):** starlette 0.37→1.x means
  fastapi 0.115→newest (0.128+), which changes Depends resolution
  semantics and Pydantic v2 defaults for Optional. **Needs a dedicated
  regression run** (all backend pytest + at least one full ORA
  session-flow smoke test).
- **Coupled-risk medium:** litellm 1.80→1.84 — most breaking changes
  are in provider config schema. Since we already run our own emergent
  LLM key path, low risk, but recommend running the LLM probe once
  post-bump to confirm all provider calls still succeed.

---

## Item #18 · IDOR / Authorization Boundary Self-Audit

**Method:** grep every `find_one` / `find` / `update_*` / `delete_*`
call in `backend/routers/*.py` for the presence of `user_id` (or
equivalent ownership key) in the filter dict. Then read source of the
top-8 mutating routers to confirm ownership is checked upstream.

**Routers audited (top-of-risk sampled):**

| Router              | Sensitive endpoints                                  | Verdict |
|---------------------|------------------------------------------------------|---------|
| `managed_db.py`     | user-app CRUD `/find,insert,update,delete`           | ✅ CLEAN — `_verify_app_ownership()` + `build_scoped_filter()` on every mutation, `app_id`/`user_id`/`_collection` stripped from client patch payload. |
| `supabase.py`       | `provision/status/downgrade/destroy/transfer`        | ✅ CLEAN — `_verify_paid_app_ownership()` on every handler. |
| `scaffold.py`       | `get/regen/materialize/preview/delete/transfer-repo` | ✅ CLEAN — `_read_draft(db, draft_id, user_id)` always includes user_id in filter. |
| `cto_projects.py`   | 14 `find_one` calls on `cto_projects` / `cto_tasks` / `warm_start_jobs` / `github_installations` | ✅ CLEAN — every single query includes `user_id`. Verified line-by-line. |
| `automations.py`    | `run/toggle/delete/{automation_id}`                  | ✅ CLEAN — every handler filters by `_id` + `user_id`. |
| `loop.py`           | `confirm/approve/cancel/rollback/{loop_id}`          | ✅ CLEAN — `loop_sessions.find_one({loop_id, user_id})`. |
| `hosted_deploy.py`  | `status/disconnect/{project_id}`                     | ✅ CLEAN — `cto_projects.find_one({project_id, user_id})`. |
| `deploy.py`         | `config/{project_id}, log/{run_id}, runs/{run_id}/logs` | ✅ CLEAN — every doc lookup includes user_id. |
| `chat.py`           | `sessions/{session_id}` CRUD + SSE                   | ⚠️ ONE LOW-SEV EDGE CASE (see below). Rest clean. |
| `ora_chat.py`       | `sessions/{session_id}`                              | ✅ (spot-check clean; full audit pending). |
| `support.py`        | `tickets/{ticket_id}` private + public-token         | ✅ CLEAN — private filters by user_id; public uses `user_email` + HMAC token. |

### ⚠️ ONE low-sev IDOR edge case — `chat.py:1644`

```python
_sess = await _db.chat_sessions.find_one(
    {"session_id": body.session_id},              # ← no user_id in FILTER
    {"_id": 0, "pending_fix_task": 1, "user_id": 1},
)
_pending = (_sess or {}).get("pending_fix_task") if _sess else None
if _pending and (not _sess.get("user_id") or _sess.get("user_id") == user_id):
    #                ^^^^^^^^^^^^^^^^^^^^^ — "if row has no user_id (legacy row), allow"
    await _db.chat_sessions.update_one(
        {"session_id": body.session_id},   # ← again, no user_id in filter
        {"$unset": {"pending_fix_task": ""}},
    )
```

**Impact:** an authenticated user who knows another user's `session_id`
can `$unset pending_fix_task` on a legacy row (row with no `user_id`
field). The comment above the code (line 1633) states *"we no longer
act on it; it's kept on the schema only so we don't break older
deployments mid-roll"* — so the field is effectively dead.

**Severity:** **P3 / LOW** — cosmetic write to a deprecated field on a
row whose user_id backfill hasn't happened. No PII disclosure, no
privilege escalation.

**Recommended fix (5-line PR):** move the ownership check into the
Mongo filter and drop the legacy-row allowance:
```python
_sess = await _db.chat_sessions.find_one(
    {"session_id": body.session_id, "user_id": user_id},
    {"_id": 0, "pending_fix_task": 1},
)
if _sess and _sess.get("pending_fix_task"):
    await _db.chat_sessions.update_one(
        {"session_id": body.session_id, "user_id": user_id},
        {"$unset": {"pending_fix_task": ""}},
    )
```

### Overall IDOR verdict

**The codebase's IDOR posture is strong.** The dominant pattern
(`find_one({resource_id, user_id})`) is applied uniformly across every
mutating router I sampled. `managed_db.py` in particular uses the
gold-standard `build_scoped_filter` helper that prevents filter
injection through the request body.

**Coverage gap:** I sampled 11 of ~60 routers, focused on the
highest-risk (data mutations, resource provisioning, deploy). Full
audit of the remaining 49 (mostly admin routers, which are already
gated by the `require_admin_dep` dependency at the router level, and
static/utility routers) is queued as a follow-up. Given the uniform
pattern seen in the sampled 11, expected additional findings are LOW.

---

## Recommended action ordering

1. **Now (blocks Payments/OpenRouter closure):** nothing — this audit
   is memo-only. Founder should close the two live issues first.
2. **Next slice (P1, small-blast-radius patches):** ship pillow +
   httplib2 + h2 + pyasn1 + python-dotenv bumps as one PR. All
   semver-patch, zero API risk. Includes the chat.py:1644 5-line IDOR
   tightening.
3. **Slice after (P1, medium risk):** aiohttp + pyjwt + cryptography
   bumps as a second PR with the pre-deploy gate re-run + smoke test.
4. **Deferred (P2, coupled):** starlette + fastapi + litellm major
   bumps as a dedicated PR with a full regression run.
5. **Accept + document:** ecdsa side-channel, paramiko (no fix yet).
6. **Item #19 (frontend bundle secrets sweep):** kicks off after (2)
   ships — will be a fresh audit against the deployed `dist/*.js`.

---

## Slice 2 shipped — 2026-02-14 (preview-verified)

Backend restart clean, `/api/health` returns build `ed5b698`, DB=true.

| Package        | Before → After   | CVEs closed |
|----------------|------------------|-------------|
| pillow         | 12.2.0 → 12.3.0  | 12          |
| httplib2       | 0.31.2 → 0.32.0  | 1           |
| h2             | 4.3.0  → 4.4.1   | 1           |
| pyasn1         | 0.6.3  → 0.6.4   | 2           |
| python-dotenv  | 1.0.1  → 1.2.2   | 1           |
| chat.py IDOR   | Iter 388-aa      | P3 tightened |

`pip-audit` re-run: **95 → 67 vulns, 15 → 10 packages (28 CVEs closed)**.

Regression:
- Full backend pytest: 537 pass, 12 fail — verified pre-existing (also
  failed on git-stashed baseline pre-my-change), unrelated to Slice 2.
- 4/4 new IDOR regression tests in
  `backend/tests/test_iter388aa_chat_pending_fix_task_idor.py` pass.
- Mode-D tests (`test_iter212m46_mode_d_no_autoship.py`): 3/3 pass.

Awaiting founder ack to queue deploy for Slice 2 (Rule 3 — Lane 6
gate output: only Tavily WARN remains, OpenRouter probe clean after
top-up).

---

## Iter 388-ad — IDOR audit EXPANDED coverage (2026-02-14)

Second pass — audited the remaining 19 routers with `{path_param}`
patterns that were not covered in Iter 388-aa's 11-router sample.

### Admin routers (8) — router-level admin gate

All 8 admin routers with path params (`admin_analytics`, `admin_bin`,
`admin_health`, `admin_observability`, `admin_ops_config`,
`admin_projects_brain`, `admin_support`, `admin_users`) have
`require_admin_dep` (or equivalent) on every endpoint. Endpoint-count
vs admin-gate-count:

| Router                    | Endpoints | Admin gates |
|---------------------------|-----------|-------------|
| admin_analytics.py        | 40        | 38          |
| admin_bin.py              |  8        | 16 (belt+suspenders) |
| admin_health.py           |  5        |  2          |
| admin_observability.py    |  2        |  2          |
| admin_ops_config.py       | 23        | 28          |
| admin_projects_brain.py   | 14        | 18          |
| admin_support.py          |  8        | 12          |
| admin_users.py            | 16        | 20          |

The two routers where the gate count is LOWER than the endpoint count
(`admin_analytics`, `admin_health`) apply the gate at the sub-router
level, not each endpoint — verified by inspection: both include
`dependencies=[Depends(require_admin_dep)]` on the router itself.

**Verdict: all 8 admin routers ✅ CLEAN — router-boundary gate.**

### Non-admin path-param routers (11)

| Router               | Verdict | Notes                                                       |
|----------------------|---------|-------------------------------------------------------------|
| payments.py          | ✅      | `/payments/status/{session_id}` does ownership check on the second line: `if pay.get("user_id") != user.get("user_id"): raise 404`. Second `find_one` uses `stripe_sub_id` — server-side webhook context. |
| github_app.py        | ✅      | User-facing `DELETE /installations/{id}` filters by `{installation_id, user_id}`. Other `find_one` calls (lines 164, 513, 534) are in WEBHOOK path — server-to-server, no user auth applies. |
| fix_pipeline.py      | ✅      | Every `find_one` includes `user_id`.                        |
| chat_commits.py      | ✅      | Every `find_one` includes `user_id`.                        |
| domain.py            | ✅      | Every `find_one` includes `user_id`.                        |
| shipwall.py          | ✅      | `cto_tasks` lookup is intentionally public (Shipwall feed of "done" tasks) — no PII disclosure, only marketing-safe fields.  |
| mcp.py               | ✅      | API-key auth model — the `api_keys.find_one({key, active})` IS the ownership check. All resource lookups then include `user_id`. |
| suggestions.py       | ✅      | Every `find_one` includes `user_id`.                        |
| thinking_hints.py    | ✅      | `thinking_hints_config`/`thinking_hints` are global singletons (not user-scoped) — public content by design. |
| stacks.py            | ✅      | No mutating `find_one` calls — read-only public stack list. |
| dev_sse_probe.py     | ✅      | Dev tooling, no DB find calls.                              |

### Combined IDOR audit coverage (Iter 388-aa + ad)

- **30 routers audited** (11 in Iter 388-aa + 19 in 388-ad).
- **0 exploitable IDORs** found.
- **1 P3/LOW** cleared in `chat.py:1644` (fixed in Iter 388-ab Slice 2).
- Remaining ~47 routers are internal utilities / async cron entry
  points / models / no path params — very low IDOR surface area.

**Overall IDOR posture: STRONG.** The codebase uniformly applies the
`find_one({resource_id, user_id})` pattern at every user-facing path
param endpoint. Admin routers rely on router-level `require_admin_dep`
as defense-in-depth.

---

## Iter 388-ad — Slice 4 attempt DEFERRED (2026-02-14)

**Attempted:**
- `fastapi 0.115.0 → 0.141.1`
- `starlette 0.37.2 → 1.6.0`

**Runtime status: CLEAN** — supervisor restart, `/api/health` 200,
15 supervised tasks alive, LongCat probe green.

**Blocker discovered:** starlette 1.6 introduces a **breaking change**
on `request.state.<key>` access — missing keys now raise `KeyError`
instead of the older `AttributeError`. Impacts:

1. Every `.state.<x>` access site in the codebase that assumed
   AttributeError → needs try/except migration.
2. Test collection via `TestClient(app)` (without a `with` block)
   doesn't fire lifespan startup, so `request.state.db` is unset.
   This causes ~40 route-registration and TestClient-based tests to
   throw KeyError instead of the older graceful path.

**Decision (Rule 2 — no false claims):** ROLLED BACK to fastapi 0.115
+ starlette 0.37.2 on preview. `requirements.txt` was already at the
rolled-back versions (my in-process pip install had never been
`pip freeze`d), so **prod was never exposed** to Slice 4.

**Deferred as P2:** Slice 4 needs a dedicated migration branch:
1. Audit every `request.state.<key>` access site — wrap in try/except
   or use `getattr(request.state, "key", default)`.
2. Fix `TestClient(app)` usages to use `with TestClient(app) as c:` so
   lifespan runs.
3. Full backend regression run against the migrated code.
4. Then bump + ship independently.

`litellm 1.80 → 1.84` also part of Slice 4 — deferred separately
because our `litellm` is pinned to an internal Emergent-hosted wheel
(`https://customer-assets.emergentagent.com/…litellm-1.80.0-py3-none-any.whl`),
not PyPI. Bumping requires Emergent's asset pipeline to publish 1.84
first. No action for me — flag for founder.

