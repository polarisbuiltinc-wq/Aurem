# V0 — Detect-Exists-First (V1, 2026-08-30)

10-line reuse map, per L17 (reuse before build). Nothing below is
reimplemented — only referenced/extended.

1. **Browser service**: `services/browser_self_test.py` (D1) —
   existing Playwright launch pattern (`async_playwright`,
   `chromium.launch(headless=True)`), already installed dep
   (`playwright==1.61.0` in requirements.txt). **Reused, not
   duplicated** — V1a's engine launches with the identical args.
2. **Screenshot capture**: `services/preview_capture.py::capture_screenshot`
   already has 375px (`"phone"`) + desktop (`1440x900`) viewports and
   JPEG q80 capture. **Reused directly** for V1a's screenshot step.
3. **S3-compatible receipts storage**: `services/preview_capture.py::
   upload_receipt`/`fetch_receipt`, R2 `deploy-receipts/` prefix,
   reusing `services/db_backup.py`'s credentialed R2 client factory.
   **Reused directly** — no new bucket/prefix/client.
4. **Meter-line insertion point**: `frontend/src/pages/AdminMaintenance.jsx`
   line 241 — existing `"{count_30d} in last 30d · {duration}"` style.
   **New verify meter line follows this exact convention.**
5. **Guardrail/SSRF test suite location**: `backend/tests/` (flat,
   `test_iter<N>_<topic>.py` convention) — new file
   `test_v1_deploy_verify_security_fence_2026_08_30.py` added there.
6. **SSRF blocking machinery**: `services/ora_chat/deep_research.py::
   _is_safe_public_url` / `_ip_is_public` — scheme/private-IP/DNS-
   rebinding blocking ALREADY EXISTS and is tested
   (`tests/test_iter270_ssrf_guard.py`, 9 tests). **Reused directly**,
   not reimplemented — this is exactly V1c rule 1's spec.
7. **Existing shallow deploy-verify**: `routers/deploy.py::
   _verify_and_capture` (S3-D4, prior round) — HTTP-200 + screenshot
   only, no build-match/runtime-health/changed-route/geometry. **This
   is V1's wiring target (V1d)** — extended in place, not replaced;
   its honest-fail-states pattern (`verified: False` + `verify_note`,
   never a fake pass) is preserved and reused as the model for V1a's
   own `verdict`/`fail_reason` shape.
8. **Receipt UI**: `frontend/src/components/DeployPanel.jsx::ReceiptCard`
   — existing 3-state honest UI (pending/success/failed). **Extended**
   with the richer verdict text, not replaced.
9. **Admin monitor tile**: `frontend/src/pages/AdminSystemHealth.jsx`'s
   Preview & Deploy Monitor card — **extended** with a verify success-
   rate + last-fail-reason row.
10. **Playwright trace support**: confirmed available in the installed
    `playwright==1.61.0` (`context.tracing.start/stop`) — no version
    bump needed, no new dep.

**Genuinely new this round (nothing pre-existing covers it)**:
version-identity build-match check, changed-route/API-direct
assertion, geometry (overflow/overlap) checks, the full security-fence
wrapper AROUND a verify run specifically (domain allowlist + mid-run
re-verify + isolated context + audit log — `deep_research.py`'s guard
protects one-shot fetches, not a multi-navigation browser session),
the gated LLM judgment layer, and `verify_browser=local|cloud` flag
(F29, not built, ledger entry only).
