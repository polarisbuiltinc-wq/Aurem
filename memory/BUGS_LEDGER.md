# BUGS_LEDGER — Systemic Bug Institutional Memory

Fresh ledger (Feb 2026) for recurring / high-signal bugs that
represent PATTERNS rather than one-off code errors. Legacy bug
numbers (Bug 1–29) live scattered across CHANGELOG and PRD; this
ledger starts its own numbering (L-01, L-02…) to avoid collision
and to make institutional pattern-recognition faster in future
sessions.

Each entry MUST record: symptom pattern, false hypotheses tried
first (so future agents don't repeat them), root cause with
evidence, and the verification playbook that DOES work.

---

## L-01 · Vite lazy-chunk false-negative in bundle verification

**First diagnosed**: 2026-02-15 · Iter 389 (Meta Pixel conversion events)
**Recurrence pattern**: potentially recurs on ANY frontend feature that adds new event names or conversion tracking helpers — because the natural verification instinct ("grep the bundle") fails silently against Vite's shared-chunk code-splitting.

### Symptom
After deploying a frontend change that adds new string-literal
sentinels (e.g. `"CompleteRegistration"`, `"Lead"`, `"Purchase"`),
the founder or agent runs a bundle grep on the initial JS chunks
loaded by the page and sees **zero hits**. This looks identical
to a "deploy didn't propagate" bug and derails debugging for hours.

### False hypotheses that ate hours before diagnosis
Every one of these was chased and disproven before the actual root
cause was found. Future agents should recognize the pattern and
JUMP TO §"Verification playbook" first.

1. **"Backend-only deploy detector false negative"** — hypothesised
   the pipeline saw the commit and only redeployed backend. Disproven:
   deployer confirmed frontend build step ran, Vite reported
   "4973 modules transformed", artifacts pushed to R2.
2. **"Stale Cloudflare edge cache on custom-domain zone"** — full
   RCA (`/app/deployer-agent-docs/RCA_bc85023a-…MD`) attributed the
   failure to a best-effort post-promote cache purge missing. Also
   wrong: SHA-256 across custom-domain / origin / local-build were
   all IDENTICAL. There was no cache miss.
3. **"Second deploy will fix it" (Track c3)** — believed a redeploy
   would fire a fresh purge and land the code. Actually the code
   was already landed the first time; the second deploy just
   confirmed the same artifacts.
4. **"Build ran but ate my changes"** — checked local build output;
   even LOCAL build's Signup/OAuthFinish/Settings chunks did not
   contain `CompleteRegistration`. Nearly concluded the build was
   broken. Actually correct — the string lives in a DIFFERENT chunk.

### Actual root cause
Vite production build code-splits shared modules into their own
chunks. When `Signup.jsx` imports `metaCompleteRegistration` from
`lib/analytics.js`, Vite creates a separate `analytics-<hash>.js`
chunk (in Iter 389 case: `analytics-DAsU-d0r.js`, 898 bytes total)
that contains the actual string literal `"CompleteRegistration"`.
The Signup chunk only contains a symbolic reference to the export.

The initial page HTML references the MAIN entry chunk. Route-level
lazy chunks (Signup, OAuthFinish, Settings, etc.) and their
shared-module chunks (analytics.js) are loaded on-demand as
React Router mounts them. If the verifier only inspects the
initially-loaded bundles (typical `curl` + grep on visible
`<script src>` entries), the shared analytics chunk is never
fetched → grep returns 0 hits → false negative.

### Verification playbook (the ONE that works)
1. Fetch the target HTML (`curl https://auremcto.com/signup`).
2. Extract the MAIN entry chunk from `<script type="module" crossorigin src="...">`.
3. Fetch the main chunk.
4. Grep the main chunk for ALL `assets/[a-zA-Z0-9_-]+-[a-zA-Z0-9]+\.js` references — these are the lazy sub-chunks Vite may load.
5. Fetch EACH sub-chunk.
6. Grep every fetched chunk (main + sub-chunks) for the expected sentinel strings.
7. Compute SHA-256 of the chunks and compare with the local build's dist output — byte-identical means the deploy is real.

If sentinel appears in ANY chunk → deploy is verified.
If sentinel appears in NO chunk → THEN escalate as build/deploy issue.

### Automated guard
`frontend/scripts/postdeploy-verify.mjs` (created 2026-02-15)
implements this playbook. It fetches the served HTML, walks the
entire chunk dependency tree, and asserts a MANIFEST of expected
sentinel strings appears somewhere in the tree. Fails loud with
`process.exit(1)` if any sentinel is missing. Rerun after every
deploy and before claiming any tracking / analytics change is live.

Manifest is maintained inline in the script; any future feature
that adds new tracking events MUST add its literal event name to
the manifest in the same PR so future silent-drops are impossible.

### Standing rule reinforced
- "bundle-verified" ≠ "trigger-verified".
- bundle-verified = sentinel strings present in served chunks (SHA-256 comparable).
- trigger-verified = real user flow fires the expected network event (verified via DevTools Network filter or Meta Events Manager Test Events).
- Both labels are separately valid; do not conflate. A code deploy proves the code is on prod, NOT that any given code path runs at runtime.
