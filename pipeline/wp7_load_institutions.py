#!/usr/bin/env python3
"""WP7 机构入库：补国家字典 + 合并种子机构 + 导入 institution/alias。

输入（data/batch_001/）：_institutions.tsv, _aliases.tsv, _countries.txt
步骤：
  1) country：203 国补全 name_en/iso3，name_zh 常见国有中文、其余英文名兜底
  2) 种子机构：11 个无 ROR 的按名称匹配新机构，回填 ror_id/country（保留 institution_id）
  3) institution：31388 个按 ROR 导入（ON CONFLICT ror_id）
  4) institution_alias：182 万 raw串→机构，COPY staging 后按 ror 关联入库

幂等可重跑。
"""
import json
import os
from pathlib import Path

import pycountry

from config import pg_path
from dbutil import query_psql, run_psql

DATA = Path(__file__).parent / "data" / "batch_001"
TMP = Path(os.environ.get("TEMP", "/tmp"))

# 常见国家中文名（其余用英文兜底）
ZH = {
    "US": "美国", "CN": "中国", "GB": "英国", "DE": "德国", "FR": "法国",
    "JP": "日本", "CA": "加拿大", "AU": "澳大利亚", "IT": "意大利", "ES": "西班牙",
    "NL": "荷兰", "CH": "瑞士", "SE": "瑞典", "NO": "挪威", "DK": "丹麦",
    "FI": "芬兰", "BE": "比利时", "AT": "奥地利", "KR": "韩国", "IN": "印度",
    "BR": "巴西", "RU": "俄罗斯", "ZA": "南非", "SG": "新加坡", "TW": "中国台湾",
    "HK": "中国香港", "MO": "中国澳门", "IL": "以色列", "PL": "波兰", "PT": "葡萄牙",
    "IE": "爱尔兰", "NZ": "新西兰", "MX": "墨西哥", "AR": "阿根廷", "TR": "土耳其",
}


def esc(s: str) -> str:
    return (s or "").replace("'", "''")


def main():
    # ---------- 1) 国家字典 ----------
    codes = [l.strip() for l in open(DATA / "_countries.txt", encoding="utf-8") if l.strip()]
    country_sql = []
    for cc in codes:
        obj = pycountry.countries.get(alpha_2=cc)
        en = obj.name if obj else cc
        iso3 = obj.alpha_3 if obj else ""
        zh = ZH.get(cc, en)
        country_sql.append(
            f"INSERT INTO country(country_code,name_zh,name_en,iso3) VALUES "
            f"('{cc}','{esc(zh)}','{esc(en)}','{iso3}') "
            f"ON CONFLICT (country_code) DO NOTHING;"
        )
    print(f"[1/4] 补全国家字典 {len(codes)} 国...")
    run_psql("\n".join(country_sql), tail=3)

    # ---------- 2) 种子机构按名称合并 ----------
    inst_rows = []
    for line in open(DATA / "_institutions.tsv", encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 4:
            inst_rows.append({"ror": p[0], "name": p[1], "country": p[2], "oa_id": p[3]})
    by_name = {}
    for r in inst_rows:
        by_name[r["name"].lower()] = r
    seed_merge = []
    f = TMP / "wp7_seed.sql"
    # 先读种子机构名
    seed_query = query_psql(
        "SELECT institution_id || chr(124) || canonical_name "
        "FROM institution WHERE ror_id IS NULL;"
    )
    seeds = []
    for line in seed_query.split("\n"):
        if "|" in line:
            iid, nm = line.split("|", 1)
            seeds.append((int(iid), nm))
    merge_sql = []
    for iid, nm in seeds:
        match = by_name.get(nm.lower())
        if match:
            merge_sql.append(
                f"UPDATE institution SET ror_id='{match['ror']}', "
                f"country_code=COALESCE(country_code,'{match['country']}') "
                f"WHERE institution_id={iid} AND ror_id IS NULL;")
            seed_merge.append((iid, nm, match["ror"]))
    print(f"[2/4] 种子机构合并: {len(seed_merge)}/{len(seeds)} 匹配到 ROR")
    for iid, nm, ror in seed_merge:
        print(f"    id={iid} {nm} -> {ror}")
    if merge_sql:
        run_psql("\n".join(merge_sql), tail=3)

    # ---------- 3) 机构主表 ----------
    stage = DATA / "_inst_stage.csv"
    import csv
    with open(stage, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        for r in inst_rows:
            w.writerow([r["ror"], r["name"], r["country"] or None])
    n_inst = len(inst_rows)
    print(f"[3/4] 导入机构主表 {n_inst} 个...")
    inst_sql = f"""
BEGIN;
CREATE TEMP TABLE inst_stage(ror_id TEXT, canonical_name TEXT, country_code CHAR(2));
\\copy inst_stage FROM '{pg_path(stage)}' WITH (FORMAT csv, NULL '')
INSERT INTO institution(ror_id,canonical_name,country_code)
SELECT ror_id,canonical_name,country_code FROM inst_stage
ON CONFLICT (ror_id) DO NOTHING;
COMMIT;
"""
    run_psql(inst_sql, tail=5)

    # ---------- 4) 别名表 ----------
    # 注意：raw署名串可能含反斜杠/引号，必须用 CSV 格式（text 模式会把 \ 当转义符导致列错位）
    alias_stage = DATA / "_aliases.csv"
    if alias_stage.exists():
        alias_stage.unlink()
    import csv
    n_alias = 0
    with open(DATA / "_aliases.tsv", encoding="utf-8") as fi, \
         open(alias_stage, "w", encoding="utf-8", newline="") as fo:
        w = csv.writer(fo)
        for line in fi:
            p = line.rstrip("\n").split("\t")
            if len(p) == 3:
                w.writerow(p)
                n_alias += 1
    print(f"[4/4] 导入别名 {n_alias} 条（CSV 格式，COPY 可能需 1-2 分钟）...")
    alias_sql = f"""
BEGIN;
CREATE TEMP TABLE alias_stage(raw_name TEXT, ror_id TEXT, n INT);
\\copy alias_stage FROM '{pg_path(alias_stage)}' WITH (FORMAT csv)
INSERT INTO institution_alias(raw_name,institution_id,status,evidence_url)
SELECT a.raw_name, i.institution_id, 'confirmed', 'openalex_affiliations'
FROM alias_stage a JOIN institution i ON i.ror_id=a.ror_id
ON CONFLICT (raw_name,institution_id) DO NOTHING;
COMMIT;
"""
    run_psql(alias_sql, tail=5)

    # ---------- 核验 ----------
    print("\n=== 核验 ===")
    verify = """
SELECT (SELECT count(*) FROM country) countries,
       (SELECT count(*) FROM institution) institutions,
       (SELECT count(*) FROM institution WHERE ror_id IS NOT NULL) with_ror,
       (SELECT count(*) FROM institution_alias) aliases;
"""
    run_psql(verify, tail=3)

    # 清理临时文件（保留抽取的原始 TSV 供复核）
    for p in [stage, alias_stage]:
        p.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
