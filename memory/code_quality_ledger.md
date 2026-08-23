# Code Quality Master Tracking Ledger

Generated 2026-08-22 (Phase 0/1 baseline). One row per bloated file (173, tool count) + one row per complex function (454, tool count — founder's earlier estimate of 451/173 was close; exact live tool output used here, discrepancy noted honestly, not massaged to match).

**Total rows: 627** (173 file-level + 454 function-level).

**Known tool gap (CONFIRMED):** `architecture_health.py`'s complexity scanner is Python-only (uses `radon`) — JSX/JS complexity is NOT measured at all. Frontend rows below only ever appear in the file-size list, never the complexity list, which understates frontend risk. Flagged, not fixed in this pass.

**Status values:** not started / in progress / covered (≥60%, no structural change yet) / refactored (structurally split, floor already met) / deliberately deferred (reason required).

## backend/services (329 rows)

| Name | Metric | Coverage | Status |
|---|---|---|---|
| backend/services/loop_engine.py | 4258 lines | 8% | in progress — 2c coverage wave |
| backend/services/orchestrator.py | 2557 lines | 40% | not started |
| backend/services/local_tools.py | 2253 lines | 12% | in progress — 2c coverage wave |
| backend/services/qa_matrix.py | 1047 lines | 25% | Phase 1: harvesters relocated in (2026-08-22) |
| backend/services/ora_chat/deep_research.py | 928 lines | 55% | not started |
| backend/services/dev_skills.py | 886 lines | 7% | not started |
| backend/services/integration_health.py | 861 lines | 9% | not started |
| backend/services/project_brain.py | 814 lines | 10% | not started |
| backend/services/mode_d_debugger.py | 694 lines | 25% | not started |
| backend/services/ora_fix_learning.py | 668 lines | 29% | not started |
| backend/services/vanguard_scanner.py | 652 lines | 53% | not started |
| backend/services/supabase_provisioner.py | 604 lines | 16% | not started |
| backend/services/vanguard_verify_agent.py | 597 lines | 19% | not started |
| backend/services/vercel_skills.py | 591 lines | 13% | not started |
| backend/services/llm/openrouter_providers.py | 566 lines | 11% | not started |
| backend/services/llm/_meta.py | 564 lines | 18% | not started |
| backend/services/bug_hunt_rules.py | 550 lines | 44% | not started |
| backend/services/health_score.py | 550 lines | 12% | not started |
| backend/services/repo_context.py | 549 lines | 12% | not started |
| backend/services/funnel_nudge_cron.py | 518 lines | 13% | not started |
| backend/services/architecture_health.py | 483 lines | 0% | not started |
| backend/services/ora_context.py | 480 lines | 48% | not started |
| backend/services/ora_chat/codebase_index.py | 479 lines | 82% | not started |
| backend/services/health_checks.py | 478 lines | 18% | not started |
| backend/services/tools_bridge.py | 475 lines | 20% | not started |
| backend/services/ora_chat/safety.py | 474 lines | 94% | not started |
| backend/services/web_skills.py | 455 lines | 12% | not started |
| backend/services/finding_fix_applier.py | 448 lines | 9% | not started |
| backend/services/loop_speed_diagnostic.py | 438 lines | 0% | not started |
| backend/services/graph_builder.py | 433 lines | 13% | not started |
| backend/services/repo_indexing.py | 429 lines | 11% | not started |
| backend/services/admin_analytics_cache.py | 424 lines | 15% | not started |
| backend/services/ora_chat/grounding_check.py | 424 lines | 31% | not started |
| backend/services/github_deploy_service.py | 417 lines | 14% | not started |
| backend/services/fix_job_manager.py | 405 lines | 45% | not started |
| backend/services/ora_chat/slash_commands.py | 388 lines | 26% | not started |
| backend/services/github_app.py | 386 lines | 21% | not started |
| backend/services/financials.py | 379 lines | 17% | Phase 1: price-matcher relocated in (2026-08-22) |
| backend/services/loop_execute.py | 374 lines | 0% | not started |
| backend/services/llm/__init__.py | 357 lines | 45% | not started |
| backend/services/mode_e_auditor.py | 345 lines | 16% | not started |
| backend/services/github_api_writer.py | 342 lines | 15% | not started |
| backend/services/onboarding_email.py | 338 lines | 20% | not started |
| backend/services/topup_alerts.py | 336 lines | 14% | not started |
| backend/services/repo_heal.py | 335 lines | 42% | not started |
| backend/services/risk_routing.py | 333 lines | 23% | not started |
| backend/services/ora_council_retriever.py | 333 lines | 18% | not started |
| backend/services/agents.py | 330 lines | 34% | not started |
| backend/services/loop_rollback.py | 329 lines | 14% | not started |
| backend/services/correction_rules.py | 324 lines | 22% | not started |
| backend/services/loop_safety.py | 322 lines | 14% | not started |
| backend/services/first50_campaign.py | 321 lines | 26% | not started |
| backend/services/fix_triage.py | 310 lines | 69% | not started |
| backend/services/generation_rules.py | 307 lines | 89% | not started |
| backend/services/billing_cron.py | 305 lines | 30% | not started |
| backend/services/payment_recovery_email.py | 304 lines | 72% | not started |
| backend/services/rate_limiter.py | 304 lines | 65% | not started |
| backend/services/ora_learning.py | 303 lines | 27% | not started |
| backend/services/reasoning_evals.py | 302 lines | 16% | not started |
| backend/services/orchestrator.py::chat_with_tools (L1453) | CC=191 | 40% | not started |
| backend/services/project_brain.py::build_brain_v2 (L570) | CC=71 | 10% | not started |
| backend/services/bug_hunt_rules.py::scan_bug_hunt (L362) | CC=65 | 44% | not started |
| backend/services/graph_builder.py::build_graph (L202) | CC=62 | 13% | not started |
| backend/services/llm/_meta.py::_call_llm_with_meta_inner (L108) | CC=58 | 18% | not started |
| backend/services/loop_engine.py::LoopEngine._do_execute (L1072) | CC=54 | 8% | in progress — 2c coverage wave |
| backend/services/tools_bridge.py::extract_tool_calls (L184) | CC=54 | 20% | not started |
| backend/services/orchestrator.py::_synthesise_max_iters_summary (L193) | CC=52 | 40% | not started |
| backend/services/funnel_nudge_cron.py::classify_users (L350) | CC=52 | 13% | not started |
| backend/services/loop_engine.py::LoopEngine._do_ship (L2727) | CC=46 | 8% | in progress — 2c coverage wave |
| backend/services/finding_fix_applier.py::apply_finding_fix (L245) | CC=43 | 9% | not started |
| backend/services/personal_track_smoke.py::run_smoke (L68) | CC=42 | 0% | not started |
| backend/services/repo_heal.py::heal_project (L155) | CC=41 | 42% | not started |
| backend/services/seo/orchestrator.py::run_seo_fixes (L86) | CC=41 | 23% | not started |
| backend/services/loop_engine.py::LoopEngine.confirm_ship (L3187) | CC=40 | 8% | in progress — 2c coverage wave |
| backend/services/local_tools.py::write_repo_file (L718) | CC=38 | 12% | in progress — 2c coverage wave |
| backend/services/qa_matrix.py::matrix_coverage_gap (L220) | CC=37 | 25% | Phase 1: harvesters relocated in (2026-08-22) |
| backend/services/dev_skills.py::detect_framework (L357) | CC=37 | 7% | not started |
| backend/services/llm/openrouter_providers.py::_call_deepseek (L361) | CC=37 | 11% | not started |
| backend/services/ora_chat/deep_research.py::orchestrate (L815) | CC=36 | 55% | not started |
| backend/services/loop_engine.py::LoopEngine._do_verify (L1874) | CC=34 | 8% | in progress — 2c coverage wave |
| backend/services/vanguard_verify_agent.py::_llm_review (L298) | CC=34 | 19% | not started |
| backend/services/loop_engine.py::LoopEngine._do_plan (L522) | CC=33 | 8% | in progress — 2c coverage wave |
| backend/services/dev_skills.py::find_usages (L77) | CC=33 | 7% | not started |
| backend/services/architecture_health.py::_scan_boundaries (L359) | CC=32 | 0% | not started |
| backend/services/loop_speed_diagnostic.py::compute_speed_report (L300) | CC=31 | 0% | not started |
| backend/services/vanguard_scanner.py::scan_text (L107) | CC=30 | 53% | not started |
| backend/services/repo_context.py::_build_blob (L287) | CC=30 | 12% | not started |
| backend/services/topup_alerts.py::upsert_alerts_from_snapshot (L97) | CC=30 | 14% | not started |
| backend/services/dev_skills.py::get_dependencies (L193) | CC=30 | 7% | not started |
| backend/services/local_tools.py::list_repo_files (L1074) | CC=29 | 12% | in progress — 2c coverage wave |
| backend/services/ora_chat/canary.py::run_canary (L182) | CC=29 | 22% | not started |
| backend/services/mode_d_debugger.py::run_debug_session (L537) | CC=28 | 25% | not started |
| backend/services/loop_engine.py::LoopEngine._heal_full_scan_findings (L2582) | CC=28 | 8% | in progress — 2c coverage wave |
| backend/services/web_skills.py::fetch_url (L151) | CC=28 | 12% | not started |
| backend/services/local_tools.py::_search_repo_via_api (L1496) | CC=28 | 12% | in progress — 2c coverage wave |
| backend/services/vanguard_verify_agent.py::verify_patch (L509) | CC=28 | 19% | not started |
| backend/services/minimal_edit.py::_apply_op (L75) | CC=27 | 15% | not started |
| backend/services/repo_indexing.py::_analyse_tree (L289) | CC=27 | 11% | not started |
| backend/services/seo/sitemap.py::extract_routes (L27) | CC=27 | 14% | not started |
| backend/services/web_skills.py::firecrawl_crawl_site (L339) | CC=26 | 12% | not started |
| backend/services/full_scan_scanners.py::scan_docker_cis (L78) | CC=26 | 16% | not started |
| backend/services/dev_skills.py::get_commit_history (L460) | CC=25 | 7% | not started |
| backend/services/dev_skills.py::list_issues (L533) | CC=25 | 7% | not started |
| backend/services/deploy_readiness.py::get_deploy_readiness (L68) | CC=25 | 19% | not started |
| backend/services/ora_chat/grounding_check.py::run_post_response_check (L386) | CC=25 | 31% | not started |
| backend/services/loop_engine.py::_generate_plan (L3920) | CC=24 | 8% | in progress — 2c coverage wave |
| backend/services/local_tools.py::read_repo_files (L618) | CC=24 | 12% | in progress — 2c coverage wave |
| backend/services/local_tools.py::semantic_search_repo (L1637) | CC=24 | 12% | in progress — 2c coverage wave |
| backend/services/local_tools.py::execute_bash (L2017) | CC=24 | 12% | in progress — 2c coverage wave |
| backend/services/reasoning_evals.py::validate_plan_shape (L57) | CC=24 | 16% | not started |
| backend/services/dev_skills.py::get_pr_comments (L611) | CC=24 | 7% | not started |
| backend/services/qa_matrix.py::run_regression_locks (L1007) | CC=23 | 25% | Phase 1: harvesters relocated in (2026-08-22) |
| backend/services/ora_chat/grounding_check.py::classify_claims (L335) | CC=23 | 31% | not started |
| backend/services/web_skills.py::web_search (L76) | CC=22 | 12% | not started |
| backend/services/web_skills.py::firecrawl_scrape (L283) | CC=22 | 12% | not started |
| backend/services/supabase_provisioner.py::_migrate_supabase_to_shared_mongo (L612) | CC=22 | 16% | not started |
| backend/services/local_tools.py::_search_snapshot_sync (L1349) | CC=22 | 12% | in progress — 2c coverage wave |
| backend/services/rollback_two_phase.py::execute_rollback_from_snapshot (L106) | CC=22 | 0% | not started |
| backend/services/project_brain.py::update_brain_after_task (L783) | CC=22 | 10% | not started |
| backend/services/loop_speed_diagnostic.py::_phase_durations_from_events (L107) | CC=22 | 0% | not started |
| backend/services/ora_chat/adversarial_review.py::run_review (L163) | CC=22 | 19% | not started |
| backend/services/loop_engine.py::_run_security_scan (L4097) | CC=21 | 8% | in progress — 2c coverage wave |
| backend/services/loop_engine.py::_run_diff_security_scan (L4205) | CC=21 | 8% | in progress — 2c coverage wave |
| backend/services/loop_engine.py::lookup_or_rehydrate (L4350) | CC=21 | 8% | in progress — 2c coverage wave |
| backend/services/web_skills.py::web_search_and_summarize (L226) | CC=21 | 12% | not started |
| backend/services/payment_reconciliation.py::run_reconciliation (L39) | CC=21 | 12% | not started |
| backend/services/repo_map.py::format_repo_map (L72) | CC=21 | 0% | not started |
| backend/services/supabase_provisioner.py::migrate_from_shared_mongo (L465) | CC=21 | 16% | not started |
| backend/services/git_identity.py::resolve_git_identity (L167) | CC=21 | 27% | not started |
| backend/services/ora_fix_learning.py::record_fix_outcome (L137) | CC=21 | 29% | not started |
| backend/services/project_brain.py::_build_context_string (L152) | CC=21 | 10% | not started |
| backend/services/ora_learning.py::extract_session_patterns (L224) | CC=21 | 27% | not started |
| backend/services/error_classifier.py::classify_error (L59) | CC=21 | 0% | not started |
| backend/services/orchestrator.py::_extract_web_sources (L50) | CC=21 | 40% | not started |
| backend/services/llm/openrouter_client.py::call_openrouter_model (L119) | CC=21 | 18% | not started |
| backend/services/rollback_manager.py::execute_rollback (L66) | CC=20 | 0% | not started |
| backend/services/financials.py::compute_financials (L279) | CC=20 | 17% | Phase 1: price-matcher relocated in (2026-08-22) |
| backend/services/db_restore.py::restore_to_scratch (L81) | CC=20 | 12% | not started |
| backend/services/risk_routing.py::score_change (L150) | CC=20 | 23% | not started |
| backend/services/project_brain.py::format_brain_for_agent (L873) | CC=20 | 10% | not started |
| backend/services/design_linter.py::lint_file_blocks (L156) | CC=20 | 0% | not started |
| backend/services/ora_chat/providers.py::stream_call (L67) | CC=20 | 17% | not started |
| backend/services/browser_self_test.py::classify_frontend_change (L73) | CC=19 | 13% | not started |
| backend/services/local_tools.py::_run_syntax_check (L83) | CC=19 | 12% | in progress — 2c coverage wave |
| backend/services/local_tools.py::search_repo (L1418) | CC=19 | 12% | in progress — 2c coverage wave |
| backend/services/vanguard_scanner.py::run_two_round_scan (L611) | CC=19 | 53% | not started |
| backend/services/loop_execute.py::_localize_change_target (L117) | CC=19 | 0% | not started |
| backend/services/ora_learning.py::maybe_log_ora_escalation (L67) | CC=19 | 27% | not started |
| backend/services/ora_chat/hallucination_classifier.py::classify_batch (L106) | CC=19 | 47% | not started |
| backend/services/ora_chat/slash_commands.py::_rule (L352) | CC=19 | 26% | not started |
| backend/services/full_scan_orchestrator.py::_normalise (L67) | CC=18 | 0% | not started |
| backend/services/full_scan_orchestrator.py::run_full_scan (L114) | CC=18 | 0% | not started |
| backend/services/health_notifier.py::_tick_once (L187) | CC=18 | 18% | not started |
| backend/services/finding_fix_applier.py::_generate_patched_content (L142) | CC=18 | 9% | not started |
| backend/services/user_deletion.py::cascade_delete_user_data (L38) | CC=18 | 0% | not started |
| backend/services/loop_safety.py::github_request_with_retry (L75) | CC=18 | 14% | not started |
| backend/services/health_score.py::score_performance (L342) | CC=18 | 12% | not started |
| backend/services/health_score.py::_ci_pass_rate_30d (L515) | CC=18 | 12% | not started |
| backend/services/orchestrator.py::_wants_execute (L1194) | CC=18 | 40% | not started |
| backend/services/file_selector.py::select_relevant_files (L79) | CC=18 | 15% | not started |
| backend/services/llm/__init__.py::call_emergent_watchdog (L320) | CC=18 | 45% | not started |
| backend/services/ora_chat/session.py::maybe_update_summary (L224) | CC=18 | 37% | not started |
| backend/services/ora_chat/deep_research.py::_fetch_github (L263) | CC=18 | 55% | not started |
| backend/services/loop_engine.py::LoopEngine._run_full_scan_pass (L2447) | CC=17 | 8% | in progress — 2c coverage wave |
| backend/services/health_coverage_scan.py::run_coverage_scan (L64) | CC=17 | 0% | not started |
| backend/services/usage.py::get_usage (L81) | CC=17 | 53% | not started |
| backend/services/fix_triage.py::_suggest_marker (L285) | CC=17 | 69% | not started |
| backend/services/browser_self_test.py::run_smoke (L172) | CC=17 | 13% | not started |
| backend/services/local_tools.py::_index_tfidf_search (L1737) | CC=17 | 12% | in progress — 2c coverage wave |
| backend/services/ora_context.py::validate_founder_pod_command (L471) | CC=17 | 48% | not started |
| backend/services/loop_full_scan.py::persist_findings_to_backlog (L57) | CC=17 | 0% | not started |
| backend/services/rollback_drill.py::run_drill (L90) | CC=17 | 0% | not started |
| backend/services/loop_rollback.py::run_rollback (L174) | CC=17 | 14% | not started |
| backend/services/dev_skills.py::get_env_vars (L284) | CC=17 | 7% | not started |
| backend/services/dev_skills.py::validate_syntax (L780) | CC=17 | 7% | not started |
| backend/services/ora_chat/slash_commands.py::_loop_stats (L222) | CC=17 | 26% | not started |
| backend/services/scaffold_security_gate.py::scan_files (L55) | CC=16 | 0% | not started |
| backend/services/mode_d_debugger.py::llm_diagnosis (L460) | CC=16 | 25% | not started |
| backend/services/loop_engine.py::LoopEngine (L402) | CC=16 | 8% | in progress — 2c coverage wave |
| backend/services/loop_engine.py::LoopEngine._persist_chat_turns (L3604) | CC=16 | 8% | in progress — 2c coverage wave |
| backend/services/loop_engine.py::LoopEngine._emit (L3652) | CC=16 | 8% | in progress — 2c coverage wave |
| backend/services/sandbox_runner.py::validate_generated_files (L151) | CC=16 | 12% | not started |
| backend/services/local_tools.py::get_commit_diff (L1783) | CC=16 | 12% | in progress — 2c coverage wave |
| backend/services/local_tools.py::save_finding (L1911) | CC=16 | 12% | in progress — 2c coverage wave |
| backend/services/architecture_health.py::_scan_imports (L238) | CC=16 | 0% | not started |
| backend/services/loop_execute.py::_generate_one_inner (L219) | CC=16 | 0% | not started |
| backend/services/qa_matrix.py::decide_scope (L467) | CC=16 | 25% | Phase 1: harvesters relocated in (2026-08-22) |
| backend/services/loop_speed_diagnostic.py::_execute_per_file_calls (L214) | CC=16 | 0% | not started |
| backend/services/agents.py::ReviewerAgent (L156) | CC=16 | 34% | not started |
| backend/services/ora_council_retriever.py::get_council_few_shot (L273) | CC=16 | 18% | not started |
| backend/services/house_rules.py::get_active_house_rules (L212) | CC=15 | 0% | not started |
| backend/services/parallel_agents.py::decompose_task (L42) | CC=15 | 0% | not started |
| backend/services/onboarding_email.py::eligible_users (L218) | CC=15 | 20% | not started |
| backend/services/first50_campaign.py::render_stage (L106) | CC=15 | 26% | not started |
| backend/services/fix_triage.py::_normalise (L253) | CC=15 | 69% | not started |
| backend/services/loop_independent_verifier.py::verify (L132) | CC=15 | 25% | not started |
| backend/services/url_fetcher.py::_is_safe_host (L64) | CC=15 | 20% | not started |
| backend/services/graph_builder.py::_llm_describe_files (L141) | CC=15 | 13% | not started |
| backend/services/supabase_sweeper.py::_process_one (L48) | CC=15 | 42% | not started |
| backend/services/ora_fix_learning.py::_finding_category (L102) | CC=15 | 29% | not started |
| backend/services/vanguard_verify_agent.py::_e2b_smoke (L471) | CC=15 | 19% | not started |
| backend/services/fix_job_manager.py::update_result_verified (L205) | CC=15 | 45% | not started |
| backend/services/fix_job_manager.py::close (L252) | CC=15 | 45% | not started |
| backend/services/fix_job_manager.py::subscribe (L301) | CC=15 | 45% | not started |
| backend/services/citation_guard.py::_read_paths_this_turn (L65) | CC=15 | 20% | not started |
| backend/services/citation_guard.py::CitationGuard.enforce (L124) | CC=15 | 20% | not started |
| backend/services/ora_learning.py::load_user_patterns (L294) | CC=15 | 27% | not started |
| backend/services/agents.py::ReviewerAgent.review (L165) | CC=15 | 34% | not started |
| backend/services/file_selector.py::score_file (L50) | CC=15 | 15% | not started |
| backend/services/ora_chat/deep_research.py::_fetch_one_url (L518) | CC=15 | 55% | not started |
| backend/services/house_rules.py::set_house_rules_doc (L165) | CC=14 | 0% | not started |
| backend/services/mode_classifier.py::classify_intent_v2 (L90) | CC=14 | 60% | not started |
| backend/services/restore_drill_cron.py::run_restore_drill (L52) | CC=14 | 26% | not started |
| backend/services/loop_engine.py::LoopEngine._apply_integrity_guard_to_report (L1809) | CC=14 | 8% | in progress — 2c coverage wave |
| backend/services/loop_engine.py::LoopEngine._do_scan (L2280) | CC=14 | 8% | in progress — 2c coverage wave |
| backend/services/error_translator.py::_llm_rewrite (L208) | CC=14 | 22% | not started |
| backend/services/minimal_edit.py::try_minimal_edit (L166) | CC=14 | 15% | not started |
| backend/services/correction_rules.py::match_rules (L162) | CC=14 | 22% | not started |
| backend/services/github_sync.py::_compute (L71) | CC=14 | 0% | not started |
| backend/services/task_diff.py::build_files_changed (L35) | CC=14 | 7% | not started |
| backend/services/ora_fix_learning.py::record_scan_run (L189) | CC=14 | 29% | not started |
| backend/services/topup_alerts.py::_render_email (L294) | CC=14 | 14% | not started |
| backend/services/repo_indexing.py::build_repo_index (L117) | CC=14 | 11% | not started |
| backend/services/admin_analytics_cache.py::cached_agg (L135) | CC=14 | 15% | not started |
| backend/services/bin_context.py::build_bin_context (L120) | CC=14 | 49% | not started |
| backend/services/inventory_service.py::scan_git_range (L118) | CC=14 | 26% | not started |
| backend/services/rate_limiter.py::_ensure_redis (L173) | CC=14 | 65% | not started |
| backend/services/orchestrator.py::run_post_edit_hook (L397) | CC=14 | 40% | not started |
| backend/services/funnel_nudge_cron.py::current_stage_for_user (L298) | CC=14 | 13% | not started |
| backend/services/ora_client.py::call_ora (L175) | CC=14 | 26% | not started |
| backend/services/mode_e_auditor.py::build_audit_report (L262) | CC=14 | 16% | not started |
| backend/services/mode_e_auditor.py::run_audit (L322) | CC=14 | 16% | not started |
| backend/services/llm/openrouter_providers.py::_call_longcat (L191) | CC=14 | 11% | not started |
| backend/services/ora_chat/deep_research.py::_is_safe_public_url (L387) | CC=14 | 55% | not started |
| backend/services/mode_d_debugger.py::parse_f12_payload (L237) | CC=13 | 25% | not started |
| backend/services/code_reviewer.py::_parse_file_blocks (L119) | CC=13 | 0% | not started |
| backend/services/url_fetcher.py::_fetch_one (L117) | CC=13 | 20% | not started |
| backend/services/supabase_provisioner.py::create_project (L187) | CC=13 | 16% | not started |
| backend/services/local_tools.py::read_repo_file (L541) | CC=13 | 12% | in progress — 2c coverage wave |
| backend/services/local_tools.py::_ensure_repo_snapshot (L1242) | CC=13 | 12% | in progress — 2c coverage wave |
| backend/services/finding_fix_applier.py::_fetch_file_content (L64) | CC=13 | 9% | not started |
| backend/services/ora_fix_learning.py::get_rule_stats (L224) | CC=13 | 29% | not started |
| backend/services/rollback_two_phase.py::preview_rollback (L42) | CC=13 | 0% | not started |
| backend/services/loop_verify.py::verify_files (L124) | CC=13 | 15% | not started |
| backend/services/repo_context.py::get_repo_context (L533) | CC=13 | 12% | not started |
| backend/services/loop_diff_classifier.py::classify (L62) | CC=13 | 42% | not started |
| backend/services/g22_idle_spend_guard.py::check_idle_window_spend (L49) | CC=13 | 24% | not started |
| backend/services/project_brain.py::_maybe_append_github_commits (L94) | CC=13 | 10% | not started |
| backend/services/rollback_drill.py::_resolve_write_token (L57) | CC=13 | 0% | not started |
| backend/services/health_score.py::_rollback_penalty (L473) | CC=13 | 12% | not started |
| backend/services/dev_skills.py::find_package_docs (L695) | CC=13 | 7% | not started |
| backend/services/loop_task_specs.py::freeze (L59) | CC=13 | 24% | not started |
| backend/services/mode_e_auditor.py::llm_deep_audit (L181) | CC=13 | 16% | not started |
| backend/services/ora_chat/session.py::append_message (L114) | CC=13 | 37% | not started |
| backend/services/ora_chat/deep_research.py::_gh_fetch_repo_contents (L206) | CC=13 | 55% | not started |
| backend/services/parallel_agents.py::parse_file_blocks (L182) | CC=12 | 0% | not started |
| backend/services/scaffold_design_review.py::_parse (L97) | CC=12 | 31% | not started |
| backend/services/scaffold_design_review.py::verify_scaffold (L124) | CC=12 | 31% | not started |
| backend/services/welcome_email.py::_resend_send (L231) | CC=12 | 0% | not started |
| backend/services/loop_engine.py::LoopEngine._with_budget (L887) | CC=12 | 8% | in progress — 2c coverage wave |
| backend/services/loop_ship_diff.py::compute_files_diff (L41) | CC=12 | 8% | not started |
| backend/services/full_scan_orchestrator.py::_touches_web_or_dockerfile (L236) | CC=12 | 0% | not started |
| backend/services/graph_builder.py::extract_symbols (L97) | CC=12 | 13% | not started |
| backend/services/pat_vault.py::get_repo_token (L68) | CC=12 | 58% | not started |
| backend/services/mermaid_diagram.py::_compact_summary (L166) | CC=12 | 0% | not started |
| backend/services/local_tools.py::_fetch_subtree_contents (L1018) | CC=12 | 12% | in progress — 2c coverage wave |
| backend/services/task_diff.py::build_unified_diff_hunks (L119) | CC=12 | 7% | not started |
| backend/services/ora_fix_learning.py::recall_fabrication_caution (L554) | CC=12 | 29% | not started |
| backend/services/repo_context.py::_format_tree (L199) | CC=12 | 12% | not started |
| backend/services/repo_context.py::_fetch_subtree_contents (L443) | CC=12 | 12% | not started |
| backend/services/scaffold_llm.py::_parse_llm_response (L159) | CC=12 | 0% | not started |
| backend/services/scaffold_llm.py::generate_scaffold_via_parliament (L217) | CC=12 | 0% | not started |
| backend/services/codebase_indexer.py::_format_context_block (L199) | CC=12 | 22% | not started |
| backend/services/billing_cron.py::bill_maxx_overages (L56) | CC=12 | 30% | not started |
| backend/services/billing_cron.py::grant_referral_reward (L140) | CC=12 | 30% | not started |
| backend/services/qa_matrix.py::regression_index (L91) | CC=12 | 25% | Phase 1: harvesters relocated in (2026-08-22) |
| backend/services/qa_matrix.py::verify_pass_is_real (L871) | CC=12 | 25% | Phase 1: harvesters relocated in (2026-08-22) |
| backend/services/daily_digest.py::_run_once (L142) | CC=12 | 22% | not started |
| backend/services/repo_indexing.py::_render_codebase_md (L408) | CC=12 | 11% | not started |
| backend/services/mcp_scoped_tools.py::classify_tool_groups (L156) | CC=12 | 41% | not started |
| backend/services/integration_health.py::_probe_stripe (L106) | CC=12 | 9% | not started |
| backend/services/integration_health.py::_probe_emergent_llm (L755) | CC=12 | 9% | not started |
| backend/services/project_brain.py::_gh_list_files (L472) | CC=12 | 10% | not started |
| backend/services/verification_email.py::_resend_send (L140) | CC=12 | 27% | not started |
| backend/services/loop_token_ledger.py::log_llm_usage (L94) | CC=12 | 37% | not started |
| backend/services/mode_e_auditor.py::check_quick_wins (L222) | CC=12 | 16% | not started |
| backend/services/llm/_probes.py::periodic_longcat_reprobe (L231) | CC=12 | 62% | not started |
| backend/services/ora_chat/adversarial_review.py::_parse_flags (L98) | CC=12 | 19% | not started |
| backend/services/ora_chat/codebase_index.py::bm25_relevant_files (L389) | CC=12 | 82% | not started |
| backend/services/ora_chat/codebase_index.py::system_highlights (L439) | CC=12 | 82% | not started |
| backend/services/ora_chat/deep_research.py::_robots_allows (L442) | CC=12 | 55% | not started |
| backend/services/ora_chat/canary.py::_send_message (L120) | CC=12 | 22% | not started |
| backend/services/seo/schema_markup.py::detect_page_type (L15) | CC=12 | 15% | not started |
| backend/services/seo/meta_tags.py::patch_meta_tags (L38) | CC=12 | 27% | not started |
| backend/services/aurem_managed_db.py::validate_against_schema (L86) | CC=11 | 21% | not started |
| backend/services/loop_outcomes.py::record_shipped_commit (L49) | CC=11 | 27% | not started |
| backend/services/stripe_client.py::stripe_key (L66) | CC=11 | 51% | not started |
| backend/services/github_org_client.py::push_file (L161) | CC=11 | 0% | not started |
| backend/services/payment_recovery_email.py::_resend_send (L148) | CC=11 | 72% | not started |
| backend/services/supabase_provisioner.py::translate_schema_to_sql (L380) | CC=11 | 16% | not started |
| backend/services/rollback_snapshot.py::create_snapshot (L44) | CC=11 | 0% | not started |
| backend/services/local_tools.py::_repo_ctx_from (L246) | CC=11 | 12% | in progress — 2c coverage wave |
| backend/services/supabase_sweeper.py::sweep_once (L169) | CC=11 | 42% | not started |
| backend/services/git_identity.py::build_commit_message (L121) | CC=11 | 27% | not started |
| backend/services/ora_fix_learning.py::recall_similar_fixes (L365) | CC=11 | 29% | not started |
| backend/services/ci_ingest_heartbeat.py::heartbeat_status (L39) | CC=11 | 29% | not started |
| backend/services/reasoning_evals.py::scan_finding_matches (L199) | CC=11 | 16% | not started |
| backend/services/vanguard_scanner.py::scan_file_blocks (L273) | CC=11 | 53% | not started |
| backend/services/architecture_health.py::_iter_source_files (L140) | CC=11 | 0% | not started |
| backend/services/architecture_health.py::summarise (L507) | CC=11 | 0% | not started |
| backend/services/scaffold_llm.py::_path_is_safe (L67) | CC=11 | 0% | not started |
| backend/services/topup_alerts.py::classify (L58) | CC=11 | 14% | not started |
| backend/services/loop_safety.py::validate_github_token (L37) | CC=11 | 14% | not started |
| backend/services/vercel_skills.py::_vercel_get (L83) | CC=11 | 13% | not started |
| backend/services/citation_guard.py::CitationGuard (L87) | CC=11 | 20% | not started |
| backend/services/qa_matrix.py::_frontend_coverage_summary (L156) | CC=11 | 25% | Phase 1: harvesters relocated in (2026-08-22) |
| backend/services/qa_matrix.py::canary_e2e (L348) | CC=11 | 25% | Phase 1: harvesters relocated in (2026-08-22) |
| backend/services/qa_matrix.py::_run_secret_leak_scan (L628) | CC=11 | 25% | Phase 1: harvesters relocated in (2026-08-22) |
| backend/services/boilerplate_audit.py::read_js_constant (L127) | CC=11 | 68% | not started |
| backend/services/github_app.py::get_installation_token (L193) | CC=11 | 21% | not started |
| backend/services/project_brain.py::update_brain_from_conversation (L297) | CC=11 | 10% | not started |
| backend/services/project_brain.py::_gh_read_small (L511) | CC=11 | 10% | not started |
| backend/services/mock_reality_check.py::check_openrouter (L110) | CC=11 | 0% | not started |
| backend/services/deploy_readiness.py::_remote_state (L44) | CC=11 | 19% | not started |
| backend/services/ora_chat/providers.py::one_shot (L144) | CC=11 | 17% | not started |
| backend/services/ora_chat/deep_research.py::_fetch_reddit (L642) | CC=11 | 55% | not started |
| backend/services/ora_chat/cost_tracker.py::_maybe_send_threshold_alert (L247) | CC=11 | 27% | not started |
| backend/services/seo/image_alts.py::patch_image_alts (L83) | CC=11 | 21% | not started |
| backend/services/seo/schema_markup.py::generate_schema (L32) | CC=11 | 15% | not started |

## backend/routers (182 rows)

| Name | Metric | Coverage | Status |
|---|---|---|---|
| backend/routers/chat.py | 3877 lines | 11% | in progress — 2c coverage wave |
| backend/routers/cto_projects.py | 3560 lines | 9% | in progress — 2c coverage wave |
| backend/routers/admin_analytics.py | 1992 lines | 18% | in progress — 2c coverage wave |
| backend/routers/mcp.py | 1806 lines | 23% | not started |
| backend/routers/ora_chat.py | 1589 lines | 19% | not started |
| backend/routers/loop.py | 1337 lines | 14% | not started |
| backend/routers/scaffold.py | 1270 lines | 17% | not started |
| backend/routers/codebase_health.py | 1171 lines | 10% | in progress — 2c coverage wave |
| backend/routers/admin_users.py | 1113 lines | 15% | not started |
| backend/routers/security_scan.py | 1096 lines | 15% | Phase 1: _scan_text relocated out (2026-08-22) |
| backend/routers/auth.py | 935 lines | 22% | not started |
| backend/routers/admin_ops_config.py | 904 lines | 24% | not started |
| backend/routers/admin_qa.py | 901 lines | 23% | Phase 1: harvesters relocated out (2026-08-22) |
| backend/routers/fix_pipeline.py | 834 lines | 44% | not started |
| backend/routers/admin.py | 827 lines | 12% | not started |
| backend/routers/payments.py | 800 lines | 40% | Phase 1: price-matcher relocated out (2026-08-22) |
| backend/routers/github_app.py | 754 lines | 21% | not started |
| backend/routers/deploy.py | 545 lines | 25% | not started |
| backend/routers/github_oauth.py | 538 lines | 51% | not started |
| backend/routers/admin_payments.py | 534 lines | 21% | not started |
| backend/routers/admin_bin.py | 467 lines | 18% | not started |
| backend/routers/findings.py | 446 lines | 53% | not started |
| backend/routers/admin_projects_brain.py | 446 lines | 23% | not started |
| backend/routers/supabase.py | 407 lines | 24% | not started |
| backend/routers/oauth.py | 404 lines | 28% | not started |
| backend/routers/version.py | 371 lines | 44% | Phase 1: github-push resolver relocated out (2026-08-22) |
| backend/routers/repo_status.py | 361 lines | 17% | not started |
| backend/routers/promo_first50.py | 361 lines | 36% | not started |
| backend/routers/admin_bi.py | 360 lines | 14% | not started |
| backend/routers/founder_offer.py | 355 lines | 26% | not started |
| backend/routers/admin_support.py | 309 lines | 25% | not started |
| backend/routers/advisor_context.py | 304 lines | 12% | not started |
| backend/routers/cto_projects.py::_run_task_via_api (L2418) | CC=166 | 9% | in progress — 2c coverage wave |
| backend/routers/chat.py::chat_send (L713) | CC=72 | 11% | in progress — 2c coverage wave |
| backend/routers/security_scan.py::run_security_scan (L336) | CC=71 | 15% | Phase 1: _scan_text relocated out (2026-08-22) |
| backend/routers/github_oauth.py::callback (L219) | CC=65 | 51% | not started |
| backend/routers/chat.py::chat_stream (L1302) | CC=63 | 11% | in progress — 2c coverage wave |
| backend/routers/github_app.py::install_webhook (L383) | CC=60 | 21% | not started |
| backend/routers/advisor_context.py::get_advisor_context (L74) | CC=52 | 12% | not started |
| backend/routers/cto_projects.py::_run_task_with_git (L3517) | CC=51 | 9% | in progress — 2c coverage wave |
| backend/routers/admin_analytics.py::loop_metrics (L1690) | CC=46 | 18% | in progress — 2c coverage wave |
| backend/routers/payments.py::stripe_webhook (L491) | CC=43 | 40% | Phase 1: price-matcher relocated out (2026-08-22) |
| backend/routers/admin_users.py::get_user (L226) | CC=40 | 15% | not started |
| backend/routers/admin_users.py::admin_funnel_dashboard (L622) | CC=40 | 15% | not started |
| backend/routers/codebase_health.py::scan (L653) | CC=39 | 10% | in progress — 2c coverage wave |
| backend/routers/fix_pipeline.py::_run_bulk_job (L304) | CC=38 | 44% | not started |
| backend/routers/codebase_health.py::request_fix (L996) | CC=36 | 10% | in progress — 2c coverage wave |
| backend/routers/findings.py::backlog_list (L109) | CC=35 | 53% | not started |
| backend/routers/admin.py::_compute_activation_funnel (L589) | CC=35 | 12% | not started |
| backend/routers/admin_users.py::admin_send_user_offer (L822) | CC=34 | 15% | not started |
| backend/routers/scaffold.py::materialize_draft (L580) | CC=33 | 17% | not started |
| backend/routers/repo_status.py::connection_status (L117) | CC=32 | 17% | not started |
| backend/routers/security_scan.py::_create_draft_pr (L879) | CC=32 | 15% | Phase 1: _scan_text relocated out (2026-08-22) |
| backend/routers/ora_chat.py::ora_upload (L1482) | CC=31 | 19% | not started |
| backend/routers/security_scan.py::apply_security_fix (L1076) | CC=31 | 15% | Phase 1: _scan_text relocated out (2026-08-22) |
| backend/routers/admin.py::_compute_stage_buckets (L472) | CC=31 | 12% | not started |
| backend/routers/loop.py::pause_response (L521) | CC=30 | 14% | not started |
| backend/routers/security_scan.py::_normalize_findings (L607) | CC=29 | 15% | Phase 1: _scan_text relocated out (2026-08-22) |
| backend/routers/wrapped.py::my_wrapped (L50) | CC=29 | 16% | not started |
| backend/routers/codebase_health.py::_build_text_cache (L533) | CC=28 | 10% | in progress — 2c coverage wave |
| backend/routers/mcp.py::_execute_vanguard_scan (L1010) | CC=27 | 23% | not started |
| backend/routers/security_scan.py::_gh_get (L196) | CC=27 | 15% | Phase 1: _scan_text relocated out (2026-08-22) |
| backend/routers/automations.py::github_webhook (L37) | CC=27 | 20% | not started |
| backend/routers/feature_window.py::feature_window_status (L43) | CC=26 | 15% | not started |
| backend/routers/mcp.py::_handle_one (L1508) | CC=25 | 23% | not started |
| backend/routers/admin_bi.py::_fetch_inference_metrics (L213) | CC=25 | 14% | not started |
| backend/routers/loop.py::start_loop (L88) | CC=24 | 14% | not started |
| backend/routers/chat.py::draft_support_email (L3924) | CC=23 | 11% | in progress — 2c coverage wave |
| backend/routers/admin_payments.py::admin_get_stripe_config (L229) | CC=22 | 21% | not started |
| backend/routers/vanguard_ci.py::_normalise_trufflehog (L76) | CC=22 | 17% | not started |
| backend/routers/admin_analytics.py::admin_overview_metrics (L1312) | CC=22 | 18% | in progress — 2c coverage wave |
| backend/routers/admin_payments.py::admin_set_stripe_prices (L479) | CC=21 | 21% | not started |
| backend/routers/notify_interest.py::notify_interest (L63) | CC=21 | 32% | not started |
| backend/routers/admin_ops_config.py::admin_set_github_app_config (L496) | CC=21 | 24% | not started |
| backend/routers/deploy.py::_run_deploy_ftp_or_sftp (L330) | CC=21 | 25% | not started |
| backend/routers/admin_projects_brain.py::code_surface (L280) | CC=21 | 23% | not started |
| backend/routers/cto_projects.py::search_graph (L660) | CC=20 | 9% | in progress — 2c coverage wave |
| backend/routers/cto_projects.py::add_project (L852) | CC=20 | 9% | in progress — 2c coverage wave |
| backend/routers/cto_projects.py::get_project_tree (L1349) | CC=20 | 9% | in progress — 2c coverage wave |
| backend/routers/admin_payments.py::reconcile_pending_payments (L110) | CC=20 | 21% | not started |
| backend/routers/admin_payments.py::admin_get_stripe_prices (L399) | CC=20 | 21% | not started |
| backend/routers/auth.py::login (L437) | CC=20 | 22% | not started |
| backend/routers/qa_probe.py::chat_probe (L80) | CC=20 | 30% | not started |
| backend/routers/admin_qa.py::_harvest_ci_status (L250) | CC=19 | 23% | Phase 1: harvesters relocated out (2026-08-22) |
| backend/routers/github_bot.py::push (L74) | CC=19 | 37% | not started |
| backend/routers/security_scan.py::_generate_remediation_report (L669) | CC=19 | 15% | Phase 1: _scan_text relocated out (2026-08-22) |
| backend/routers/upload.py::upload_convert (L156) | CC=19 | 26% | not started |
| backend/routers/admin_analytics.py::loop_inspect (L2032) | CC=19 | 18% | in progress — 2c coverage wave |
| backend/routers/mcp.py::_tool_get_recent_commits (L782) | CC=18 | 23% | not started |
| backend/routers/codebase_health.py::_scan_security (L82) | CC=18 | 10% | in progress — 2c coverage wave |
| backend/routers/admin_health.py::status_all (L136) | CC=18 | 24% | not started |
| backend/routers/loop.py::cancel_loop (L936) | CC=18 | 14% | not started |
| backend/routers/repo_status.py::cleanup_delete (L335) | CC=18 | 17% | not started |
| backend/routers/admin_bi.py::_subscription_monthly_usd (L60) | CC=18 | 14% | not started |
| backend/routers/admin_analytics.py::admin_system_stats (L228) | CC=18 | 18% | in progress — 2c coverage wave |
| backend/routers/codebase_health.py::_scan_dependencies (L308) | CC=17 | 10% | in progress — 2c coverage wave |
| backend/routers/admin_payments.py::admin_set_stripe_config (L333) | CC=17 | 21% | not started |
| backend/routers/admin_ops_config.py::db_health (L358) | CC=17 | 24% | not started |
| backend/routers/vanguard_ci.py::ingest_ci_findings (L119) | CC=17 | 17% | not started |
| backend/routers/vanguard_ci.py::list_ci_findings (L194) | CC=17 | 17% | not started |
| backend/routers/repo_status.py::cleanup_summary (L286) | CC=17 | 17% | not started |
| backend/routers/ora_chat.py::run_slash (L152) | CC=17 | 19% | not started |
| backend/routers/ora_chat.py::send_message (L287) | CC=17 | 19% | not started |
| backend/routers/admin_analytics.py::agent_tokens (L669) | CC=17 | 18% | in progress — 2c coverage wave |
| backend/routers/admin_analytics.py::scope_drift_audit (L2119) | CC=17 | 18% | in progress — 2c coverage wave |
| backend/routers/codebase_health.py::_scan_code_quality (L219) | CC=16 | 10% | in progress — 2c coverage wave |
| backend/routers/loop.py::rollback_loop (L1149) | CC=16 | 14% | not started |
| backend/routers/admin_ops_config.py::admin_github_app_diagnostics (L611) | CC=16 | 24% | not started |
| backend/routers/fix_pipeline.py::preview_cost (L173) | CC=16 | 44% | not started |
| backend/routers/fix_pipeline.py::restart_job (L819) | CC=16 | 44% | not started |
| backend/routers/version.py::_read_commit (L96) | CC=16 | 44% | Phase 1: github-push resolver relocated out (2026-08-22) |
| backend/routers/admin_analytics.py::learning_health (L781) | CC=16 | 18% | in progress — 2c coverage wave |
| backend/routers/cto_projects.py::test_project_pat (L1260) | CC=15 | 9% | in progress — 2c coverage wave |
| backend/routers/cto_projects.py::get_project_file (L1436) | CC=15 | 9% | in progress — 2c coverage wave |
| backend/routers/cto_projects.py::retry_task (L1972) | CC=15 | 9% | in progress — 2c coverage wave |
| backend/routers/chat.py::_f12_has_real_signal (L361) | CC=15 | 11% | in progress — 2c coverage wave |
| backend/routers/chat.py::_maybe_guard_shell_handoff_followup (L1199) | CC=15 | 11% | in progress — 2c coverage wave |
| backend/routers/admin_ops_config.py::admin_get_github_app_config (L417) | CC=15 | 24% | not started |
| backend/routers/admin_users.py::first_message_sample (L1013) | CC=15 | 15% | not started |
| backend/routers/admin_bi.py::_fetch_stripe_metrics (L95) | CC=15 | 14% | not started |
| backend/routers/user_rollback.py::revert_last_ship (L89) | CC=15 | 21% | not started |
| backend/routers/auth.py::google_session (L344) | CC=15 | 22% | not started |
| backend/routers/admin.py::_github_app_live_probe (L318) | CC=15 | 12% | not started |
| backend/routers/codebase_health.py::scanner_feedback (L1171) | CC=14 | 10% | in progress — 2c coverage wave |
| backend/routers/oauth.py::oauth_token (L347) | CC=14 | 28% | not started |
| backend/routers/suggestions.py::_analyze_with_groq (L99) | CC=14 | 28% | not started |
| backend/routers/loop.py::loop_history (L1262) | CC=14 | 14% | not started |
| backend/routers/loop.py::loop_diagnostics (L1387) | CC=14 | 14% | not started |
| backend/routers/chat.py::classify_intent (L439) | CC=14 | 11% | in progress — 2c coverage wave |
| backend/routers/admin_users.py::user_patterns_insights (L1077) | CC=14 | 15% | not started |
| backend/routers/payments.py::_match_discovered_price (L238) | CC=14 | 40% | Phase 1: price-matcher relocated out (2026-08-22) |
| backend/routers/payments.py::create_checkout (L331) | CC=14 | 40% | Phase 1: price-matcher relocated out (2026-08-22) |
| backend/routers/admin_bin.py::llm_credits (L310) | CC=14 | 18% | not started |
| backend/routers/auth.py::login_2fa_verify (L533) | CC=14 | 22% | not started |
| backend/routers/github_oauth.py::connect (L110) | CC=14 | 51% | not started |
| backend/routers/admin_analytics.py::token_pnl (L533) | CC=14 | 18% | in progress — 2c coverage wave |
| backend/routers/admin_projects_brain.py::get_architecture (L88) | CC=14 | 23% | not started |
| backend/routers/cto_projects.py::_hallucination_reasons (L2396) | CC=13 | 9% | in progress — 2c coverage wave |
| backend/routers/onboarding.py::send_connect_nudge (L46) | CC=13 | 34% | not started |
| backend/routers/chat.py::chat_task_followup (L3755) | CC=13 | 11% | in progress — 2c coverage wave |
| backend/routers/admin_users.py::list_users (L83) | CC=13 | 15% | not started |
| backend/routers/support.py::create_ticket_public (L180) | CC=13 | 39% | not started |
| backend/routers/github_app.py::install_callback (L260) | CC=13 | 21% | not started |
| backend/routers/version.py::_env_from_host (L219) | CC=13 | 44% | Phase 1: github-push resolver relocated out (2026-08-22) |
| backend/routers/mcp.py::_tool_search_repo (L921) | CC=12 | 23% | not started |
| backend/routers/mcp.py::_tool_get_project_info (L1196) | CC=12 | 23% | not started |
| backend/routers/mfa.py::disable (L178) | CC=12 | 28% | not started |
| backend/routers/admin_first50_campaign.py::dispatch (L115) | CC=12 | 25% | not started |
| backend/routers/codebase_health.py::_scan_performance (L171) | CC=12 | 10% | in progress — 2c coverage wave |
| backend/routers/codebase_health.py::_scan_database (L361) | CC=12 | 10% | in progress — 2c coverage wave |
| backend/routers/cto_projects.py::build_project_brain (L191) | CC=12 | 9% | in progress — 2c coverage wave |
| backend/routers/cto_projects.py::get_graph_tour (L619) | CC=12 | 9% | in progress — 2c coverage wave |
| backend/routers/cto_projects.py::graph_impact (L712) | CC=12 | 9% | in progress — 2c coverage wave |
| backend/routers/cto_projects.py::_classify_phase (L2173) | CC=12 | 9% | in progress — 2c coverage wave |
| backend/routers/usage.py::public_stats (L71) | CC=12 | 49% | not started |
| backend/routers/admin_qa.py::_harvest_a11y (L200) | CC=12 | 23% | Phase 1: harvesters relocated out (2026-08-22) |
| backend/routers/admin_qa.py::guard16_auth_hardening (L689) | CC=12 | 23% | Phase 1: harvesters relocated out (2026-08-22) |
| backend/routers/admin_qa.py::ci_vs_local_drift (L972) | CC=12 | 23% | Phase 1: harvesters relocated out (2026-08-22) |
| backend/routers/synthetic_checks_ci.py::ingest_synthetic_check (L37) | CC=12 | 40% | not started |
| backend/routers/chat.py::_persist_turn (L617) | CC=12 | 11% | in progress — 2c coverage wave |
| backend/routers/vanguard_ci.py::ci_ingest_status (L266) | CC=12 | 17% | not started |
| backend/routers/deploy.py::run_deploy (L451) | CC=12 | 25% | not started |
| backend/routers/version.py::_fetch_last_github_push (L279) | CC=12 | 44% | Phase 1: github-push resolver relocated out (2026-08-22) |
| backend/routers/github_funnel.py::funnel_stats (L135) | CC=12 | 48% | not started |
| backend/routers/promo_first50.py::verify_email (L128) | CC=12 | 36% | not started |
| backend/routers/codebase_health.py::_check_scan_rate_limit (L935) | CC=11 | 10% | in progress — 2c coverage wave |
| backend/routers/cto_projects.py::update_project (L1495) | CC=11 | 9% | in progress — 2c coverage wave |
| backend/routers/cto_projects.py::rollback_task (L1712) | CC=11 | 9% | in progress — 2c coverage wave |
| backend/routers/founder_offer.py::claim_offer (L170) | CC=11 | 26% | not started |
| backend/routers/engagement.py::my_streak (L149) | CC=11 | 18% | not started |
| backend/routers/admin_ops_config.py::purge_caches (L204) | CC=11 | 24% | not started |
| backend/routers/admin_users.py::admin_delete_user (L768) | CC=11 | 15% | not started |
| backend/routers/fix_pipeline.py::_compute_diff_lines (L87) | CC=11 | 44% | not started |
| backend/routers/fix_pipeline.py::_verify_commit_exists (L642) | CC=11 | 44% | not started |
| backend/routers/fix_pipeline.py::retry_verify_commit (L774) | CC=11 | 44% | not started |
| backend/routers/ora_chat.py::image_generate (L1680) | CC=11 | 19% | not started |
| backend/routers/github_app.py::_upsert_installation (L128) | CC=11 | 21% | not started |
| backend/routers/security_scan.py::_scan_text (L146) | CC=11 | 15% | Phase 1: _scan_text relocated out (2026-08-22) |
| backend/routers/scaffold.py::create_live_preview (L902) | CC=11 | 17% | not started |
| backend/routers/admin_bin.py::_fetch_openrouter_balance (L271) | CC=11 | 18% | not started |
| backend/routers/github_oauth.py::_request_origin (L31) | CC=11 | 51% | not started |
| backend/routers/admin_projects_brain.py::brain_recent_commits (L370) | CC=11 | 23% | not started |

## frontend/components (43 rows)

| Name | Metric | Coverage | Status |
|---|---|---|---|
| frontend/src/components/ChatPanel.jsx | 5445 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/MessageBubble.jsx | 1177 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/Shell.jsx | 1104 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/SecurityScanDrawer.jsx | 951 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/LoopLiveFeed.jsx | 822 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/NewUserWizard.jsx | 818 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/demo/demoSteps.jsx | 806 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/PreviewPanel.jsx | 755 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/GraphPanel.jsx | 722 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/FixProgressDrawer.jsx | 697 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/OraChatDrawer.jsx | 674 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/ORASidePanel.jsx | 664 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/DeployPanel.jsx | 645 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/nav/RailShell.jsx | 641 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/dashboard/v2/SidebarBound.jsx | 605 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/OperationHistory.jsx | 592 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/AdminHouseRules.jsx | 586 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/AddProjectWizard.jsx | 568 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/ShipConfirmModal.jsx | 542 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/LoopStatusChip.jsx | 542 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/KnowledgeGraph.jsx | 490 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/dashboard/v2/AskAdvisorReal.jsx | 476 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/OraPreviewPanel.jsx | 458 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/PricingCards.jsx | 454 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/AuremAdminPanel.jsx | 449 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/AdminThinkingHints.jsx | 449 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/RenderedMessage.jsx | 444 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/LiveTaskPopup.jsx | 444 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/LoopStepBar.jsx | 426 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/FixJobContext.jsx | 421 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/FounderOfferCard.jsx | 399 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/LiveBusinessIntelligence.jsx | 354 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/SecretScanCard.jsx | 353 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/dashboard/v2/GraphView.jsx | 352 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/NotificationBell.jsx | 341 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/TaskLiveTape.jsx | 341 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/ShipPendingCard.jsx | 321 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/demo/WalkthroughPlayer.jsx | 320 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/dashboard/v2/ChatView.jsx | 317 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/CookieConsentBanner.jsx | 309 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/TabBar.jsx | 302 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/VercelCard.jsx | 302 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/components/TwoFactorCard.jsx | 301 lines | n/a (frontend — see coverage-summary.json) | not started |

## backend/other (30 rows)

| Name | Metric | Coverage | Status |
|---|---|---|---|
| backend/main.py | 2996 lines | 49% | not started |
| backend/scripts/init_prod_collections.py | 471 lines | 16% | not started |
| backend/scripts/iter309_sse_reconnect_harness.py | 333 lines | 0% | not started |
| backend/evals/runner.py | 330 lines | 19% | not started |
| backend/evals/harness.py | 307 lines | 35% | not started |
| backend/scripts/self_scan.py::main (L81) | CC=40 | 0% | not started |
| backend/main.py::lifespan (L269) | CC=39 | 49% | not started |
| backend/scripts/iter309_sse_reconnect_harness.py::main (L171) | CC=26 | 0% | not started |
| backend/main.py::_resolve_build_hash (L2533) | CC=20 | 49% | not started |
| backend/scripts/self_scan.py::build_local_text_cache (L33) | CC=19 | 0% | not started |
| backend/scripts/g15_dependency_scan.py::_run_yarn_audit (L79) | CC=18 | 30% | not started |
| backend/evals/runner.py::run (L246) | CC=17 | 19% | not started |
| backend/scripts/migrate_iter34.py::main (L58) | CC=17 | 0% | not started |
| backend/scripts/ci_check_test_style.py::main (L109) | CC=17 | 0% | not started |
| backend/scripts/iter309_sse_reconnect_harness.py::_stream_once (L99) | CC=17 | 0% | not started |
| backend/main.py::NoSQLOpASGIGuard.__call__ (L1834) | CC=16 | 49% | not started |
| backend/evals/runner.py::_run_prompt (L102) | CC=16 | 19% | not started |
| backend/main.py::_route_cache_mw (L2042) | CC=15 | 49% | not started |
| backend/scripts/timeout_audit.py::run_audit (L139) | CC=14 | 20% | not started |
| backend/evals/harness.py::leak_scorer (L188) | CC=13 | 35% | not started |
| backend/scripts/g21_security_scan.py::scan_misconfig (L64) | CC=13 | 58% | not started |
| backend/scripts/loop_speed_report.py::main (L36) | CC=13 | 0% | not started |
| backend/scripts/timeout_audit.py::audit_python_file (L51) | CC=13 | 20% | not started |
| backend/scripts/g1_route_smoke_sweep.py::_crawl_one (L46) | CC=12 | 0% | not started |
| backend/scripts/g15_dependency_scan.py::_run_pip_audit (L42) | CC=12 | 30% | not started |
| backend/main.py::health_ora (L2801) | CC=11 | 49% | not started |
| backend/main.py::diag_memory (L3018) | CC=11 | 49% | not started |
| backend/evals/runner.py::_dispatch_scorers (L61) | CC=11 | 19% | not started |
| backend/scripts/session_start_dashboard.py::main (L75) | CC=11 | 0% | not started |
| backend/scripts/init_prod_collections.py::_ensure_indexes (L342) | CC=11 | 16% | not started |

## frontend/pages (29 rows)

| Name | Metric | Coverage | Status |
|---|---|---|---|
| frontend/src/pages/Admin.jsx | 3579 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/AdminOverview.jsx | 2138 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/Projects.jsx | 1946 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/Landing.jsx | 1563 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/OraDirect.jsx | 1461 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/Both.jsx | 1010 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/CodebaseHealth.jsx | 733 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/Dashboard.jsx | 708 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/AdminSystemHealth.jsx | 703 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/AdminQADashboard.jsx | 678 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/admin/PersonalTrackAdmin.jsx | 597 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/Settings.jsx | 551 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/Login.jsx | 542 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/Integrations.jsx | 540 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/BugHunt.jsx | 537 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/FeatureWindow.jsx | 532 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/AdminFinancials.jsx | 527 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/AdminCockpit.jsx | 492 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/SystemStatsPage.jsx | 425 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/WhyOra.jsx | 403 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/BrainDump.jsx | 386 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/Analytics.jsx | 375 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/Signup.jsx | 356 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/AdminApiKeys.jsx | 356 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/AdminInspectSpeedDiagnostic.jsx | 354 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/ShippedRowHarness.jsx | 352 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/AdminVanguard.jsx | 309 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/Deploy.jsx | 307 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/pages/personal/DraftReview.jsx | 306 lines | n/a (frontend — see coverage-summary.json) | not started |

## backend/core (8 rows)

| Name | Metric | Coverage | Status |
|---|---|---|---|
| backend/core/parliament.py | 1218 lines | 23% | not started |
| backend/core/intent_gateway.py | 461 lines | 33% | not started |
| backend/core/intent_gateway.py::_classify_heuristic (L113) | CC=20 | 33% | not started |
| backend/core/quality_monitor.py::QualityMonitor._compute_score (L59) | CC=19 | 0% | not started |
| backend/core/parliament.py::_ceo_judge_call_with_rescue (L865) | CC=15 | 23% | not started |
| backend/core/parliament.py::_llm_call_protected (L360) | CC=14 | 23% | not started |
| backend/core/tool_router.py::get_tools_for_task (L109) | CC=14 | 80% | not started |
| backend/core/parliament.py::_score_output (L82) | CC=12 | 23% | not started |

## backend/cto_services (4 rows)

| Name | Metric | Coverage | Status |
|---|---|---|---|
| backend/cto_services/codebase_indexer.py | 308 lines | 0% | not started |
| backend/cto_services/auth.py::current_dev (L18) | CC=17 | 51% | not started |
| backend/cto_services/codebase_indexer.py::_format_context_block (L203) | CC=12 | 0% | not started |
| backend/cto_services/auth.py::require_admin (L85) | CC=11 | 51% | not started |

## frontend/other (2 rows)

| Name | Metric | Coverage | Status |
|---|---|---|---|
| frontend/src/App.jsx | 498 lines | n/a (frontend — see coverage-summary.json) | not started |
| frontend/src/lib/api.js | 392 lines | n/a (frontend — see coverage-summary.json) | not started |
