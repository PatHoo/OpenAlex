-- ============================================================
-- 规则测试种子：19 项（来自 Excel「规则测试」sheet，全部 passed）
-- detail 保留原始 JSON；executed_at 使用导入时默认值（原表无时间戳）。
-- 可重复执行：按 (test_name, rule_version) 冲突时更新。
-- ============================================================

BEGIN;

INSERT INTO rule_test (test_name, rule_version, status, detail) VALUES
    ('__main__.EngineTests.test_actual_annual_support_and_provenance', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_actual_annual_support_and_provenance","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_c1', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_c1","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_c2_suppresses_c1', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_c2_suppresses_c1","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_c3_multiple_and_reappearance', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_c3_multiple_and_reappearance","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_c3_support_only_added_country', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_c3_support_only_added_country","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_c4_all_years_support_termination_and_no_reappearance_bridge', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_c4_all_years_support_termination_and_no_reappearance_bridge","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_c4_first_next_year_termination', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_c4_first_next_year_termination","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_c4_late_gap_keeps_confirmed_length_but_pending_end', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_c4_late_gap_keeps_confirmed_length_but_pending_end","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_c4_three_states_and_no_duplicate', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_c4_three_states_and_no_duplicate","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_c4_tie_or_unknown_next_primary_is_pending', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_c4_tie_or_unknown_next_primary_is_pending","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_c5_no_migration_and_dedupe', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_c5_no_migration_and_dedupe","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_c5_requires_original_provenance_on_both_rows', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_c5_requires_original_provenance_on_both_rows","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_country_only_interrupts_institution_comparison', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_country_only_interrupts_institution_comparison","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_eight_classes', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_eight_classes","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_gap_breaks_continuity_c4_pending', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_gap_breaks_continuity_c4_pending","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_no_valid_work_fallback_for_new_primary', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_no_valid_work_fallback_for_new_primary","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_threshold_boundary_and_low_volume_third_year', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_threshold_boundary_and_low_volume_third_year","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_tied_nonnull_primary_not_transition', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_tied_nonnull_primary_not_transition","status":"passed","source_sheet":"规则测试"}'::jsonb),
    ('__main__.EngineTests.test_tied_observed_year_not_skipped', 'A_base_060', 'passed', '{"test":"__main__.EngineTests.test_tied_observed_year_not_skipped","status":"passed","source_sheet":"规则测试"}'::jsonb)
ON CONFLICT (test_name, rule_version) DO UPDATE SET
    status = EXCLUDED.status,
    detail = EXCLUDED.detail;

COMMIT;
