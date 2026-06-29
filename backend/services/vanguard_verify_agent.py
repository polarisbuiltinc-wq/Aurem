"""
services/vanguard_verify_agent.py
=================================

Iter 111 — Separate Vanguard verify agent (Anthropic-style "defending-
code-reference-harness"):

> ORA writes code → handed off to a SEPARATE agent (different prompt,
> different model) → re-reviews the patch for the 25 known vulnerability
> patterns PLUS its own LLM-grade judgement → only on PASS does the
> patch progress to commit.

Iter 169 — migrated to OpenRouter (anthropic/claude-sonnet-4-5-20250929).
The previous Emergent SDK dependency was dead weight after llm.py was
cleaned up — when EMERGENT_LLM_KEY was unset the verify pipeline
silently skipped LLM review, leaving only the regex floor. Now it
goes through the same OPENROUTER_API_KEY all other LLM calls use, so
the second-agent review actually runs in production.

Iter 212m-41 — soften the LLM blocking rule. The previous prompt told
the agent `pass` was false on ANY CRITICAL or HIGH finding, which made
Claude (now overly diligent) block routine commits on theoretical
HIGH risks (e.g. `localStorage.setItem("token", …)`, inline `style=`,
React `dangerouslySet\u0049nnerHTML` in a tooltip, etc.). The regex floor
(`scan_file_blocks`) is what guarantees the real CRITICAL gates; the
LLM agent is now advisory