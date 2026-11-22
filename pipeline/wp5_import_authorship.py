#!/usr/bin/env python3
"""WP5 署名落库：从 works.jsonl 建 authorship（作品↔学者关联）。

映射来源：
  - src_id -> scholar_id / oa_author_id ： identity_map(openalex) JOIN scholar
  - work.id -> work_id ： work 表（已去重 865111 篇）
署名定位：在 work.authorships 里找 author.id == oa_author_id 的那条；
          未命中则用 raw_author_name 与源 authfull 姓氏兜底；仍不中则跳过。

字段：
  author_position, raw_name(raw_author_name), raw_affiliation(三条字符串用 ' | ' 拼接),
  matched_institution_id 留空（机构表尚未导入，后续独立步骤补）。

幂等：UNIQUE(work_id,scholar_id)，COPY 进 staging 后 INSERT ON CONFLICT DO NOTHING。
"""
import argparse
import csv
import json
import os
import subprocess
import time
from pathlib import Path

DATA = Path(__file__).parent / "data" / "batch_001"
SCHOLAR_MAP = DATA / "_scholar_map.txt"   # src_id|external_id|scholar_id
WORK_MAP = DATA / "_work_map.txt"         # work_id|openalex_url


def load_maps():
    scholar = {}
    for line in open(SCHOLAR_MAP, encoding="utf-8"):
        p = line.rstrip("\n").split("|")
        if len(p) == 3:
            scholar[int(p[0])] = (p[1], int(p[2]))  # oaid, scholar_id
    work = {}
    for line in open(WORK_MAP, encoding="utf-8"):
        p = line.rstrip("\n").split("|")
        if len(p) == 2:
            work[p[1]] = int(p[0])  # openalex_url -> work_id
    src_name = {}
    for line in open(DATA / "source_scholar.jsonl", encoding="utf-8"):
        d = json.loads(line)
        src_name[d["src_id"]] = d.get("authfull") or ""
    return scholar, work, src_name


def bare(url: str) -> str:
    return (url or "").rstrip("/").split("/")[-1]


def find_authorship(auths: list, oaid: str, src_name: str):
    """定位目标作者署名：先 author.id，再 raw_name 姓氏兜底。"""
    for a in auths:
        aid = bare((a.get("author") or {}).get("id") or "")
        if aid == oaid:
            return a
    # 兜底：源姓名姓氏（逗号前）出现在 raw_author_name
    surname = (src_name or "").split(",")[0].strip().lower()
    if surname:
        for a in auths:
            rn = (a.get("raw_author_name") or "").lower()
            if surname and surname in rn:
                return a
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只扫前N行(测试用)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offset", type=int, default=0)
    args = ap.parse_args()

    scholar, work, src_names = load_maps()
    print(f"映射: scholar={len(scholar)} work={len(work)} src_names={len(src_names)}")

    staging = DATA / "_authorship_stage.csv"
    state_path = DATA / "authorship_progress.json"
    state = {"offset": 0, "scanned": 0, "rows": 0, "skip_unsolved": 0,
             "skip_no_auth": 0, "skip_no_work": 0}
    if state_path.exists() and not args.limit:
        state = json.load(open(state_path))

    start = args.offset or state["offset"]
    out = open(staging, "a", encoding="utf-8", newline="")
    writer = csv.writer(out, quoting=csv.QUOTE_MINIMAL)
    stat = {"total": 0, "hit": 0, "hit_by_name": 0, "skip_unsolved": 0,
            "skip_no_auth": 0, "skip_no_work": 0}
    seen_pairs = set()
    t0 = time.time()

    with open(DATA / "works.jsonl", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i < start:
                continue
            if args.limit and (i - start) >= args.limit:
                break
            d = json.loads(line)
            sid = d["src_id"]
            stat["total"] += 1
            if sid not in scholar:
                stat["skip_unsolved"] += 1
                continue
            oaid, sch_id = scholar[sid]
            w = d.get("work") or {}
            wid = work.get(w.get("id"))
            if not wid:
                stat["skip_no_work"] += 1
                continue
            pair = (wid, sch_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            auths = w.get("authorships") or []
            hit = None
            by_name = False
            for a in auths:
                if bare((a.get("author") or {}).get("id") or "") == oaid:
                    hit = a
                    break
            if not hit:
                hit = find_authorship(auths, oaid, src_names.get(sid, ""))
                by_name = bool(hit)
            if not hit:
                stat["skip_no_auth"] += 1
                continue
            stat["hit"] += 1
            if by_name:
                stat["hit_by_name"] += 1
            pos = hit.get("author_position") or ""
            if pos not in ("first", "middle", "last"):
                pos = "middle"
            raw_name = hit.get("raw_author_name") or ""
            affs = hit.get("raw_affiliation_strings") or []
            raw_aff = " | ".join(affs)[:2000] if affs else ""
            writer.writerow([wid, sch_id, pos, raw_name, raw_aff, "t"])

    out.close()
    print(f"扫描 {stat['total']} 行, 用时 {time.time()-t0:.1f}s")
    print(f"  命中署名 {stat['hit']} (其中姓名兜底 {stat['hit_by_name']})")
    print(f"  跳过: 未定案 {stat['skip_unsolved']}, 无work {stat['skip_no_work']}, 无署名 {stat['skip_no_auth']}")
    print(f"  新增staging行: {stat['hit']}")

    if args.dry_run:
        print("DRY RUN, staging 文件已写但未入库")
        return

    state["offset"] = start + stat["total"]
    state["rows"] += stat["hit"]
    state["skip_unsolved"] += stat["skip_unsolved"]
    json.dump(state, open(state_path, "w"))

    # COPY staging -> authorship（一次批量）
    wsl_stage = "/mnt/c" + str(staging).replace("C:\\", "/").replace("\\", "/")
    sql = f"""
BEGIN;
CREATE TEMP TABLE auth_stage(work_id BIGINT, scholar_id BIGINT, author_position TEXT,
    raw_name TEXT, raw_affiliation TEXT, is_valid BOOLEAN);
\\copy auth_stage FROM '{wsl_stage}' WITH (FORMAT csv)
INSERT INTO authorship(work_id,scholar_id,author_position,raw_name,raw_affiliation,is_valid)
SELECT work_id,scholar_id,author_position,raw_name,raw_affiliation,is_valid
FROM auth_stage
ON CONFLICT (work_id,scholar_id) DO NOTHING;
COMMIT;
"""
    sqlfile = Path(os.environ.get("TEMP", "/tmp")) / "wp5_load.sql"
    sqlfile.write_text(sql, encoding="utf-8")
    wsl_sql = "/mnt/c" + str(sqlfile).replace("C:\\", "/").replace("\\", "/")
    cmd = ["wsl", "-e", "bash", "-c",
           f'PASS=$(cat ~/.openalex_db_pass); PGPASSWORD="$PASS" psql -h 127.0.0.1 -p 5432 -U postgres -d openalex -v ON_ERROR_STOP=1 -f {wsl_sql} 2>&1 | tail -15']
    r = subprocess.run(cmd, capture_output=True, text=True)
    print("=== psql ===")
    print(r.stdout or r.stderr)
    # 清空 staging 供下批
    out = open(staging, "w")
    out.close()


if __name__ == "__main__":
    main()
