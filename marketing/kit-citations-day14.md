# Kit Citations — Day-0 Baseline (Visibility Kit Phase A / A7)

**Captured**: 2026-08-28 (Day 0, proxy run — see below). **Real Day-0 protocol run**: 2026-09-09
(founder-run, real Gemini/ChatGPT/Perplexity). **Re-run due**: 2026-09-11 (Day 14) — note the
real protocol's actual Day 0 is 2026-09-09, not 2026-08-28; Day 14 should be measured from
2026-09-09, i.e. **2026-09-23**, using the same 5 queries × 3 engines.

## DAY 0 (REAL, founder-run, 2026-09-09) — this is the baseline Day-14 improves FROM

5 category/branded queries × 3 engines (Gemini, ChatGPT, Perplexity).

### Category queries (4 queries × 3 engines = 12 checks)
"best AI SEO scanner", "GEO vs SEO", "how to get cited by ChatGPT", "AI readiness checker"

**AUREM cited: 0/12.** Not mentioned anywhere across any of the 4 category queries on any of
the 3 engines. The names that ARE cited in this category: **Semrush, Ahrefs, Cloudflare,
Otterly, Profound.**

### Branded query: "what is auremcto"
| Engine | Cited? | Note |
|---|---|---|
| Gemini | ✅ Yes | — |
| Perplexity | ✅ Yes | Caveat: citations are primarily from AUREM's own website — not an independent third-party eval. |
| ChatGPT | ❌ No | Responded "not sure, maybe a typo." |

### KEY READ (the strategic finding, not just the numbers)
- The engines that DO know AUREM (Gemini, Perplexity on the branded query) categorize it as
  **"an autonomous AI software engineer / automated developer assistant"** — NOT as "an AI SEO
  scanner." AUREM is recognized as an **AI-dev-that-fixes**, not an AI-scanner.
- AUREM is completely absent from the "AI SEO scanner / AI readiness checker" category queries,
  a category dominated by Semrush/Ahrefs/Cloudflare/Otterly.
- **This is the wedge** — see GEO strategy below. AUREM isn't unknown; it's mis-shelved. The
  fix isn't "get discovered," it's "get corroborated in the category the engines already put it
  in."

## GEO STRATEGY (direction, recorded — not built this round)

**Do NOT compete head-on for "best AI SEO scanner" / "AI readiness checker."** That category is
Semrush/Ahrefs/Cloudflare/Otterly turf — a small new product loses head-on there, and the day-0
data confirms zero presence today against that entrenched set.

**THE WEDGE**: AUREM's recognized + winnable category is not "AI SEO scanner" — it's **"the AI
developer that FIXES and SHIPS,"** which is exactly how the engines that already know AUREM
describe it (see KEY READ above).

**The #1 GEO lever** (per all 3 engines' own "how to get cited" answers, and consistent with the
day-0 finding that AUREM is currently ONLY cited from its own site): **third-party
corroboration.** The #1 predictor of AI citation is INDEPENDENT mentions — tech blogs, dev.to,
Hacker News "Show HN," Product Hunt, Reddit dev threads — not optimizing AUREM's own site
harder (A1-A5 dogfood work already covers the on-site half; it's necessary but not sufficient).

**So: the GEO content strategy = get AUREM mentioned INDEPENDENTLY in the "autonomous AI
developer that fixes & ships" category — not "AI SEO scanner."**

## CONTENT DIRECTION (the plan — marketing/founder-executed, NOT product build)

Own the "AI developer that fixes and ships" category via:
1. **Original-data / demonstration content** — the strongest citation trigger across all 3
   engines' own guidance. E.g. "we fixed N real issues across M repos — here's the before/
   after." First-party data is the top citation trigger; AUREM's own ship/fix history is exactly
   this kind of raw material.
2. **Independent third-party mentions** — dev.to posts, HN "Show HN," Product Hunt launch,
   genuine dev-community threads (Reddit r/webdev etc.). This is the #1 citation driver per the
   engines' own answers, and the one AUREM currently has zero of (day-0: cited only from its own
   site).
3. **Positioning**: "the AI developer that FIXES and SHIPS, not just finds bugs" — explicitly
   contrasted against the audit-only SEO/scanner tools that dominate the category AUREM should
   NOT be fighting for.

These are content/marketing actions (founder or outsourced) — explicitly NOT built into the
product this round, per instruction.

---

## Historical: Day-0 proxy baseline (2026-08-28) — superseded by the real run above, kept for record

**Captured**: 2026-08-28 (Day 0). **Re-run due**: 2026-09-11 (Day 14).

## Methodology — read this before quoting any number below

The spec (§4 A7) asks for 3 runs per engine across ChatGPT, Perplexity, and
Gemini's own consumer products, logging citations + position + URL cited.
I do not have direct automated access to those three consumer products from
this environment (no ChatGPT/Perplexity/Gemini UI automation, and the
Emergent LLM key covers raw model text generation, not their web-search-
augmented consumer citation behavior specifically).

What's below instead is a **day-0 general web-visibility proxy**, captured
via a citation-style web-search tool (same "search → synthesize → cite
sources" mechanism the target engines use, though not the engines
themselves). Treat this as a directional baseline, not the literal
per-engine number the day-14 comparison should ultimately use. **The real
protocol above (2026-09-09, founder-run) supersedes this as the actual
Day-0 baseline** — this section stays only for historical record.

## Day-0 results (1 run each, proxy search, 2026-08-28)

| Keyword | auremcto.com / ORA cited? | Who IS cited today |
|---|---|---|
| "best AI SEO scanner" | **No** | CiteFlow, AISEOScanner (useaiseo.app), SE Ranking, CrawlHound |
| "GEO vs SEO" | **No** | Semrush, WordStream, LoudPixel, Automaton Agency, ContextBolt |
| "how to get cited by ChatGPT" | **No** | GoGoChimp, Ranki.io, TheSocialTarget, SEOMods, Search Engine Land |
| "what is auremcto" | **Yes — cited as the primary/only source** (auremcto.com homepage, /terms, /subprocessors) | auremcto.com itself (branded query, expected) |
| "AI readiness checker for websites" | **No** | Agent Ready, Search Engine Land's own checker, isready.ai, ISYourWebsiteReady.com, aireadinesschecker.com |

## Honest read

- The branded query ("what is auremcto") citing us is expected and not a
  meaningful GEO signal — it's not competitive.
- On all 4 competitive/generic keywords, ORA/AUREM has **zero presence**
  today. A crowded, established set of dedicated AI-SEO-scanner and
  AI-readiness-checker products already occupy this space — worth noting
  for positioning, since "AI readiness checker" is close to ORA's own
  pitch and several direct competitors (Agent Ready, isready.ai) already
  rank there.
- This is the honest day-0 floor. If Phase A's dogfood work (robots.txt,
  llms.txt, JSON-LD — already largely in place pre-dating this round, see
  CHANGELOG "Iter 212m-68") plus the day-14 re-run shows any lift on the
  generic keywords, that's the real signal — not the branded one.
- **Consistent with the real 2026-09-09 protocol run above** — both the
  proxy and the real engines agree: zero presence in the AI-SEO-scanner
  category, dominated by the same class of entrenched tools (Semrush/
  Ahrefs/Cloudflare/Otterly in the real run; CiteFlow/SE Ranking/Agent
  Ready in the proxy). This corroboration is why the GEO strategy above
  pivots away from that category entirely rather than fighting for it.

## Next action

- **Day 14 (target 2026-09-23, 14 days from the REAL 2026-09-09 protocol run)**:
  re-run the same 5 queries × 3 engines (Gemini/ChatGPT/Perplexity), compare
  against the DAY 0 (REAL) section above, update the numbers here. Flag if
  zero lift (per R4 — no false numbers in kit marketing copy either way).
  The 2026-09-11 date below was tied to the OLDER proxy run's 2026-08-28
  start date and is now superseded by 2026-09-23.
- ~~Day 14 (2026-09-11)~~ — superseded, see above.

