# Dead "Sign in" link on production — diagnosis (ranked), 2026-08-28

**Non-repro on Preview confirmed**: live click test on this pod
(`https://bin-context-pat.preview.emergentagent.com/`) navigates
`nav-login` → `/login` correctly. Non-repro alone does NOT mean
"environment delta, not logic" by default — checked (a) first, below,
before assuming that.

## (a) Is production running the commit we tested?

**Checked live, both sides, via the app's own `/api/aurem-dev/version`
endpoint (no login needed):**

```
prod:    {"commit_sha":"02bdea1b0064","built_at":"2026-08-28T22:08:26Z"}
preview: {"commit_sha":"a6d741b3f34e","built_at":"2026-08-28T21:38:10Z",
          "last_github_push":{"commit_sha":"02bdea1b0064","pushed_at":"2026-08-28T22:01:47Z"}}
```

`02bdea1b0064` **is** reachable in this pod's own git history
(`git cat-file -t 02bdea1b0064` → `commit`) and is in fact this pod's
current `HEAD` at the time of writing. **Source code is NOT diverged** —
production is running the same lineage as this pod, including the
current `Landing.jsx`. This directly rules out "stale/different commit"
as the explanation. `git log` on `Landing.jsx`'s `nav-login` link also
shows no recent change to that specific element in the last several
commits touching the file — it isn't a regression from a recent edit
either.

## (b) The click handler + "auth-URL construction" — there is none to trace

`frontend/src/pages/Landing.jsx` (~line 778):
```jsx
<Link className="nav-link" to="/login" data-testid="nav-login">Sign in</Link>
```
This is a plain `react-router-dom` `<Link>` to an **internal SPA route**.
There is no OAuth redirect URL being constructed, no origin/env var
read, no `window.location` assignment for this button. **Per your own
instruction: the code shows no env-sensitive path at all for this
specific element — stating that plainly rather than inventing an
origin/redirect-URI theory that doesn't exist in this code.**

## (c) origin / redirect-URI / auth-domain env resolution

N/A per (b) — nothing in this click path reads an env var. (Google OAuth
elsewhere in the app does have real env-sensitive redirect logic, but
that is not what this button does.)

## (d) CSP / base-URL / service-worker caching

- **CSP**: `curl -I https://auremcto.com/` returns no
  `content-security-policy` header at all — nothing there to block a
  client-side route change.
- **Base URL**: `<BrowserRouter>` (App.jsx:270) with no `basename` prop —
  same in both environments, not env-sensitive.
- **Service worker — the one real, concrete, env-sensitive difference
  found**: `frontend/src/main.jsx:17-23` registers `/sw.js` scope `/` on
  every page load (`"serviceWorker" in navigator"` — true on any HTTPS
  origin, so this fires on production, and would also fire on Preview
  since Preview is HTTPS too — but Preview browser contexts used for
  testing are typically fresh/no prior SW registration, while a
  founder's own long-lived browser profile on `auremcto.com` accumulates
  SW state across every past deploy).

  `public/sw.js` (`CACHE_VERSION = "aurem-v3"`): navigation requests are
  network-first (good — `index.html` itself shouldn't go stale), static
  JS/CSS use stale-while-revalidate. Vite content-hashes JS chunk
  filenames, so a genuinely new deploy should produce a new URL and miss
  the old cache entry — in principle this SW design is low-risk for
  exactly this bug. But: `self.skipWaiting()` / `clients.claim()` take
  over on activate, which handles *future* installs correctly, but a
  browser can still be running an **older SW script** than `v3` if that
  browser tab/profile hasn't had a qualifying navigation/reload since
  before `v3` shipped — an older SW version could have had different
  (possibly buggy, e.g. cache-first) fetch-handling logic for
  navigations, serving a genuinely stale bundle indefinitely until a hard
  refresh or manual SW unregister.

## Ranked hypotheses

| # | Hypothesis | Likelihood | Exact thing to check on prod |
|---|---|---|---|
| 1 | Founder's own browser has a **pre-v3 (or otherwise older) service worker still registered** for auremcto.com, serving a stale bundle on repeat visits | **HIGH** | DevTools → Application → Service Workers on `https://auremcto.com`. Look at the registered worker's "Source" and whether it shows `aurem-v3` (search the SW script for `CACHE_VERSION`). Also run `caches.keys()` in the Console — if it shows anything other than `aurem-v3-static`/`aurem-v3-runtime`, it's stale. Fix if confirmed: click "Unregister" + hard reload (Cmd/Ctrl+Shift+R), or add a version-bump banner that posts `SKIP_WAITING` (the SW already listens for that message, `sw.js:115-117`, just nothing calls it yet). |
| 2 | A stale **cached JS chunk** served by the SW's stale-while-revalidate strategy on the FIRST load after a deploy (network fetch happens in background, this page load still ran the old chunk) | MEDIUM | In the Network tab, reload once, check if `/assets/index-*.js`'s "Size" column says "(ServiceWorker)" instead of a real byte count/"(disk cache)" on that exact load — if so, reload a 2nd time and see if Sign-in starts working (confirms staleness self-heals after one extra reload, matching this exact mechanism) |
| 3 | Click lands on an invisible overlapping element at the founder's specific viewport/zoom (topstrip marquee, cookie-consent banner, mobile nav drawer) that swallows the click without visibly changing anything | LOW-MEDIUM | Right-click exactly on the "Sign in" text on production → "Inspect" → confirm the highlighted element in DevTools is the `<a data-testid="nav-login">` itself, not some other node on top of it. Also check window width — Landing.jsx has a `@media max-width:720px` rule hiding all `.nav-link` except the last one (Sign in happens to be last, so it *should* still show, but worth confirming at the founder's actual window size) |
| 4 | Browser extension / ad-blocker blocking the click | LOW | Reproduce in an Incognito window with all extensions disabled |

**Not a hypothesis, ruled out**: different deployed commit, CSP
blocking navigation, base-URL/router basename mismatch, any
env-var-driven redirect logic (none exists for this button).

## Recommended founder action
Check #1 first (cheapest, highest-likelihood) — open DevTools on
`auremcto.com`, Application tab, Service Workers, and paste back what it
shows. If it's already `aurem-v3` and activated, move to #2, then #3.
