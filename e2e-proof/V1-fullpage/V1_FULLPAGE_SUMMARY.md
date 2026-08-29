# V1 full-page screenshot upgrade (2026-08-30)

Agent-tested, NOT founder-confirmed.

## What was built
- `services/deploy_verify.py` — Check 7 (CAPTURE) now takes a THIRD
  shot, `full_page=True`, on the SAME already-loaded page right after
  the existing desktop viewport shot — no additional `page.goto`. New
  `result["screenshots"]["fullpage"]` (byte length) +
  `result["_raw_screenshots"]["fullpage"]` (bytes) +
  `result["lazy_load_note"]` ("Full-page shot captures rendered
  content; scroll-triggered lazy elements may not appear.") — the
  honest caveat, surfaced on the receipt per spec.
- `run_judgment()` (V1b, still pending) — hardened with a real,
  testable guard: raises `TypeError` if ever called with raw image
  bytes (fullpage or otherwise). V1b's only acceptable future input is
  a text accessibility snapshot; a full-page image would need slicing
  + resizing (1568px, ~28px/token) first — not built, not needed while
  V1b stays pending, but the guard is live NOW regardless.
- `routers/deploy.py::_verify_and_capture` (V1d wiring) — the
  full-page bytes get their OWN receipt key
  (`{run_id}-verify-engine-fullpage.jpg`, separate from the existing
  viewport `receipt_key`), plus `lazy_load_note` persisted onto
  `verify_engine`. Receipt now carries BOTH shots' keys.

## Tests — 3 new engine tests + 1 new wiring test, all pass
(`tests/test_v1_deploy_verify_2026_08_30.py`, full file 25/25;
`tests/test_v1d_deploy_verify_wiring_2026_08_30.py`, full file 3/3)
- `test_fullpage_captured` — `fullpage` key present in both
  `screenshots` (size) and `_raw_screenshots` (bytes), non-zero,
  `lazy_load_note` present verbatim.
- `test_fullpage_no_renavigate` — a per-path GET-request counter on
  the fixture's own HTTP server proves the full-page capture adds
  ZERO additional navigations beyond the engine's 2 pre-existing ones
  (initial `goto` + the unconditional "navigate back to primary URL"
  reset ahead of Check 5 — both pre-existing, unrelated to this
  round). Caught and fixed a stale leftover `python -m http.server`
  process from an earlier debug session that was masking the
  counter (see "issue found" below).
- `test_fullpage_not_in_llm_path` — `run_judgment(<jpeg bytes>, ...)`
  raises `TypeError`; the normal text path still works unaffected.
- `test_verify_engine_wired_persists_fullpage_receipt_key` — the
  wiring layer uploads the fullpage bytes to their own key and
  persists both `fullpage_receipt_key` and `lazy_load_note` onto
  `verify_engine`, distinct from the existing `receipt_key`.

## Issue found + fixed during this item
A `python -m http.server 8899` process left running from an earlier,
unrelated manual debug session in this pod silently made the fixture
test suite reuse a DUMB server instead of its own counting-enabled
one — `test_fullpage_no_renavigate` first read 0 requests for
everything. Killed the stale process (`kill -9`), confirmed the port
was genuinely free (`ss -tln`), waited out the brief `TIME_WAIT`
window, then the module's own fixture correctly started fresh and the
counter worked. Not a code bug — a leftover process from prior manual
testing in this same pod.

## Live E2E proof (real Playwright, real screenshots, saved to disk)
1. Against the existing local fixture (`index.html`, short page):
   `verdict: pass`, `screenshots: {mobile_375: 8251, desktop: 14136,
   fullpage: 14136}` — fullpage == desktop byte-for-byte here because
   this fixture page is SHORTER than the desktop viewport height (no
   below-fold content to add) — an honest, not-a-bug result.
2. To prove the full-page capture genuinely captures below-fold
   content, built a temporary taller test page (2700px, "ABOVE THE
   FOLD" dark banner + "BELOW THE FOLD" yellow banner) and ran the
   SAME engine against it: `screenshots: {desktop: 12239, fullpage:
   40519}` — fullpage more than 3x larger.
   - `viewport_desktop` shot: shows ONLY "ABOVE THE FOLD" (dark banner
     filling the frame, nothing else visible).
   - `fullpage` shot: shows the ENTIRE page — both the dark "ABOVE THE
     FOLD" banner AND the yellow "BELOW THE FOLD — only visible in a
     full-page shot" banner beneath it.
   - Saved: `/app/e2e-proof/V1-fullpage/tall_viewport_desktop.jpg`,
     `/app/e2e-proof/V1-fullpage/tall_fullpage.jpg` (also
     `viewport_desktop.jpg`/`fullpage.jpg`/`mobile_375.jpg` from the
     standard fixture run). Temporary test page removed after.

## STATUS: V1 full-page upgrade CLOSED (agent-tested, not
founder-confirmed).
