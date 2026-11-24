#!/usr/bin/env python3
"""Export analysis datasets and runnable templates for local SPSS / Stata / ArcGIS.

This is a phase-2 deliverable: it prepares standardized inputs and script templates.
It does NOT execute SPSS, Stata or ArcGIS, and does NOT fabricate their outputs.

Run under WSL:
  export PGPASSWORD=$(cat ~/.openalex_db_pass)
  $HOME/.venvs/openalex/bin/python pipeline/export_local_tools.py
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deliver" / "local_tools"
DATA = OUT / "data"
RULE_EVENT = "A_1000_v1"


def export_csv(conn, filename: str, sql: str):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        # 空结果也要写带表头的 CSV：下游 SPSS/Stata/ArcGIS 模板把它当合法空输入。
        # 机构经纬度在 1,000 人试跑数据里全为空（2026-11-24 实测），
        # 此前 raise SystemExit 会让整个导出中断、模板文件都不生成。
        columns = [d.name for d in cur.description] if cur.description else []
    if not rows and not columns:
        raise SystemExit(f"no rows and no columns for {filename}")
    path = DATA / filename
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns or list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
    if rows:
        print(f"  {filename}: {len(rows):,} rows")
    else:
        print(f"  {filename}: 0 rows (仅表头；源数据该字段为空)")


SPSS = "" "* OpenAlex 科学家流动数据库 — SPSS 语法模板（第二期：本地软件协同）\n" \
       "* 读取标准化 CSV，输出频数表与交叉表；请在 SPSS Statistics 中打开运行。\n" \
       "GET DATA /TYPE=TXT\n" \
       f"  /FILE='{DATA.as_posix()}/did_panel.csv'\n" \
       "  /DELIMITERS=\",\"\n" \
       "  /QUALIFIER='\"'\n" \
       "  /ARRANGEMENT=DELIMITED\n" \
       "  /FIRSTCASE=2\n" \
       "  /VARIABLES=\n" \
       "    scholar_id F8.0\n" \
       "    year F4.0\n" \
       "    treated F1.0\n" \
       "    post F1.0\n" \
       "    outcome_moves F8.0\n" \
       "    outcome_intl_moves F8.0\n" \
       "    outcome_cn_link F8.0\n" \
       "    outcome_log_works F12.4.\n" \
       "CACHE.\n" \
       "EXECUTE.\n\n" \
       "FREQUENCIES VARIABLES=treated post /ORDER=ANALYSIS.\n" \
       "CROSSTABS TABLES=outcome_moves BY treated\n" \
       "  /CELLS=COUNT ROW COLUMN /STATISTICS=CHISQ.\n" \
       "MEANS TABLES=outcome_moves outcome_intl_moves BY treated /CELLS=MEAN COUNT STDDEV.\n"

STATA = "* OpenAlex 科学家流动数据库 — Stata do-file 模板（第二期：本地软件协同）\n" \
        "* 复算 DID 面板描述统计与基准回归。样本为 1,000 人试跑，结论仅供流程验证。\n" \
        f"import delimited \"{DATA.as_posix()}/did_panel.csv\", clear varnames(1) encoding(utf8)\n" \
        "describe\n" \
        "summarize outcome_moves outcome_intl_moves outcome_cn_link outcome_log_works\n" \
        "tabulate treated post\n" \
        "regress outcome_moves i.treated##i.post, vce(cluster scholar_id)\n" \
        "regress outcome_intl_moves i.treated##i.post, vce(cluster scholar_id)\n" \
        f"import delimited \"{DATA.as_posix()}/did_robust_annual_effect.csv\", clear varnames(1) encoding(utf8)\n" \
        "twoway (line coefficient year, sort) (rline ci_low ci_high year, sort), \\\n" \
        "  title(\"Event study: moves_any\") yline(0) xtitle(\"Year\") legend(off)\n"

ARCGIS = '''"""OpenAlex 科学家流动数据库 — ArcGIS Pro / ArcPy 模板（第二期：本地软件协同）。

在已配置 ArcPy 的 Python 环境中运行。只生成本地要素类，不依赖在线底图服务。
坐标为 WGS84；如需与国内合规底图叠加，请在 ArcGIS 中执行坐标转换为 GCJ-02。
"""
from pathlib import Path
import arcpy

DATA = Path(r"__DATA_DIR__")
OUT_GDB = DATA.parent / "openalex_local.gdb"

if not arcpy.Exists(str(OUT_GDB)):
    arcpy.management.CreateFileGDB(str(DATA.parent), "openalex_local.gdb")

inst = DATA / "institution_points.csv"
if inst.exists():
    arcpy.management.CopyFeatures(
        str(inst), str(OUT_GDB / "institution_points")
    )
    arcpy.management.DefineProjection(str(OUT_GDB / "institution_points"), 4326)

flow = DATA / "country_flows.csv"
if flow.exists():
    arcpy.management.CopyRows(str(flow), str(OUT_GDB / "country_flows"))

print("ArcGIS local feature classes ready:", OUT_GDB)
'''.replace("__DATA_DIR__", DATA.as_posix())


def main():
    password = os.environ.get("PGPASSWORD", "")
    if not password:
        raise SystemExit("PGPASSWORD is required.")
    DATA.mkdir(parents=True, exist_ok=True)
    dsn = dict(host=os.environ.get("PGHOST", "127.0.0.1"),
               port=int(os.environ.get("PGPORT", "5432")),
               user=os.environ.get("PGUSER", "postgres"),
               password=password,
               dbname=os.environ.get("PGDATABASE", "openalex"))
    conn = psycopg2.connect(**dsn)
    try:
        print("exporting CSVs:")
        export_csv(conn, "did_panel.csv", "SELECT scholar_id, year, treated, post, outcome_moves, outcome_intl_moves, outcome_cn_link, outcome_log_works, cohort_confidence FROM did_panel ORDER BY scholar_id, year")
        export_csv(conn, "did_robust_estimate.csv", "SELECT outcome, specification, coefficient, std_error, t_stat, p_value, ci_low, ci_high, n_obs, n_scholars, n_clusters, n_treated, n_control, joint_f_stat, joint_p_value, parallel_trend_passed FROM did_robust_estimate WHERE rule_version='DID_2018_A1000_v2' ORDER BY outcome, specification")
        export_csv(conn, "did_robust_annual_effect.csv", "SELECT outcome, year, lead_or_lag, coefficient, std_error, t_stat, p_value, ci_low, ci_high FROM did_robust_annual_effect WHERE rule_version='DID_2018_A1000_v2' ORDER BY outcome, year")
        export_csv(conn, "mobility_event_sample.csv", f"""
            SELECT scholar_id, event_type, paper_year, actual_year, year_source,
                   from_country, to_country, from_inst, to_inst,
                   evidence_level, status, is_suppressed
            FROM mobility_event WHERE rule_version='{RULE_EVENT}'
            ORDER BY evidence_level, paper_year LIMIT 1000
        """)
        export_csv(conn, "mobility_class_summary.csv", f"""
            SELECT observed_class,
                   count(*) AS observed,
                   count(*) FILTER (WHERE is_classified) AS formal,
                   count(*) FILTER (WHERE NOT is_classified) AS uncertain
            FROM mobility_class WHERE rule_version='{RULE_EVENT}'
            GROUP BY observed_class ORDER BY observed_class
        """)
        export_csv(conn, "institution_points.csv", """
            SELECT institution_id, canonical_name, country_code, city, lat, lon
            FROM institution
            WHERE lat IS NOT NULL AND lon IS NOT NULL
            ORDER BY institution_id LIMIT 5000
        """)
        export_csv(conn, "country_flows.csv", f"""
            SELECT from_country, to_country, count(*) AS total
            FROM mobility_event
            WHERE rule_version='{RULE_EVENT}' AND from_country IS NOT NULL AND to_country IS NOT NULL
            GROUP BY from_country, to_country ORDER BY total DESC
        """)
    finally:
        conn.close()

    (OUT / "run_spss.sps").write_text(SPSS, encoding="utf-8")
    (OUT / "run_stata.do").write_text(STATA, encoding="utf-8")
    (OUT / "arcgis_publish.py").write_text(ARCGIS, encoding="utf-8")
    (OUT / "README.md").write_text(
        "# 本地软件协同数据模板（第二期）\n\n"
        "本目录由 `pipeline/export_local_tools.py` 生成，供 SPSS / Stata / ArcGIS 本地调度使用。\n"
        "数据为 1,000 人试跑快照，不代表全量交付。脚本仅生成标准输入与调用模板，"
        "不会自动执行本机软件，也不伪造软件输出。\n\n"
        "- `data/did_panel.csv`：DID 组-年面板\n"
        "- `data/did_robust_estimate.csv` / `did_robust_annual_effect.csv`：聚类稳健估计与逐年效应\n"
        "- `data/mobility_event_sample.csv`：事件样本（年份多为署名代理年）\n"
        "- `data/mobility_class_summary.csv`：M1–M8 分类分布\n"
        "- `data/institution_points.csv` / `country_flows.csv`：机构点与国家流向\n"
        "- `run_spss.sps` / `run_stata.do` / `arcgis_publish.py`：本地脚本模板\n",
        encoding="utf-8")
    print("templates written to", OUT)


if __name__ == "__main__":
    main()
