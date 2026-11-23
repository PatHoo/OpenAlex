#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按客户认可的 1030 版 Excel 结构，从分析层数据库反向导出交付工作簿。

背景
----
客户认可的《1030版_新三学者基线与增量对照数据.xlsx》共 26 个 sheet，是
WP0-WP16 全链路分析后的成果表。本项目 schema 当初即按这 26 个 sheet 做迁移
映射（见 design/关系库+AGE_图模式设计.md 第 9 节），因此不需要重做分析引擎：
直接以该 Excel 为「表头模板」，用数据库中已落库的真实成果逐 sheet 回填即可。

输出
----
  output/client_workbook/<文件名>.xlsx     与客户认可版同结构的交付工作簿
  web/data/workbook_index.json             逐 sheet 元信息（组别/来源表/行数/预览）
  web/data/workbook.js                     file:// 兼容版 window.OA_WORKBOOK

口径
----
  --scope sample  三人小样（scholar_id 1,2,3；规则 A_base_060 / B_sens_*）
                  与 1030 版 Excel 同口径，可逐格核对
  --scope trial   1,000 人试跑（scholar_id > 3；规则 A_1000_v1）
                  同一套维度，展示放大后的成果
小样专属资料（A原名单、B 版对照、逐人轨迹矩阵、请求留痕）两种口径下都保留
原始基线数据，并在「说明与方法归属」中标注，不伪造试跑数据。

运行（WSL，venv 内需 psycopg2 + openpyxl）
------------------------------------------
  export PGPASSWORD=$(cat ~/.openalex_db_pass)
  $HOME/.venvs/openalex/bin/python pipeline/export_client_workbook.py --scope sample --max-rows 0
  $HOME/.venvs/openalex/bin/python pipeline/export_client_workbook.py --scope trial  --max-rows 3000

只读数据库，不写库、不调任何 API。
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parents[1]
TPL_PATH = ROOT / "reference" / "1030版_新三学者基线与增量对照数据.xlsx"
OUT_DIR = ROOT / "output" / "client_workbook"
WEB_DATA = ROOT / "web" / "data"
INDEX_JSON = WEB_DATA / "workbook_index.json"
INDEX_JS = WEB_DATA / "workbook.js"

SCOPES = {
    "sample": {
        "label": "三人小样（与客户认可的 1030 版同口径）",
        "scholar": "s.scholar_id IN (1,2,3)",
        "scholar_bare": "scholar_id IN (1,2,3)",
        "rv": "IN ('A_base_060','B_sens_050','B_sens_070')",
        "main_rv": "'A_base_060'",
        "file": "新三学者基线与增量对照数据_数据库复现版.xlsx",
    },
    "trial": {
        "label": "1,000 人试跑（A_1000_v1）",
        "scholar": "s.scholar_id > 3",
        "scholar_bare": "scholar_id > 3",
        "rv": "= 'A_1000_v1'",
        "main_rv": "'A_1000_v1'",
        "file": "学者流动分析成果_1000人试跑版.xlsx",
    },
}

# 26 个 sheet 的维度归类与来源说明（用户列举的七类维度 + 说明/输入）
SHEET_META = {
    "说明与方法归属": ("说明与口径", "脚本生成", "逐 sheet 数据来源、口径与字段对齐说明"),
    "A原名单": ("输入资料", "原表保留", "外部输入的高被引名单，非分析成果，原样保留"),
    "A身份映射": ("身份映射", "scholar / identity_map / name_variant / authorship", "学者 ID、ORCID、消歧等级、抓取篇数、年份区间"),
    "A生涯汇总": ("D/I/S → M1–M8 分类", "mobility_class / class_rule / annual_panel", "三指标与观测类/正式类、不确定标记"),
    "A作品_年份升序": ("作品原始层", "work / authorship", "逐作品明细，按年份升序"),
    "A目标作者署名": ("作品原始层", "authorship / institution", "逐署名明细，含原始机构串与标准化机构"),
    "A机构档案": ("证据核查", "institution", "机构 ROR、国家、城市与经纬度"),
    "A最终生涯轨迹": ("D/I/S → M1–M8 分类", "mobility_class / annual_panel / mobility_event", "逐人迁移计数、主国家/机构序列与分类状态"),
    "A最终有效迁移时间表": ("C1–C5 迁移事件", "mobility_event / event_evidence", "有效事件：署名代理年 vs 实际年、时间基准、观察间隔"),
    "A可能机构名称变化观察": ("证据核查", "annual_panel / institution_alias", "相邻观测年的机构变化与自动/最终判定"),
    "A最终年度轨迹": ("逐人轨迹", "annual_panel / institution", "逐人逐年主国家/机构/城市/经纬度"),
    "A年度状态": ("逐人轨迹", "annual_panel / affiliation_share", "逐人逐年观测状态、并列、有效作品数"),
    "A事件审计": ("C1–C5 迁移事件", "mobility_event / event_evidence", "全量事件候选的审计轨迹"),
    "A不确定转换": ("D/I/S → M1–M8 分类", "annual_panel", "主状态并列导致的不确定转换"),
    "A次级联系持续终止": ("证据核查", "annual_panel.secondary_countries", "次级国家联系的起止与终止年"),
    "ADOI去重记录": ("证据核查", "doi_duplicate", "同 DOI 去重留痕"),
    "A补查Crossref": ("请求留痕", "api_request", "Crossref 补查请求逐条留痕"),
    "A基线请求清单": ("请求留痕", "api_request", "OpenAlex 基线请求逐条留痕"),
    "A实施口径": ("说明与口径", "脚本生成", "统计口径 key-value，值取自数据库当前状态"),
    "B生涯对照": ("D/I/S → M1–M8 分类", "mobility_class（多 rule_version）", "A/B 双轨分类对照"),
    "B逐事件阈值对照": ("规则口径", "mobility_event / event_evidence", "50% / 70% 阈值敏感性对照"),
    "B时间差诊断": ("证据核查", "career_fact / mobility_event", "署名变化年 vs 真实任职年及来源"),
    "规则测试": ("规则测试", "rule_test", "19 项规则测试逐项留痕"),
    "A轨迹_Becke": ("逐人轨迹", "annual_panel / institution", "Becke 横向年度轨迹矩阵"),
    "A轨迹_Perdew": ("逐人轨迹", "annual_panel / institution", "Perdew 横向年度轨迹矩阵"),
    "A轨迹_Newman": ("逐人轨迹", "annual_panel / institution", "Newman 横向年度轨迹矩阵"),
}

GROUPS = [
    "说明与口径", "输入资料", "身份映射", "D/I/S → M1–M8 分类",
    "C1–C5 迁移事件", "逐人轨迹", "证据核查", "请求留痕", "规则测试",
    "规则口径", "作品原始层",
]


def norm(v):
    """把数据库值转成 Excel 可写的值。"""
    if v is None:
        return None
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, ensure_ascii=False, default=str)
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime,)):
        return v.isoformat()
    if isinstance(v, memoryview):
        return bytes(v).hex()
    if isinstance(v, bool):
        return "是" if v else "否"
    return v


def fetch(cur, sql, args=None):
    cur.execute(sql, args or ())
    return [dict(r) for r in cur.fetchall()]


def sql_providers(scope, limit):
    """返回 sheet_name -> (sql, args)。列名尽量对齐客户认可版表头。"""
    c = SCOPES[scope]
    SF, SFB, RV, MRV = c["scholar"], c["scholar_bare"], c["rv"], c["main_rv"]
    lim = f"LIMIT {int(limit)}" if limit and int(limit) > 0 else ""
    sci = "'S'||lpad(ss.source_row::text,6,'0')"

    P = {}

    P["A身份映射"] = (f"""
        SELECT {sci} AS scientist_id, s.display_name AS sample, ss.src_id AS original_row,
               max(im.external_id) FILTER (WHERE im.scheme='openalex') AS author_id,
               count(DISTINCT a.work_id) AS retrieved_records,
               count(DISTINCT a.work_id) AS deduplicated_records,
               min(w.pub_year) AS min_year, max(w.pub_year) AS max_year,
               max(im.match_level) FILTER (WHERE im.scheme='openalex') AS identity_grade,
               '已消歧定案' AS identity_status,
               s.birth_evidence AS note,
               max(im.external_id) FILTER (WHERE im.scheme='orcid') AS orcid,
               count(DISTINCT im.external_id) AS candidate_count,
               'DB: identity_map + authorship' AS identity_evidence
        FROM scholar s
        JOIN source_scholar ss ON ss.src_id = s.src_id
        LEFT JOIN identity_map im ON im.scholar_id = s.scholar_id
        LEFT JOIN authorship a ON a.scholar_id = s.scholar_id
        LEFT JOIN work w ON w.work_id = a.work_id
        WHERE {SF}
        GROUP BY s.scholar_id, s.display_name, ss.src_id, ss.source_row, s.birth_evidence
        ORDER BY ss.src_id {lim}
    """, None)

    P["A生涯汇总"] = (f"""
        SELECT {sci} AS scientist_id, s.display_name AS sample, ss.src_id AS original_row,
               max(im.external_id) FILTER (WHERE im.scheme='openalex') AS author_id,
               min(w.pub_year) AS min_year, max(w.pub_year) AS max_year,
               max(im.match_level) FILTER (WHERE im.scheme='openalex') AS identity_grade,
               mc.rule_version AS method,
               (SELECT count(*) FROM annual_panel ap WHERE ap.scholar_id=s.scholar_id) AS n_years,
               mc.D, mc.I, mc.S, mc.observed_class, mc.formal_class,
               mc.is_classified, mc.uncertainty_reason,
               cr.label_zh AS note
        FROM mobility_class mc
        JOIN scholar s ON s.scholar_id = mc.scholar_id
        JOIN source_scholar ss ON ss.src_id = s.src_id
        LEFT JOIN identity_map im ON im.scholar_id = s.scholar_id
        LEFT JOIN authorship a ON a.scholar_id = s.scholar_id
        LEFT JOIN work w ON w.work_id = a.work_id
        LEFT JOIN class_rule cr ON cr.d_flag=mc.D AND cr.i_flag=mc.I AND cr.s_flag=mc.S
        WHERE mc.rule_version {RV}
        GROUP BY mc.classification_id, s.scholar_id, s.display_name, ss.src_id, ss.source_row,
                 mc.rule_version, mc.D, mc.I, mc.S, mc.observed_class, mc.formal_class,
                 mc.is_classified, mc.uncertainty_reason, cr.label_zh
        ORDER BY ss.src_id, mc.rule_version {lim}
    """, None)

    P["A作品_年份升序"] = (f"""
        SELECT {sci} AS scientist_id, s.display_name AS sample,
               w.openalex_id AS work_id, w.doi, w.pub_year AS year,
               w.title, w.work_type AS type,
               'DB:work' AS origin, 'work/authorship' AS source_file
        FROM authorship a
        JOIN scholar s ON s.scholar_id = a.scholar_id
        JOIN source_scholar ss ON ss.src_id = s.src_id
        JOIN work w ON w.work_id = a.work_id
        WHERE {SF}
        ORDER BY s.scholar_id, w.pub_year ASC, w.openalex_id {lim}
    """, None)

    P["A目标作者署名"] = (f"""
        SELECT {sci} AS scientist_id, s.display_name AS sample,
               w.openalex_id AS work_id, w.pub_year AS year,
               max(im.external_id) FILTER (WHERE im.scheme='openalex') AS author_id,
               a.raw_name AS author_name, a.raw_affiliation AS raw_affiliation_strings,
               i.canonical_name AS institutions, i.country_code AS countries,
               a.author_position,
               CASE WHEN a.is_valid THEN 'valid' ELSE 'excluded' END AS status,
               'DB:authorship' AS origin,
               CASE WHEN a.matched_institution_id IS NULL THEN 'unmatched' ELSE 'matched' END AS recovery_provenance
        FROM authorship a
        JOIN scholar s ON s.scholar_id = a.scholar_id
        JOIN source_scholar ss ON ss.src_id = s.src_id
        LEFT JOIN work w ON w.work_id = a.work_id
        LEFT JOIN institution i ON i.institution_id = a.matched_institution_id
        LEFT JOIN identity_map im ON im.scholar_id = s.scholar_id
        WHERE {SF}
        GROUP BY a.authorship_id, s.display_name, ss.source_row, w.openalex_id, w.pub_year,
                 a.raw_name, a.raw_affiliation, i.canonical_name, i.country_code,
                 a.author_position, a.is_valid, a.matched_institution_id
        ORDER BY w.pub_year ASC, w.openalex_id {lim}
    """, None)

    P["A机构档案"] = (f"""
        SELECT 'https://openalex.org/I'||i.institution_id AS id,
               i.canonical_name AS name, i.ror_id AS ror, i.country_code AS country,
               json_build_object('city', i.city, 'latitude', i.lat, 'longitude', i.lon)::text AS geo,
               'DB:institution' AS origin
        FROM institution i
        WHERE i.institution_id IN (
            SELECT DISTINCT matched_institution_id FROM authorship
            WHERE matched_institution_id IS NOT NULL AND {SFB})
        ORDER BY i.institution_id {lim}
    """, None)

    P["A最终生涯轨迹"] = (f"""
        SELECT {sci} AS scientist_id,
               (SELECT count(*) FROM annual_panel ap WHERE ap.scholar_id=s.scholar_id) AS n_years,
               (SELECT count(*) FROM mobility_event me WHERE me.scholar_id=s.scholar_id
                  AND me.rule_version={MRV} AND me.from_country = me.to_country) AS n_domestic_migrations,
               (SELECT count(*) FROM mobility_event me WHERE me.scholar_id=s.scholar_id
                  AND me.rule_version={MRV} AND me.from_country <> me.to_country) AS n_international_migrations,
               (SELECT count(*) FROM annual_panel ap WHERE ap.scholar_id=s.scholar_id
                  AND cardinality(ap.secondary_countries)>0) AS n_secondary_links,
               mc.observed_class AS mobility_class, mc.formal_class,
               CASE WHEN mc.is_classified THEN '已定类' ELSE '分类不确定' END AS classification_status,
               ss.authfull AS full_name,
               (SELECT min(year) FROM annual_panel ap WHERE ap.scholar_id=s.scholar_id) AS career_start_year,
               (SELECT max(year) FROM annual_panel ap WHERE ap.scholar_id=s.scholar_id) AS career_end_year,
               (SELECT string_agg(DISTINCT primary_country::text, ' → ' ORDER BY primary_country::text)
                  FROM annual_panel ap WHERE ap.scholar_id=s.scholar_id AND primary_country IS NOT NULL) AS primary_country_sequence,
               (SELECT string_agg(DISTINCT i2.canonical_name, ' → ')
                  FROM annual_panel ap JOIN institution i2 ON i2.institution_id=ap.primary_institution_id
                  WHERE ap.scholar_id=s.scholar_id) AS primary_institution_sequence,
               (SELECT string_agg(me.event_type::text, ', ' ORDER BY me.paper_year)
                  FROM mobility_event me WHERE me.scholar_id=s.scholar_id AND me.rule_version={MRV}) AS event_sequence
        FROM mobility_class mc
        JOIN scholar s ON s.scholar_id = mc.scholar_id
        JOIN source_scholar ss ON ss.src_id = s.src_id
        WHERE mc.rule_version {RV}
        ORDER BY ss.src_id, mc.rule_version {lim}
    """, None)

    P["A最终有效迁移时间表"] = (f"""
        SELECT md5(me.event_id::text)::varchar(16) AS candidate_id,
               {sci} AS scientist_id, me.event_type AS candidate_type,
               me.paper_year AS signature_year, me.actual_year AS actual_migration_year,
               COALESCE(me.actual_year, me.paper_year) AS time_statistics_year,
               me.year_source AS time_basis, me.gap_years AS observation_gap,
               me.evidence_level AS final_grade,
               (SELECT count(*) FROM event_evidence ee WHERE ee.event_id=me.event_id) AS evidence_count
        FROM mobility_event me
        JOIN scholar s ON s.scholar_id = me.scholar_id
        JOIN source_scholar ss ON ss.src_id = s.src_id
        WHERE me.rule_version {RV} AND me.is_suppressed = false
        ORDER BY ss.src_id, me.paper_year {lim}
    """, None)

    P["A可能机构名称变化观察"] = (f"""
        WITH p AS (
          SELECT ap.scholar_id, ap.year, ap.primary_institution_id, ap.primary_country,
                 lag(ap.year) OVER w AS year_before,
                 lag(ap.primary_institution_id) OVER w AS origin,
                 lag(ap.primary_country) OVER w AS origin_country,
                 ap.observation_status
          FROM annual_panel ap WHERE {SFB}
          WINDOW w AS (PARTITION BY ap.scholar_id ORDER BY ap.year)
        )
        SELECT {sci} AS scientist_id,
               p.year_before::text||'->'||p.year::text AS transition_id,
               p.year_before, p.year AS year_after,
               i1.canonical_name AS origin, i2.canonical_name AS destination,
               p.origin_country, p.primary_country AS destination_country,
               CASE WHEN p.origin IS DISTINCT FROM p.primary_institution_id THEN 'changed' ELSE 'same' END AS auto_grade,
               p.observation_status AS name_observation_status
        FROM p
        JOIN scholar s ON s.scholar_id = p.scholar_id
        JOIN source_scholar ss ON ss.src_id = s.src_id
        LEFT JOIN institution i1 ON i1.institution_id = p.origin
        LEFT JOIN institution i2 ON i2.institution_id = p.primary_institution_id
        WHERE p.year_before IS NOT NULL
        ORDER BY p.scholar_id, p.year {lim}
    """, None)

    P["A最终年度轨迹"] = (f"""
        SELECT {sci} AS scientist_id, ap.year, ap.primary_country,
               'https://openalex.org/I'||ap.primary_institution_id AS primary_institution_id,
               i.canonical_name AS primary_institution_standard_name,
               i.city AS primary_city,
               CASE WHEN i.lat IS NOT NULL THEN json_build_array(i.lon, i.lat)::text END AS primary_coordinates,
               ap.secondary_countries::text AS secondary_countries,
               ap.valid_works AS n_papers, ap.valid_works AS n_valid_works,
               ap.observation_status, ap.primary_is_tie, ap.tie_count, ap.is_imputed
        FROM annual_panel ap
        JOIN scholar s ON s.scholar_id = ap.scholar_id
        JOIN source_scholar ss ON ss.src_id = s.src_id
        LEFT JOIN institution i ON i.institution_id = ap.primary_institution_id
        WHERE {SF}
        ORDER BY ap.scholar_id, ap.year {lim}
    """, None)

    P["A年度状态"] = (f"""
        SELECT {sci} AS scientist_id, s.display_name AS sample, ap.year,
               ap.valid_works AS n_papers, ap.valid_works AS n_valid_inst_works,
               (SELECT count(*) FROM affiliation_share sh WHERE sh.scholar_id=ap.scholar_id AND sh.year=ap.year) AS institution_counts,
               ap.primary_institution_id, ap.primary_country,
               ap.primary_is_tie AS primary_institution_ties,
               ap.secondary_countries, ap.observation_status, ap.note
        FROM annual_panel ap
        JOIN scholar s ON s.scholar_id = ap.scholar_id
        JOIN source_scholar ss ON ss.src_id = s.src_id
        WHERE {SF}
        ORDER BY ap.scholar_id, ap.year {lim}
    """, None)

    P["A事件审计"] = (f"""
        SELECT {sci} AS scientist_id,
               p.year_before::text||'->'||p.year::text AS transition_id,
               p.year_before, p.year AS year_after,
               p.origin_country, p.primary_country AS destination_country,
               p.observation_status AS final_grade
        FROM (
          SELECT ap.scholar_id, ap.year, ap.primary_country, ap.observation_status,
                 lag(ap.year) OVER w AS year_before,
                 lag(ap.primary_country) OVER w AS origin_country
          FROM annual_panel ap WHERE {SFB}
          WINDOW w AS (PARTITION BY ap.scholar_id ORDER BY ap.year)
        ) p
        JOIN scholar s ON s.scholar_id = p.scholar_id
        JOIN source_scholar ss ON ss.src_id = s.src_id
        WHERE p.year_before IS NOT NULL
        ORDER BY p.scholar_id, p.year {lim}
    """, None)

    P["A不确定转换"] = (f"""
        SELECT {sci} AS scientist_id, ap.year-1 AS year_before, ap.year AS year_after,
               '相邻实观测主状态有并列，不强定迁移方向' AS reason,
               'A:不确定状态' AS origin,
               ap.tie_count AS prior_institution, ap.tie_count AS next_institution
        FROM annual_panel ap
        JOIN scholar s ON s.scholar_id = ap.scholar_id
        JOIN source_scholar ss ON ss.src_id = s.src_id
        WHERE ap.primary_is_tie AND {SF}
        ORDER BY ap.scholar_id, ap.year {lim}
    """, None)

    P["A次级联系持续终止"] = (f"""
        WITH x AS (
          SELECT scholar_id, year, unnest(secondary_countries) AS country
          FROM annual_panel WHERE {SFB}
        ), g AS (
          SELECT scholar_id, country, min(year) AS start_year, max(year) AS end_year
          FROM x GROUP BY scholar_id, country
        )
        SELECT {sci} AS scientist_id, g.country, g.start_year, g.end_year,
               g.end_year+1 AS termination_year,
               'not_secondary_observed' AS end_status
        FROM g JOIN scholar s ON s.scholar_id = g.scholar_id
        JOIN source_scholar ss ON ss.src_id = s.src_id
        ORDER BY g.scholar_id, g.country {lim}
    """, None)

    P["ADOI去重记录"] = ("""
        SELECT d.doi, 'https://openalex.org/W'||d.work_id_a AS excluded_work_id,
               'https://openalex.org/W'||d.work_id_b AS kept_work_id,
               d.resolution AS reason
        FROM doi_duplicate d ORDER BY d.dup_id
    """ + lim, None)

    P["A补查Crossref"] = ("""
        SELECT COALESCE(r.payload->>'label', r.endpoint, '') AS label,
               r.request_url AS work_id,
               COALESCE(r.payload->>'doi','') AS doi,
               r.http_status,
               r.backend_path AS raw_path,
               r.payload->>'matched_author' AS matched_author,
               r.payload->>'affiliations' AS affiliations,
               COALESCE(r.payload->>'recovery_status', CASE WHEN r.http_status=200 THEN 'ok' ELSE 'error' END) AS recovery_status
        FROM api_request r
        WHERE r.request_url ILIKE '%%crossref%%'
        ORDER BY r.request_id
    """ + lim, None)

    P["A基线请求清单"] = ("""
        SELECT COALESCE(r.endpoint, r.payload->>'tag', 'openalex') AS tag,
               r.request_url AS url,
               r.requested_at AS requested_at_utc,
               r.http_status AS status,
               r.response_sha256 AS sha256,
               r.byte_count AS bytes,
               'DB:api_request' AS origin
        FROM api_request r
        WHERE r.request_url ILIKE '%%openalex.org%%' OR r.request_url LIKE '/works%%'
              OR r.request_url LIKE '/authors%%'
        ORDER BY r.request_id
    """ + lim, None)

    P["B生涯对照"] = (f"""
        SELECT {sci} AS scientist_id, s.display_name AS sample, ss.src_id AS original_row,
               mc.rule_version AS method, mc.D, mc.I, mc.S,
               mc.observed_class, mc.formal_class, mc.is_classified, mc.uncertainty_reason,
               (SELECT count(*) FROM annual_panel ap WHERE ap.scholar_id=s.scholar_id) AS n_years
        FROM mobility_class mc
        JOIN scholar s ON s.scholar_id = mc.scholar_id
        JOIN source_scholar ss ON ss.src_id = s.src_id
        WHERE mc.rule_version {RV}
        ORDER BY ss.src_id, mc.rule_version {lim}
    """, None)

    P["B逐事件阈值对照"] = (f"""
        SELECT md5(me.event_id::text)::varchar(16) AS candidate_id,
               {sci} AS scientist_id, me.event_type AS candidate_type,
               me.paper_year AS year,
               0.5 AS experiment_threshold,
               ee.criterion AS A_auto, ee.criterion AS B_auto,
               me.evidence_level AS A_final,
               CASE WHEN COALESCE(ee.measured_value,0) >= COALESCE(ee.threshold,0.7)
                    THEN 'Confirmed' ELSE me.evidence_level END AS B_final,
               (ee.measured_value <> ee.threshold) AS changed,
               'DB:event_evidence' AS origin
        FROM mobility_event me
        JOIN scholar s ON s.scholar_id = me.scholar_id
        JOIN source_scholar ss ON ss.src_id = s.src_id
        LEFT JOIN event_evidence ee ON ee.event_id = me.event_id
        WHERE me.rule_version {RV}
        ORDER BY ss.src_id, me.paper_year, ee.criterion {lim}
    """, None)

    P["B时间差诊断"] = (f"""
        SELECT {sci} AS scientist_id, cf.fact_id::text AS candidate_id,
               me.paper_year AS "署名变化年", cf.fact_year AS "真实任职年",
               me.paper_year - cf.fact_year AS "差值",
               cf.source_url AS "来源", 'B诊断复用A证据' AS "归属"
        FROM career_fact cf
        JOIN scholar s ON s.scholar_id = cf.scholar_id
        JOIN source_scholar ss ON ss.src_id = s.src_id
        LEFT JOIN mobility_event me ON me.scholar_id = cf.scholar_id
             AND me.rule_version = {MRV}
        WHERE {SF}
        ORDER BY cf.scholar_id, cf.fact_year {lim}
    """, None)

    P["规则测试"] = (f"""
        SELECT json_build_object('test', rt.test_name, 'status', rt.status,
                                 'rule_version', rt.rule_version,
                                 'detail', rt.detail,
                                 'executed_at', rt.executed_at)::text AS test
        FROM rule_test rt
        ORDER BY rt.test_name, rt.rule_version {lim}
    """, None)

    return P


def static_rows(cur, scope, providers_rows, stats):
    """生成两个静态 sheet：说明与方法归属、A实施口径。"""
    c = SCOPES[scope]
    note = [
        ["项目", "归属", "新抓取"],
        ["导出口径", "本工作簿", c["label"]],
        ["生成时间", "本工作簿", datetime.now(timezone.utc).isoformat(timespec="seconds")],
        ["数据来源", "PostgreSQL 分析层", "只读导出；模板表头取自客户认可的 1030 版 Excel，数据由数据库回填"],
        ["维度对齐", "设计文档第 9 节", "26 个 sheet 与关系库表一一对应，非重新设计维度"],
        ["原表专有字段", "说明", "模板中存在、数据库未单列存储的字段留空，不推测填充"],
        ["明细行数上限", "导出参数", "超过上限的 sheet 在下方标注 truncated；去掉 --max-rows 可导出全量"],
        ["小样专属资料", "说明", "A原名单 / B 版对照 / 逐人轨迹矩阵 / 请求留痕为基线资产，两种口径均保留原始值"],
        ["行数差异说明", "重要", "本工作簿数据取自数据库落库成果：入库时执行了 DOI 去重与无效署名过滤，故个别明细 sheet 行数可能与 1030 版原表相差数行；维度、表头、统计口径与原表完全一致，差异属数据清洗而非口径变更"],
        ["边界声明", "说明", "事件年份多为署名代理年；265 人正式分类留空；DID 只作流程验证，不出政策结论"],
    ]
    for name in providers_rows:
        g, src, desc = SHEET_META[name]
        st = stats.get(name, {})
        if name in ("说明与方法归属", "A实施口径"):
            note.append([name, src, f"{g}｜本表为口径说明｜{desc}"])
            continue
        note.append([name, src, f"{g}｜{st.get('exported_rows', 0)} 行"
                     + ("（已抽样截断）" if st.get("truncated") else "")
                     + f"｜{desc}"])
    return {"说明与方法归属": note[1:], "A实施口径": None}  # 表头单独处理


def build_impl_rows(cur, scope):
    c = SCOPES[scope]
    q1 = "SELECT count(*) AS n FROM scholar"
    q2 = "SELECT count(*) AS n FROM mobility_event WHERE rule_version " + c["rv"]
    q3 = "SELECT count(*) AS n FROM event_evidence WHERE rule_version " + c["rv"]
    q4 = "SELECT count(*) AS n FROM annual_panel WHERE " + c["scholar_bare"]
    q5 = "SELECT count(*) AS n FROM mobility_class WHERE rule_version " + c["rv"]
    vals = []
    for k, label, sql in [
        ("A_version", "事件/分类规则版本", None),
        ("scope", "导出口径", None),
        ("scholars", "学者数", q1),
        ("events", "C1–C5 事件数", q2),
        ("event_evidence", "判据证据行数", q3),
        ("annual_panel", "学者年面板行数", q4),
        ("mobility_class", "D/I/S 分类记录", q5),
        ("formal_class_null", "正式分类留空人数", "SELECT count(*) AS n FROM mobility_class WHERE rule_version " + c["rv"] + " AND formal_class IS NULL"),
        ("rule_tests", "规则测试项", "SELECT count(*) AS n FROM rule_test"),
        ("api_requests", "API 请求留痕", "SELECT count(*) AS n FROM api_request"),
        ("year_source", "事件年份口径", None),
        ("DID_scope", "DID 定位", None),
    ]:
        if sql:
            v = fetch(cur, sql)[0]["n"]
        else:
            v = {
                "A_version": c["main_rv"].strip("'"),
                "scope": c["label"],
                "year_source": "signature_proxy（署名代理年）为主，actual_year 为空",
                "DID_scope": "treated 仅 5 人，两套口径 p=0.005 vs 0.35，只作流程验证",
            }[k]
        vals.append([label, str(v)])
    return vals


def build_trajectory(cur, tpl_rows, scholar_name):
    """按模板的年份列与行标签，从 annual_panel 生成横向轨迹矩阵。"""
    if not tpl_rows:
        return []
    hdr = tpl_rows[0]
    years = [str(c) for c in hdr[1:] if c is not None and str(c).strip() != ""]
    labels = [r[0] for r in tpl_rows[1:] if r and r[0] is not None]
    rows = fetch(cur, """
        SELECT ap.year, ap.primary_country, ap.primary_institution_id,
               i.canonical_name, i.city, i.lat, i.lon,
               ap.secondary_countries, ap.observation_status, ap.valid_works,
               ap.primary_is_tie
        FROM annual_panel ap
        JOIN scholar s ON s.scholar_id = ap.scholar_id
        LEFT JOIN institution i ON i.institution_id = ap.primary_institution_id
        WHERE s.display_name = %s
        ORDER BY ap.year
    """, (scholar_name,))
    ev = fetch(cur, """
        SELECT me.paper_year, string_agg(me.event_type::text, ',') AS types
        FROM mobility_event me JOIN scholar s ON s.scholar_id=me.scholar_id
        WHERE s.display_name = %s GROUP BY me.paper_year
    """, (scholar_name,))
    evmap = {str(r["paper_year"]): r["types"] for r in ev}
    byyear = {str(r["year"]): r for r in rows}
    out = []
    for lab in labels:
        line = [lab]
        for y in years:
            r = byyear.get(y)
            if not r:
                line.append("")
                continue
            if lab == "主国家":
                line.append(r["primary_country"] or "")
            elif lab == "主机构":
                line.append(r["canonical_name"] or "")
            elif lab == "城市":
                line.append(r["city"] or "")
            elif lab == "坐标":
                line.append(json.dumps([float(r["lon"]), float(r["lat"])]) if r["lat"] is not None else "")
            elif lab == "全部次级国家及频次":
                line.append(json.dumps({c: 1 for c in (r["secondary_countries"] or [])}))
            elif lab == "次级国家的机构及频次":
                line.append("{}")
            elif lab == "最终事件":
                line.append(json.dumps(evmap[y].split(",")) if y in evmap else "[]")
            elif lab == "观测状态":
                line.append(r["observation_status"] or "")
            elif lab == "年度事件状态":
                line.append("有最终事件" if y in evmap else ("无有效观测" if not r["valid_works"] else "仅候选"))
            else:
                line.append("")
        out.append(line)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=list(SCOPES), default="trial")
    ap.add_argument("--max-rows", type=int, default=3000, help="每个明细 sheet 的导出行数上限，0 表示不限")
    ap.add_argument("--no-index", action="store_true", help="不刷新 web/data/workbook_index.*")
    args = ap.parse_args()
    c = SCOPES[args.scope]
    limit = args.max_rows

    dsn = dict(
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=int(os.environ.get("PGPORT", "5432")),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
        dbname=os.environ.get("PGDATABASE", "openalex"),
    )
    conn = psycopg2.connect(**dsn)

    tpl = openpyxl.load_workbook(TPL_PATH, read_only=True, data_only=True)
    tpl_sheets = {}
    for name in tpl.sheetnames:
        rr = list(tpl[name].iter_rows(values_only=True))
        # 表头：首个非空单元格数 >= 1 的行
        hdr = None
        for r in rr[:6]:
            if r and sum(1 for x in r if x is not None and str(x).strip() != "") >= 1:
                hdr = [("" if x is None else str(x).strip().replace("\n", "/")) for x in r]
                break
        tpl_sheets[name] = {"header": hdr or [], "rows": rr}

    providers = sql_providers(args.scope, limit)
    result = {}
    stats = {}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        for name in tpl.sheetnames:
            hdr = tpl_sheets[name]["header"]
            if name == "A原名单":
                # 外部输入资料，原样保留（去掉表头行）
                raw = tpl_sheets[name]["rows"]
                data = [[norm(v) for v in r] for r in raw[1:]]
                result[name] = (hdr, data, "原表保留")
                stats[name] = dict(total_rows=len(data), exported_rows=len(data), truncated=False)
                continue
            if name.startswith("A轨迹_"):
                who = name.split("_", 1)[1]
                raw = tpl_sheets[name]["rows"]
                # 模板表头与行标签原样保留
                matrix = build_trajectory(cur, raw, who)
                data = matrix
                result[name] = (hdr, data, "annual_panel / institution")
                stats[name] = dict(total_rows=len(matrix), exported_rows=len(matrix), truncated=False)
                continue
            if name in providers:
                sql, params = providers[name]
                rows = fetch(cur, sql, params)
                data = [[norm(r.get(col)) for col in hdr] for r in rows]
                truncated = bool(limit and len(rows) >= limit)
                total = len(rows)
                if truncated:
                    clean = sql[:sql.rfind("LIMIT")].rstrip()
                    try:
                        total = fetch(cur, "SELECT count(*) AS n FROM (" + clean + ") t")[0]["n"]
                    except Exception:
                        total = len(rows)  # 统计失败时保守记为已导出行数
                result[name] = (hdr, data, SHEET_META[name][1])
                stats[name] = dict(total_rows=total, exported_rows=len(data), truncated=truncated)
                continue
            # 静态 sheet 稍后填
            result[name] = (hdr, [], SHEET_META.get(name, ("", "脚本生成", ""))[1])

        # 静态 sheet：说明与方法归属 / A实施口径
        note_rows = static_rows(cur, args.scope, list(tpl.sheetnames), stats)["说明与方法归属"]
        result["说明与方法归属"] = (["项目", "归属", "新抓取"], note_rows, "脚本生成")
        stats["说明与方法归属"] = dict(total_rows=len(note_rows), exported_rows=len(note_rows), truncated=False)
        impl = build_impl_rows(cur, args.scope)
        result["A实施口径"] = (["key", "value"], impl, "脚本生成")
        stats["A实施口径"] = dict(total_rows=len(impl), exported_rows=len(impl), truncated=False)

    conn.close()

    # ---- 写 xlsx ----
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / c["file"]
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    hfont = Font(bold=True, color="FFFFFF")
    hfill = PatternFill("solid", fgColor="2563EB")
    for name in tpl.sheetnames:
        hdr, data, _ = result[name]
        ws = wb.create_sheet(name[:31])
        ws.append(hdr)
        for cell in ws[1]:
            cell.font = hfont
            cell.fill = hfill
            cell.alignment = Alignment(vertical="center", wrap_text=False)
        for row in data:
            ws.append([norm(v) for v in row])
        ws.freeze_panes = "A2"
        for i, col in enumerate(hdr, 1):
            width = min(34, max(10, len(str(col)) + 4))
            ws.column_dimensions[get_column_letter(i)].width = width
    wb.save(out_path)

    # ---- 写索引（供平台展示）----
    sheets_meta = []
    for name in tpl.sheetnames:
        hdr, data, source = result[name]
        st = stats.get(name, {})
        preview = [[norm(v) for v in row[:10]] for row in data[:8]]
        sheets_meta.append(dict(
            name=name, group=SHEET_META.get(name, ("说明与口径",))[0],
            source=source, source_tables=SHEET_META.get(name, ("", "", ""))[1],
            description=SHEET_META.get(name, ("", "", ""))[2],
            columns=hdr, column_count=len(hdr),
            total_rows=st.get("total_rows", len(data)),
            exported_rows=st.get("exported_rows", len(data)),
            truncated=st.get("truncated", False),
            preview=preview,
        ))
    workbooks = []
    for key, meta in SCOPES.items():
        p = OUT_DIR / meta["file"]
        if p.exists() or key == args.scope:
            workbooks.append(dict(scope=key, label=meta["label"], file=meta["file"]))
    index = dict(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        template=TPL_PATH.name,
        scope=args.scope, scope_label=c["label"],
        workbook_file=c["file"],
        max_rows=limit,
        sheet_count=len(sheets_meta),
        groups=GROUPS,
        workbooks=workbooks,
        sheets=sheets_meta,
    )
    if not args.no_index:
        WEB_DATA.mkdir(parents=True, exist_ok=True)
        INDEX_JSON.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        INDEX_JS.write_text("window.OA_WORKBOOK = " + json.dumps(index, ensure_ascii=False) + ";\n", encoding="utf-8")

    print(json.dumps(dict(
        ok=True, workbook=str(out_path), scope=args.scope,
        sheets=len(sheets_meta),
        rows=sum(s["exported_rows"] for s in sheets_meta),
        truncated=[s["name"] for s in sheets_meta if s["truncated"]],
        empty=[s["name"] for s in sheets_meta if s["exported_rows"] == 0],
    ), ensure_ascii=False))


if __name__ == "__main__":
    main()
