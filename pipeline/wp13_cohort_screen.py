#!/usr/bin/env python3
"""WP13：中国背景候选队列筛选（不伪造出生国）。

口径（见 pipeline/cohort_rules.py）：
- 输入信号：强/弱拼音姓氏 token、中国内地/中国香港/中国台湾机构轨迹、外部履历/提示、
  2017 年美国主机构面板（无 2017 时允许 2014-2017 最近一年回退，但不得进 high）。
- high = 可靠出生提示，或强拼音 + 中国内地/港澳台机构署名轨迹 + 2017 年美国非并列主机构；
  medium = 弱拼音/回退在美 + 中国内地/港澳台轨迹，或强拼音 + 中国轨迹但年份回退/并列；
  low = 仅拼音或仅中国轨迹 + 在美。
- high 才可作为 DID treated 代理；medium/low 只进复核清单。
- 除 birth_country_hint 且有 birth_evidence 外，cohort_member.birth_country 保持 NULL；
  determination_method 明确写 algorithm_inference_v1，避免被误读为出生国事实。

幂等：清本规则的证据与未人工复核的算法行后重建；reviewed_by 非空的行不动。
运行：python wp13_cohort_screen.py [--dry-run]
"""
from __future__ import annotations

import argparse
import time

from cohort_rules import (
    COHORT_RULE_VERSION,
    GREATER_CHINA_CODES,
    STRONG_PINYIN_SURNAMES,
    WEAK_PINYIN_SURNAMES,
    regex_for_surnames,
)
from dbutil import run_psql

RV = COHORT_RULE_VERSION
STRONG_RE = regex_for_surnames(STRONG_PINYIN_SURNAMES)
WEAK_RE = regex_for_surnames(WEAK_PINYIN_SURNAMES)
GC_ARRAY = "ARRAY['CN','HK','TW']::char(2)[]"

CLASSIFY_CTE = f"""
WITH names AS (
    SELECT s.scholar_id, s.display_name,
           regexp_replace(
             lower(coalesce(s.display_name,'') || ' ' || coalesce(ss.authfull,'')),
             '[^a-z ]', ' ', 'g') AS n
    FROM scholar s JOIN source_scholar ss USING(src_id)
),
py AS (
    SELECT scholar_id, display_name, n,
           (regexp_match(n, '{STRONG_RE}'))[2] AS strong_surname,
           (regexp_match(n, '{WEAK_RE}'))[2] AS weak_surname
    FROM names
),
us_exact AS (
    SELECT scholar_id, primary_country, primary_is_tie,
           2017::smallint AS us_year, 'exact2017'::text AS us_basis
    FROM annual_panel
    WHERE year=2017 AND note LIKE 'wp9:%' AND primary_country='US'
),
us_fallback AS (
    SELECT DISTINCT ON (scholar_id) scholar_id, primary_country, primary_is_tie,
           year::smallint AS us_year, 'fallback2014_2017'::text AS us_basis
    FROM annual_panel
    WHERE year BETWEEN 2014 AND 2017 AND note LIKE 'wp9:%' AND primary_country='US'
    ORDER BY scholar_id, year DESC
),
us AS (
    SELECT * FROM us_exact
    UNION ALL
    SELECT f.* FROM us_fallback f
    WHERE NOT EXISTS (SELECT 1 FROM us_exact e WHERE e.scholar_id=f.scholar_id)
),
gc_panel AS (
    SELECT scholar_id,
           bool_or(primary_country = ANY({GC_ARRAY})) AS gc_primary,
           bool_or('CN'=ANY(secondary_countries) OR 'HK'=ANY(secondary_countries)
                   OR 'TW'=ANY(secondary_countries)) AS gc_secondary
    FROM annual_panel
    WHERE note LIKE 'wp9:%'
    GROUP BY scholar_id
),
gc_share AS (
    SELECT scholar_id, bool_or(country_code = ANY({GC_ARRAY})) AS gc_share
    FROM affiliation_share GROUP BY scholar_id
),
gc_cv AS (
    SELECT scholar_id,
           bool_or(verified AND country_code = ANY({GC_ARRAY})) AS gc_cv_verified
    FROM career_fact GROUP BY scholar_id
),
gc_hint AS (
    SELECT scholar_id, birth_country_hint,
           (birth_country_hint = ANY({GC_ARRAY}) AND birth_evidence IS NOT NULL) AS birth_hint
    FROM scholar
),
cand AS (
    SELECT p.scholar_id, p.display_name, p.strong_surname, p.weak_surname,
           u.us_basis, u.us_year, u.primary_is_tie,
           coalesce(gp.gc_primary,false) AS gc_primary,
           coalesce(gp.gc_secondary,false) AS gc_secondary,
           coalesce(gs.gc_share,false) AS gc_share,
           coalesce(gv.gc_cv_verified,false) AS gc_cv_verified,
           coalesce(h.birth_hint,false) AS birth_hint,
           h.birth_country_hint,
           (coalesce(gp.gc_primary,false) OR coalesce(gp.gc_secondary,false)
            OR coalesce(gs.gc_share,false) OR coalesce(gv.gc_cv_verified,false)
            OR coalesce(h.birth_hint,false)) AS gc_any,
           (coalesce(gp.gc_primary,false) OR coalesce(gv.gc_cv_verified,false)
            OR coalesce(h.birth_hint,false)) AS gc_strong
    FROM py p
    JOIN us u ON u.scholar_id=p.scholar_id
    LEFT JOIN gc_panel gp ON gp.scholar_id=p.scholar_id
    LEFT JOIN gc_share gs ON gs.scholar_id=p.scholar_id
    LEFT JOIN gc_cv gv ON gv.scholar_id=p.scholar_id
    LEFT JOIN gc_hint h ON h.scholar_id=p.scholar_id
),
classified AS (
    SELECT c.*,
           CASE
             WHEN c.birth_hint THEN 'high'
             WHEN c.strong_surname IS NOT NULL AND c.gc_any
                  AND c.us_basis='exact2017' AND NOT coalesce(c.primary_is_tie,false)
               THEN 'high'
             WHEN (c.strong_surname IS NOT NULL OR c.weak_surname IS NOT NULL)
                  AND c.gc_any THEN 'medium'
             WHEN c.strong_surname IS NOT NULL OR c.weak_surname IS NOT NULL
                  OR c.gc_any THEN 'low'
           END::text AS confidence
    FROM cand c
)
"""

DRY_RUN_SQL = "BEGIN; CREATE TEMP TABLE _dry_classified ON COMMIT DROP AS " + CLASSIFY_CTE + f"""
SELECT * FROM classified;
SELECT confidence, count(*) AS n,
       count(*) FILTER (WHERE us_basis='exact2017') AS exact_2017,
       count(*) FILTER (WHERE gc_any) AS with_gc_signal
FROM _dry_classified GROUP BY confidence ORDER BY confidence;
SELECT scholar_id, display_name, confidence, strong_surname, weak_surname,
       us_basis, gc_primary, gc_secondary, gc_share, gc_cv_verified
FROM _dry_classified WHERE confidence='high' ORDER BY scholar_id;
COMMIT;
"""

BUILD_SQL = "BEGIN; CREATE TEMP TABLE _cohort_classified ON COMMIT DROP AS " + CLASSIFY_CTE + f"""
SELECT * FROM classified;

DELETE FROM cohort_evidence WHERE rule_version='{RV}';
DELETE FROM cohort_member
WHERE reviewed_by IS NULL AND determination_method LIKE 'algorithm_inference_v1:%';

INSERT INTO cohort_member
    (scholar_id, birth_country, current_country, birth_evidence,
     determination_method, confidence, reviewed_by, reviewed_at)
SELECT c.scholar_id,
       CASE WHEN c.birth_hint THEN c.birth_country_hint::char(2) ELSE NULL END,
       'US'::char(2),
       jsonb_build_object(
         'rule_version', '{RV}',
         'strong_pinyin_surname', c.strong_surname,
         'weak_pinyin_surname', c.weak_surname,
         'greater_china_primary', c.gc_primary,
         'greater_china_secondary', c.gc_secondary,
         'greater_china_share', c.gc_share,
         'greater_china_cv_verified', c.gc_cv_verified,
         'birth_hint', c.birth_hint,
         'us_basis', c.us_basis,
         'us_year', c.us_year,
         'us_primary_is_tie', coalesce(c.primary_is_tie,false),
         'birth_country_asserted', c.birth_hint
       )::text,
       'algorithm_inference_v1: pinyin + greater-China affiliation + 2017 US panel; birth_country not asserted',
       c.confidence, NULL, NULL
FROM _cohort_classified c
WHERE c.confidence IS NOT NULL
ON CONFLICT (scholar_id) DO UPDATE SET
    birth_country        = EXCLUDED.birth_country,
    current_country      = EXCLUDED.current_country,
    birth_evidence       = EXCLUDED.birth_evidence,
    determination_method = EXCLUDED.determination_method,
    confidence           = EXCLUDED.confidence
WHERE cohort_member.reviewed_by IS NULL;

INSERT INTO cohort_evidence
    (scholar_id, rule_version, signal_code, signal_value, evidence_detail,
     source_table, source_key, weight)
SELECT scholar_id, '{RV}', 'pinyin_strong', coalesce(strong_surname,''),
       'display_name/authfull matched strong pinyin surname token', 'scholar,source_scholar',
       display_name, 30
FROM _cohort_classified WHERE strong_surname IS NOT NULL
UNION ALL
SELECT scholar_id, '{RV}', 'pinyin_weak', coalesce(weak_surname,''),
       'display_name/authfull matched weak romanization token', 'scholar,source_scholar',
       display_name, 10
FROM _cohort_classified WHERE weak_surname IS NOT NULL
UNION ALL
SELECT scholar_id, '{RV}', 'gc_primary', '1',
       'observed annual primary institution in CN/HK/TW', 'annual_panel', 'primary_country', 30
FROM _cohort_classified WHERE gc_primary
UNION ALL
SELECT scholar_id, '{RV}', 'gc_secondary', '1',
       'observed annual secondary countries include CN/HK/TW', 'annual_panel', 'secondary_countries', 15
FROM _cohort_classified WHERE gc_secondary
UNION ALL
SELECT scholar_id, '{RV}', 'gc_share', '1',
       'affiliation share rows include CN/HK/TW', 'affiliation_share', 'country_code', 15
FROM _cohort_classified WHERE gc_share
UNION ALL
SELECT scholar_id, '{RV}', 'gc_cv_verified', '1',
       'verified career fact in CN/HK/TW', 'career_fact', 'country_code', 40
FROM _cohort_classified WHERE gc_cv_verified
UNION ALL
SELECT scholar_id, '{RV}', 'birth_hint', coalesce(birth_country_hint::text,''),
       'external birth_country_hint with birth_evidence', 'scholar', 'birth_country_hint', 60
FROM _cohort_classified WHERE birth_hint
UNION ALL
SELECT scholar_id, '{RV}', 'us_2017_primary', 'US',
       '2017 wp9 panel primary country US', 'annual_panel', '2017:US', 25
FROM _cohort_classified WHERE us_basis='exact2017'
UNION ALL
SELECT scholar_id, '{RV}', 'us_fallback', 'US',
       'nearest observed primary country US in 2014-2017 (no 2017 row)', 'annual_panel',
       us_year::text || ':US', 10
FROM _cohort_classified WHERE us_basis='fallback2014_2017'
ON CONFLICT DO NOTHING;
COMMIT;
"""

VERIFY_SQL = f"""
SELECT confidence, count(*) AS n,
       count(*) FILTER (WHERE birth_country IS NOT NULL) AS asserted_birth_country
FROM cohort_member GROUP BY confidence ORDER BY confidence;

SELECT signal_code, count(*) AS n, sum(weight) AS total_weight
FROM cohort_evidence WHERE rule_version='{RV}'
GROUP BY signal_code ORDER BY signal_code;

SELECT cm.scholar_id, s.display_name, cm.confidence, cm.birth_country,
       string_agg(ce.signal_code || ':' || coalesce(ce.signal_value,''), ', ' ORDER BY ce.signal_code) AS signals
FROM cohort_member cm
JOIN scholar s USING(scholar_id)
LEFT JOIN cohort_evidence ce ON ce.scholar_id=cm.scholar_id AND ce.rule_version='{RV}'
GROUP BY cm.scholar_id, s.display_name, cm.confidence, cm.birth_country
ORDER BY cm.confidence DESC, cm.scholar_id;
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        print(f"=== DRY RUN：中国背景候选队列（rule_version={RV}），不写库 ===")
        run_psql(DRY_RUN_SQL, name="wp13_dry")
        return

    print(f"=== WP13 中国背景候选队列（rule_version={RV}）===")
    t0 = time.time()
    run_psql(BUILD_SQL, name="wp13_build", timeout=900)
    print(f"队列构建用时 {time.time()-t0:.1f}s")

    print("\n=== 核验 ===")
    run_psql(VERIFY_SQL, name="wp13_verify")


if __name__ == "__main__":
    main()
