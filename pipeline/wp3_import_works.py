"""WP3：把 batch_001/works.jsonl 的作品元数据去重后导入 work 表。

口径（用户 2026-11-22 确认）：只落作品元数据，不建 scholar/authorship 关联，
不自动接受第一候选为确认身份。作品按 openalex_id 去重（UNIQUE 约束），
同一作品被多名学者抓到时只保留一份；合作者的署名信息留在 JSONL 中，
待消歧定案后再从原始文件补 authorship。

设计：
  - 流式读取 8.6G JSONL，不全量载入内存
  - 按 openalex_id 去重（内存里维护 seen 集合，约 86.5 万条 ID）
  - 每 BATCH 条用 COPY 写入临时表，再 INSERT ... ON CONFLICT DO NOTHING 合并进 work
  - 幂等：重复运行不会产生重复行
  - 进度写 import_progress.json，可中断续跑

用法：
  # WSL：OA_PATH_MODE=wsl，密码从 ~/.openalex_db_pass 读取
  cd <项目目录>/pipeline
  export OA_PATH_MODE=wsl
  PGPASSWORD=$(cat ~/.openalex_db_pass) \\
    $HOME/.venvs/openalex/bin/python -u wp3_import_works.py
  # 原生 Linux 云主机：OA_PATH_MODE=linux，密码从环境变量读取
  export OA_PATH_MODE=linux PGPASSWORD='<数据库密码>'
  python3 -u wp3_import_works.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import batch_dir  # noqa: E402

BATCH = 20_000          # 每批 COPY 行数
DB_DSN = {
    "host": os.environ.get("PGHOST", "127.0.0.1"),
    "port": int(os.environ.get("PGPORT", "5432")),  # 原生 PG17
    "user": os.environ.get("PGUSER", "postgres"),
    "dbname": os.environ.get("PGDATABASE", "openalex"),
}
PASSWORD = os.environ.get("PGPASSWORD", "")

# work 表列顺序（COPY 用）
COLS = (
    "openalex_id", "doi", "title", "pub_year", "work_type",
    "cited_by_count", "is_preprint", "raw_sha256", "fetched_at",
)
UPSERT_SQL = f"""
INSERT INTO work ({', '.join(COLS)})
SELECT {', '.join(COLS)} FROM work_stage
ON CONFLICT (openalex_id) DO NOTHING
"""


def clean_text(v):
    if v is None:
        return None
    s = str(v)
    return s if s else None


def smallint(v):
    if v is None:
        return None
    try:
        n = int(v)
        return n if -32768 <= n <= 32767 else None
    except (ValueError, TypeError):
        return None


def integer(v):
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def copy_batch(cur, rows: list[tuple]) -> int:
    """用 COPY CSV 把一个批次写入临时表再合并，返回本批实际新增行数。

    必须用 CSV 而非 text 格式：作品 title/doi 里可能含制表符、换行符或引号，
    text 格式会破坏列对齐导致数值列灌入字符串而报错。CSV 格式配合 csv.writer
    的标准转义能正确处理这些字符。
    """
    cur.execute("TRUNCATE work_stage")
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for row in rows:
        # CSV 里 NULL 用空字符串 + FORCE_NULL 指定列，或统一写 \N 并用 NULL '\N'
        # 这里用 NULL '\N'：None 写 \N，其余原样（csv.writer 负责引号/换行转义）
        writer.writerow("\\N" if v is None else v for v in row)
    buf.seek(0)
    cur.copy_expert(
        f"COPY work_stage ({', '.join(COLS)}) "
        "FROM STDIN WITH (FORMAT csv, NULL '\\N')",
        buf,
    )
    cur.execute(UPSERT_SQL)
    return cur.rowcount


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="只扫描前 N 行（小样本验证用；0=全量）")
    ap.add_argument("--reset", action="store_true",
                    help="忽略已有断点，从头开始")
    args = ap.parse_args()

    d = batch_dir()
    works_path = d / "works.jsonl"
    state_path = d / f"import_progress{('_' + str(args.limit)) if args.limit else ''}.json"

    state = {"offset": 0, "seen_count": 0, "inserted": 0, "started_at": None}
    if args.reset and state_path.exists():
        state_path.unlink()
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        print(f"[RESUME] offset={state['offset']} seen={state['seen_count']}", flush=True)

    # 断点续跑：重放已处理行的 ID 以重建 seen 集合（只需扫文本前 offset 行，很快）
    seen: set[str] = set()
    if state["offset"] > 0:
        with works_path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= state["offset"]:
                    break
                seen.add(json.loads(line)["work"].get("id"))
        print(f"[RESUME] 重建 seen 集合 {len(seen)} 条", flush=True)

    conn = psycopg2.connect(**DB_DSN, password=PASSWORD)
    conn.autocommit = False
    cur = conn.cursor()
    # 临时表：与 work 同列结构（无 PK/序列/外键），仅本次会话使用
    cur.execute("""
        CREATE TEMP TABLE IF NOT EXISTS work_stage (
            openalex_id    TEXT,
            doi            TEXT,
            title          TEXT,
            pub_year       SMALLINT,
            work_type      TEXT,
            cited_by_count INTEGER,
            is_preprint    BOOLEAN,
            raw_sha256     CHAR(64),
            fetched_at     TIMESTAMPTZ
        ) ON COMMIT PRESERVE ROWS
    """)
    conn.commit()

    batch_rows: list[tuple] = []
    offset = state["offset"]
    fetched_at = datetime.now(timezone.utc).isoformat()
    t0 = time.time()
    last_report = offset

    with works_path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if args.limit and i >= args.limit:
                break
            if i < offset:
                continue
            rec = json.loads(line)
            w = rec.get("work") or {}
            oid = w.get("id")
            if not oid or oid in seen:
                offset = i + 1
                continue
            seen.add(oid)

            batch_rows.append((
                oid,
                clean_text(w.get("doi")),
                clean_text(w.get("title")),
                smallint(w.get("publication_year")),
                clean_text(w.get("type")) or "article",
                integer(w.get("cited_by_count")),
                False,   # is_preprint：源数据无此字段，统一 false
                None,    # raw_sha256：本次不落原始载荷哈希
                fetched_at,
            ))
            offset = i + 1

            if len(batch_rows) >= BATCH:
                added = copy_batch(cur, batch_rows)
                conn.commit()
                state["inserted"] += added
                state["offset"] = offset
                state["seen_count"] = len(seen)
                if state["started_at"] is None:
                    state["started_at"] = datetime.now(timezone.utc).isoformat()
                state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                elapsed = time.time() - t0
                rate = (offset - last_report) / max(elapsed, 1)
                print(f"[PROGRESS] 扫描 {offset:,} 行 | 唯一 {len(seen):,} | "
                      f"新增 {state['inserted']:,} | {rate:,.0f} 行/s", flush=True)
                last_report = offset
                batch_rows.clear()

    # 收尾批次
    if batch_rows:
        added = copy_batch(cur, batch_rows)
        conn.commit()
        state["inserted"] += added

    state["offset"] = offset
    state["seen_count"] = len(seen)
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    cur.execute("SELECT count(*) FROM work")
    total = cur.fetchone()[0]
    print(f"[DONE] 扫描 {offset:,} 行 | 唯一作品 {len(seen):,} | "
          f"本次新增 {state['inserted']:,} | work 表总数 {total:,}", flush=True)
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
