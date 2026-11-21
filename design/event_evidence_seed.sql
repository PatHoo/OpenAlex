-- ============================================================
-- 事件证据种子：6 个有效迁移事件 × 5 项判据 = 30 行
-- 来源：A事件审计 / B逐事件阈值对照 / B时间差诊断（经 Excel 提取）
-- 判据：two_year_dominant / share_threshold / min3works / tie_policy / external_cv
-- measured_value 仅在原表可计算时填写：support_work_ids 数量、validation_years 数量、并列数。
-- 占比原表无逐事件份额值，share_threshold 的 measured_value 留空；
--   B50/B70 敏感性结果记入 note，不伪造 B 轨事件。
-- 外部履历 URL 从 external_evidence 提取；仅 Perdew 在 api_request 有 HTTP 留痕，其余留空。
-- 可重复执行：按 evidence_id 1..30 冲突时更新，并修正序列。
-- ============================================================

BEGIN;

INSERT INTO event_evidence (
    evidence_id, event_id, criterion, passed, measured_value, threshold,
    rule_version, source_url, source_sha256, http_status, observed_at, note
) VALUES
    (1, 1, 'two_year_dominant', true, 2, 2, 'A_base_060', NULL, NULL, NULL, NULL, 'validation_years=[1986, 1987]; 连续日历年主状态、份额及不同作品支持满足自动标准'),
    (2, 1, 'share_threshold', true, NULL, 0.6, 'A_base_060', NULL, NULL, NULL, NULL, 'A_auto=Probable（A=0.60）；原表无逐事件份额值，measured_value 留空。; B50: A_auto=Probable B_auto=Probable A_final=Probable B_final=Probable changed=0; B70: A_auto=Probable B_auto=Possible A_final=Probable B_final=Possible changed=1'),
    (3, 1, 'min3works', true, 5, 3, 'A_base_060', NULL, NULL, NULL, NULL, 'support_work_ids 共 5 篇不同作品；连续日历年主状态、份额及不同作品支持满足自动标准'),
    (4, 1, 'tie_policy', true, 0, 0, 'A_base_060', NULL, NULL, NULL, NULL, '事件年与前一年 primary_is_tie=false、tie_count=0（由关系库 annual_panel 核对）；name_observation_status=空'),
    (5, 1, 'external_cv', false, NULL, NULL, 'A_base_060', NULL, NULL, NULL, NULL, 'external_status=searched_not_found; careers/evidence.json;公开ORCID空或官方路径失败，未获得能确认本次转换的履历；保留自动等级'),
    (6, 2, 'two_year_dominant', true, 2, 2, 'A_base_060', NULL, NULL, NULL, NULL, 'validation_years=[2007, 2008]; 连续日历年主状态、份额及不同作品支持满足自动标准'),
    (7, 2, 'share_threshold', true, NULL, 0.6, 'A_base_060', NULL, NULL, NULL, NULL, 'A_auto=Probable（A=0.60）；原表无逐事件份额值，measured_value 留空。; B50: A_auto=Probable B_auto=Probable A_final=Probable B_final=Probable changed=0; B70: A_auto=Probable B_auto=Probable A_final=Probable B_final=Probable changed=0'),
    (8, 2, 'min3works', true, 4, 3, 'A_base_060', NULL, NULL, NULL, NULL, 'support_work_ids 共 4 篇不同作品；连续日历年主状态、份额及不同作品支持满足自动标准'),
    (9, 2, 'tie_policy', true, 0, 0, 'A_base_060', NULL, NULL, NULL, NULL, '事件年与前一年 primary_is_tie=false、tie_count=0（由关系库 annual_panel 核对）；name_observation_status=空'),
    (10, 2, 'external_cv', false, NULL, NULL, 'A_base_060', NULL, NULL, NULL, NULL, 'external_status=searched_not_found; careers/evidence.json;公开ORCID空或官方路径失败，未获得能确认本次转换的履历；保留自动等级'),
    (11, 3, 'two_year_dominant', true, 2, 2, 'A_base_060', NULL, NULL, NULL, NULL, 'validation_years=[2024, 2025]; 连续日历年主状态、份额及不同作品支持满足自动标准；正式传记证实2023返回Tulane，年差1年'),
    (12, 3, 'share_threshold', true, NULL, 0.6, 'A_base_060', NULL, NULL, NULL, NULL, 'A_auto=Probable（A=0.60）；原表无逐事件份额值，measured_value 留空。; B50: A_auto=Probable B_auto=Probable A_final=Confirmed B_final=Confirmed changed=0; B70: A_auto=Probable B_auto=Probable A_final=Confirmed B_final=Confirmed changed=0'),
    (13, 3, 'min3works', true, 17, 3, 'A_base_060', NULL, NULL, NULL, NULL, 'support_work_ids 共 17 篇不同作品；连续日历年主状态、份额及不同作品支持满足自动标准；正式传记证实2023返回Tulane，年差1年'),
    (14, 3, 'tie_policy', true, 0, 0, 'A_base_060', NULL, NULL, NULL, NULL, '事件年与前一年 primary_is_tie=false、tie_count=0（由关系库 annual_panel 核对）；name_observation_status=空'),
    (15, 3, 'external_cv', true, NULL, NULL, 'A_base_060', 'https://fi.edu/en/awards/laureates/john-p-perdew', 'a083e0e196d745d290dc7659b3fbb6485614b6c34d5e7ff6151e6182d69ec019', 200, '2026-11-01T02:15:00.754996+00:00', 'external_status=matched_formal_biography; https://fi.edu/en/awards/laureates/john-p-perdew 正式获奖传记：Tulane returned in 2023; Temple 2013–2023'),
    (16, 4, 'two_year_dominant', true, 2, 2, 'A_base_060', NULL, NULL, NULL, NULL, 'validation_years=[1992, 1993]; 连续日历年主状态、份额及不同作品支持满足自动标准；官方CV确认任职转换，采用履历年'),
    (17, 4, 'share_threshold', true, NULL, 0.6, 'A_base_060', NULL, NULL, NULL, NULL, 'A_auto=Probable（A=0.60）；原表无逐事件份额值，measured_value 留空。; B50: A_auto=Probable B_auto=Probable A_final=Confirmed B_final=Confirmed changed=0; B70: A_auto=Probable B_auto=Probable A_final=Confirmed B_final=Confirmed changed=0'),
    (18, 4, 'min3works', true, 21, 3, 'A_base_060', NULL, NULL, NULL, NULL, 'support_work_ids 共 21 篇不同作品；连续日历年主状态、份额及不同作品支持满足自动标准；官方CV确认任职转换，采用履历年'),
    (19, 4, 'tie_policy', true, 0, 0, 'A_base_060', NULL, NULL, NULL, NULL, '事件年与前一年 primary_is_tie=false、tie_count=0（由关系库 annual_panel 核对）；name_observation_status=空'),
    (20, 4, 'external_cv', true, NULL, NULL, 'A_base_060', 'https://www-personal.umich.edu/~mejn/Newman_CV.pdf', NULL, NULL, NULL, 'external_status=matched_CV; https://www-personal.umich.edu/~mejn/Newman_CV.pdf 第1页 Employment；同方向转换且年差1年'),
    (21, 5, 'two_year_dominant', true, 2, 2, 'A_base_060', NULL, NULL, NULL, NULL, 'validation_years=[1997, 1998]; 连续日历年主状态、份额及不同作品支持满足自动标准；官方CV确认任职转换，采用履历年'),
    (22, 5, 'share_threshold', true, NULL, 0.6, 'A_base_060', NULL, NULL, NULL, NULL, 'A_auto=Probable（A=0.60）；原表无逐事件份额值，measured_value 留空。; B50: A_auto=Probable B_auto=Probable A_final=Confirmed B_final=Confirmed changed=0; B70: A_auto=Probable B_auto=Probable A_final=Confirmed B_final=Confirmed changed=0'),
    (23, 5, 'min3works', true, 14, 3, 'A_base_060', NULL, NULL, NULL, NULL, 'support_work_ids 共 14 篇不同作品；连续日历年主状态、份额及不同作品支持满足自动标准；官方CV确认任职转换，采用履历年'),
    (24, 5, 'tie_policy', true, 0, 0, 'A_base_060', NULL, NULL, NULL, NULL, '事件年与前一年 primary_is_tie=false、tie_count=0（由关系库 annual_panel 核对）；name_observation_status=空'),
    (25, 5, 'external_cv', true, NULL, NULL, 'A_base_060', 'https://www-personal.umich.edu/~mejn/Newman_CV.pdf', NULL, NULL, NULL, 'external_status=matched_CV; https://www-personal.umich.edu/~mejn/Newman_CV.pdf 第1页 Employment；同方向转换且年差1年'),
    (26, 6, 'two_year_dominant', true, 2, 2, 'A_base_060', NULL, NULL, NULL, NULL, 'validation_years=[2003, 2004]; 连续日历年主状态、份额及不同作品支持满足自动标准；官方CV确认任职转换，采用履历年'),
    (27, 6, 'share_threshold', true, NULL, 0.6, 'A_base_060', NULL, NULL, NULL, NULL, 'A_auto=Probable（A=0.60）；原表无逐事件份额值，measured_value 留空。; B50: A_auto=Probable B_auto=Probable A_final=Confirmed B_final=Confirmed changed=0; B70: A_auto=Probable B_auto=Probable A_final=Confirmed B_final=Confirmed changed=0'),
    (28, 6, 'min3works', true, 25, 3, 'A_base_060', NULL, NULL, NULL, NULL, 'support_work_ids 共 25 篇不同作品；连续日历年主状态、份额及不同作品支持满足自动标准；官方CV确认任职转换，采用履历年'),
    (29, 6, 'tie_policy', true, 0, 0, 'A_base_060', NULL, NULL, NULL, NULL, '事件年与前一年 primary_is_tie=false、tie_count=0（由关系库 annual_panel 核对）；name_observation_status=空'),
    (30, 6, 'external_cv', true, NULL, NULL, 'A_base_060', 'https://www-personal.umich.edu/~mejn/Newman_CV.pdf', NULL, NULL, NULL, 'external_status=matched_CV; https://www-personal.umich.edu/~mejn/Newman_CV.pdf 第1页 Employment；同方向转换且年差1年')
ON CONFLICT (evidence_id) DO UPDATE SET
    event_id        = EXCLUDED.event_id,
    criterion       = EXCLUDED.criterion,
    passed          = EXCLUDED.passed,
    measured_value  = EXCLUDED.measured_value,
    threshold       = EXCLUDED.threshold,
    rule_version    = EXCLUDED.rule_version,
    source_url      = EXCLUDED.source_url,
    source_sha256   = EXCLUDED.source_sha256,
    http_status     = EXCLUDED.http_status,
    observed_at     = EXCLUDED.observed_at,
    note            = EXCLUDED.note;

SELECT setval(pg_get_serial_sequence('event_evidence', 'evidence_id'),
               (SELECT max(evidence_id) FROM event_evidence), true);

COMMIT;
