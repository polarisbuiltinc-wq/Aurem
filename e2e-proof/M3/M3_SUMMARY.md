# M3 — output_guard.py context-aware file-path fix (2026-08-30)

Founder-directed fix, root cause found during M2's fence-rate retest.

## Root cause
`services/output_guard.py`'s bare-file-path redaction (part of the
"Show the Outcome, Never the Engine" leak guard) was a **blanket
strip**: any file-path-shaped token in a reply → "a project file",
with no awareness of whether the user themselves had already named
that exact file in the current turn. Reproduced live in M2
(`/app/e2e-proof/M1-M2/m2_fence_2.json`): user asked to ship a fix to
`services/response_confidence.py`; the real model's reply correctly
referenced that file, but the guard rewrote every mention to "a
project file", producing a self-contradictory, confusing answer
("the file `a project file` does not exist... let me know which
file...") and no ship fence.

## Fix
- Split the bare-file-path pattern into its own `_FILE_PATH_PATTERN`
  (was buried in the `_MACHINERY_LEAK_PATTERNS` list).
- New `extract_named_files(user_prompt)` — pulls every file-path-
  shaped token out of the user's OWN message this turn.
- `strip_machinery_leak(..., user_named_files=...)` — the file-path
  redaction now skips any match equal (case-insensitive) to a file
  the user already named; every other path (anything the model
  surfaces on its own) is still redacted, unchanged. Scope unchanged
  otherwise — still explain-mode-only (`universal_only=False`), same
  as before.
- **Also broadened the file-path regex itself** — it previously only
  matched `.py/.js/.jsx/.ts/.tsx` for bare (no-slash) filenames, so a
  bare root file like `README.md` or `.gitignore` never matched at
  all (a second latent bug, caught by `t_output_guard_keeps_user_named_file`
  failing before this widening). Now covers `md/txt/json/yml/yaml/
  toml/cfg/ini/env/lock` plus dotfiles.
- **New**: secret/token-shaped strings (`AKIA...`, `ghp_...`,
  `sk_live_...`, `mongodb(+srv)://...`) are now UNCONDITIONALLY
  redacted in `_UNIVERSAL_LEAK_PATTERNS` — this mechanical net didn't
  exist in this module before (only a persona-level self-censor
  instruction did); added per the founder's explicit boundary ("keep
  redaction for secrets/tokens... independent of the file-name
  relaxation").
- Wired `user_named_files=extract_named_files(body.prompt)` into both
  `/chat/send` and `/chat/stream`'s leak-strip calls.

## Tests (all 3 founder-named + 1 regression guard, 4/4 pass)
`backend/tests/test_m3_output_guard_named_file_fix_2026_08_30.py`:
- `test_t_output_guard_keeps_user_named_file`
- `test_t_output_guard_redacts_unnamed_file` (regression guard — an
  unnamed path is still redacted, unchanged)
- `test_t_output_guard_still_redacts_secrets`
- `test_t_output_guard_fenced_reply_survives`

## E2E
Module-level (deterministic text-transform, no model call needed —
`test_t_output_guard_fenced_reply_survives` IS the E2E for this fix:
constructs a real reply shape naming a user-requested file +
carrying a real `\`\`\`aurem-handoff` fence, proves both the filename
and the fence survive intact). Mock-mode live check
(`/app/e2e-proof/M3/e2e_mock_reply.json`) confirms mock replies are
generic canned text that never mentions file paths at all — this
guard's file-path branch is inherently a real-model-adjacent concern
(the model has to actually generate a filename mention for the guard
to have anything to act on), already reproduced live once in M2 (the
BEFORE state) and unit-tested exhaustively for the AFTER state; no new
real-model spend was used for M3 (founder's ask was a ~30min text-fix
round, not a new bounded window).

## Regression
`pytest tests/test_m3_output_guard_named_file_fix_2026_08_30.py
tests/test_iter2026_08_27_p5_engine_leak_cleanup.py
tests/test_iter_customer_raw_error_leak_fix.py
tests/test_iter212m211_advisor_tool_leak.py
tests/test_iter55_tool_call_leak_and_timeout.py
tests/test_iter212m168_execute_bash_scope_leak.py
tests/test_iter2026_08_27_ci_leak_linter.py
tests/test_iter212m16_admin_password_leak_and_health.py`:
**63 passed, 7 pre-existing (baseline-confirmed) failures, 0 new.**

**Investigated one A/B false alarm**: `test_chat_stream_sse` and
`test_chat_stream_returns_real_response_and_writes_audit` initially
looked like new regressions in a broader `-k` sweep. Root-caused via
a direct `git checkout` A/B (not `git stash`, which would have also
reverted an unrelated, pre-existing, already-modified `frontend/.env`
and produced a false comparison) on just the 2 touched files + a
backend restart: **the underlying bug — `/chat/stream` never persists
to chat history at all (0 messages after a real completed stream,
`/chat/send` persists correctly) — reproduces identically on
unmodified baseline code.** Confirmed pre-existing, NOT caused by M3.
Flagged below, not fixed (out of scope this round).

## Flags/state
No MOCK_LLM flip needed for M3 (text-transform fix, unit-tested).
`MOCK_LLM=true` unchanged throughout.

## NEEDS-FOUNDER (one-liner, newly found during M3's regression check, not fixed)
`POST /api/aurem-dev/chat/stream` completes successfully (SSE
done-frame, correct content) but never writes the turn to
`chat_sessions`/history — `GET /chat/history?session_id=...`
returns 0 messages right after a real completed stream, while
`/chat/send`'s equivalent flow persists correctly. Confirmed
pre-existing (reproduces on unmodified baseline code, unrelated to
M3). Worth its own small fix round.

## New this round (2026-08-30, founder follow-up — M3 E2E gap close-out)

Founder correction: the earlier live mock-chat E2E check (above) never
demonstrated the fix, since the mock reply contained neither a
filename nor a secret — proved nothing either way. Closed with a new,
explicit combined test:

`test_t_output_guard_m3_e2e_combined_no_llm_no_network` — one
string-in/string-out reply containing (a) the user-named file
`README.md`, (b) a secret-shaped token `AKIAABCDEFGHIJKLMNOP`, AND (c)
a real ` ```aurem-handoff ` fence, all together. Asserts all three
guarantees hold simultaneously: filename survives, secret redacted,
fence intact. Zero LLM, zero network — deterministic and durable
(doesn't depend on what a live/mock model happens to say).

**Full M3 suite now 5/5** (was 4/4): the 3 founder-named tests +
1 regression guard + this new combined E2E. Re-run:
`pytest tests/test_m3_output_guard_named_file_fix_2026_08_30.py -v`
→ 5 passed.

## STATUS: M3 CLOSED (agent-tested, not founder-confirmed)
