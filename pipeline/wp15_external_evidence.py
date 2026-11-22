#!/usr/bin/env python3
"""WP15：外部出生国证据采集落库与定案（公开来源、逐条留痕）。

设计边界：
- 只对 WP13 的 high 候选核对；不批量给 896 人贴出生国标签。
- 证据来自公开机构官网/CV/百科/主流媒体，逐条存 URL + 原文摘录 + 身份核对依据。
- verdict 由 external_evidence_rules.decide_verdict 计算，不硬编码；
  eligible_treated 仅 verified/probable 为真。
- 幂等：按 rule_version 先删后插；重跑不改变人工复核结论（本表只由本脚本管）。
- 不修改 cohort_member.birth_country（保持 NULL，除非已有 birth_hint）；
  是否进 treated 由 cohort_external_review.eligible_treated 决定，WP14 会读取。

运行：python wp15_external_evidence.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import time

from cohort_rules import COHORT_RULE_VERSION
from config import batch_dir
from dbutil import run_psql
from external_evidence_rules import (
    EXTERNAL_RULE_VERSION,
    TREATED_VERDICTS,
    decide_verdict,
    is_treated,
)
from external_evidence_seed import SUBJECTS, all_items

RV = EXTERNAL_RULE_VERSION


def _lit(s: str) -> str:
    """SQL 单引号字面量：只转义单引号，不含用户输入拼接风险（种子受控）。"""
    return "'" + s.replace("'", "''") + "'"


def build_sql() -> str:
    items = all_items()
    lines = ["BEGIN;",
             f"DELETE FROM external_birth_evidence WHERE rule_version='{RV}';",
             f"DELETE FROM cohort_external_review WHERE rule_version='{RV}';"]

    for subj in SUBJECTS:
        for it in subj.items:
            lines.append(
                "INSERT INTO external_birth_evidence "
                "(scholar_id, rule_version, source_type, source_url, source_title, quote_text, "
                " claim, birth_country, weight, identity_check) VALUES ("
                f"{it.scholar_id}, {_lit(RV)}, {_lit(it.source_type)}, {_lit(it.source_url)}, "
                f"{_lit(it.source_title)}, {_lit(it.quote_text)}, {_lit(it.claim)}, "
                f"{_lit(it.birth_country) if it.birth_country else 'NULL'}, "
                f"{it.weight}, {_lit(subj.identity_check)});"
            )

    for subj in SUBJECTS:
        verdict = decide_verdict(subj.identity_check, list(subj.items))
        birth_country = next(
            (it.birth_country for it in subj.items if it.claim == 'birth_place_cn'), None
        )
        lines.append(
            "INSERT INTO cohort_external_review "
            "(scholar_id, rule_version, display_name, orcid, identity_check, identity_note, "
            " verdict, eligible_treated, source_count, birth_country, review_note) VALUES ("
            f"{subj.scholar_id}, {_lit(RV)}, {_lit(subj.display_name)}, {_lit(subj.orcid)}, "
            f"{_lit(subj.identity_check)}, {_lit(subj.identity_note)}, {_lit(verdict)}, "
            f"{'true' if is_treated(verdict) else 'false'}, {len(subj.items)}, "
            f"{_lit(birth_country) if birth_country else 'NULL'}, {_lit(subj.review_note)});"
        )
    lines.append("COMMIT;")
    return "\n".join(lines)


VERIFY_SQL = f"""
SELECT verdict, eligible_treated, count(*) AS n
FROM cohort_external_review WHERE rule_version='{RV}'
GROUP BY 1,2 ORDER BY 1;

SELECT cr.scholar_id, cr.display_name, cr.identity_check, cr.verdict,
       cr.eligible_treated, cr.source_count,
       count(ce.evidence_id) AS evidence_rows
FROM cohort_external_review cr
LEFT JOIN external_birth_evidence ce
  ON ce.scholar_id=cr.scholar_id AND ce.rule_version='{RV}'
WHERE cr.rule_version='{RV}'
GROUP BY 1,2,3,4,5,6 ORDER BY cr.scholar_id;

-- 高置信度算法队列中，外部证据不支持/身份未定的，必须被剔除
SELECT cm.scholar_id, s.display_name, cm.confidence, cr.verdict
FROM cohort_member cm
JOIN scholar s USING(scholar_id)
LEFT JOIN cohort_external_review cr USING(scholar_id)
WHERE cm.confidence='high'
ORDER BY cm.scholar_id;
"""

EXPORT_SQL = f"""
COPY (
  SELECT cr.scholar_id, cr.display_name, cr.identity_check, cr.verdict,
         cr.eligible_treated, cr.birth_country, cr.review_note, cr.identity_note,
         ce.source_type, ce.source_url, ce.source_title, ce.quote_text, ce.claim, ce.weight
  FROM cohort_external_review cr
  LEFT JOIN external_birth_evidence ce
    ON ce.scholar_id=cr.scholar_id AND ce.rule_version=cr.rule_version
  WHERE cr.rule_version='{RV}'
  ORDER BY cr.scholar_id, ce.claim, ce.source_url
) TO STDOUT WITH CSV HEADER;
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"=== WP15 外部出生国证据（rule_version={RV}）===")
    print("规则：官方/CV/百科逐条留 URL 与原文；身份锚点先于内容；不臆造出生国。")

    print("\n=== 拟定案（未写库）===")
    for subj in SUBJECTS:
        verdict = decide_verdict(subj.identity_check, list(subj.items))
        flag = "TREATED" if verdict in TREATED_VERDICTS else "EXCLUDE"
        print(f"  {subj.scholar_id:>4} {subj.display_name:<18} "
              f"identity={subj.identity_check:<8} verdict={verdict:<20} {flag}")

    if args.dry_run:
        print("\n[DRY RUN] 未写库。")
        return

    t0 = time.time()
    run_psql(build_sql(), name="wp15_build", timeout=300)
    print(f"\n落库用时 {time.time()-t0:.1f}s")

    print("\n=== 核验 ===")
    run_psql(VERIFY_SQL, name="wp15_verify")

    out = batch_dir()
    csv_text = run_psql(EXPORT_SQL, name="wp15_export", verbose=False)
    path = out / "external_birth_evidence.csv"
    path.write_text(csv_text, encoding="utf-8")
    print(f"证据审计导出：{path}")


if __name__ == "__main__":
    main()
