-- ============================================================
-- 三人小样导入后一致性核对（只读，不修改任何数据）
-- 前置：已执行 schema.sql + 三人小样_seed.sql
-- 用法：psql -U postgres -d openalex -f verify_poc.sql
-- 判定：所有 check_name 的 status 为 OK；出现 FAIL 即需回查。
-- ============================================================

\echo '=== 1. 行数核对 ==='
SELECT 'row_counts' AS check_name,
       CASE WHEN a=3 AND b=3 AND c=11 AND d=139 AND e=126 AND f=6 AND g=4 AND h=9 AND i=6
            THEN 'OK' ELSE 'FAIL' END AS status,
       jsonb_build_object(
         'source_scholar',a,'scholar',b,'institution',c,'annual_panel',d,
         'affiliation_share',e,'mobility_event',f,'career_fact',g,
         'mobility_class',h,'identity_map',i) AS actual
FROM (
  SELECT (SELECT count(*) FROM source_scholar) a,
         (SELECT count(*) FROM scholar) b,
         (SELECT count(*) FROM institution) c,
         (SELECT count(*) FROM annual_panel) d,
         (SELECT count(*) FROM affiliation_share) e,
         (SELECT count(*) FROM mobility_event) f,
         (SELECT count(*) FROM career_fact) g,
         (SELECT count(*) FROM mobility_class) h,
         (SELECT count(*) FROM identity_map) i
) x;

\echo '=== 2. 外键完整性（应为 0 行） ==='
SELECT 'orphan_scholar_src' AS check_name, count(*) AS bad_rows
FROM scholar s LEFT JOIN source_scholar ss ON ss.src_id=s.src_id
WHERE ss.src_id IS NULL;
SELECT 'orphan_panel_institution' AS check_name, count(*) AS bad_rows
FROM annual_panel ap LEFT JOIN institution i ON i.institution_id=ap.primary_institution_id
WHERE ap.primary_institution_id IS NOT NULL AND i.institution_id IS NULL;
SELECT 'orphan_event_institution' AS check_name, count(*) AS bad_rows
FROM mobility_event e LEFT JOIN institution f ON f.institution_id=e.from_inst
LEFT JOIN institution t ON t.institution_id=e.to_inst
WHERE (e.from_inst IS NOT NULL AND f.institution_id IS NULL)
   OR (e.to_inst IS NOT NULL AND t.institution_id IS NULL);
SELECT 'orphan_career_fact' AS check_name, count(*) AS bad_rows
FROM career_fact cf LEFT JOIN institution i ON i.institution_id=cf.institution_id
WHERE cf.institution_id IS NOT NULL AND i.institution_id IS NULL;

\echo '=== 3. 次级国家字典覆盖（触发器之外再验一次，应为 0 行） ==='
SELECT 'secondary_country_dictionary' AS check_name, count(*) AS bad_rows
FROM annual_panel ap
WHERE EXISTS (
  SELECT 1 FROM unnest(coalesce(ap.secondary_countries, ARRAY[]::CHAR(2)[])) AS c(code)
  WHERE NOT EXISTS (SELECT 1 FROM country WHERE country_code = c.code)
);

\echo '=== 4. 事件起止机构/国家与图投影映射一致（应为 0 行） ==='
WITH expected(event_id,from_inst,to_inst,from_country,to_country) AS (
  VALUES (1,2,3,'CA','CA'), (2,3,2,'CA','CA'), (3,8,7,'US','US'),
         (4,9,4,'GB','US'), (5,4,10,'US','US'), (6,10,11,'US','US')
)
SELECT 'event_endpoints' AS check_name, count(*) AS bad_rows
FROM expected x JOIN mobility_event e USING (event_id)
WHERE e.from_inst IS DISTINCT FROM x.from_inst
   OR e.to_inst IS DISTINCT FROM x.to_inst
   OR e.from_country IS DISTINCT FROM x.from_country::CHAR(2)
   OR e.to_country IS DISTINCT FROM x.to_country::CHAR(2);

\echo '=== 5. 履历证据字段（应为 0 行） ==='
WITH expected_fact(fact_id,source_type,institution_id) AS (
  VALUES (1,'award',7), (2,'cv',4), (3,'cv',10), (4,'cv',11)
)
SELECT 'career_fact_fields' AS check_name, count(*) AS bad_rows
FROM expected_fact x JOIN career_fact f USING (fact_id)
WHERE f.source_type IS DISTINCT FROM x.source_type
   OR f.institution_id IS DISTINCT FROM x.institution_id
   OR coalesce(f.source_url,'') !~ '^https://'
   OR coalesce(f.source_url,'') LIKE '% %';

SELECT 'corroboration_coverage' AS check_name,
       CASE WHEN COUNT(*)=4 THEN 'OK' ELSE 'FAIL' END AS status,
       COUNT(*) AS confirmed_with_matching_fact
FROM mobility_event e
WHERE e.evidence_level='Confirmed'
  AND EXISTS (
    SELECT 1 FROM career_fact cf
    WHERE cf.scholar_id=e.scholar_id AND cf.verified=true
      AND cf.institution_id=e.to_inst
      AND abs(cf.fact_year - e.paper_year)<=2
  );

\echo '=== 6. 阈值边界演示（不写库；按 share 升序预期 passed = f,t,f,t,f,t,t） ==='
WITH sample(share,threshold,expected_passed) AS (
  VALUES (0.59::NUMERIC,0.60::NUMERIC,false),
         (0.60::NUMERIC,0.60::NUMERIC,true),
         (0.70::NUMERIC,0.60::NUMERIC,true),
         (0.49::NUMERIC,0.50::NUMERIC,false),
         (0.50::NUMERIC,0.50::NUMERIC,true),
         (0.69::NUMERIC,0.70::NUMERIC,false),
         (0.70::NUMERIC,0.70::NUMERIC,true)
)
SELECT 'threshold_boundary' AS check_name,
       CASE WHEN bool_and((share>=threshold)=expected_passed) THEN 'OK' ELSE 'FAIL' END AS status,
       string_agg((share>=threshold)::text,',' ORDER BY share,threshold) AS passed_sequence
FROM sample;

\echo '=== 7. 占比种子边界说明（信息项，不是 FAIL） ==='
SELECT 'share_seed_scope' AS check_name, 'INFO' AS status,
       count(*) AS rows, min(share) AS min_share, max(share) AS max_share,
       '小样 share 恒为 1.0，仅能演示口径；阈值逻辑用第 6 节 VALUES 用例验证' AS note
FROM affiliation_share;

\echo '=== 8. API 请求日志核对（两份种子都导入后预期 OK） ==='
SELECT 'api_request_baseline' AS check_name,
       CASE WHEN count(*) FILTER (WHERE request_id BETWEEN 1 AND 64) = 64
                 AND count(*) FILTER (WHERE request_id BETWEEN 1 AND 64
                                      AND response_sha256 ~ '^[0-9a-f]{64}$') = 64
                 AND count(DISTINCT payload->>'tag') FILTER (WHERE request_id BETWEEN 1 AND 64) = 64
                 AND min(requested_at) FILTER (WHERE request_id BETWEEN 1 AND 64) IS NOT NULL
            THEN 'OK' ELSE 'FAIL' END AS status,
       count(*) FILTER (WHERE request_id BETWEEN 1 AND 64) AS rows,
       count(DISTINCT payload->>'tag') FILTER (WHERE request_id BETWEEN 1 AND 64) AS distinct_tags,
       min(requested_at) FILTER (WHERE request_id BETWEEN 1 AND 64) AS first_request,
       max(requested_at) FILTER (WHERE request_id BETWEEN 1 AND 64) AS last_request
FROM api_request;

SELECT 'api_request_crossref' AS check_name,
       CASE WHEN count(*) FILTER (WHERE request_id BETWEEN 65 AND 220) = 156
                 AND count(*) FILTER (WHERE request_id BETWEEN 65 AND 220
                                      AND backend_path IS NOT NULL
                                      AND request_url LIKE 'https://api.crossref.org/works/%') = 156
                 AND count(*) FILTER (WHERE request_id BETWEEN 65 AND 220
                                      AND payload->>'doi' IS NOT NULL) = 156
            THEN 'OK' ELSE 'FAIL' END AS status,
       count(*) FILTER (WHERE request_id BETWEEN 65 AND 220) AS rows,
       count(*) FILTER (WHERE request_id BETWEEN 65 AND 220 AND http_status = 429) AS http_429,
       count(*) FILTER (WHERE request_id BETWEEN 65 AND 220 AND payload->>'recovery_status' = 'http_error') AS http_errors
FROM api_request;

\echo '=== 8b. 事件证据核对（导入 event_evidence_seed.sql 后预期 OK） ==='
SELECT 'event_evidence_seed' AS check_name,
       CASE WHEN count(*) = 30
                 AND count(DISTINCT event_id) = 6
                 AND count(*) FILTER (WHERE criterion = 'external_cv' AND passed = true) = 4
                 AND count(*) FILTER (WHERE criterion = 'external_cv' AND passed = false) = 2
                 AND count(*) FILTER (WHERE source_url IS NOT NULL) = 4
                 AND count(*) FILTER (WHERE source_sha256 ~ '^[0-9a-f]{64}$') = 1
            THEN 'OK' ELSE 'FAIL' END AS status,
       count(*) AS rows,
       count(DISTINCT event_id) AS events,
       count(*) FILTER (WHERE criterion = 'external_cv' AND passed = true) AS cv_passed,
       count(*) FILTER (WHERE source_sha256 IS NOT NULL) AS with_sha256
FROM event_evidence;

\echo '=== 8c. 规则测试核对（导入 rule_test_seed.sql 后预期 OK） ==='
SELECT 'rule_test_seed' AS check_name,
       CASE WHEN count(*) = 19
                 AND count(*) FILTER (WHERE status = 'passed') = 19
                 AND count(*) FILTER (WHERE detail->>'test' IS NOT NULL) = 19
            THEN 'OK' ELSE 'FAIL' END AS status,
       count(*) AS rows,
       count(*) FILTER (WHERE status = 'passed') AS passed,
       count(DISTINCT test_name) AS distinct_tests
FROM rule_test;

\echo '=== 9. A 轨不可变触发器（手动执行，预期报错，不要放在自动脚本里） ==='
-- UPDATE mobility_class SET formal_class='M8'
-- WHERE scholar_id=1 AND rule_version='A_base_060';
-- 预期：ERROR  baseline rule_version A_base_060 is immutable; use B track instead

\echo '=== 10. 图查询请单独执行 三人小样_AGE投影.sql 的 V1-V5 ==='
-- V1=3, V2=4 行 Newman 轨迹, V3=6 行事件, V4=Newman 1992 GB->US, V5=6
-- ============================================================
-- END
-- ============================================================
