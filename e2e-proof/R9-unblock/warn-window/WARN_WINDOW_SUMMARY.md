# P1 — R9 warn-window, corrected data (2026-08-30)

Agent-analyzed, NOT founder-confirmed/reviewed.

## Correct source (prior round read the wrong one)
- `guard_config` (Mongo, `_id="path_guard"`) — the actual Wave-1
  path-guard mode config, same doc `GET /admin/guardrails` reads.
- `guardrail_events` — the WARN/BLOCK hit log written by
  `services/write_guard.py::check_write_paths`, the single choke
  point every real GitHub write goes through
  (`services/github_api_writer.commit_files`/`revert_commit`/
  `close_and_retract` all call it before writing).
- **NOT** production QA "Gate Parity" (loop-start denial rates) —
  that's a completely different signal (session admission, not write
  content) and was the prior round's mistake.

## Data (live read, this pod, 2026-08-30)
- `guard_config.path_guard` document: **absent** → mode defaults to
  `"warn"` (confirmed by reading `write_guard.py` directly — a
  missing config doc is the safe default, not an open/disabled gate).
- Organic legit ship writes in the trailing 48h (sessions with a
  landed `context.commit.full_sha`, i.e. writes that necessarily went
  through `commit_files`): **1** — `loop_7014cd440aaf4c`, the
  pre-existing P6 write-guard drill, timestamped
  `2026-08-27T20:32:40Z`. (16 total shipped sessions exist all-time in
  this pod; only 1 falls inside the 48h window.)
- `guardrail_events` (any rule, all-time, before this round's action):
  **0 documents** — an empty collection.

## Insufficient organic traffic → filled per instruction
1 < 5. Per the explicit instruction ("trigger a few more controlled
test ships in Preview... to fill the window, THEN report — do NOT
invent numbers"), ran 4 additional REAL writes through the SAME
choke point (`services.github_api_writer.commit_files`), against the
same reachable drill repo already used for the T2/R1a drills
(`TJSNDHU/Aurem`, installation `157161705`) — script:
`fill_drill_script.py` in this folder.

- Write 1/4: `5804a48e...`
- Write 2/4: `eff783aa...`
- Write 3/4: (see script output)
- Write 4/4: (see script output)
- Cleanup commit (removes the marker file, raw REST, NOT through
  `commit_files` — doesn't count as one of the 4): confirmed committed.

Post-fill re-check: **`guardrail_events` WARN (`GW_WARN_*`) count in
the 48h window: 0. BLOCK count: 0.** (Still 0 after the 4 fill
writes — expected, the marker filename doesn't match any deny
pattern.)

## Positive control (proves 0-warn is a true negative)
Called `check_write_paths(db, ['.env', 'app/src/foo.py'], ...)`
directly (no real GitHub write, pure guard-logic exercise) →
correctly fired exactly one `GW_WARN_PATH` event for `.env`, correctly
ignored `app/src/foo.py`. Test event deleted immediately after — **not
counted** in the 48h stats above. This confirms the detector itself
is live and functioning; the 0-warn result on real writes is a
genuine clean signal, not a broken/silent guard.

## Output (per the requested format)
**5 clean ship writes (1 organic + 4 drill-filled), 0 warn events,
window = trailing 48h from 2026-08-30 (repo cleaned up after).
Verdict: CLEAN — filled with 4 more (organic traffic alone was
insufficient: 1 < 5). Guard independently confirmed functional via a
positive control.**

This is R9 item-2's data. **Founder review of this verdict is still
the remaining step** — this file presents the corrected numbers only,
it does not itself close the gate.
