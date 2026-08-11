# AUREM CTO — Changelog (append-only)

## 2026-02-12 (Batch 8a → 8b day)

- **Iter 314 — BUILD_INFO.txt lag fixed** + Deploy Verification Checklist rewrite
  - `backend/BUILD_INFO.txt` untracked; `scripts/git_hooks/post-commit` stamps HEAD SHA
  - `scripts/install_hooks.sh` bootstraps hook into fresh sessions
  - `/app/memory/DEPLOY_VERIFICATION_CHECKLIST.md` rewritten:
    removed SHA-pinning assumption per Emergent Support,
    named Manage Publishes → Overview as primary source of truth,
    documented 3 HEAD-mutation channels (A/B/C),
    added "no build in-flight" + "intended commits landed" pre-dispatch rules
  - Verified live: prod SHA `42aba1160e0e` == local HEAD (best case)
  - 7 new tests in `test_build_info_stamping.py`
  - 3 tests in `test_deploy_verification_discipline.py` rewritten to pin new invariants

- **Batch 8a — 7 router files, 10 sites migrated to `services.http.ext_client`**
  - admin_qa.py (3): GitHub Actions x2 + VSCode Marketplace (new dep)
  - admin_bin.py (2): GitHub HEAD probe (4s in-loop) + OpenRouter credits
  - admin_projects_brain.py (1): internal_probe dep (breaker isolation)
  - admin_ops_config.py (1): cloudflare dep
  - admin_users.py (1): resend dep (15s)
  - upload.py (1): OpenRouter vision (45s explicit preserve)
  - fix_pipeline.py (1): GitHub commit verification (10s)
  - Verified live: prod SHA `39ba1122764f` == local HEAD (best case, ~12min build)
  - 13 new tests in `test_phase3_http_wrapper_migration_batch8a.py`

- **Batch 8b — SOLO: `github_oauth.py::_gh_primary_email`**
  - Migrated `httpx.AsyncClient(timeout=10)` → `ext_client("github", timeout=httpx.Timeout(10.0))`
  - Broad `except Exception` guard preserved (load-bearing for OAuth signup with private emails)
  - 7 new tests in `test_phase3_http_wrapper_migration_batch8b.py` — including runtime tests
    that simulate ExternalCallError + HTTPStatusError → confirm graceful-degrade contract
  - Verified live: prod SHA `51be15a52d09` == local HEAD (best case, ~24min build)
  - Live functional check on OAuth `/connect` (401 gate) + `/callback` (400 clean error) — no 500s
  - E2E OAuth signup flow with a private-email GitHub account requires human test

- **Middleware "No response returned" fix** (Iter 313)
  - Defensive try/except around `_global_rate_limit_guard`'s `call_next()` + `check_rate_limit_async()`
  - Live on prod (confirmed via Emergent Support; earlier live-signal was ambiguous due to BUILD_INFO.txt lag which Iter 314 subsequently fixed)

- **Deploy discipline track record established today:**
  - 3 races in one day (all resolved) → 3 consecutive best-case deploys under revised model
  - Pipeline model confirmed: "snapshot at build-start" (no SHA pinning)
  - Deploy incident report + 6 pipeline questions sent to Emergent Support

**Cumulative Phase 3 progress after today: 65 sites / 23 files migrated.**

See `/app/memory/DEPLOY_VERIFICATION_CHECKLIST.md` for mandatory deploy protocol.
See `/app/memory/PRD.md` for full backlog + prioritization.
