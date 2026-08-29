# T3 — First-Experience Wave: B4 real-model window (2026-08-30, T2-T5 GO chain)

Founder-authorized bounded real-model validation: MOCK_LLM off, hard $3
cap, restore + zero-spend proof after. Timestamps + spend below.

## Flip timestamps
- **Flip OFF (MOCK_LLM=false)**: 2026-08-29T16:07:22Z
- **Flip ON (MOCK_LLM=true)**: 2026-08-29T16:10:43Z
- Window: ~3m21s

## Spend (real `llm_cost_ledger` totals, before/after)
- Before window: **$5.226704**
- After window: **$5.265372**
- **This window's real spend: $0.038668** (1.3% of the $3 cap — well under)
- Post-restore check (3 more real calls attempted): total unchanged at
  **$5.265372** — confirms MOCK_LLM=true gate is working, zero real
  spend leaking through (`/app/e2e-proof/T3/B4-real-model/mock_restored_msg_*.log`,
  providers `mock`/`intent-gateway-casual` only, no LLM spend).

## 5 real-model messages sent (session `t3-b4-drill-1`, project
`funnel-repro` / `polarisbuiltinc-wq/ora-grounding`)
Full transcript: `/app/e2e-proof/T3/B4-real-model/transcript.txt`.

| # | Prompt | Tier | Provider | Finding |
|---|---|---|---|---|
| 1 | "Hi, what does this tool do?" | casual | intent-gateway-casual | **NEW finding, P1**: answer was factually wrong/hallucinated — "It helps you quickly find and analyze audio data for any project." AUREM/ORA is a repo-connected coding/CTO assistant, not an audio tool. A brand-new user's very first message gets a confidently wrong product description. |
| 2 | "I'm really frustrated, my last fix broke my site... fix it fast" (K2 complaint-handling probe) | agentic | longcat-2.0 | Good — acknowledges frustration, actually checked real repo history, gave an accurate, specific, non-generic answer (not a filler apology). K2 (complaint-handling) reads healthy on this sample. |
| 3 | "In README.md, add a comment at the top saying: T3 B4 real-model probe" (real fence-emission probe) | clarify (intent LLM timed out at 2s) | intent-gateway-casual | The intent-classifier LLM call hit its own 2s budget and fell back to an ambiguous "did you want me to take an action, or were you just chatting?" clarify — a plain, unambiguous file-edit instruction needed a second round-trip. Matches the pre-existing "casual-tier calls misclassified" finding from the prior T1/R8 round (not new, same root cause: intent-gateway latency budget). |
| 4 | "...explain in detail everything about how loops, PRs, rollback, and the deploy pipeline all work together..." (K5/over-explaining probe) | agentic | longcat-2.0 | **NEW finding, P1 (K5-adjacent)**: response was VERBATIM IDENTICAL to message 2's "your site isn't broken" answer — a completely different question got the prior turn's answer repeated, not a real answer to the actual question asked. Same token cost (Δ65 both times) supports this being a real anchoring/context bug, not coincidence — the model latched onto the earlier "is my site broken" narrative and never engaged with the new question. |
| 5 | "ok cool thanks" | casual | intent-gateway-casual | Clean, appropriately short close. No stray "via ORA" suffix (K9) observed on this sample. |

## K-table update (supersedes `PART_D_E_F_SYNTHESIS_2026_08_28.md`'s
`NEEDS REAL-MODEL RE-TEST` rows for K2/K5/K9, sample size n=1 each —
small sample, not exhaustive)
- K1 (ship-CTA fallback): not exercised this window (message 3 never
  reached ship stage — clarify branch). Still `NEEDS REAL-MODEL RE-TEST`.
- K2 (complaint handling): **observed healthy** — grounded, specific,
  not generic.
- K5 (over-explaining / off-topic drift): **observed FAILING** — see
  message 4 finding above. New, not previously documented at this
  specificity.
- K9 (stray "via ORA" suffix): **not observed** on this small sample
  (still worth a larger sample before declaring closed).
- **New finding, not in the original K-table**: message 1's factually
  wrong casual-tier product description. Flagged for founder — this is
  the FIRST message a brand-new user could ever send, and it lied about
  what the product does.

## Disposition
Both new findings (msg1 wrong product description, msg4 context-
anchoring repeat) are reported here as findings, NOT fixed this round —
fixing model/prompt-behavior issues is its own workstream (prompt
engineering + a larger real-model sample), out of scope for T2-T5's
literal ask (journey verification + report), and the founder's
instruction was explicit: "Do not stop... T2 → T3 → T4 → T5 ... Report."
Not silently dropped — carried into the T5 final report + flagged for
founder triage as a new backlog item.
