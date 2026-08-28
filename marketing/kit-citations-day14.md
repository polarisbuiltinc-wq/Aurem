# Kit Citations — Day-0 Baseline (Visibility Kit Phase A / A7)

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
per-engine number the day-14 comparison should ultimately use. **The actual
3x/engine protocol should be run manually (by the founder, or via a future
ChatGPT/Perplexity/Gemini-specific integration) at both day-0 and day-14 for
an apples-to-apples number** — this file will be updated once that happens.

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

## Next action

- **Day 14 (2026-09-11)**: re-run this same proxy check + (ideally) the
  real 3x/engine ChatGPT/Perplexity/Gemini protocol, compare against this
  file, update the numbers here. Flag if zero lift (per R4 — no false
  numbers in kit marketing copy either way).
