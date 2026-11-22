#!/usr/bin/env python3
"""WP9：在 1,000 人数据地基上建年度面板与机构占比。

口径严格按 design/关系库+AGE_图模式设计.md 3.1：
- 有效署名 = authorship.is_valid=true 且 work.work_type<>'preprint'
  （本次导入的 work.is_preprint 列全为 false，不可用；以 work_type 为准）
- 主机构 = 年度众数；并列时 primary_is_tie=true、tie_count=并列机构数，不强选唯一
  （并列时 primary_institution_id 按 institution_id 升序取第一个，仅作确定性的显示值）
- secondary_countries / secondary_institutions = 该年非主机构及其国家（C3/C4 事件依赖）
- affiliation_share.share = 该机构署名数 / 该年有效署名总数（含所有匹配机构，合计=1）
- 不写 LOCF 填充年（全部 observed）；imputed 年永不进入事件识别

幂等：ON CONFLICT DO UPDATE，可重复运行。
运行：python wp9_build_panel.py [--dry-run]
"""
from __future__ import annotations

import argparse
import time

from dbutil import run_psql

BASE_CTE = """
WITH base AS (
    SELECT a.scholar_id,
           w.pub_year AS year,
           a.matched_institution_id AS institution_id,
           i.country_code,
           count(*)::int AS cnt
    FROM authorship a
    JOIN work w ON w.work_id = a.work_id
    JOIN institution i ON i.institution_id = a.matched_institution_id
    WHERE a.is_valid = true
      AND w.work_type IS DISTINCT FROM 'preprint'
      AND w.pub_year IS NOT NULL
    GROUP BY 1,2,3,4
),
ranked AS (
    SELECT *, max(cnt) OVER (PARTITION BY scholar_id, year) AS max_cnt
    FROM base
),
primary_pick AS (
    SELECT DISTINCT ON (scholar_id, year)
           scholar_id, year, institution_id, country_code, max_cnt
    FROM ranked
    WHERE cnt = max_cnt
    ORDER BY scholar_id, year, institution_id
)
"""

BUILD_SQL = "BEGIN;" + BASE_CTE + """
, agg AS (
    SELECT r.scholar_id,
           r.year,
           count(*) FILTER (WHERE r.cnt = r.max_cnt)::int AS tie_count,
           sum(r.cnt)::int AS total_valid,
           array_agg(DISTINCT r.institution_id) FILTER (WHERE r.cnt < r.max_cnt)
               AS secondary_institutions,
           array_agg(DISTINCT r.country_code) FILTER
               (WHERE r.country_code IS DISTINCT FROM p.country_code)
               AS secondary_countries
    FROM ranked r
    JOIN primary_pick p ON p.scholar_id = r.scholar_id AND p.year = r.year
    GROUP BY r.scholar_id, r.year, p.country_code
)
INSERT INTO annual_panel (scholar_id, year, primary_institution_id, primary_country,
    primary_is_tie, tie_count, valid_works, secondary_countries, secondary_institutions,
    is_imputed, observation_status, note)
SELECT a.scholar_id, a.year, p.institution_id, p.country_code,
       a.tie_count > 1, a.tie_count, a.total_valid,
       coalesce(a.secondary_countries, ARRAY[]::char(2)[]),
       coalesce(a.secondary_institutions, ARRAY[]::bigint[]),
       false, 'observed', 'wp9: 1000p authorship annual mode'
FROM agg a
JOIN primary_pick p ON p.scholar_id = a.scholar_id AND p.year = a.year
ON CONFLICT (scholar_id, year) DO UPDATE SET
    primary_institution_id = EXCLUDED.primary_institution_id,
    primary_country        = EXCLUDED.primary_country,
    primary_is_tie         = EXCLUDED.primary_is_tie,
    tie_count              = EXCLUDED.tie_count,
    valid_works            = EXCLUDED.valid_works,
    secondary_countries    = EXCLUDED.secondary_countries,
    secondary_institutions = EXCLUDED.secondary_institutions,
    note                   = EXCLUDED.note;
""" + BASE_CTE + """
, totals AS (
    SELECT r.scholar_id, r.year, sum(r.cnt)::int AS total_valid
    FROM ranked r GROUP BY 1,2
)
INSERT INTO affiliation_share (scholar_id, year, institution_id, country_code,
    work_count, share, is_primary)
SELECT r.scholar_id, r.year, r.institution_id, r.country_code,
       r.cnt,
       round(r.cnt::numeric / NULLIF(t.total_valid, 0), 4),
       r.cnt = r.max_cnt
FROM ranked r
JOIN totals t ON t.scholar_id = r.scholar_id AND t.year = r.year
ON CONFLICT (scholar_id, year, institution_id) DO UPDATE SET
    country_code = EXCLUDED.country_code,
    work_count   = EXCLUDED.work_count,
    share        = EXCLUDED.share,
    is_primary   = EXCLUDED.is_primary;
COMMIT;
"""

DRY_RUN_SQL = """
SELECT count(*) AS valid_affiliations,
       count(DISTINCT (a.scholar_id, w.pub_year)) AS scholar_years,
       count(DISTINCT a.scholar_id) AS scholars
FROM authorship a
JOIN work w ON w.work_id = a.work_id
WHERE a.is_valid = true
  AND a.matched_institution_id IS NOT NULL
  AND w.work_type IS DISTINCT FROM 'preprint'
  AND w.pub_year IS NOT NULL;
"""

VERIFY_SQL = """
SELECT count(*) AS panel_rows,
       count(DISTINCT scholar_id) AS scholars,
       min(year) AS min_year, max(year) AS max_year,
       count(*) FILTER (WHERE primary_is_tie) AS tie_years,
       count(*) FILTER (WHERE cardinality(secondary_countries) > 0) AS with_secondary_country
FROM annual_panel;

SELECT count(*) AS share_rows,
       count(DISTINCT scholar_id) AS scholars,
       count(*) FILTER (WHERE is_primary) AS primary_rows,
       round(min(share), 4) AS min_share, round(max(share), 4) AS max_share
FROM affiliation_share;

-- 孤儿外键应为 0
SELECT count(*) AS orphan_panel_inst
FROM annual_panel p LEFT JOIN institution i ON i.institution_id=p.primary_institution_id
WHERE p.primary_institution_id IS NOT NULL AND i.institution_id IS NULL;
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        print("=== DRY RUN：只统计，不写库 ===")
        run_psql(DRY_RUN_SQL, name="wp9_dry")
        return

    print("=== WP9 建年度面板 + 机构占比 ===")
    t0 = time.time()
    run_psql(BUILD_SQL, name="wp9_build")
    print(f"建表用时 {time.time()-t0:.1f}s")

    print("\n=== 核验 ===")
    run_psql(VERIFY_SQL, name="wp9_verify")


if __name__ == "__main__":
    main()
