# ROADMAP — prioritized backlog (post 2026-08-27 ORA Chat v2 P1-P5 checkpoint)

See PRD.md for original requirements/architecture, CHANGELOG.md for what's shipped.

## P0 — in-flight / founder-blocking (updated 2026-08-27, post-checkpoint decisions)
- **GUARDRAIL REMEDIATION (2026-08-28, master build plan approved by founder)** — Wave 0 (read-only re-verify) + Wave 1 (#2 path guard) DONE, see CHANGELOG.md for full detail. Current state:
  - `guard_config.path_guard.mode` = **warn** (default, Preview) — no writes are blocked yet, only logged + alerted. Founder reviews 48h of `GW_WARN_PATH` events (via `GET /admin/guardrails`), then flips to `block` via `POST /admin/guardrails/path_guard/mode`.
  - Next: Wave 2 (#8 ship-via-PR, effort M) is next up once founder reviews the Wave 1 WARN log — **waiting on founder go-ahead, do not start Wave 2 opportunistically.**
  - Wave 3 (#22 cost guard) is a **hard gate before DashScope key activation** — key activation is blocked until Wave 3 lands per founder's locked sequence.
- **DASHSCOPE_API_KEY** — founder is sending separately. Two conditions before real-model flip counts as "P1-P5 done on real model":
  a. Founder sets a $20 spend cap on the key in the DashScope console (founder-side action).
  b. Main agent runs this smoke test the moment `MOCK_LLM=false` is flipped: 2x plain chat (no tools), 3x tool-call flows (any 3 of the 6 read-only tools), 2x action propose→approve→execute (1 READ-tier tool call, 1 REVERSIBLE action), 1x rate-limit boundary (21st msg/hr → explicit cap message, no silent drop), 1x mock-vs-real shape check (delimiters, token pills). Then report cost-per-10-messages + per-session token totals from logs — this becomes the beta pricing base number. **Key discipline: env only, never in logs/fixtures/responses. Founder will rotate it when beta pricing is set.**
  c. **UPDATE (round 3)**: the founder can now ALSO paste the key via the new Admin Self-Serve LLM Settings UI (`/admin/settings` → Models & LLM → Add provider → role=chat → Test → Set active) instead of an env var — either path works, DB-active-config takes priority over env. Whichever path is used, run the same smoke test above once live.
- **Action Audit View** — SHIPPED 2026-08-27 (round 2). In-chat panel (`ora-chat-actions-btn` → `ora-action-audit-panel`) lists every proposal grouped by `proposal_id` (latest status only, not raw events) with risk-tier badge + description ("what will change"). Tested (backend unit + frontend component test), live-screenshotted with real seeded data.
- **Admin Self-Serve LLM Settings** — SHIPPED 2026-08-27 (round 3). `/admin/settings` → Models & LLM: add/edit/test/activate/delete any LLM provider config, zero code/deploy/restart. Fully tested (12 named unit tests + testing_agent E2E, zero action items). See CHANGELOG.md for full detail.
- **Visibility Kit Phase A (dogfood)** — IN PROGRESS (2026-08-28). Master spec received from founder (full text archived in LOOP-STATE.md's "PHASE 3" section). A1/A2/A3/A4 done (A1/A2/A4 pre-existing from an earlier SEO overhaul, A3 had 1 gap closed). A5 (PreferredSourceButton) built+tested. **Gate A NOT closed**: A6 (ChatGPT site verification) is founder-only/manual; A7's real 3x/engine citation protocol needs either founder manual runs or a future ChatGPT/Perplexity/Gemini-specific integration — day-0 proxy baseline captured in `marketing/kit-citations-day14.md`, real day-14 re-run due 2026-09-11. Phase B (product code) correctly not started per spec's own mandatory ordering.
- **P6 Page Inspector — ON HOLD**, prep-only so far:
  - Confirmed current DashScope Qwen-VL model via `integration_expert` (round 3): **`qwen3.7-plus`** is the current recommended vision model (legacy `qwen-vl-max` still works but is not recommended for new work). Pricing (list price, Beijing region, may vary/discount): `qwen3.7-plus` ¥2/1M input tokens + ¥8/1M output tokens; legacy `qwen-vl-max` ¥1.6/1M in + ¥4/1M out; `qwen3-vl-plus` ¥1/1M in + ¥10/1M out. Vision billing is token-based (image pixels → tokens), not a flat per-image price.
  - Updated `backend/.env`: `LLM_VISION_MODEL=qwen3.7-plus`. `LLM_VISION_BASE_URL`/`LLM_VISION_API_KEY` env plumbing (+ now also the DB-config `role="vision"` path from the new Settings UI) both resolve generically in `llm_client.py`. **Greenlight = a config/key + a build-time decision to actually call it from a new P6 endpoint — the client layer is ready but nothing invokes vision yet.**
  - Still needed before greenlight: 1 day of real-model (text) cost logs once `MOCK_LLM=false` → report cost/chat-session AND an estimated cost/screenshot-analysis (using the confirmed per-token pricing above + a rough token-count for a typical compressed screenshot). Founder greenlights or defers P6 after seeing that number.
- **P7 Morning Brief — DEFERRED to v2** per founder (overlaps with Visibility Kit's gate-C status notifications; also needs an email-provider decision). Spec: scheduled morning brief reusing existing supervise/Resend infra, 1 email + 1 bell event/day/recipient. Do not build until founder revisits.
- **GitHub connect — production incident**: Preview-side recovery-link UI aid shipped and tested; founder will re-run the connect flow post-deploy and confirm status goes active. `/callback` auto-links orphaned installs whenever a recoverable `state` string is present (even if the DB row expired) — logged `GH_CONNECT_STATE_RECOVERED`. The only non-recoverable case (state entirely missing/forged) now logs `GH_CONNECT_ORPHANED_INSTALL` with installation_id + GitHub account login + up to 10 repo names (round 3), so a real incident is greppable.

## P1 — known gaps / non-urgent housekeeping (explicitly deferred by founder, not to be picked up opportunistically)
- **Pre-existing backend test failure — now quarantined, not fixed** (`tests/test_ora_chat.py::TestSystemPromptLayering::test_default_house_rules_content_matches_spec`). 2026-08-28 (guardrail remediation C4): tagged `@pytest.mark.known_fail_audit_2026_08` and excluded from the default `pytest` run via `pytest.ini` addopts — so it can never again be silently confused with a NEW regression. Still runs on demand via `pytest -m known_fail_audit_2026_08`; still red, still not fixed (founder ruling: backlog, not fix-now).
- **Lint cleanup pass** for the 37 pre-existing ruff/oxlint errors — captured verbatim in `/app/lint-baseline.txt` (2026-08-28, guardrail remediation C4) so future CI lint gates (Wave 4/5) diff against this baseline and only fail on genuinely NEW violations. Founder explicitly said add-to-backlog, not fix-now.
- `backend/.env` has a harmless-but-confusing duplicate `LLM_MODEL` key (line 14 legacy value now unused, superseded by `DEEPSEEK_COUNCIL_MODEL`; ORA v2's own `LLM_MODEL=qwen3.8-27b` is the live one). Left alone per the "never delete .env keys" rule.
- `routers/ora_chat.py` is ~1130 lines (down from 1777 after removing legacy pipeline) — testing_agent suggested splitting into `ora_chat_v2_router.py` once P6-P9 land, to bring it back under ~700 lines. Not blocking.
- Promptfoo `qa/simulated-user/promptfooconfig.yaml` — the `1f deploy intent` case re-tag (from the P1-P7 "Run all tasks" round) was deprioritized in favor of live P6 proof and never circled back to. Still outstanding.

## P2 — future / nice-to-have
- `ora_chat_v2` catalog currently has a small, deliberately-scoped action set (create/park backlog item, toggle a single whitelisted flag, trigger digest, set funnel SLO, set whitelisted env). Expanding the catalog is a P2+ decision for the founder to make explicitly per-action (each new action needs its own risk-tier + idempotency + audit wiring).

## FUTURE LEDGER (v2 / PARKED)

**Standing rules (R1-R7):**
- R1 — Entries here are PARKED, not work items. No agent may build an F-ID without an explicit founder go-ahead to promote it off this ledger. (no-block: a parked item never blocks or pauses an active round.)
- R2 — Any NEW parked item discovered during any session gets a new F-ID appended here (all 6 fields: ID / Name / What / Why parked / Trigger / Effort) before that session's loop ends. (dev-duties: this is every agent's own responsibility, not something to ask the founder to do.)
- R3 — No renaming, removing, or silently rewording an existing F-ID's fields without a founder ruling — append a dated note instead.
- R4 — "Trigger" is the exact condition that promotes the item off PARKED — not a date, a fact (e.g. "founder review", "Wave 2 stable 2 weeks").
- R5 — This section is founder-owned backlog, not a roadmap commitment — presence here is not a promise of when/if it ships.
- R6 — Reactivation of a parked item requires a fresh spec/message from the founder at that time, not a resurrection of old assumptions — context and constraints may have changed since parking.
- R7 — Founder does a monthly review pass of this whole ledger (park/promote/drop); agents don't self-initiate that review.

**F1-F18** (F1-F15 re-forwarded verbatim by founder 2026-08-29 after the seed text was confirmed absent from disk on 2026-08-28 — used exactly as given, not reworded. F16/F17 were seeded 2026-08-28. F18 added this round.)

| ID | Name | What | Why parked | Trigger | Effort |
|---|---|---|---|---|---|
| F1 | Multi-provider LLM failover | v2 | auto-failover can mask a broken active config (no-silent-failure violation); no value with 1 provider | trigger: a 2nd LLM provider actually in active use | — |
| F2 | Cloudflare Workers AI fallback | v2 | redundancy deferred until DashScope proven | trigger: before prod launch, after ~30 days stable DashScope | — |
| F3 | Morning Brief (P7: daily email + bell) | v2 | needs email provider; overlaps kit notifications | trigger: email provider chosen + kit Gate C done | — |
| F4 | AI Share-of-Voice monitoring (paid) | v2 | highest-value paid add-on | trigger: after Kit Phase B GA + pricing validated | — |
| F5 | Kit Phase B (apply engine) | next | C7 interlock (after guardrail Waves 1+4) | trigger: founder message "kit green, start B" | — |
| F6 | Kit Phase C (Readiness score UX) | next | depends on Phase B | trigger: after F5 gate | — |
| F7 | Admin Kit & SEO Dashboard | next | kit data is file-only today, no admin surface | trigger: with F5 build OR after A7 day-14 — founder's call | — |
| F8 | Bloat audit (user-facing) | v2 | brand-IP caution (Ponytail-inspired only); needs ladder baseline | trigger: after ladder 2-week baseline report | — |
| F9 | Delete-chat undo (30s undo toast) | v2 | needs backend tombstone/soft-delete infra | trigger: when chat volume justifies (founder's call) | — |
| F10 | Ladder Items 2+3 (prompt ladder + preflight gate) | next | needs 2-week lines/ship + cost baseline | trigger: founder message "ladder green, build 2+3" after the baseline report | — |
| F11 | Personal Track official launch | v2 | founder ruling: future moat, hidden now, code intact | trigger: founder's explicit GO | — |
| F12 | ChatPanel engine migration (user chat on Qwen v2) | v2 | 6k-line engine migration = future project | trigger: after UX wave + P1 wave stable 2 weeks | — |
| F13 | Kit v2 (Article/BlogPosting schema, hreflang, CWV code fixes, internal-link/orphan, topical clusters, multilingual llms.txt) | v2 | out of v1 scope | trigger: Kit GA + demand | — |
| F14 | Ladder prompt in ORA v2 admin copilot | v2 | internal tool, separate decision | trigger: founder's call, maybe never | — |
| F15 | Housekeeping batch (404 test baseline + 37 lint) | housekeeping | baselined, untouched | trigger: dedicated cleanup day (founder's call) | — |
| F16 | Day-1 lighter repo-connect | A lower-friction repo-connect path so a fresh signup can see product value before completing external GitHub OAuth | v2 scope; found in Session 2 — fresh signups cannot connect a repo without an external OAuth popup, no manual/paste fallback exists, confirmed the #1 quit-risk in the J1 live journey | After UX wave; founder design call | M |
| F17 | 3-ship-surface component consolidation | Fold the 3 live ship surfaces (`ShipDialog` inline, `LoopLiveFeed` ShippedRow, `ShipConfirmModal`) into ONE canonical "approve a change" component + status set | Copy is already unified ("Approve the fix" everywhere per N3), but merging 3 live, independently-tested surfaces + the new Wave-2 PR flow in one pass carries real regression risk | After Wave 2 (ship-via-PR) is stable in Preview for 2 weeks | M |
| F18 | Per-user `/ora` PIN | next | per-account lockout shipped; but the PIN is still ONE shared secret (`ORA_QUICK_PIN`); per-user needs schema + migration | trigger: dedicated auth-harden wave (founder's call) | M |
| F25 | Full pre-deploy "After fix" site preview (sandboxed execution of the changed app) | v2 | dependent on sandbox/execution layer; v1 (S1-P3, shipped this round) is the honest screenshot+affected-pages-list interim, not a fabricated fixed render | trigger: sandbox execution layer built | L |

**Note on F19-F24 (2026-08-29):** this ledger's own file only contains F1-F18 on disk at the time F25 was appended — no F19-F24 rows exist to preserve or renumber. Not fabricated/backfilled per R3; flagging the gap here instead of silently inventing entries. F25 cross-refs: unblocks P3-full, receipts full-verify, and the "fix actually works" gap this round's S1-P3 leaves open (see LOOP-STATE.md / REPORT-final-loop.md, W3-S5).

**Housekeeping index (2026-08-28):** the two standing regression-discipline items are tracked at their own source-of-truth files, not duplicated here — `backend/test-baseline.txt` (404 pre-existing failures/errors) and `lint-baseline.txt` (37 backend errors fixed 2026-08-28 overnight round, count now 0 — see CHANGELOG; 1 frontend suppressed via eslint-disable comment, not removed from source). F15 stays the index pointer regardless of current counts.

