#!/usr/bin/env python3
"""WP11：在 1,000 人地基上按方案 8.5 做 D/I/S 学者分类与三类次数统计。

口径来源：reference/科学家空间流动数据库建立操作1030.docx 8.5.1/8.5.2/8.6.2 原文
（见 design/class_rule_85_seed.sql 的 8 条权威映射、README「分类口径」）。

D = 存在未抑制的 C1 且 evidence_level ∈ {Confirmed, Probable}
I = 存在未抑制的 C2 且 evidence_level ∈ {Confirmed, Probable}
S = 存在任一有效观测年 annual_panel.secondary_countries 非空
    （8.5.1「年度主次国家矩阵，结合 C3/C4」；只用 wp9 观测年）
observed_class = class_rule 按 (D,I,S) 查表（8.5.2 全组合）

formal_class / is_classified 的保守定类门槛（8.5.2「缺失与不确定事件」）：
- 某阴性维度（D=0 或 I=0）若存在同维度的 Possible 未决候选，
  则不将"未识别"等同于"确定无流动"，formal_class 留 NULL、
  is_classified=false，并在 uncertainty_reason 写明是哪一维有未决候选。
- 无未决候选者 formal_class=observed_class、is_classified=true。
注意：S 用次级署名直接判定，不设 Possible 门槛（8.5.1 明确
  「S 不直接等同于履历确认的正式兼职」，只要署名有效即记 1）。

三类次数与学者类型分开统计（8.5.2「执行规则」+ 8.6.2）：
- 国内迁移次数 = 未抑制 C1 且 Confirmed/Probable 的事件行数
- 国际迁移次数 = 未抑制 C2 且 Confirmed/Probable 的事件行数
- 次级联系次数 = secondary_countries 中"某国首次出现"的去对数（每新增一国记一次）
- 持续联系次数 = C4 事件行数（不按持续年份重复计数）
次数写入独立视图 v_mobility_counts（rule_version=A_1000_v1），不改 mobility_class 表结构。

幂等：先按 rule_version 清本批分类再重建；A_base_060 三人小样不动。
运行：python wp11_classify.py [--dry-run]
"""
from __future__ import annotations

import argparse
import time

from dbutil import run_psql

RV = "A_1000_v1"

DRY_RUN_SQL = f"""
-- 预估：按 8.5 口径的 D/I/S 覆盖与组合分布（不写库）
WITH d AS (SELECT DISTINCT scholar_id FROM mobility_event
           WHERE rule_version='{RV}' AND event_type='C1'
             AND evidence_level IN ('Confirmed','Probable') AND NOT is_suppressed),
     i AS (SELECT DISTINCT scholar_id FROM mobility_event
           WHERE rule_version='{RV}' AND event_type='C2'
             AND evidence_level IN ('Confirmed','Probable') AND NOT is_suppressed),
     s AS (SELECT DISTINCT scholar_id FROM annual_panel
           WHERE note LIKE 'wp9:%' AND cardinality(secondary_countries)>0),
     f AS (SELECT sc.scholar_id,
             CASE WHEN d.scholar_id IS NOT NULL THEN 1 ELSE 0 END AS dv,
             CASE WHEN i.scholar_id IS NOT NULL THEN 1 ELSE 0 END AS iv,
             CASE WHEN s.scholar_id IS NOT NULL THEN 1 ELSE 0 END AS sv
           FROM scholar sc LEFT JOIN d USING(scholar_id)
             LEFT JOIN i USING(scholar_id) LEFT JOIN s USING(scholar_id))
SELECT dv AS D, iv AS I, sv AS S, count(*) AS n
FROM f GROUP BY 1,2,3 ORDER BY 1,2,3;
"""

BUILD_SQL = f"""
BEGIN;

DELETE FROM mobility_class WHERE rule_version='{RV}';

-- D/I：仅 Confirmed/Probable 且未抑制；S：wp9 观测年里有跨国次级署名
-- Possible 未决候选只用于 formal 确定性判断，不计入 D/I 的 1
WITH d AS (SELECT DISTINCT scholar_id FROM mobility_event
           WHERE rule_version='{RV}' AND event_type='C1'
             AND evidence_level IN ('Confirmed','Probable') AND NOT is_suppressed),
     i AS (SELECT DISTINCT scholar_id FROM mobility_event
           WHERE rule_version='{RV}' AND event_type='C2'
             AND evidence_level IN ('Confirmed','Probable') AND NOT is_suppressed),
     s AS (SELECT DISTINCT scholar_id FROM annual_panel
           WHERE note LIKE 'wp9:%' AND cardinality(secondary_countries)>0),
     dp AS (SELECT DISTINCT scholar_id FROM mobility_event
            WHERE rule_version='{RV}' AND event_type='C1'
              AND evidence_level='Possible' AND NOT is_suppressed),
     ip AS (SELECT DISTINCT scholar_id FROM mobility_event
            WHERE rule_version='{RV}' AND event_type='C2'
              AND evidence_level='Possible' AND NOT is_suppressed),
     f AS (SELECT sc.scholar_id,
             CASE WHEN d.scholar_id IS NOT NULL THEN 1 ELSE 0 END::smallint AS dv,
             CASE WHEN i.scholar_id IS NOT NULL THEN 1 ELSE 0 END::smallint AS iv,
             CASE WHEN s.scholar_id IS NOT NULL THEN 1 ELSE 0 END::smallint AS sv,
             (d.scholar_id IS NULL AND dp.scholar_id IS NOT NULL) AS d_undecided,
             (i.scholar_id IS NULL AND ip.scholar_id IS NOT NULL) AS i_undecided
           FROM scholar sc
             LEFT JOIN d USING(scholar_id) LEFT JOIN i USING(scholar_id)
             LEFT JOIN s USING(scholar_id) LEFT JOIN dp USING(scholar_id)
             LEFT JOIN ip USING(scholar_id))
INSERT INTO mobility_class
    (scholar_id, rule_version, D, I, S, observed_class,
     formal_class, is_classified, uncertainty_reason)
SELECT f.scholar_id, '{RV}', f.dv, f.iv, f.sv,
       cr.class_code,
       CASE WHEN f.d_undecided OR f.i_undecided THEN NULL ELSE cr.class_code END,
       (NOT (f.d_undecided OR f.i_undecided)),
       CASE
         WHEN f.d_undecided AND f.i_undecided
           THEN 'D、I 阴性维度存在 Possible 未决候选，不将未识别视为确定无流动'
         WHEN f.d_undecided
           THEN 'D 阴性维度存在 Possible 未决候选，不将未识别视为确定无流动'
         WHEN f.i_undecided
           THEN 'I 阴性维度存在 Possible 未决候选，不将未识别视为确定无流动'
         ELSE NULL
       END
FROM f
JOIN class_rule cr
  ON cr.d_flag=f.dv AND cr.i_flag=f.iv AND cr.s_flag=f.sv;

-- 三类次数统计：独立视图，与 mobility_class 类型字段分开（8.5.2 执行规则）
CREATE OR REPLACE VIEW v_mobility_counts AS
WITH c1 AS (SELECT scholar_id, count(*) AS n FROM mobility_event
            WHERE rule_version='{RV}' AND event_type='C1'
              AND evidence_level IN ('Confirmed','Probable') AND NOT is_suppressed
            GROUP BY 1),
     c2 AS (SELECT scholar_id, count(*) AS n FROM mobility_event
            WHERE rule_version='{RV}' AND event_type='C2'
              AND evidence_level IN ('Confirmed','Probable') AND NOT is_suppressed
            GROUP BY 1),
     -- C3「每新增一个国家记一次」：按(学者,国)在观测年内首次出现的去对数
     c3 AS (SELECT scholar_id, count(*) AS n FROM (
              SELECT DISTINCT ap.scholar_id, ctry
              FROM annual_panel ap
              CROSS JOIN LATERAL unnest(ap.secondary_countries) AS ctry
              WHERE ap.note LIKE 'wp9:%'
            ) t GROUP BY 1),
     -- C4 不按持续年重复：按事件行数
     c4 AS (SELECT scholar_id, count(*) AS n FROM mobility_event
            WHERE rule_version='{RV}' AND event_type='C4' GROUP BY 1)
SELECT sc.scholar_id,
       coalesce(c1.n,0)::int AS domestic_migration_count,
       coalesce(c2.n,0)::int AS international_migration_count,
       coalesce(c3.n,0)::int AS secondary_link_count,
       coalesce(c4.n,0)::int AS sustained_link_count
FROM scholar sc
LEFT JOIN c1 USING(scholar_id) LEFT JOIN c2 USING(scholar_id)
LEFT JOIN c3 USING(scholar_id) LEFT JOIN c4 USING(scholar_id);

COMMIT;
"""

VERIFY_SQL = f"""
SELECT count(*) AS classified,
       count(*) FILTER (WHERE is_classified) AS formal_ok,
       count(*) FILTER (WHERE NOT is_classified) AS undecided
FROM mobility_class WHERE rule_version='{RV}';

SELECT observed_class,
       count(*) AS n,
       count(*) FILTER (WHERE is_classified) AS formal_n
FROM mobility_class WHERE rule_version='{RV}'
GROUP BY 1 ORDER BY 1;

-- 跨国/非跨国迁移型划分（8.6.2：含 I=1 的 M3/M5/M7/M8 为跨国迁移型）
SELECT CASE WHEN I=1 THEN '跨国迁移型' ELSE '非跨国迁移型' END AS grp,
       CASE WHEN is_classified THEN 'formal' ELSE 'observed_only' END AS level,
       count(*) AS n
FROM mobility_class WHERE rule_version='{RV}'
GROUP BY 1,2 ORDER BY 1,2;

-- D/I/S 与 class_rule 对齐检查：应 0 行不一致
SELECT count(*) AS mismatch
FROM mobility_class m
LEFT JOIN class_rule cr
  ON cr.d_flag=m.D AND cr.i_flag=m.I AND cr.s_flag=m.S
WHERE m.rule_version='{RV}' AND cr.class_code IS NULL;

SELECT count(*) AS orphan FROM mobility_class m
LEFT JOIN scholar s USING(scholar_id)
WHERE m.rule_version='{RV}' AND s.scholar_id IS NULL;

-- 三类次数汇总（与类型分开）
SELECT sum(domestic_migration_count) AS c1_total,
       sum(international_migration_count) AS c2_total,
       sum(secondary_link_count) AS c3_distinct_countries,
       sum(sustained_link_count) AS c4_total
FROM v_mobility_counts;
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        print(f"=== DRY RUN：D/I/S 组合预估（rule_version={RV}），不写库 ===")
        run_psql(DRY_RUN_SQL, name="wp11_dry")
        return

    print(f"=== WP11 学者分类（rule_version={RV}）===")
    t0 = time.time()
    run_psql(BUILD_SQL, name="wp11_build", timeout=900)
    print(f"分类构建用时 {time.time()-t0:.1f}s")

    print("\n=== 核验 ===")
    run_psql(VERIFY_SQL, name="wp11_verify")


if __name__ == "__main__":
    main()
