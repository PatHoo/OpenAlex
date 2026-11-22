#!/usr/bin/env python3
"""WP4 定案落库：把 identity_decisions.jsonl 写入 openalex 库。

口径（用户确认）：
  - 严格 A 级：姓名+机构+引用倍数三项一致 -> match_level='A'
  - B 级：姓名匹配但信号不全 -> match_level='B'（待复核）
  - 源重复行(DUP)：每组只保留 h17 最高的主行建映射，其余跳过
  - HOLD：不动；SEED：三人种子已有映射，保持不动

幂等：按 src_id 建 scholar（ON CONFLICT DO NOTHING），
      按 (scholar_id,scheme,external_id) 建 identity_map，可重复执行。
"""
import argparse
import json
import os
import subprocess
from pathlib import Path

DATA = Path(__file__).parent / "data" / "batch_001"
DEC = DATA / "identity_decisions.jsonl"
SRC = DATA / "source_scholar.jsonl"


def load_inputs():
    src = {}
    for l in open(SRC, encoding="utf-8"):
        d = json.loads(l)
        src[d["src_id"]] = d
    dec = [json.loads(l) for l in open(DEC, encoding="utf-8")]
    return src, dec


def pick_primary(dup_rows: list, src: dict):
    """DUP 组主行：h17 最高，其次 citations 最高，最后 src_id 最小。"""
    return sorted(dup_rows,
                  key=lambda sid: (-(src[sid].get("h17") or 0),
                                   -(src[sid].get("citations") or 0), sid))[0]


def build_plan(src: dict, dec: list):
    """返回 [(src_id, oaid, level, name, orcid)]，已处理 DUP 主行选择。"""
    # 先按 top_id 聚合 DUP 组
    dup_by_id = {}
    for d in dec:
        if d["level"] == "DUP":
            aid = d["signals"]["top_id"]
            dup_by_id.setdefault(aid, []).append(d["src_id"])

    primary_of = {}
    for aid, sids in dup_by_id.items():
        primary = pick_primary(sids, src)
        for s in sids:
            primary_of[s] = primary

    plan = []
    for d in dec:
        if d["level"] not in ("A", "B", "DUP"):
            continue
        sid = d["src_id"]
        if d["level"] == "DUP" and primary_of[sid] != sid:
            continue  # 非主行跳过
        aid = d["signals"]["top_id"]
        if not aid:
            continue
        # 从候选元数据取 orcid（decisions 里没存，回头补）
        plan.append((sid, aid, d["level"], src[sid].get("authfull"), None))
    return plan, primary_of


def psql(sql: str) -> str:
    """通过 WSL psql 执行 SQL，返回 stdout。"""
    cmd = [
        "wsl", "-e", "bash", "-c",
        f'PGPASSWORD=$(cat ~/.openalex_db_pass) psql -h 127.0.0.1 -p 5432 '
        f'-U postgres -d openalex -tAc {json.dumps(sql)}'
    ]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src, dec = load_inputs()
    plan, primary_of = build_plan(src, dec)

    # 补 orcid：从 candidates.jsonl 第一候选
    cands = {}
    for l in open(DATA / "candidates.jsonl", encoding="utf-8"):
        d = json.loads(l)
        if d["candidates"]:
            cands[d["src_id"]] = d["candidates"][0]
    plan = [(sid, aid, lv, name, (cands.get(sid, {}).get("orcid") or ""))
            for sid, aid, lv, name, _ in plan]

    print(f"待建映射: {len(plan)}  （A={sum(1 for p in plan if p[2]=='A')}, "
          f"B={sum(1 for p in plan if p[2]=='B')}, DUP主行={sum(1 for p in plan if p[2]=='DUP')}）")
    print(f"DUP 主行选择: {json.dumps(primary_of, ensure_ascii=False)}")
    if args.dry_run:
        print("DRY RUN，不写库")
        return

    # 生成 SQL：用临时表 + 事务，逐条插入 scholar / identity_map / name_variant
    # external_id 用裸 ID（与三人种子格式一致）
    # DUP 是内部标记，match_level 是 char(1)，落库时按 A 级（重复行主行均三项一致）
    sql_lines = ["BEGIN;"]
    for sid, aid, lv, name, orcid in plan:
        level_sql = "A" if lv == "DUP" else lv
        oid = aid.rstrip("/").split("/")[-1]
        nm = (name or "").replace("'", "''")
        oc = (orcid or "").replace("'", "''")
        sql_lines.append(f"""
DO $$
DECLARE v_scholar BIGINT;
BEGIN
  INSERT INTO scholar(src_id, display_name)
  VALUES ({sid}, '{nm}')
  ON CONFLICT (src_id) DO UPDATE SET display_name=EXCLUDED.display_name
  RETURNING scholar_id INTO v_scholar;
  IF v_scholar IS NULL THEN
    SELECT scholar_id INTO v_scholar FROM scholar WHERE src_id={sid};
  END IF;
  INSERT INTO identity_map(scholar_id, scheme, external_id, match_level, evidence_url, matched_at)
  VALUES (v_scholar, 'openalex', '{oid}', '{level_sql}', 'https://openalex.org/{oid}', now())
  ON CONFLICT (scholar_id, scheme, external_id) DO UPDATE
    SET match_level=EXCLUDED.match_level, evidence_url=EXCLUDED.evidence_url;
  INSERT INTO name_variant(scholar_id, raw_name, source, is_primary)
  VALUES (v_scholar, '{nm}', 'table_s1_career_2017', true)
  ON CONFLICT DO NOTHING;
  IF '{oc}' <> '' THEN
    INSERT INTO identity_map(scholar_id, scheme, external_id, match_level, evidence_url, matched_at)
    VALUES (v_scholar, 'orcid', '{oc}', '{level_sql}', 'https://orcid.org/{oc}', now())
    ON CONFLICT (scholar_id, scheme, external_id) DO UPDATE
      SET match_level=EXCLUDED.match_level;
  END IF;
END $$;
""".strip())
    sql_lines.append("COMMIT;")

    tmp = Path(os.environ.get("TEMP", "/tmp")) / "wp4_load.sql"
    tmp.write_text("\n".join(sql_lines), encoding="utf-8")
    print(f"SQL 已生成: {tmp}  ({len(sql_lines)} 行)")

    # 通过 WSL 路径执行
    wsl_path = "/mnt/c" + str(tmp).replace("C:\\", "/").replace("\\", "/")
    cmd = ["wsl", "-e", "bash", "-c",
           f'PGPASSWORD=$(cat ~/.openalex_db_pass) psql -h 127.0.0.1 -p 5432 '
           f'-U postgres -d openalex -v ON_ERROR_STOP=1 -f {wsl_path} 2>&1 | tail -20']
    r = subprocess.run(cmd, capture_output=True, text=True)
    print("=== psql 输出 ===")
    print(r.stdout or r.stderr)


if __name__ == "__main__":
    main()
