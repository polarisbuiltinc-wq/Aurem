# Personal Track Tone Layer — Spec

**Status**: Backlog · P1
**Created**: 2026-02-12 (Iter 388d)
**Owner**: TBD
**Estimated scope**: ~200-300 LOC (safety.py + house_rules.py + one test file)

---

## Problem

`DEFAULT_HOUSE_RULES` (upgraded Iter 388c, 1148 chars) now enforces
anti-fabrication + retract discipline for every non-founder user.
But the wording is developer-flavored — `/read`, `/find`, `/defs`
slash-commands, "FILENAME INDEX block", "(unverified)" inline flag
markers. This is correct for **Developer Track** users (pro devs
who know what a filename index is).

For **Personal Track** users (T0-T4, non-technical — building small
apps, not code repos), the same content lands as:
- Confusing (they've never typed `/read`)
- Cold (senior-engineer voice, no warmth)
- Off-brand (Personal Track is marketed as accessible)

The 5 behavioral rules must **stay the same** — anti-fabrication and
retract discipline apply universally. Only the **surface wording**
needs to soften for Personal Track.

## Solution — Tone Layer, not a rule change

Add a second constant in `services/ora_chat/safety.py`:
`PERSONAL_TRACK_HOUSE_RULES` — same 5 rules, softer wording, no
developer-jargon slash-commands referenced. Route selection at
`get_effective_text()` time based on the user's `track` field
in `dev_users`.

### Selection logic (in `services/ora_chat/house_rules.py::get_effective_text`)

```python
async def get_effective_text(user_id: str) -> str:
    row = await get_current(user_id)
    if row and row.get("rules_text"):
        return row["rules_text"]           # custom overrides everything
    # No custom row → pick the tone-appropriate default
    db = get_db()
    if db is not None:
        u = await db.dev_users.find_one(
            {"user_id": user_id}, {"track": 1, "_id": 0},
        )
        if u and u.get("track", "").startswith("personal"):
            return PERSONAL_TRACK_HOUSE_RULES
    return DEFAULT_HOUSE_RULES              # developer / unknown
```

### Draft `PERSONAL_TRACK_HOUSE_RULES` (~900 chars, matches 5-rule contract)

```
1. Give direct, honest answers. Never dodge or sugar-coat. If you
   don't know something, say "I'm not sure" — a guess is worse than
   a "let me check".
2. Verify before you commit. If a request sounds off or is missing
   a piece, ask instead of assuming — a 5-second question saves
   a wrong build.
3. Only name a specific file, screen, or feature if you've actually
   looked at it this conversation. If you haven't, say so plainly:
   "I haven't opened that one yet — want me to?"
4. If you catch yourself about to say something you're not fully
   sure of, flag it in the same reply — write "(I think)" or
   "(let me double-check)" next to it. Don't wait for the user
   to ask "are you sure?"
5. If the user comes back and says "wait, that's not right" —
   accept it, don't argue. Say what you got right, what you got
   wrong, and where you were guessing. A clean retraction wins
   more trust than a defensive follow-up.
```

Key deltas vs developer default:
- No `/read`, `/find`, `/defs`, "FILENAME INDEX" references
- "file, screen, or feature" instead of "filename/function/line-number"
- "(I think)" / "(let me double-check)" instead of `(unverified)`
- "wait, that's not right" instead of "sure ho?", "padha hai?"
- Warmer framing: "a guess is worse than a 'let me check'"

## Tests to write

`backend/tests/test_iter388d_personal_track_tone_layer.py`:

1. `PERSONAL_TRACK_HOUSE_RULES` exists, length ∈ [700, 1500]
2. Contains all 5 rule concepts (same test coverage as 388c but
   with softer keyword synonyms: `soft.*claim`, `flag.*same.*reply`,
   `retract.*don't.*argue`, `verify.*before`, `honest`)
3. Does NOT contain developer jargon: `/read`, `/find`, `/defs`,
   `FILENAME INDEX`, `slash-command`, `(unverified)`
4. `get_effective_text(personal_track_user_id)` returns
   `PERSONAL_TRACK_HOUSE_RULES` when the user's `track` starts with
   `"personal"` and no custom row exists
5. `get_effective_text(dev_track_user_id)` returns `DEFAULT_HOUSE_RULES`
   when track ≠ personal
6. `get_effective_text(uid_with_custom)` still returns the custom
   row regardless of track (custom always wins — regression guard)

## Rollout

- Preview → real test with one Personal Track user in DB → screenshot
  chat response showing the softer wording lands
- No prod env var / migration needed (schema is code-only)
- No frontend change

## Non-goals (do NOT do in this task)

- Do NOT weaken the 5 rules for Personal Track. The rules stay
  identical in behavior. Only the surface wording softens.
- Do NOT touch Layer 1 (CORE_SAFETY_RULES) or Layer 2 (AUREM_CONTEXT).
- Do NOT add new tracks beyond `developer` / `personal` — schema
  supports more but tone-layer selection is a 2-way switch by design.

## Open questions for founder

1. Should we also inject one line at the top telling ORA it's
   speaking to a Personal Track user (e.g. "The user is building
   a small app, not a repo") so the LLM's answers themselves
   also soften? Or is prompt-tone enough?
2. Should the Reset-to-Default button in the House Rules admin UI
   auto-detect the track and reset to the matching default?
