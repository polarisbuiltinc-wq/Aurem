# Deploy-Pipeline Frontend-Lag RCA — Iter 386 · Session 2 · Layer 7

**Author:** dev, 2026-02-08
**Trigger:** Founder-flagged pattern — backend deploy completes fast, frontend deploy trails, users hit new BE endpoints with stale FE bundle. **Recurrence count: 3** (Phase 2 CSP tightening, Phase 4 upload gating, and general pattern observed on 2026-02-08).
**Status:** Root cause identified. Concrete fix shipped (below). Second fix parked in backlog (Fix 1 — build-SHA polling banner).

---

## 1. Root Cause

The failure mode is **cross-service deploy-order + client-side caching**, not any single code bug:

1. Emergent's deploy pipeline builds and rolls out `backend` and `frontend` as **two independent images** with independent rollouts. They don't share an atomic "both-or-neither" promote step.
2. **Backend image is smaller** (~150MB python:3.11-slim + deps) → builds and pushes fast.
3. **Frontend image is larger** (~250MB node:20-alpine build stage → nginx:alpine runtime) → yarn install + yarn build push takes longer.
4. New backend rolls out first → new API contracts live.
5. Frontend is still rolling → browsers hitting the old frontend get **old bundle** references.
6. If the old bundle is already loaded in an open tab (SPA — no page reload), it calls the NEW backend contract with the OLD payload shape → **mismatch** (400/422/silent-empty response).
7. Even a hard-refresh during the lag window can serve stale content because:
   - `index.html` may still be cached at the CDN or browser layer.
   - Vite/CRA emit **hash-named bundles** (`main.abc123.js`) so their filenames self-bust, BUT `index.html` has no hash — it's the mutable pointer to the current bundle set.
   - If `index.html` is cached for even 30s post-frontend-roll, a hard refresh in that window loads the OLD html → OLD bundle refs → same silently-broken SPA.

**Why this hit Phase 2 and Phase 4 specifically:** both phases added new backend contract fields the frontend consumed on the NEXT deploy (CSP header shape in Phase 2, upload MIME whitelist in Phase 4). Any deploy that expands the contract AND relies on frontend to consume the expansion the same day is exposed to this pattern.

**Why it doesn't hit backend-only changes:** if the frontend didn't change, both images are identical to previous deploy after backend-only edits → no lag observable client-side.

## 2. Ideal Fix (out of scope — depends on Emergent Support)

Two mitigations sit on the **platform** side, outside our repo:
- **Atomic deploy gate**: the pipeline should not promote EITHER image to prod until BOTH have built + pushed successfully. This eliminates the race entirely. Tracked with Emergent Support.
- **Backwards-compat contract windows**: BE releases with new fields should be additive; the previous FE should continue to work against the new BE for at least one release cycle. This is a discipline layer, not a code layer — captured in the release-checklist doc.

## 3. Concrete Fix Shipped This Session

**Fix 2 — Cache-Control on `index.html`.** Applied in `frontend/Dockerfile`'s nginx config:

```nginx
location = /index.html {
  add_header Cache-Control "no-cache, no-store, must-revalidate" always;
  add_header Pragma        "no-cache" always;
  add_header Expires       "0" always;
  try_files $uri =404;
}
```

Effect:
- Every request for `index.html` (which is EVERY SPA entry, EVERY hard refresh) forces revalidation with the origin.
- Hashed JS/CSS bundles remain fully cacheable (their filename change is the cache-buster — they self-invalidate at the URL level).
- **Result**: the moment the frontend image finishes rolling, the very next `index.html` fetch by any browser is guaranteed fresh. The window of stale-HTML-pointing-at-old-bundles collapses from `min(browser_cache_ttl, cdn_ttl)` (which was open-ended) to zero.

This does NOT fix the atomicity race — a user with a SPA tab OPEN during the roll will still hit stale-bundle-vs-new-backend for the duration of that session. It DOES fix the class of "hard refresh should recover but it didn't" complaints.

## 4. Parked in Backlog (Fix 1 — not shipped this session per founder ruling)

Concept: embed the git SHA at frontend build time (`REACT_APP_BUILD_SHA`), add `/api/build-info` returning backend SHA, add a small frontend hook that polls every ~30s and shows a "New version available — refresh to update" toast on mismatch. This turns any residual lag into a **visible, actionable UX prompt** for open-tab users too.

- **Cost**: ~2h build + 1 new BE endpoint + 1 FE hook.
- **Trigger to promote**: if Fix 2 + Emergent Support pipeline-fix don't drop the recurrence to zero after two more deploys, ship Fix 1.

## 5. Acceptance Test (per Session 2 rules)

- ✅ `frontend/Dockerfile` diff shows the nginx `location = /index.html` block with `Cache-Control: no-cache` (visible on redeploy).
- 🔜 On the NEXT frontend redeploy (whenever that is), curl `https://auremcto.com/index.html -I` should show `Cache-Control: no-cache, no-store, must-revalidate` in the response headers.
- 🔜 Founder confirms — no recurrence of the FE-lag pattern on the next contract-expanding deploy that touches both surfaces.

## 6. What This Doc Is NOT

- It's not a promise the pattern is fully resolved — atomicity is still on Emergent Support's side.
- It's not license to add more infra costs. Layer 12 (aggregated log search) remains hard-gated on paying-customer revenue.
- It's not an excuse to skip Fix 1 forever — if the next two contract-expanding deploys STILL show the lag, ship Fix 1 immediately.
