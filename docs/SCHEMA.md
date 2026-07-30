# SCHEMA — MongoDB collections actually in use (living doc)

Last updated: 2026-06-30 (Iter 358). 101 collections live (listed via
`db.list_collection_names()`). Core ones documented; the rest are
feature-local and named self-descriptively.

## Identity & billing
- `dev_users` — accounts (email, user_id, tier, github identity).
  Test-account exclusion rules: backend/services/test_accounts.py.
- `github_connections`, `oauth_codes`, `oauth_states` — GitHub OAuth.
- `login_attempts`, `revoked_tokens` — auth hardening.
- `cto_payments` — Stripe ledger (webhook-synced), `cto_token_grants`,
  `founder_offer`, `referrals`, `referral_clicks`.
- `api_keys` — sk-aurem API keys (masked in admin UI).

## Projects & tasks
- `cto_projects` — connected repos (project_id, github_owner/repo).
- `cto_tasks` — classic task pipeline (status, commit_sha, user_id).
- `project_brains`, `project_brains_v2`, `project_graphs`,
  `project_plans`, `repo_contexts`, `cto_codebase_index` — per-repo
  memory/index layers.

## Chat
- `chat_sessions` — sidebar sessions (session_id, user_id, project_id,
  turns). User-facing lists EXCLUDE `^(prod-e2e-|qa-e2e-|e2e-test-)`
  (E2E debris, Iter 356). Cleanup endpoint:
  POST /admin/qa/cleanup-e2e-sessions.
- `ora_chat_sessions`, `ora_chat_usage`, `ora_chat_house_rules`,
  `house_rules` — ORA chat engine + standing rules.
- `intent_classifications`, `mode_classifications` — router telemetry.

## Loop engine
- `loop_sessions` — one per loop run (user_id, project_id, created_at/
  updated_at epoch floats, last_event{state, data.commit_sha}). Ships =
  last_event.state=completed + commit_sha (counted by wrapped/chip).
- `loop_events`, `loop_plans`, `loop_task_specs`, `loop_outcomes`,
  `loop_errors`, `loop_failures`, `loop_locks`, `loop_run_log`,
  `loop_verification_log`, `loop_intent_stats` — loop telemetry chain.

## Quality / learning
- `ora_council_logs` — council decisions (mode, correction_applied,
  lint_blocked — feeds public stats correction rate).
- `correction_rules`, `correction_rule_settings` — Phase 1 persistent
  corrections. `parliament_log`, `quality_scores`, `ora_patterns`,
  `ora_learning_logs`, `ora_fix_learning`, `ora_review_log`,
  `ora_hallucination_log`, `ora_eval_runs`, `ora_skill_usage`.

## Security / scanning
- `vanguard_audit`, `vanguard_ci_findings`, `cto_open_findings`,
  `fixed_findings`, `fix_jobs`, `scan_fix_usage`,
  `scaffold_scan_overrides`, `aurem_cto_vault_audit_log`,
  `cto_vault_audit_log`, `audit_log`, `admin_audit`, `ora_audit`.

## Ops / health / alerts
- `topup_alerts` — the critical-alerts banner engine (integration
  alerts + Guard 8 `github_sync` rows; severity, status active/
  resolved, email_sent).
- `integration_health`, `integration_health_history`,
  `council_health_probes`, `deploy_events`, `aurem_cto_deploy_runs`,
  `frontend_errors`, `smoke_test_runs`, `smoke_test_kv`,
  `ora_canary_runs`.
- `settings`, `ui_settings`, `app_config`, `feature_flags`.

## Misc feature-local
`cto_automations`, `cto_support(_messages)`, `cto_founder_suggestions`,
`cto_maxx_usage`, `cto_notification_dismissals`, `onboarding_emails`,
`preview_sandboxes`, `scaffold_drafts`, `scaffold_generations`,
`thinking_hints(_config)`, `tool_notify_interest`, `user_seo_claims`,
`warm_start_jobs`, `issues_cache`, `analytics_persistent_cache`,
`repo_context_timings`, `ora_prompt_snapshots`, `ora_reviewer_errors`,
`ora_scan_learning`, `iter274_bg_probe`.

Planned (guards charter): `synthetic_checks` (Guard 1), reconciliation
log (Guard 7), postmortem/incident log (Guard 20).
