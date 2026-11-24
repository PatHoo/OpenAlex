"""WP0：读取 Elsevier 2017 高引名单，导入 source_scholar，生成试跑子集。

输出（均在 pipeline/data/batch_001/）：
  source_scholar.jsonl   105,026 行，字段对齐 schema.sql 的 source_scholar 表
  subset.jsonl           前 N 行，按 src_id 排序、确定性可复现
  subset_source.sql      幂等 upsert 的 SQL，导入数据库后可跑 WP1

N 由环境变量 OA_SUBSET_SIZE 决定（默认 1000）。扩到 5000 人时设 OA_SUBSET_SIZE=5000，
rerun_1000.py 再配合 --limit 5000 与提高的 OA_COST_CAP_USD。

注意：Excel 国家码是 3 字母（usa/che/...），source_scholar.cntry 是 CHAR(2)，
原始 3 字母码完整保留在 raw_payload.cntry_iso3，不用 2 字母猜测覆盖。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    EXPECTED_DATA_ROWS,
    SOURCE_SHEET,
    SOURCE_XLSX,
    batch_dir,
)

# 按表头名精确定位；运行时用 header.index(name) 覆盖为真实位置
HEADER_NAMES = [
    "authfull", "inst_name", "cntry", "np6017", "firstyr", "lastyr",
    "h17", "nc9617", "c", "cprat", "rank", "name1",
]


def load_rows() -> list[dict]:
    wb = openpyxl.load_workbook(SOURCE_XLSX, read_only=True, data_only=True)
    ws = wb[SOURCE_SHEET]
    header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    idx = {name: header.index(name) for name in HEADER_NAMES if name in header}
    print(f"[WP0] header fields used: {idx}")

    out: list[dict] = []
    # 编号口径与已入库的三人 POC 保持一致：
    #   source_row = 数据序号（不含表头，从 1 起）
    #   src_id     = Excel 实际行号（含表头，从 2 起）= source_row + 1
    # 已有种子：Becke src_id=17/source_row=16，Perdew 18/17，Newman 29/28
    for data_row, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=1):
        def get(name, default=None):
            i = idx.get(name)
            return row[i] if i is not None and i < len(row) else default

        authfull = get("authfull")
        if authfull is None or str(authfull).strip() == "":
            continue
        raw = {
            "authfull": authfull,
            "inst_name": get("inst_name"),
            "cntry_iso3": get("cntry"),
            "np6017": get("np6017"),
            "firstyr": get("firstyr"),
            "lastyr": get("lastyr"),
            "h17": get("h17"),
            "citations": get("nc9617"),  # 真正的引用总数；c 列是小数比率，不能当引用数
            "cprat": get("cprat"),
            "rank": get("rank"),
            "sm1": get("sm-1"),
            "name1": get("name1"),
        }
        out.append(
            {
                "source_row": data_row,
                "excel_row": data_row + 1,
                "authfull": str(authfull),
                "inst_name": str(get("inst_name") or ""),
                "cntry": None,  # CHAR(2) 暂不推断；ISO3 存 raw_payload
                "np6017": get("np6017"),
                "firstyr": get("firstyr"),
                "lastyr": get("lastyr"),
                "h17": get("h17"),
                "citations": get("nc9617"),
                "discipline": get("name1"),
                "raw_payload": raw,
            }
        )
    return out


def sql_literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("'", "''")
    return f"'{s}'"


def row_sql(r: dict, src_id: int) -> str:
    cols = [
        "src_id", "source_row", "authfull", "inst_name", "cntry",
        "np6017", "firstyr", "lastyr", "h17", "citations", "discipline", "raw_payload",
    ]
    vals = [
        str(src_id), str(r["source_row"]), sql_literal(r["authfull"]),
        sql_literal(r["inst_name"]), sql_literal(r["cntry"]),
        sql_literal(r["np6017"]), sql_literal(r["firstyr"]), sql_literal(r["lastyr"]),
        sql_literal(r["h17"]), sql_literal(r["citations"]), sql_literal(r["discipline"]),
        sql_literal(json.dumps(r["raw_payload"], ensure_ascii=False)) + "::jsonb",
    ]
    return (
        "INSERT INTO source_scholar(" + ",".join(cols) + ") VALUES ("
        + ",".join(vals)
        + ") ON CONFLICT (source_row) DO UPDATE SET "
        + "authfull=EXCLUDED.authfull, inst_name=EXCLUDED.inst_name, "
        + "raw_payload=EXCLUDED.raw_payload;"
    )


def subset_size(total_rows: int) -> int:
    """解析 OA_SUBSET_SIZE：默认 1000，非法值回退 1000，超过名单行数按名单行数。"""
    try:
        n = int(os.environ.get("OA_SUBSET_SIZE", "1000"))
    except (ValueError, TypeError):
        n = 1000
    if n < 1:
        n = 1
    return min(n, total_rows)


def main() -> None:
    rows = load_rows()
    print(f"[WP0] data rows = {len(rows)} (expected {EXPECTED_DATA_ROWS})")
    if len(rows) != EXPECTED_DATA_ROWS:
        print("[WP0][WARN] 行数与方案口径不一致，请核对 Excel 是否被改动")

    d = batch_dir()
    all_path = d / "source_scholar.jsonl"
    with all_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"src_id": r["excel_row"], **r}, ensure_ascii=False) + "\n")
    print(f"[WP0] wrote {all_path} ({len(rows)} rows)")

    # 子集：前 N 条（src_id 1..N），确定性可复现；N 默认 1000，可用 OA_SUBSET_SIZE 调整
    n = subset_size(len(rows))
    if n != int(os.environ.get("OA_SUBSET_SIZE", "1000") or 1000):
        print(f"[WP0][WARN] OA_SUBSET_SIZE 被夹取到名单范围：{n} / {len(rows)}")
    subset = rows[:n]
    sub_path = d / "subset.jsonl"
    with sub_path.open("w", encoding="utf-8") as f:
        for r in subset:
            f.write(json.dumps({"src_id": r["excel_row"], **r}, ensure_ascii=False) + "\n")
    print(f"[WP0] wrote {sub_path} ({len(subset)} rows, OA_SUBSET_SIZE={n})")

    sql_path = d / "subset_source.sql"
    with sql_path.open("w", encoding="utf-8") as f:
        f.write(f"-- WP0 子集：src_id = Excel 行号（含表头），前 {n} 数据行；按 source_row 幂等 upsert\n")
        f.write("BEGIN;\n")
        for r in subset:
            f.write(row_sql(r, r["excel_row"]) + "\n")
        f.write("SELECT setval(pg_get_serial_sequence('source_scholar','src_id'), "
                "GREATEST((SELECT max(src_id) FROM source_scholar),1));\n")
        f.write("COMMIT;\n")
    print(f"[WP0] wrote {sql_path}")


if __name__ == "__main__":
    main()
