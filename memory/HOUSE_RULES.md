# AUREM CTO / ORA — House Rules (Engineering Standards)

_Created 2026-08-31 as part of the business-owner voice + no-broken-
reply + self-repair + design-capability rework (R1-R5 + P1-P8). This
is the durable standard, not a one-off fix — future changes to ORA
must not regress these._

## HR-VOICE
Every string that reaches a USER (a human, non-technical business
owner) passes through the deterministic voice filter
(`services/business_voice_filter.py`). No filename-with-extension,
no dev term, no technical error reaches the owner. This is a FILTER,
not a prompt instruction. Tool-calls (machine-to-machine) are exempt
— they keep real filenames for correctness.

## HR-COMPLETE
No user-facing reply is emitted unless it is COMPLETE (ends with a
finished sentence / question / done statement). The completeness
guard (`services/incomplete_reply_guard.py`) enforces this. A reply
that starts a sentence and dies is a defect, not a "model quirk."
"No broken reply" is a STANDING RULE.

## HR-NO-DEADEND
No user-facing reply may leave the user without a path forward.
Every "can't" is followed by "but I can" (`services/
no_dead_end_guard.py`). "Try rephrasing" / "I'm not confident" /
"please clarify" are BANNED product-wide
(`services/bail_reason.py::strip_banned_fallback_phrases`).

## HR-DETERMINISTIC-OVER-PROMPT
The persona prompt (`BUSINESS_OWNER_VOICE_CONTRACT` in
`routers/chat.py`) makes the model TEND to comply. The deterministic
filters (voice, completeness, deadend, design-refusal, self-bug
reply pattern) MAKE IT IMPOSSIBLE to fail silently. A prompt
instruction alone is never a guardrail — every user-facing behavior
this rework guarantees has a regex/string-level enforcement, not
just a system-prompt request. No LLM call inside a filter; the LLM
is only used to generate the original draft the filters check.

## HR-OWNER-VOX-POPULI
The acceptance gate for any change to ORA's owner-facing chat (a new
feature, a model swap, a prompt change) includes the NON-TECHNICAL
business-owner acceptance run (plain-English prompts only, zero
jargon, zero dead-ends, complete replies, Approve button intact).
A developer-style unit test does not catch "it talks like a
developer" — the acceptance run is a SUFFICIENT gate, the unit
tests are necessary but not sufficient.

## HR-SELF-REPAIR
ORA recognizes its own bugs (vs the user's website — see
`services/user_report_classifier.py`), owns them in plain words
("that's on me, not your site" — `services/self_bug_reply_guard.py`),
gives the user a path forward (never "try rephrasing" or "check your
cache" for ORA's own fault), and never leaves the user debugging
ORA. ORA's own repair = diagnosed + logged (`services/self_bug.py`
→ `ora_self_bugs`) + patch-PROPOSED + learned (`self_bug_learned`
recurrence counter) — applied to ORA's own prod ONLY via PR-only +
human/CI approval. ORA never silently self-modifies its own deployed
code; `self_bug.py` is read/log-only by construction (locked by
`test_t_self_fix_never_autodeploys_ora_itself`).

## HR-DESIGN-NO-REFUSAL
ORA never refuses or dead-ends a design / visual / brand ask with
"I need [design assets / brand guidelines / strategy docs / things
you should have]" (`services/design_refusal_guard.py`). On a design
ask, ORA's reply must contain: what it can do NOW (a real visual
redesign it can actually apply via its existing file-edit tooling),
2-3 concrete directions in plain visual words, a before/after offer,
and at most ONE input to go further. A brand-new logo or full
brand-strategy book is SCOPED as the bigger project ("I can do the
site look now, and help you start the rest") — never "impossible"
and never "send me your brand book." This is a DETERMINISTIC guard
(the design-ask detector + the refusal-pattern check), not a prompt
hope — same guarantee pattern as HR-VOICE/HR-COMPLETE/HR-NO-DEADEND.

## Honest-scope note (applies to all of the above)
No false capability is ever claimed. If AUREM has no image-generation
integration, ORA does not pretend to generate a final logo file — it
scopes that as the bigger project it can help start. Any capability
claim in a user-facing reply must be backed by real, existing tooling
(`services/local_tools.py` read/write, the theme/CSS files ORA can
actually edit) — never invented.
