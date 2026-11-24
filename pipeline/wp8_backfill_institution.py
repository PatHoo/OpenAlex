#!/usr/bin/env python3
"""WP8 回填：把 authorship.raw_affiliation 匹配到 institution，回填 matched_institution_id。

匹配口径：
  - raw_affiliation 是 OpenAlex 多条 raw_affiliation_strings 用 ' | ' 拼接；
    wp5 落库时未清理空白，wp6 生成 institution_alias 时做了空白折叠。
    两侧统一做 btrim + 空白折叠（regexp_replace '\\s+' -> ' '）后再精确匹配。
  - 一个署名串可能命中多个机构：取序号最小（第一条署名=OpenAlex 主机构）者定案。
  - 规范化后无 key 冲突（同一规范化串只指向一个 institution_id），可安全匹配。

幂等：仅更新 matched_institution_id IS NULL 的行，可安全重跑补漏。

用法：
  python wp8_backfill_institution.py --dry-run   # 只统计覆盖率，不写库
  python wp8_backfill_institution.py             # 正式回填 + 核验
"""
import argparse
import os
import time
from pathlib import Path

from dbutil import run_psql

TMP = Path(os.environ.get("TEMP", "/tmp"))

PREP = """
CREATE TEMP TABLE alias_norm AS
SELECT DISTINCT btrim(regexp_replace(raw_name, '\\s+', ' ', 'g')) AS k, institution_id
FROM institution_alias;
CREATE INDEX ON alias_norm(k);
ANALYZE alias_norm;

CREATE TEMP TABLE aff_seg AS
SELECT s.authorship_id, x.ord,
       btrim(regexp_replace(x.seg, '\\s+', ' ', 'g')) AS k
FROM authorship s
CROSS JOIN LATERAL unnest(string_to_array(s.raw_affiliation, ' | ')) WITH ORDINALITY AS x(seg, ord)
WHERE s.matched_institution_id IS NULL AND s.raw_affiliation IS NOT NULL;

CREATE TEMP TABLE match_first AS
SELECT DISTINCT ON (g.authorship_id) g.authorship_id, al.institution_id
FROM aff_seg g JOIN alias_norm al ON al.k = g.k
ORDER BY g.authorship_id, g.ord;
CREATE INDEX ON match_first(authorship_id);
"""

DRY_RUN = PREP + """
SELECT (SELECT count(*) FROM authorship) AS total,
       (SELECT count(*) FROM authorship WHERE raw_affiliation IS NOT NULL) AS with_aff,
       (SELECT count(*) FROM authorship WHERE matched_institution_id IS NOT NULL) AS already_matched,
       (SELECT count(*) FROM match_first) AS candidates;

SELECT i.institution_id, i.canonical_name, count(*) AS n
FROM match_first m JOIN institution i ON i.institution_id = m.institution_id
GROUP BY i.institution_id, i.canonical_name
ORDER BY n DESC LIMIT 10;
"""

BACKFILL = PREP + """
UPDATE authorship a
SET matched_institution_id = m.institution_id
FROM match_first m
WHERE a.authorship_id = m.authorship_id
  AND a.matched_institution_id IS NULL;
"""

VERIFY = """
SELECT count(*) AS total,
       count(matched_institution_id) AS matched,
       round(100.0 * count(matched_institution_id) / count(*), 2) AS pct,
       count(*) FILTER (WHERE raw_affiliation IS NOT NULL) AS with_aff,
       round(100.0 * count(matched_institution_id)
             / NULLIF(count(*) FILTER (WHERE raw_affiliation IS NOT NULL), 0), 2) AS pct_of_with_aff
FROM authorship;
-- 孤儿外键检查（应为 0）
SELECT count(*) AS orphan_fk
FROM authorship a LEFT JOIN institution i ON i.institution_id = a.matched_institution_id
WHERE a.matched_institution_id IS NOT NULL AND i.institution_id IS NULL;
-- 覆盖学者数
SELECT count(DISTINCT scholar_id) AS scholars_with_inst
FROM authorship WHERE matched_institution_id IS NOT NULL;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        print("=== DRY RUN：只统计，不写库 ===")
        t0 = time.time()
        run_psql(DRY_RUN, tail=40)
        print(f"用时 {time.time()-t0:.1f}s")
        return

    print("=== 正式回填 matched_institution_id ===")
    t0 = time.time()
    run_psql(BACKFILL, tail=20)
    print(f"回填用时 {time.time()-t0:.1f}s")

    print("\n=== 核验 ===")
    run_psql(VERIFY, tail=40)


if __name__ == "__main__":
    main()
