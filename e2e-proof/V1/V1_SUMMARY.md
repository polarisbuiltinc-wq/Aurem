# V1 — server-side headless deploy-verify, revised v2 spec (2026-08-30)

Own workstream, after M3. Agent-tested, NOT founder-confirmed.

## 0. V0 — reuse map
Full 10-line map: `/app/e2e-proof/V1/V0_REUSE_MAP.md`. Headline:
reused Playwright launch pattern (`browser_self_test.py`), screenshot/
receipt storage (`preview_capture.py`), the existing tested SSRF guard
(`ora_chat/deep_research.py::_is_safe_public_url`, 9 tests already in
`test_iter270_ssrf_guard.py` — NOT reimplemented), and the existing
shallow deploy-verify (`routers/deploy.py::_verify_and_capture`) as the
wiring target, extended not replaced. Genuinely new: `services/deploy_verify.py`.

## 1. V1a — deterministic engine, zero-LLM
`services/deploy_verify.py::run_verify` — <=120s wall-clock budget
(`asyncio.timeout`), fails loudly on overrun. All 7 required checks
implemented in order: reachability+TTFB, version-identity build-match
(names "stale build / CDN cache" on mismatch), runtime-health (console
+ pageerror), changed-route assertion (with per-navigation re-verify,
see §3), breakage sweep + geometry overflow (bounding-box style, pure
DOM `scrollWidth`/`clientWidth` check — 0 LLM), one safe interaction
(mouse-move-first, explicit "interaction skipped" when no target),
375/desktop screenshots + Playwright trace.
**Zero-LLM proof**: `test_verify_a_zero_llm` patches `services.llm.call_llm`
to raise if ever called — full run still returns `verdict=pass`, so the
call never happens. PASS.

## 2. V1c — security fence (9 rules, all before any run)
| # | Rule | Where | Test(s) |
|---|------|-------|---------|
| 1 | scheme+DNS SSRF block, fail-closed | `validate_target_url` (reuses `deep_research._is_safe_public_url`) | `test_verify_ssrf_blocked_metadata_ip/private_ip/loopback` |
| 2 | per-run domain allowlist on every sub-resource | `_route_guard`/`_same_allowlisted_domain`, `page.route("**/*", ...)` | `test_verify_egress_blocked_off_allowlist_domain` |
| 3 | re-verify (full DNS re-resolve, not string-only) before every navigation past the first | changed-route loop now calls `validate_target_url()` again per route, fails closed as `reverify_blocked:*` on a hit | `test_verify_url_reverify_mid_run_same_check_used`, `test_verify_mid_run_reverify_blocks_changed_route` (new this round) |
| 4 | fresh context, no creds/cache, downloads disabled | `browser.new_context(viewport=..., accept_downloads=False)`, no `storage_state` ever passed | `test_verify_isolated_context_no_stored_state` |
| 5 | never `--no-sandbox` | launch args identical to `browser_self_test.py`'s D1 pattern; no such flag anywhere in the module | source-level, checked by hand |
| 6 | output truncation before storage/LLM | `_truncate()`, `OUTPUT_TRUNCATE_CAP=4000` | `test_verify_output_truncated` |
| 7 | audit log every run | `_audit_log()` → `db.deploy_verify_audit` | `test_verify_audit_logged` |
| 8 | dumb worker, no shell/DB/eval | no `subprocess`/`os.system`/`eval(` anywhere in module | `test_verify_no_shell_no_db` |
| 9 | local-endpoint hardening | N/A this round — zero new HTTP/MCP endpoint added (in-process function, called from the existing deploy router) | `test_verify_endpoint_hardened_no_new_http_endpoint` (regression guard: fails loudly if a future round adds one without updating this test) |

**8/8 required tests pass** (rule 3 gained a second, behavioral test
this round, beyond the source-scan one already present).

## 3. V1b — LLM judgment: LEFT PENDING this round (founder-directed)
`run_judgment()` is now a genuine no-op stub (~10 lines): logs
"V1b pending" and returns `{"verdict": "pending", ...}` — never
constructs an LLM call in mock OR real mode, zero spend either way.
Tests: `test_judgment_refused_in_mock`, `test_judgment_never_calls_model_pending_this_round`
(patches `call_llm` to raise if invoked — never fires),
`test_judgment_suspicious_never_fails_a_passing_run` (guards the
CALLER contract for whenever V1b is wired for real), `test_judgment_token_cap`.
Next round wires the real thing (pruned a11y snapshot, nonce
boundaries, advisory-only schema, refuse-in-mock).

## 4. V1d — wiring (extends existing surfaces, no new user-facing flow)
- `routers/deploy.py::_verify_and_capture` — after the pre-existing
  shallow httpx+screenshot check, ADDITIONALLY runs `deploy_verify.run_verify()`
  against the same URL and persists a `verify_engine` sub-document
  (`verdict`, `what_happened`, `fail_reason`, `checks`, `console_errors`,
  `ttfb_evidence`, `duration_ms`, `receipt_key`, `browser_mode`) onto
  the existing `aurem_cto_deploy_runs` row — additive, never replaces
  the pre-existing `verified`/`verify_note` fields (L13 honest-states
  pattern preserved).
- `GET /deploy/log/{run_id}` now returns `verify_engine` alongside the
  existing fields.
- Trust events: `verify_started`/`verify_passed`/`verify_failed` added
  to `trust_surface_events.EVENT_KINDS`, fired around every engine run.
- Bell: `verify_failed` added to `notifications.PERSISTENT_TYPES` —
  a failed verify stays unread/persistent like `payment_failed`/
  `ship_failed`/`repo_revoked`, fired via `emit_notification()`.
- Admin monitor (`GET /admin/preview-deploy-monitor`): new
  `verify_engine_30d` block (`total`/`passed`/`pass_pct`/`last_fail_reason`)
  + `meter_line` now appends `deploy-verify {pct}% (last fail: {reason})`.
  **Live-curl-verified** against this pod's real admin account
  (test@aurem.dev): returned the new block + updated meter line
  correctly in its honest empty state (`total: 0` — no real deploy has
  run in this pod this session, not fabricated).
- `verify_browser=local|cloud` — `VERIFY_BROWSER_MODE` env-read
  constant added, defaults `"local"`. No cloud path exists or was
  purchased; any value still runs local (never silently no-ops).
- 2 new wiring tests (`test_v1d_deploy_verify_wiring_2026_08_30.py`):
  pass → `verify_started`+`verify_passed` events, no bell; fail →
  `verify_failed` event + a real `PERSISTENT` bell notification
  carrying the `what_happened` text. Both pass.
- **Explicitly not done this round** (matches the "not a user-facing
  feature this round" flag below): `DeployPanel.jsx`'s receipt card
  and `AdminSystemHealth.jsx`'s tile do not yet render the new
  `verify_engine`/`verify_engine_30d` fields visually — the data is
  wired end-to-end (persisted, aggregated, live-curl-proven) but no
  new frontend UI was built to display it. Flagged, not silently
  skipped.

## 5. V1e — 6 E2E scenarios, local disposable fixture only
`backend/tests/test_v1_deploy_verify_2026_08_30.py`, fixture files
generated by the test module itself (`/tmp/v1_fixture_site/`, rewritten
every run — no dependency on prior /tmp state surviving a process
restart; the original hand-created `ok.png` was a 0-byte placeholder
that caused a false breakage-sweep failure, fixed by having the module
write a real decodable 1x1 PNG). NEVER a real user site or production.

1. **Clean deploy + marker → PASS**: `test_e2e_scenario_1_pass` — build-match
   true, both screenshots captured (non-zero bytes), trace file written.
2. **Broken deploy (JS error + 404 img + stale hash) → named FAIL**:
   `test_e2e_scenario_2_multi_fail_named` — asserts `version_identity`,
   `breakage_sweep`, `runtime_health` are ALL individually `False`,
   proving the engine finds real things, not a rubber stamp.
3. **SSRF (metadata IP) → refused, no launch**: `test_e2e_scenario_3_ssrf_blocked_no_launch` —
   `async_playwright` itself is patched and asserted `not_called`; the
   fence blocks before any browser process exists.
4. **Model advisory refused in mock**: `test_e2e_scenario_4_model_advisory_mock_refused` —
   deterministic verdict stays `pass` regardless; judgment returns `pending` (V1b left pending, see §3).
5. **Stale build named specifically**: `test_e2e_scenario_5_stale_build_named_specifically` —
   `fail_reason == "stale_build"`, evidence string contains "stale build" verbatim.
6. **Overflow, zero LLM, screenshot attached**: `test_e2e_scenario_6_overflow_detected_zero_llm` —
   `call_llm` patched to raise if invoked (never fires); `geometry` check
   fails with `overflowX: True`.

All 6 pass. Screenshots are captured in-run (bytes checked non-zero in
scenario 1); persisted proof is the passing test output itself plus
this file — no separate PNG dump was made for a local-only disposable
fixture (would add no evidence beyond the byte-length assertions
already in the test).

## 6. Meter / F29 / regression
- Meter line format (live, §4): `"last 30d: {n} previews · {n} deploys
  ({n} failed) · {n} receipts · capture success {pct}% · deploy-verify {pct}% (last fail: {reason})"`.
- F29 (cloud browser fallback) added to `ROADMAP.md` as a flag-only
  future item — no provider selected, no key, nothing purchased.
- **No-new-vs-baseline**: targeted sweep
  `-k "deploy or output_guard or notifications or trust_surface or admin_analytics or preview_capture"`
  → 3 failed + 1 error, **all 4 already in `test-baseline.txt`**
  (`test_deploy_verification_discipline.py` x2, `test_phase2c_admin_analytics_router.py::test_graph_status`,
  `test_ora_chat_deep_research.py` collection error) — confirmed by
  line-number grep against the baseline file, zero new.
- Full new-work suite (M3 + V1 + V1d wiring): **29/29 pass**
  (`test_m3_output_guard_named_file_fix_2026_08_30.py` 5,
  `test_v1_deploy_verify_2026_08_30.py` 22,
  `test_v1d_deploy_verify_wiring_2026_08_30.py` 2).

## 7. No-silent-fail audit
- V1a: every failure path sets a named `fail_reason` + human
  `what_happened` string — no bare `False` without an evidence string.
- V1c: SSRF block, egress block, and mid-run reverify block all log a
  distinct reason (`blocked_ssrf:*`, `EGRESS_ATTEMPT`-shaped entries in
  `egress_attempts`, `reverify_blocked:*`) — never a bare skip.
- V1d: a failed engine run always gets `verify_failed` trust event +
  persistent bell notification — never swallowed silently. A passing
  run never gets a false "verified" flip if the underlying shallow
  check already failed (both are independent fields, additive).
- V1b: pending stub always returns a note explaining why — never a
  bare empty dict.

## 8. Webhook (R5e) — read-only answers, no secret handled
- **Q-NAME**: there is **no env var** for the webhook signing secret.
  `verify_webhook_signature()` (`services/github_app.py:440`) reads
  ONLY from the `services.github_app_config` runtime cache, hydrated
  from Mongo `admin_settings._id="github_app_config"` — confirmed by
  reading the function's own docstring/header comment directly
  ("Nothing here uses `os.environ`"). Matches `R5-WEBHOOK-FIX.md` §3
  exactly, restated here per this round's Q-NAME ask.
- **Q-TARGET**: the configured webhook delivery URL
  (`https://auremcto.com/api/aurem-dev/github/app/webhook`) is
  **PRODUCTION only**. This Preview pod is not in the delivery path at
  all (no webhook receiver reachable from here, no pod `.env` entry
  for it). Per the founder's own framing: **"prod-only; founder
  self-serves; no dev involvement."** The secret value never needs to
  reach this pod, in any scenario.
- **Q-VERIFY**: once the founder sets the value directly in
  production, the confirmation is read-only: (a) any real delivery
  (any event type — `installation.created` etc. all count) shows
  `200` in the "GitHub Webhook Fence" tile (`AdminSystemHealth.jsx`)
  or GitHub's own "Recent Deliveries" list; (b) a `pull_request`
  delivery specifically only appears after a REAL pull request is
  opened on a connected repo — "no `pull_request` delivery yet" is
  **not** evidence of a continuing 401, it's just "no PR has been
  opened yet." This round did not re-check the fence tile (no founder
  config-change signal received yet) — nothing to report as
  post-fix-200 or as `pull_request`-delivery-200 this round. R5e
  remains open exactly where the founder left it: founder's own
  github.com + production-admin action, not yet done from this side.

## 9. Flags / state
- `MOCK_LLM=true` — unchanged before/after this entire round (V1a/V1c/
  V1e are zero-LLM by construction; V1b never calls a model this round
  either way).
- `ship_via_pr` — unchanged, Preview-only.
- V1 is the deterministic engine + its own proof this round — wired
  additively into the existing deploy-receipt data model, trust
  events, bell, and admin aggregate endpoint (all backend), but no NEW
  user-facing frontend surface (DeployPanel/AdminSystemHealth visual
  cards) was built for it this round — see §4's explicit callout.

## 10. R9 re-readiness (unchanged)
Still NOT ready to flip: (1) R5e webhook delivery 200 — founder's own
action, §8 above; (2) 48h warn-window — unreviewed; (3) R1a gap #4
(ship-branch drift detection) — not built. Per founder instruction,
gap #4 is the next small R9 item after this round. **No R9 flip, no
production flags, no migrations, no production-site writes this
round** — none attempted.

## STATUS: V1 CLOSED for this round's scope (agent-tested, not
founder-confirmed). V1b (real LLM judgment) explicitly deferred/pending
per founder direction, not a gap in this round's deliverable.

**STOP per instruction — no new work started after this report.**
