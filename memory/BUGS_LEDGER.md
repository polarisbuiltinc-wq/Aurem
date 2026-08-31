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


---

## L-02 · ORA "file confusion" — named file requested, different file answered (intent/target mismatch)

**First reported**: pre-2026-08-31 (founder says recurring, exact prior
instance not located in this ledger/memory — see "Not yet confirmed"
below).
**Recurrence pattern**: founder-reported TWICE now. Not yet reproduced
with a captured transcript, so root cause below is a HYPOTHESIS, not
confirmed. Log now so the NEXT occurrence can be captured properly
instead of re-diagnosed from scratch.

### Symptom
Founder asks ORA about a specific named file (e.g. `AuremHomepage.jsx`).
ORA's answer is based on inspecting a DIFFERENT file (`README.md`) —
without flagging the mismatch to the founder. The founder only caught
it because they happened to notice the wrong file named in ORA's reply.

### What the code actually does (confirmed, not a hypothesis)
`services/local_tools.py::read_repo_file` requires an EXACT path string
as `args.path` from the model. There is no fuzzy-matching/best-guess
substitution in this function — if the path doesn't exist, it returns a
loud, explicit 404 telling the model to STOP guessing and call
`list_repo_files` with a glob to discover the real path, and NOT to
answer until it has:
> "STOP guessing paths. Your next tool call MUST be `list_repo_files`
> ... Do not write a plan, do not produce a handoff brief ... until
> you have called list_repo_files and seen the actual layout."

### Leading hypothesis (UNCONFIRMED — needs a captured transcript to verify)
Because the substitution is silent (no visible error/caveat to the
founder), this is most likely an LLM tool-call-selection/prompt-adherence
gap, not a broken function:
1. `AuremHomepage.jsx` may not exist under that exact name/path in the
   connected repo (e.g. it's actually `Homepage.jsx` or `Landing.jsx`) →
   `read_repo_file` 404'd → the model did NOT follow the 404's explicit
   "call list_repo_files, don't answer yet" instruction, and instead
   fell back to a file it already had context on (README.md) to answer
   a "what's on the homepage" style question generically.
2. Less likely: the model never called `read_repo_file` with that path
   at all — jumped straight to README.md as a "get oriented" step and
   answered from there without ever attempting the named file.

### NOT yet done
- Prior occurrence transcript not located — founder should paste it
  next time (or this time, if still available) so both instances can
  be compared for a real pattern vs. two unrelated one-offs.
- No code change made this round — founder said "note it, move on."

### Verification playbook for the NEXT occurrence
1. Capture the exact tool-call trace for that turn (which tools were
   called, in what order, with what `args.path`) — most chat/admin
   surfaces log this; pull it from `loop_events`/`chat` logs or the
   session transcript.
2. Check whether `AuremHomepage.jsx` exists at the exact path the
   founder meant (`list_repo_files` glob `**/AuremHomepage*`).
3. If it exists and was never called → prompt-adherence gap (model
   picked README.md without even trying the named file).
4. If it 404'd and the model answered anyway without calling
   `list_repo_files` next → confirms hypothesis #1 above; the loud-404
   instruction is being ignored under some condition (e.g. only enforced
   within Loop mode's stricter system prompt, not regular chat).
