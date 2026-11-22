#!/usr/bin/env python3
"""WP10：在 1,000 人年度面板上跑 C1-C5 事件引擎（独立规则版本 A_1000_v1）。

口径按 design/关系库+AGE_图模式设计.md 3.2/3.3：
- 只取 wp9 建的 observed 年（annual_panel.note LIKE 'wp9:%'），imputed 年永不入内
- C2 优先于 C1；同年若有 C2，则 C1 写 is_suppressed=true
- C4 三态：首年 pending；次年前主国仍在 secondary_countries -> confirmed，
  次年存在但已不在 -> not_sustained，无次年 -> pending
- C5 改名消解不产生事件行（institution_rename 当前 0 行 verified，无改名可判）
- 三指标：two_year_dominant / share_threshold(A=0.60) / min3works / tie_policy
  全过 -> Probable，任一不过 -> Possible；有 verified 履历 ±2 年 -> Confirmed
- signature_proxy 事件的 actual_year 必须留空（不能用署名代理年冒充真实任职年）
- 不做 D/I/S 自动定类：class_rule 只回填了 M8 一条，方案 8.5 全表未给出，
  臆造映射会污染正式分类字段

幂等：先按 rule_version 清旧证据与事件，再重建；A_base_060 小样不受影响。
运行：python wp10_event_engine.py [--dry-run]
"""
from __future__ import annotations

import argparse
import time

from dbutil import run_psql

RV = "A_1000_v1"

DRY_RUN_SQL = f"""
SELECT count(*) AS candidate_rows,
       count(*) FILTER (WHERE v.event_type IS NOT NULL) AS event_candidates,
       count(DISTINCT v.scholar_id) FILTER (WHERE v.event_type IS NOT NULL) AS scholars
FROM v_event_candidate v
JOIN annual_panel ap ON ap.scholar_id=v.scholar_id AND ap.year=v.paper_year
WHERE ap.note LIKE 'wp9:%';
"""

BUILD_SQL = f"""
BEGIN;

-- 只清本规则版本，3 人小样的 A_base_060 保持不动
DELETE FROM event_evidence WHERE rule_version='{RV}';
DELETE FROM mobility_event  WHERE rule_version='{RV}';

INSERT INTO mobility_event (scholar_id, event_type, paper_year,
    from_inst, to_inst, from_country, to_country,
    status, evidence_level, year_source, actual_year, gap_years,
    rule_version, note)
SELECT v.scholar_id, v.event_type::char(2), v.paper_year::smallint,
       v.from_inst, v.to_inst, v.from_country, v.to_country,
       'pending', 'Possible', 'signature_proxy', NULL, NULL,
       '{RV}', 'wp10: observed-year candidate'
FROM v_event_candidate v
JOIN annual_panel ap ON ap.scholar_id=v.scholar_id AND ap.year=v.paper_year
WHERE ap.note LIKE 'wp9:%'
  AND v.event_type IS NOT NULL
  AND v.from_inst IS NOT NULL
  AND v.to_inst IS NOT NULL;

-- 判据 1：两年主机构均非并列（dominant）
INSERT INTO event_evidence (event_id, criterion, passed, measured_value, threshold,
                            rule_version, note)
SELECT e.event_id, 'two_year_dominant',
       (coalesce(p.primary_is_tie,false)=false AND coalesce(p2.primary_is_tie,false)=false),
       2, 2, e.rule_version, 'both observed years have unambiguous primary institution'
FROM mobility_event e
JOIN annual_panel p  ON p.scholar_id=e.scholar_id AND p.year=e.paper_year
LEFT JOIN annual_panel p2 ON p2.scholar_id=e.scholar_id AND p2.year=e.paper_year-1
WHERE e.rule_version='{RV}';

-- 判据 2：事件年主机构署名占比 >= 0.60（A 轨阈值）
INSERT INTO event_evidence (event_id, criterion, passed, measured_value, threshold,
                            rule_version, note)
SELECT e.event_id, 'share_threshold', s.share >= 0.60, s.share, 0.60, e.rule_version,
       'share of primary institution in event year'
FROM mobility_event e
JOIN affiliation_share s ON s.scholar_id=e.scholar_id AND s.year=e.paper_year
                        AND s.institution_id=e.to_inst
WHERE e.rule_version='{RV}';

-- 判据 3：事件年有效署名 >= 3 篇
INSERT INTO event_evidence (event_id, criterion, passed, measured_value, threshold,
                            rule_version, note)
SELECT e.event_id, 'min3works', p.valid_works >= 3, p.valid_works, 3, e.rule_version,
       'valid signed works in event year'
FROM mobility_event e
JOIN annual_panel p ON p.scholar_id=e.scholar_id AND p.year=e.paper_year
WHERE e.rule_version='{RV}';

-- 判据 5：外部履历（verified 且 ±2 年内、机构两端匹配）——仅 5 条小样学者命中
INSERT INTO event_evidence (event_id, criterion, passed, measured_value, threshold,
                            rule_version, source_url, note)
SELECT DISTINCT ON (e.event_id) e.event_id, 'external_cv', true,
       abs(cf.fact_year - e.paper_year), 2, e.rule_version,
       cf.source_url, 'verified career fact within 2 years'
FROM mobility_event e
JOIN career_fact cf ON cf.scholar_id=e.scholar_id AND cf.verified=true
  AND abs(cf.fact_year-e.paper_year)<=2
  AND cf.institution_id IN (e.from_inst,e.to_inst)
WHERE e.rule_version='{RV}'
ORDER BY e.event_id, abs(cf.fact_year-e.paper_year);

-- 判据 4：并列检查（并列年不强选方向）
INSERT INTO event_evidence (event_id, criterion, passed, measured_value, threshold,
                            rule_version, note)
SELECT e.event_id, 'tie_policy', NOT coalesce(p.primary_is_tie,false),
       CASE WHEN coalesce(p.primary_is_tie,false) THEN 0 ELSE 1 END, 1, e.rule_version,
       'event year primary is not a tie'
FROM mobility_event e
JOIN annual_panel p ON p.scholar_id=e.scholar_id AND p.year=e.paper_year
WHERE e.rule_version='{RV}';

-- 无外部履历：三指标全过 -> Probable，否则 Possible
WITH pc AS (
    SELECT ee.event_id,
           count(*) FILTER (WHERE ee.passed) AS passed_n,
           count(*) AS total_n
    FROM event_evidence ee
    JOIN mobility_event e ON e.event_id=ee.event_id
    WHERE e.rule_version='{RV}'
    GROUP BY ee.event_id
)
UPDATE mobility_event e
SET evidence_level = CASE WHEN pc.passed_n = pc.total_n THEN 'Probable' ELSE 'Possible' END
FROM pc
WHERE pc.event_id=e.event_id AND e.rule_version='{RV}';

-- 有 verified 履历 ±2 年且机构两端匹配 -> Confirmed（本批 1000 人无履历，保留逻辑备用）
UPDATE mobility_event e
SET evidence_level='Confirmed',
    actual_year=cf.fact_year,
    gap_years=abs(cf.fact_year - e.paper_year),
    year_source='external_actual'
FROM career_fact cf
WHERE e.rule_version='{RV}'
  AND cf.verified=true AND cf.scholar_id=e.scholar_id
  AND abs(cf.fact_year - e.paper_year) <= 2
  AND cf.institution_id IN (e.from_inst, e.to_inst);

-- C2 抑制同年 C1
UPDATE mobility_event c1
SET is_suppressed=true, suppressed_by=c2.event_id
FROM mobility_event c2
WHERE c1.rule_version='{RV}' AND c2.rule_version='{RV}'
  AND c1.scholar_id=c2.scholar_id AND c1.paper_year=c2.paper_year
  AND c1.event_type='C1' AND c2.event_type='C2';

-- C4 三态
UPDATE mobility_event e
SET status = CASE
    WHEN EXISTS (SELECT 1 FROM annual_panel nx
                 WHERE nx.scholar_id=e.scholar_id AND nx.year=e.paper_year+1
                   AND e.from_country = ANY(nx.secondary_countries)) THEN 'confirmed'
    WHEN EXISTS (SELECT 1 FROM annual_panel nx
                 WHERE nx.scholar_id=e.scholar_id AND nx.year=e.paper_year+1)
         THEN 'not_sustained'
    ELSE 'pending' END
WHERE e.rule_version='{RV}' AND e.event_type='C4';

COMMIT;
"""

VERIFY_SQL = f"""
SELECT count(*) AS events,
       count(DISTINCT scholar_id) AS scholars,
       count(*) FILTER (WHERE is_suppressed) AS suppressed,
       count(*) FILTER (WHERE actual_year IS NOT NULL) WITH_actual_year
FROM mobility_event WHERE rule_version='{RV}';

SELECT event_type, evidence_level, count(*) AS n
FROM mobility_event WHERE rule_version='{RV}'
GROUP BY 1,2 ORDER BY 1,2;

SELECT status, count(*) AS n FROM mobility_event
WHERE rule_version='{RV}' AND event_type='C4' GROUP BY 1 ORDER BY 1;

SELECT ee.criterion, count(*) AS total,
       count(*) FILTER (WHERE ee.passed) AS passed_n
FROM event_evidence ee JOIN mobility_event e USING(event_id)
WHERE e.rule_version='{RV}'
GROUP BY 1 ORDER BY 1;

SELECT count(*) AS orphan_events
FROM mobility_event e LEFT JOIN institution i ON i.institution_id=e.to_inst
WHERE e.rule_version='{RV}' AND i.institution_id IS NULL;

-- 事件覆盖的学者年占比（观察年 -> 有事件年）
SELECT count(DISTINCT (scholar_id, year)) AS observed_scholar_years FROM annual_panel
WHERE note LIKE 'wp9:%';
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        print(f"=== DRY RUN：统计候选事件（rule_version={RV}），不写库 ===")
        run_psql(DRY_RUN_SQL, name="wp10_dry")
        return

    print(f"=== WP10 事件引擎（rule_version={RV}）===")
    t0 = time.time()
    run_psql(BUILD_SQL, name="wp10_build", timeout=900)
    print(f"事件构建用时 {time.time()-t0:.1f}s")

    print("\n=== 核验 ===")
    run_psql(VERIFY_SQL, name="wp10_verify")


if __name__ == "__main__":
    main()
