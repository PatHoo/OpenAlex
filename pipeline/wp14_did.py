#!/usr/bin/env python3
"""WP14：2017-2018 中美科技战的 DID 面板与估计（1000 人试跑版）。

设计边界：
- treated = 2017 年美国非并列主机构 且 WP13 队列 high 置信度（中国背景代理，非出生国事实）；
  medium/low 既不进 treated 也不进 control，避免污染对照。
- control = 同期在美、且未进入候选队列的学者。
- post = year >= 2018；估计窗 2014-2021，2x2 前=2014-2017、后=2018-2021。
- 结果变量：moves_any(C1+C2)、moves_intl(C2)、cn_link(当年中国主/次级署名)、log_works。
  事件只取 A_1000_v1 的 Confirmed/Probable 且未抑制；事件年仍是 signature_proxy。
- 标准误用四个组-期单元样本方差的独立近似，只作描述性 DID；面板重复观测的聚类稳健 SE
  需把导出的 CSV 交给 statsmodels/linearmodels，不在本脚本伪造推断。
- 平行趋势仅做 2014-2016 逐年 lead 的 |t|<1.96 检查（非联合 F 检验），baseline=2017。

运行：python wp14_did.py [--dry-run]
"""
from __future__ import annotations

import argparse
import time

from cohort_rules import (
    BASELINE_YEAR,
    COHORT_RULE_VERSION,
    DID_RULE_VERSION,
    POST_START_YEAR,
    WORK_RULE_VERSION,
)
from config import batch_dir
from dbutil import run_psql

RV = DID_RULE_VERSION
CRV = COHORT_RULE_VERSION
WRV = WORK_RULE_VERSION

DRY_RUN_SQL = f"""
SELECT count(*) FILTER (WHERE cm.confidence='high') AS treated,
       count(*) FILTER (WHERE cm.scholar_id IS NULL) AS control,
       count(*) FILTER (WHERE cm.confidence IN ('medium','low')) AS excluded_review
FROM (
  SELECT scholar_id FROM annual_panel
  WHERE year=2017 AND note LIKE 'wp9:%' AND primary_country='US' AND NOT primary_is_tie
) b
LEFT JOIN cohort_member cm USING(scholar_id);

SELECT ap.year, count(*) AS rows,
       count(*) FILTER (WHERE cm.confidence='high') AS treated_rows
FROM annual_panel ap
LEFT JOIN cohort_member cm USING(scholar_id)
WHERE ap.year BETWEEN 2014 AND 2021 AND ap.note LIKE 'wp9:%' AND ap.primary_country='US'
GROUP BY ap.year ORDER BY ap.year;
"""

BUILD_PANEL_SQL = f"""
BEGIN;
DELETE FROM did_panel;

WITH base AS (
    SELECT scholar_id FROM annual_panel
    WHERE year={BASELINE_YEAR} AND note LIKE 'wp9:%'
      AND primary_country='US' AND NOT primary_is_tie
),
grp AS (
    SELECT b.scholar_id,
           coalesce(cm.confidence='high', false) AS treated,
           cm.confidence AS cohort_confidence
    FROM base b LEFT JOIN cohort_member cm USING(scholar_id)
    WHERE cm.scholar_id IS NULL OR cm.confidence='high'
),
ev AS (
    SELECT scholar_id, paper_year AS year,
           count(*) FILTER (WHERE event_type IN ('C1','C2')
                            AND evidence_level IN ('Confirmed','Probable')
                            AND NOT is_suppressed)::int AS moves,
           count(*) FILTER (WHERE event_type='C2'
                            AND evidence_level IN ('Confirmed','Probable')
                            AND NOT is_suppressed)::int AS intl_moves
    FROM mobility_event
    WHERE rule_version='{WRV}' AND paper_year BETWEEN 2014 AND 2021
    GROUP BY scholar_id, paper_year
),
out AS (
    SELECT ap.scholar_id, ap.year,
           coalesce(ev.moves,0) AS outcome_moves,
           coalesce(ev.intl_moves,0) AS outcome_intl_moves,
           CASE WHEN ap.primary_country='CN'
                  OR 'CN'=ANY(ap.secondary_countries) THEN 1 ELSE 0 END AS outcome_cn_link,
           round(ln(1 + ap.valid_works)::numeric, 4) AS outcome_log_works,
           jsonb_build_object(
             'valid_works', ap.valid_works,
             'primary_country', ap.primary_country,
             'primary_is_tie', ap.primary_is_tie,
             'secondary_countries', to_jsonb(ap.secondary_countries),
             'cohort_confidence', g.cohort_confidence
           ) AS covariates,
           g.treated, g.cohort_confidence
    FROM annual_panel ap
    JOIN grp g USING(scholar_id)
    LEFT JOIN ev ON ev.scholar_id=ap.scholar_id AND ev.year=ap.year
    WHERE ap.note LIKE 'wp9:%' AND ap.year BETWEEN 2014 AND 2021
)
INSERT INTO did_panel
    (scholar_id, year, treated, post, outcome_moves, outcome_intl_moves,
     outcome_cn_link, outcome_log_works, covariates, cohort_confidence)
SELECT scholar_id, year, treated, (year >= {POST_START_YEAR}),
       outcome_moves, outcome_intl_moves, outcome_cn_link, outcome_log_works,
       covariates, cohort_confidence
FROM out;
COMMIT;
"""

BUILD_ANNUAL_SQL = f"""
BEGIN;
DELETE FROM did_annual_effect WHERE rule_version='{RV}';

WITH long AS (
    SELECT d.scholar_id, d.year, d.treated, o.outcome, o.value
    FROM did_panel d
    CROSS JOIN LATERAL (VALUES
        ('moves_any',  d.outcome_moves::double precision),
        ('moves_intl', d.outcome_intl_moves::double precision),
        ('cn_link',    d.outcome_cn_link::double precision),
        ('log_works',  d.outcome_log_works::double precision)
    ) AS o(outcome, value)
),
cell AS (
    SELECT outcome, year, treated,
           count(*)::int AS n, avg(value) AS mean, stddev_samp(value) AS sd
    FROM long
    GROUP BY outcome, year, treated
),
effect AS (
    SELECT a.outcome, a.year,
           ((a.mean - a0.mean) - (c.mean - c0.mean)) AS coef,
           sqrt(a.sd*a.sd/a.n + a0.sd*a0.sd/a0.n + c.sd*c.sd/c.n + c0.sd*c0.sd/c0.n) AS se,
           a.n::int AS n_treated, c.n::int AS n_control
    FROM cell a
    JOIN cell a0 ON a0.outcome=a.outcome AND a0.year={BASELINE_YEAR} AND a0.treated=a.treated
    JOIN cell c  ON c.outcome=a.outcome AND c.year=a.year AND c.treated=false
    JOIN cell c0 ON c0.outcome=a.outcome AND c0.year={BASELINE_YEAR} AND c0.treated=false
    WHERE a.treated=true AND a.year BETWEEN 2014 AND 2021 AND a.year<>{BASELINE_YEAR}
)
INSERT INTO did_annual_effect
    (rule_version, cohort_rule_version, outcome, year, coefficient, std_error,
     t_stat, n_treated, n_control, note)
SELECT '{RV}', '{CRV}', outcome, year,
       round(coef::numeric,6), round(se::numeric,6),
       round((coef/se)::numeric,6), n_treated, n_control,
       'event study vs {BASELINE_YEAR}; independent-cell SE, not clustered'
FROM effect;
COMMIT;
"""

BUILD_ESTIMATE_SQL = f"""
BEGIN;
DELETE FROM did_estimate WHERE rule_version='{RV}';

WITH long AS (
    SELECT d.scholar_id, d.year, d.treated, d.post, o.outcome, o.value
    FROM did_panel d
    CROSS JOIN LATERAL (VALUES
        ('moves_any',  d.outcome_moves::double precision),
        ('moves_intl', d.outcome_intl_moves::double precision),
        ('cn_link',    d.outcome_cn_link::double precision),
        ('log_works',  d.outcome_log_works::double precision)
    ) AS o(outcome, value)
),
cell AS (
    SELECT outcome, treated, post,
           count(*)::int AS n, avg(value) AS mean, stddev_samp(value) AS sd
    FROM long GROUP BY outcome, treated, post
),
did AS (
    SELECT t1.outcome,
           ((t1.mean-t0.mean) - (c1.mean-c0.mean)) AS coef,
           sqrt(t1.sd*t1.sd/t1.n + t0.sd*t0.sd/t0.n + c1.sd*c1.sd/c1.n + c0.sd*c0.sd/c0.n) AS se
    FROM cell t1
    JOIN cell t0 ON t0.outcome=t1.outcome AND t0.treated=true AND t0.post=false
    JOIN cell c1 ON c1.outcome=t1.outcome AND c1.treated=false AND c1.post=true
    JOIN cell c0 ON c0.outcome=t1.outcome AND c0.treated=false AND c0.post=false
    WHERE t1.treated=true AND t1.post=true
),
pretrend AS (
    SELECT outcome,
           max(coefficient) FILTER (WHERE year=2014) AS pretrend_coef,
           bool_and(coalesce(abs(t_stat) < 1.96, true)) FILTER
             (WHERE year BETWEEN 2014 AND 2016) AS passed
    FROM did_annual_effect
    WHERE rule_version='{RV}'
    GROUP BY outcome
),
counts AS (
    SELECT count(DISTINCT scholar_id) FILTER (WHERE treated)::int AS n_treated,
           count(DISTINCT scholar_id) FILTER (WHERE NOT treated)::int AS n_control,
           count(*)::int AS n_obs
    FROM did_panel
)
INSERT INTO did_estimate
    (rule_version, cohort_rule_version, specification, outcome, coefficient,
     std_error, t_stat, n_treated, n_control, n_obs, pretrend_coef,
     parallel_trend_passed, note)
SELECT '{RV}', '{CRV}', 'did_2x2', d.outcome,
       round(d.coef::numeric,6), round(d.se::numeric,6), round((d.coef/d.se)::numeric,6),
       c.n_treated, c.n_control, c.n_obs, p.pretrend_coef, p.passed,
       'pre=2014-2017 post=2018-2021; treated=high-confidence Chinese-background proxy; independent-cell SE, descriptive only'
FROM did d
JOIN pretrend p USING(outcome)
CROSS JOIN counts c;
COMMIT;
"""

VERIFY_SQL = f"""
SELECT treated, post, count(*) AS obs, count(DISTINCT scholar_id) AS scholars
FROM did_panel GROUP BY 1,2 ORDER BY 1,2;

SELECT outcome, count(*) AS years,
       min(coefficient) AS min_coef, max(coefficient) AS max_coef
FROM did_annual_effect WHERE rule_version='{RV}'
GROUP BY outcome ORDER BY outcome;

SELECT outcome, coefficient, std_error, t_stat, n_treated, n_control, n_obs,
       pretrend_coef, parallel_trend_passed
FROM did_estimate WHERE rule_version='{RV}' ORDER BY outcome;

SELECT count(*) AS orphan
FROM did_panel d LEFT JOIN scholar s USING(scholar_id)
WHERE s.scholar_id IS NULL;
"""

EXPORT_PANEL_SQL = f"""
COPY (
  SELECT d.scholar_id, s.display_name, d.year, d.treated, d.post,
         d.outcome_moves, d.outcome_intl_moves, d.outcome_cn_link,
         d.outcome_log_works, d.cohort_confidence, d.covariates
  FROM did_panel d JOIN scholar s USING(scholar_id)
  ORDER BY d.scholar_id, d.year
) TO STDOUT WITH CSV HEADER;
"""

EXPORT_ANNUAL_SQL = f"""
COPY (
  SELECT outcome, year, coefficient, std_error, t_stat, n_treated, n_control
  FROM did_annual_effect WHERE rule_version='{RV}'
  ORDER BY outcome, year
) TO STDOUT WITH CSV HEADER;
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        print(f"=== DRY RUN：DID 队列与年份覆盖（rule_version={RV}），不写库 ===")
        run_psql(DRY_RUN_SQL, name="wp14_dry")
        return

    print(f"=== WP14 DID 面板（rule_version={RV}）===")
    t0 = time.time()
    run_psql(BUILD_PANEL_SQL, name="wp14_panel", timeout=900)
    run_psql(BUILD_ANNUAL_SQL, name="wp14_annual", timeout=900)
    run_psql(BUILD_ESTIMATE_SQL, name="wp14_estimate", timeout=900)
    print(f"DID 构建用时 {time.time()-t0:.1f}s")

    print("\n=== 核验 ===")
    run_psql(VERIFY_SQL, name="wp14_verify")

    out = batch_dir()
    panel_csv = run_psql(EXPORT_PANEL_SQL, name="wp14_export_panel", verbose=False)
    (out / "did_panel.csv").write_text(panel_csv, encoding="utf-8")
    annual_csv = run_psql(EXPORT_ANNUAL_SQL, name="wp14_export_annual", verbose=False)
    (out / "did_annual_effect.csv").write_text(annual_csv, encoding="utf-8")
    print(f"\n导出：{out / 'did_panel.csv'}、{out / 'did_annual_effect.csv'}")


if __name__ == "__main__":
    main()
