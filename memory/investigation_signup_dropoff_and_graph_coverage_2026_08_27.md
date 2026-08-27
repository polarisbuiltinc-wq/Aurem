# Investigation — Signup Drop-off (Finding 1) & Graph Coverage 0% (Finding 2)
2026-08-27. INVESTIGATION ONLY — no code shipped, no flags changed.

## FINDING 1 — new signups die between "chat opened" and "repo connected"

### Q1 — Instrumentation check
**CONFIRMED: real, material instrumentation gap.** Two different funnel
systems exist and neither tracks the actual first click a fresh user makes:

- `funnel_events` collection (chat.py/cto_projects.py) has `chat_opened`,
  `project_add_attempt`, `project_add_success/failure` — but NO click event
  for the wizard/banner CTAs themselves.
- `github_funnel_events` collection (`routers/github_funnel.py`) has a
  `cta_click` stage, but it ONLY fires from the legacy OAuth-token button
  (`NewUserWizard.jsx` `connectGithub()`, `has_token:true`) — a secondary,
  rarely-shown path.
- The PRIMARY, RECOMMENDED button every fresh user actually sees —
  **"Continue with GitHub App"** (`data-testid="wizard-app-install-btn"`,
  wired via `hooks/useGitHubConnectStatus.js::startConnect()`) — fires
  **zero client-side tracking**. It does a raw `window.open(url)`. The
  only signal anywhere is the SERVER-side `app_install_redirect` stage,
  and that ONLY fires if the popup isn't blocked and actually reaches
  `/github/app/install`.
- The persistent `ConnectRepoBanner.jsx` "Connect repo →" button
  (`data-testid="connect-repo-banner-cta"`) also fires **zero** tracking —
  its `onClick` just does `setShowWizard(true)` (local React state).
- Live 60-day aggregate confirms this is not a rare edge case:
  `oauth_redirect=124`, `repo_selected=23`, `cta_click=3`,
  `app_install_redirect=3`. The gap between `oauth_redirect` (124, an OLD/
  different flow) and `app_install_redirect` (3, the modern flow) shows the
  modern App-install click path is essentially dark in current telemetry.

**Verdict on the founder's original claim** ("they never even clicked
Connect repo"): downgrade from CONFIRMED to **LIKELY at best, more
accurately UNCERTAIN**. A user who clicked "Continue with GitHub App" and
had the popup blocked (common on mobile browsers — relevant given the
Meta-ads/mobile traffic share) would look IDENTICAL in current telemetry
to a user who never clicked anything. We cannot currently tell these two
populations apart.

**Missing events (gap list):**
1. Client-side click on `wizard-app-install-btn` (before popup open attempt)
2. Client-side click on `connect-repo-banner-cta`
3. Popup-blocked outcome (`startConnect()` returns `{ok:false, reason:"popup_blocked"}` today but nothing is recorded)
4. Click on `wizard-mode-connect` / `wizard-mode-scaffold` tabs

### Q2 — Reproduced the exact first screen (live, real account)
Created a fresh signup, set `email_verified:true` (matching the stuck
cohort's real state), logged in as that user. Screenshot evidence
captured live (two states — see below).

**What renders, in order, for a genuinely fresh (0-project) user landing
on `/dashboard`:**
1. TopBar + (hidden, since nothing's broken) `RepoCleanupBanner`
2. `ConnectRepoBanner` — headline **"Connect a repo to unlock your free
   SEO fix"** with a "Connect repo →" CTA — this renders but is
   IMMEDIATELY covered by:
3. `NewUserWizard` modal, which **auto-opens with zero clicks required**
   (`Dashboard.jsx` line 251: `if (list.length === 0 && !isWizardDismissed()) setShowWizard(true)`)
4. Inside the modal: "ORA GUIDE" robot-guide text, heading "Connect your
   GitHub repo", two tabs ("🔗 Connect a repo" / "💡 Start from an idea"),
   a "Checking GitHub connection…" spinner, then (once resolved) a card
   "Install Aurem for your repos [RECOMMENDED]" with copy "One click — no
   token to manage. You pick which repos Aurem can see. Revoke any time
   from GitHub." and a "Continue with GitHub App →" button — **duplicated**
   (same label appears twice: once in the card, once in the modal footer).
5. A separate floating "ORA GUIDE" chat bubble bottom-right repeating the
   same instruction a third time.
6. A cookie-consent banner competing for the same bottom third of the screen.

### Q3 — Is the CTA visible/prominent/understandable?
**Visible & prominent: CONFIRMED yes** — large, orange, icon-labeled button,
reinforced 3x (card button, footer button, floating guide bubble).

**Understandable to a non-technical visitor: LIKELY no.** The entire screen
(both title and both CTAs) is GitHub-vocabulary-first: "Connect your GitHub
repo", "Continue with GitHub App", a support hint about "GitHub shows 'No
repositories found'". There is no framing for someone who doesn't know what
GitHub is or doesn't have a repo — relevant because Finding 1's own evidence
says a real share of this traffic is Meta Ads (non-technical acquisition
channel). The "Start from an idea" tab exists as an alternate path but is
an unexplained secondary tab, not a "no GitHub? no problem" reframing.

### Q4 — Is there a zero-risk value moment before the permission ask?
**CONFIRMED gap, but nuanced.** The value hook DOES exist in the codebase —
`ConnectRepoBanner`'s headline "unlock your free SEO fix" is a real,
concrete, low-risk value promise. But it is rendered BEHIND the
auto-opening wizard modal for exactly the population this matters most for
(brand-new users). The modal itself carries no equivalent value-first
copy — it goes straight to "Connect your GitHub repo" / permission
mechanics. Net effect: the one good value-prop line in the product is
structurally hidden from first-time users by the very modal built to
convert them.

### Q5 — Tie-back to "first-scan aha"
**Supports it directly, does not point elsewhere.** The banner copy already
assumes a "free SEO fix" is the intended aha moment — it's written, just
not surfaced at the moment of highest friction (permission ask). This is
strong evidence FOR building the first-scan-aha moment, specifically:
show/preview it INSIDE the wizard, before or alongside the permission ask,
not only in a banner that gets covered up.

### Candidate fixes (design/copy only, ranked, nothing built)
1. **(Cheap, high-confidence) Instrument the 4 missing click/outcome events**
   listed above. Without this, every future onboarding experiment is
   flying blind — can't tell "didn't click" from "clicked, popup blocked."
2. **(Cheap) Surface the "free SEO fix" value line INSIDE the wizard modal**,
   above or beside "Connect your GitHub repo", not only in the banner it
   currently covers.
3. **(Medium) De-duplicate the two identical "Continue with GitHub App"
   buttons** in one modal view — likely reads as broken/confusing, not
   reassuring.
4. **(Medium) Non-technical-safe copy pass** — add a one-line "no GitHub
   account? Start from an idea instead" next to the tabs, and soften
   "repo"/"GitHub App" jargon on first view for cold Meta-ads traffic.
5. **(Larger, matches existing backlog) Build the first-scan-aha moment**
   itself — a concrete before-ask preview of value — as previously flagged,
   now with fresh corroborating evidence.

---

## FINDING 2 — "Graph Coverage: 0%" on all 25 connected projects

### Q1 — What the feature does / intended trigger
`services/graph_builder.py` builds a lightweight "knowledge graph" of a
repo (regex symbol/import extraction + one cheap LLM pass describing the
top 20 files), used to give the PLAN phase of a loop task a compact repo
map so it can pick files without guessing. **Intended trigger: lazy,
on-demand, inside the loop engine's PLAN phase** — NOT on connect, not
scheduled. Comment in `graph_builder.py`: "Auto-refreshes via warm-start
(rebuild trigger: graph > 1 hour old)" — i.e., it rebuilds the next time a
real task is planned, if the existing graph is stale or missing.

### Q2 — Is it wired to a real trigger? What actually happens?
**CONFIRMED, two separate real issues, not one:**

**(a) The admin dashboard is reading the wrong place — this is the
primary, immediate cause of the "0% across all 25" number.**
`routers/admin_analytics.py::admin_graph_status()` (the `/admin/graph-status`
endpoint backing the Admin.jsx "Graph:"/"Nodes:"/"Graph Built:" columns)
queries `db.cto_projects` for fields `graph_built_at` / `graph_node_count`.
Live query, right now: **`cto_projects` documents with `graph_built_at` set:
0** (out of all projects). But `graph_builder.build_graph()` (the function
that actually builds graphs) **only ever writes to `db.project_graphs`**
(`graph_builder.py` line 418) — it never touches `cto_projects` at all.
Live query, right now: **`project_graphs` collection has 8 real, fully-built
documents** — including a genuine one for a live project with real layers,
edges, an LLM-generated architecture explanation, and a rendered Mermaid
diagram (pasted as evidence in the full investigation — not empty, not a
stub). **The admin panel is structurally incapable of ever showing
"Graph: yes" for any project, regardless of whether a graph was built,
because it checks fields that no code path writes.**

**(b) Separately, real (non-display) coverage IS also genuinely low —
but for a normal, explainable reason, not a silent crash.**
The ONLY automatic build trigger is inside `loop_engine.py`'s PLAN phase
(~line 4200), and it's gated on `get_repo_token_or_error(proj)` succeeding
— if the token lookup fails (e.g. `app_installation_missing`, the same
class of issue already documented for the P2/P3 GitHub-App-install gap),
the whole block is skipped **silently** (no log, no error — the `if tok:`
condition is simply false). Combined with live funnel data showing almost
no users ever reach a real PLAN/ship cycle (`first_loop_started=1`,
`first_task_shipped=1` in 60 days, across the whole userbase), it is
entirely consistent that most of the 25 connected projects never triggered
a build attempt at all — not because of a crash, but because they never
got far enough, and/or their token lookup also silently failed.

### Q3 — Verdict
- **(a) real, silent failure** — **LIKELY, partially**: the token-gated
  silent skip in `loop_engine.py` is real and undetectable from logs, and
  plausibly affects some fraction of the 25. Cannot currently quantify
  what fraction without per-project token-health data (out of scope this
  round).
- **(b) intentionally disabled / deprecated** — **CONFIRMED false.** The
  feature is live code, actively called, and has 8 real successful builds
  in this same database. Not deprecated.
- **(c) works but admin display is wrong** — **CONFIRMED, and is the
  dominant explanation for the exact "0% of 25" figure reported.** This is
  a straightforward, fixable admin-panel bug: read from `project_graphs`
  (keyed by `project_id`) instead of nonexistent `cto_projects` fields.

### Q4 — Blast radius
- **AI quality**: degraded, not broken. Missing graph = PLAN phase loses
  the "compact repo map" hint (`repo_map.py` gracefully returns
  `has_map:false`); the planner falls back to other context-gathering
  paths. Real but soft degradation, not a hard failure.
- **Does it affect the stuck-signup cohort?** **CONFIRMED no, not
  directly** — the stuck cohort never reaches PLAN phase at all (they
  don't even finish connecting). This is a fully separate, downstream
  issue that only matters for the fraction of users who DO connect and
  DO run a real task.

### Candidate fix (design-only note, nothing built)
Highest-leverage single fix: point `admin_graph_status()` at
`project_graphs` instead of `cto_projects`. Rough effort: small
(single-endpoint query fix + verifying `nodes` field is excluded for
payload size, matching the existing `get_graph()` pattern). Separately,
add a debug log line in `loop_engine.py`'s silent-skip branch so a missing
token is at least visible in logs going forward — currently
indistinguishable from "graph was already fresh."

---

## Recommended next round (ranked, my call, awaiting your GO)
1. **Fix admin graph-status query** (Finding 2a) — small effort, directly
   fixes a misleading admin number, no user-facing risk.
2. **Instrument the 4 missing click/outcome events** (Finding 1, Q1) —
   small-medium effort, no UX change, unlocks real measurement for
   everything else on this list.
3. **Surface "free SEO fix" value line inside the wizard modal** +
   **de-dupe the two identical CTA buttons** (Finding 1, Q3/Q4) — small
   effort, copy/layout only, no new flow.
4. **Non-technical-safe copy pass for cold Meta-ads traffic** (Finding 1) —
   small-medium effort, copy only.
5. **Build the first-scan-aha moment inside the wizard** (Finding 1, Q5) —
   larger effort, ties into existing parked backlog item, highest expected
   impact on the 75% stuck-cohort number specifically.
