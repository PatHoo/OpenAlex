#!/usr/bin/env python3
"""WP16：DID 稳健统计——学者层聚类稳健 SE + 平行趋势联合 F 检验。

与 wp14 的区别：
- wp14 用四个组-期单元的独立方差近似，只作描述；本脚本把标准误聚类到学者层，
  承认同一学者跨年重复观测相关。
- wp14 平行趋势只逐 lead 看 |t|<1.96；本脚本对 2014/2015/2016 三个 lead 做联合 Wald F。
- 两种口径并存，便于甲方核对差异；不覆盖 wp14 结果。

输入：wp14 导出的 did_panel.csv（rule_version 由代码常量固定）。
输出：did_robust_estimate / did_robust_annual_effect 表 + 可视化 CSV。
统计核心在 did_stats.py（纯标准库，可单测复算）。

运行：python wp16_did_robust.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import csv
import math
import time
from pathlib import Path

from cohort_rules import (
    BASELINE_YEAR,
    COHORT_RULE_VERSION,
    POST_START_YEAR,
)
from config import batch_dir
from dbutil import run_psql
from did_stats import OLSClusterResult, ols_cluster, wald_test

RV = "DID_2018_A1000_v2"
CRV = COHORT_RULE_VERSION
PRE_YEARS = (2014, 2015, 2016, BASELINE_YEAR)
POST_YEARS = (2018, 2019, 2020, 2021)
EVENT_YEARS = PRE_YEARS[:-1] + POST_YEARS      # baseline=2017 不进哑变量
LEAD_YEARS = (2014, 2015, 2016)
OUTCOMES = ("moves_any", "moves_intl", "cn_link", "log_works")
COL_FOR_OUTCOME = {
    "moves_any": "outcome_moves",
    "moves_intl": "outcome_intl_moves",
    "cn_link": "outcome_cn_link",
    "log_works": "outcome_log_works",
}
Z_95 = 1.959964


def _as_bool(raw: str) -> bool:
    """兼容 psql COPY CSV 的 t/f 与 Python 的 true/false、1/0。"""
    return str(raw).strip().lower() in ("t", "true", "1")


def load_panel(path: Path, limit_n: int | None = None) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"empty panel: {path}")
    if limit_n is not None:
        keep = {r["scholar_id"] for r in rows[:limit_n]}
        rows = [r for r in rows if r["scholar_id"] in keep]
    out = []
    for r in rows:
        out.append({
            "scholar_id": r["scholar_id"],
            "year": int(r["year"]),
            "treated": _as_bool(r["treated"]),
            "post": _as_bool(r["post"]),
            "outcome": {o: float(r[COL_FOR_OUTCOME[o]]) for o in OUTCOMES},
        })
    return out


def design_2x2(rows: list[dict]) -> tuple[list[list[float]], dict[str, list[str]]]:
    """列：[intercept, treat, post, treat_x_post]；treat/post 系数索引=3。"""
    X, groups = [], []
    for r in rows:
        t = 1.0 if r["treated"] else 0.0
        p = 1.0 if r["post"] else 0.0
        X.append([1.0, t, p, t * p])
        groups.append(r["scholar_id"])
    return X, {"did": groups}


def design_event(rows: list[dict]) -> tuple[list[list[float]], dict[str, list[str]]]:
    """列：[intercept, treat, year dummies(ex baseline), treat×year(ex baseline)]。
    返回 treat×year 各年在矩阵中的列号，供 Wald 联合检验与逐年效应读取。
    """
    X, groups = [], []
    for r in rows:
        t = 1.0 if r["treated"] else 0.0
        base = [1.0, t]
        base += [1.0 if r["year"] == y else 0.0 for y in EVENT_YEARS]
        base += [(1.0 if r["year"] == y else 0.0) * t for y in EVENT_YEARS]
        X.append(base)
        groups.append(r["scholar_id"])
    n_year = len(EVENT_YEARS)
    idx = {y: 2 + n_year + i for i, y in enumerate(EVENT_YEARS)}
    return X, {"groups": groups, "year_idx": idx}


def estimate(rows: list[dict]) -> dict:
    """返回 {outcome: {...}}，含 2x2 与事件研究两套估计。"""
    n_treated = len({r["scholar_id"] for r in rows if r["treated"]})
    n_control = len({r["scholar_id"] for r in rows if not r["treated"]})
    result: dict = {"n_obs": len(rows), "n_treated": n_treated,
                    "n_control": n_control, "outcomes": {}}

    X2, g2 = design_2x2(rows)
    Xe, ge = design_event(rows)

    for o in OUTCOMES:
        y = [r["outcome"][o] for r in rows]

        m2 = ols_cluster(X2, y, g2["did"])
        b2, se2 = m2.beta[3], m2.se[3]
        t2 = m2.t_stat(3)
        ci2 = (b2 - Z_95 * se2, b2 + Z_95 * se2)

        me = ols_cluster(Xe, y, ge["groups"])
        lead_idx = [ge["year_idx"][y] for y in LEAD_YEARS]
        wald = wald_test(me, lead_idx, alpha=0.05)
        annual = {}
        for y_ in EVENT_YEARS:
            i = ge["year_idx"][y_]
            b, se = me.beta[i], me.se[i]
            annual[y_] = {
                "coef": b, "se": se, "t": me.t_stat(i),
                "p": me.p_value(i), "ci": (b - Z_95 * se, b + Z_95 * se),
                "kind": "lead" if y_ < BASELINE_YEAR else "lag",
            }
        # 事件研究口径的 post 平均效应 = 2018-2021 逐年系数均值
        post_coefs = [annual[y_]["coef"] for y_ in POST_YEARS]
        post_avg = sum(post_coefs) / len(post_coefs)

        result["outcomes"][o] = {
            "did_2x2": {
                "coef": b2, "se": se2, "t": t2, "p": m2.p_value(3),
                "ci": ci2, "r2": m2.r2, "n_clusters": m2.n_clusters,
            },
            "event_study": {
                "coef": post_avg, "se": math.sqrt(sum(annual[y_]["se"] ** 2
                                                     for y_ in POST_YEARS)) / len(POST_YEARS),
                "t": wald.f_stat, "p": wald.p_value,
                "ci": (post_avg, post_avg), "r2": me.r2,
                "n_clusters": me.n_clusters,
                "wald": wald,
            },
            "annual": annual,
        }
    return result


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _num(v: float) -> str:
    """数值转 SQL 字面量；NaN/Inf 写 NULL（如零方差 lead 的 t/p），不伪造 0。"""
    if v is None or not math.isfinite(v):
        return "NULL"
    return f"{v:.6f}"


def build_sql(res: dict) -> str:
    lines = ["BEGIN;"]
    for o in OUTCOMES:
        d = res["outcomes"][o]
        c2, ce = d["did_2x2"], d["event_study"]
        w = ce["wald"]
        note2 = ("cluster-robust CR1 by scholar; treated=WP15 external eligible; "
                 "small treated cluster (G_t<10) -> SE downward biased, descriptive only")
        notee = ("event study vs 2017; joint Wald F on 2014/2015/2016 leads; "
                 "cluster-robust CR1 by scholar; coefficient=mean of 2018-2021 year effects")
        if w.degraded:
            notee += ("; DEGRADED: RVR singular (zero-variance lead in treated group), "
                      "F replaced by max t^2 and Bonferroni-bound p, NOT a true F statistic")

        for spec, c in (("did_2x2_cluster", c2), ("event_study_cluster", ce)):
            lines.append(
                "INSERT INTO did_robust_estimate "
                "(rule_version, cohort_rule_version, specification, outcome, coefficient, "
                " std_error, t_stat, p_value, ci_low, ci_high, n_obs, n_scholars, n_clusters, "
                " n_treated, n_control, r_squared, joint_f_stat, joint_df1, joint_df2, "
                " joint_p_value, parallel_trend_passed, note) VALUES ("
                f"{_lit(RV)}, {_lit(CRV)}, {_lit(spec)}, {_lit(o)}, "
                f"{_num(c['coef'])}, {_num(c['se'])}, {_num(c['t'])}, {_num(c['p'])}, "
                f"{_num(c['ci'][0])}, {_num(c['ci'][1])}, "
                f"{res['n_obs']}, {res['n_treated'] + res['n_control']}, {c['n_clusters']}, "
                f"{res['n_treated']}, {res['n_control']}, {_num(c['r2'])}, "
                f"{('NULL' if spec == 'did_2x2_cluster' else _num(w.f_stat))}, "
                f"{('NULL' if spec == 'did_2x2_cluster' else w.df1)}, "
                f"{('NULL' if spec == 'did_2x2_cluster' else w.df2)}, "
                f"{('NULL' if spec == 'did_2x2_cluster' else _num(w.p_value))}, "
                f"{('NULL' if spec == 'did_2x2_cluster' else ('true' if w.passed else 'false'))}, "
                f"{_lit(note2 if spec == 'did_2x2_cluster' else notee)});"
            )

        for y_ in EVENT_YEARS:
            a = d["annual"][y_]
            kind = a["kind"]
            note = "event study baseline=2017, cluster-robust by scholar"
            if kind == "baseline":
                note = "baseline year omitted from design matrix; coefficient structurally zero"
            if not math.isfinite(a["t"]):
                note += "; DEGRADED: zero-variance cell in treated group, t/p undefined (NULL)"
            lines.append(
                "INSERT INTO did_robust_annual_effect "
                "(rule_version, cohort_rule_version, outcome, year, lead_or_lag, "
                " coefficient, std_error, t_stat, p_value, ci_low, ci_high, note) VALUES ("
                f"{_lit(RV)}, {_lit(CRV)}, {_lit(o)}, {y_}, {_lit(kind)}, "
                f"{_num(a['coef'])}, {_num(a['se'])}, {_num(a['t'])}, {_num(a['p'])}, "
                f"{_num(a['ci'][0])}, {_num(a['ci'][1])}, {_lit(note)});"
            )
    lines.append("COMMIT;")
    return "\n".join(lines)


DELETE_SQL = f"""
BEGIN;
DELETE FROM did_robust_estimate WHERE rule_version='{RV}';
DELETE FROM did_robust_annual_effect WHERE rule_version='{RV}';
COMMIT;
"""

VERIFY_SQL = f"""
SELECT specification, outcome, coefficient, std_error, t_stat, p_value,
       n_treated, n_control, n_clusters, joint_f_stat, joint_p_value, parallel_trend_passed
FROM did_robust_estimate WHERE rule_version='{RV}'
ORDER BY specification, outcome;

SELECT outcome, year, lead_or_lag, coefficient, std_error, t_stat, p_value
FROM did_robust_annual_effect WHERE rule_version='{RV}'
ORDER BY outcome, year;
"""

EXPORT_ESTIMATE_SQL = f"""
COPY (
  SELECT specification, outcome, coefficient, std_error, t_stat, p_value, ci_low, ci_high,
         n_treated, n_control, n_clusters, joint_f_stat, joint_p_value, parallel_trend_passed
  FROM did_robust_estimate WHERE rule_version='{RV}' ORDER BY specification, outcome
) TO STDOUT WITH CSV HEADER;
"""

EXPORT_ANNUAL_SQL = f"""
COPY (
  SELECT outcome, year, lead_or_lag, coefficient, std_error, t_stat, p_value, ci_low, ci_high
  FROM did_robust_annual_effect WHERE rule_version='{RV}' ORDER BY outcome, year
) TO STDOUT WITH CSV HEADER;
"""


def print_summary(res: dict) -> None:
    print(f"面板：{res['n_obs']} 组-年，treated={res['n_treated']}，control={res['n_control']}")
    print("\n=== 2x2 DID（学者层聚类 CR1）===")
    print(f"{'outcome':<12}{'coef':>12}{'SE':>10}{'t':>9}{'p':>10}{'95% CI':>24}")
    for o in OUTCOMES:
        c = res["outcomes"][o]["did_2x2"]
        print(f"{o:<12}{c['coef']:>12.4f}{c['se']:>10.4f}{c['t']:>9.3f}{c['p']:>10.4f}"
              f"{f'[{c['ci'][0]:.3f}, {c['ci'][1]:.3f}]':>24}")
    print("\n=== 平行趋势联合 F（H0: 2014/2015/2016 leads = 0, baseline=2017）===")
    print(f"{'outcome':<12}{'F':>10}{'df1':>6}{'df2':>7}{'p':>10}  结论")
    for o in OUTCOMES:
        w = res["outcomes"][o]["event_study"]["wald"]
        verdict = "不能拒绝平行趋势" if w.passed else "拒绝平行趋势（警）"
        if w.degraded:
            verdict += "[退化降级·非真F]"
        print(f"{o:<12}{w.f_stat:>10.3f}{w.df1:>6}{w.df2:>7}{w.p_value:>10.4f}  {verdict}")
    print("\n=== 逐年效应（treated×year，节选 lead）===")
    print(f"{'outcome':<12}" + "".join(f"{y:>16}" for y in LEAD_YEARS))
    for o in OUTCOMES:
        annual = res["outcomes"][o]["annual"]
        cells = "".join(f"{annual[y]['coef']:>9.3f}({annual[y]['p']:.2f})" for y in LEAD_YEARS)
        print(f"{o:<12}{cells}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="只取前 N 名学者的面板（调试用）")
    args = ap.parse_args()

    out = batch_dir()
    panel = out / "did_panel.csv"
    rows = load_panel(panel, args.limit)
    print(f"=== WP16 DID 稳健统计（rule_version={RV}）===")
    print(f"读入面板：{panel}（{len(rows)} 行）")

    t0 = time.time()
    res = estimate(rows)
    print(f"统计用时 {time.time()-t0:.2f}s\n")
    print_summary(res)

    if args.dry_run:
        print("\n[DRY RUN] 未写库。")
        return

    run_psql(DELETE_SQL, name="wp16_delete", timeout=300)
    run_psql(build_sql(res), name="wp16_build", timeout=300)
    print("\n=== 核验 ===")
    run_psql(VERIFY_SQL, name="wp16_verify")

    est_csv = run_psql(EXPORT_ESTIMATE_SQL, name="wp16_export_est", verbose=False)
    (out / "did_robust_estimate.csv").write_text(est_csv, encoding="utf-8")
    ann_csv = run_psql(EXPORT_ANNUAL_SQL, name="wp16_export_annual", verbose=False)
    (out / "did_robust_annual_effect.csv").write_text(ann_csv, encoding="utf-8")
    print(f"\n导出：{out / 'did_robust_estimate.csv'}、{out / 'did_robust_annual_effect.csv'}")


if __name__ == "__main__":
    main()
