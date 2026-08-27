# P3c — Rollout Prep ("explain_plain_english_v1" widening), 2026-08-27

**PREPARED ONLY. Not executed. Founder owns the widening (10% → 50% → 100%).**

## Widening preconditions checklist (current status)

- [x] **P2 linter + Promptfoo + canary live** — CI-wired, 18/19 Promptfoo
      passing (1 pre-existing/unrelated, tagged), leak-alert cron running.
- [x] **P1 egress net live + all source_of_truth regressions green** —
      `output_guard.py` wired into both chat_send/chat_stream; ran
      `pytest -m source_of_truth`: **12 passed, 1 skipped** (skip is
      unrelated to this feature), 0 failed.
- [x] **A non-allowlisted account confirms flag-OFF = byte-identical** —
      already covered by an existing automated regression:
      `backend/tests/test_iter2026_08_27_plain_english_contract.py::
      test_t3_flag_off_non_allowlisted_user_byte_identical` (passing).
      This is unit-level proof (mocked feature-flag check), not a live
      curl against a second real account — recommend one live spot-check
      before 100% if you want belt-and-suspenders, but not blocking.
- [ ] **P0a ship-E2E green** — Still **BLOCKED** (re-verified live 2026-08-27
      16:11 UTC): `get_repo_token_or_error()` on the drill repo project
      (`p_6d0be78cdd`, `polarisbuiltinc-wq/aurem-rollback-testbed`) returns
      `app_installation_missing`. The GitHub App install has not landed yet.
- [ ] **Founder has reviewed on their own account (test_admin_001)** —
      founder has reviewed the P0/P1/P2 *reports* and accepted them;
      no explicit confirmation yet of hands-on review of the live
      explain-branch UI (chip + shortened answers) on their own account.
      Recommend the founder do one live "how do the agents work" chat
      on test_admin_001 before widening.
- [x] **No leak-alert in the last 24h (the canary is quiet)** — re-checked
      live 2026-08-27 16:11 UTC via the exact query `leak_alert_cron.py`
      runs: `count_leak_stripped_last_24h()=0`,
      `count_internal_faults_last_24h()=0`. Genuinely quiet right now.
      (Caveat: the cron process itself has only been running ~12h in this
      pod, not the full 24h — but the underlying data query is a real
      rolling 24h window regardless of process uptime, so this reading
      is trustworthy.)

**4 of 6 preconditions are now green** (canary-quiet flipped green this
pass). **2 remain outside this codebase's control** (founder action /
founder's own hands-on review) — nothing further to build here.

## Flag-removal diff (documented, NOT committed)

When the founder decides to go to 100% (remove the flag entirely, make
the plain-English contract + output guard unconditional for every
mode="A" explain turn), the following 4 call sites simplify. This is
a preview of the diff — apply only when the founder decides to widen.

### 1. `backend/routers/chat.py` — `chat_send` (~line 441-452)
```diff
-    _plain_english_active = False
-    try:
-        if not body.ora_panel and _recall_mode_send == "A":
-            from services.feature_flags import is_enabled as _pee_enabled
-            if await _pee_enabled(
-                "explain_plain_english_v1",
-                user_id=user.get("user_id"), tier=user.get("tier"),
-            ):
-                extra_sys = (extra_sys + "\n\n" + PLAIN_ENGLISH_EXPLAIN_CONTRACT).strip()
-                _plain_english_active = True
-    except Exception as _pee_exc:
-        logger.debug("plain_english_contract skipped (chat/send): %r", _pee_exc)
+    _plain_english_active = not body.ora_panel and _recall_mode_send == "A"
+    if _plain_english_active:
+        extra_sys = (extra_sys + "\n\n" + PLAIN_ENGLISH_EXPLAIN_CONTRACT).strip()
```

### 2. `backend/routers/chat.py` — `chat_stream` (~line 1403-1412)
Same shape as above, mirrored at the streaming call site.

### 3-4. Output guard gates (~line 652, ~line 3015)
No change needed — both already read `if _plain_english_active and content:`,
so they inherit the simplified variable automatically once #1/#2 land.

### Feature-flag record
- `services/feature_flags.py` — once at 100%, the `explain_plain_english_v1`
  flag document can be archived/removed from the flags collection (founder's
  call on timing — leaving it at 100% enabled with no allowlist is also a
  safe interim state that requires zero code change).

## Statement
**P3c is PREPARED. Founder executes the widening (10% → 50% → 100%)
behind the flag. This codebase will not touch the rollout.**
