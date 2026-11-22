#!/usr/bin/env python3
"""WP6 机构抽取：从 works.jsonl 全量抽出 institution 与 institution_alias。

输出（在 data/batch_001/）：
  _institutions.tsv   ror_id \t canonical_name \t country_code \t oa_id \t n_mentions
  _aliases.tsv        raw_name \t ror_id \t n_mentions
  _countries.txt      出现过的国家码

机构以 ROR 去重（OpenAlex 返回的机构 100% 带 ROR）。
别名来自 authorship.affiliations[].raw_affiliation_string + institution_ids。
"""
import json
import time
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).parent / "data" / "batch_001"
WORKS = DATA / "works.jsonl"


def bare(url: str) -> str:
    return (url or "").rstrip("/").split("/")[-1]


def main():
    inst = {}  # ror -> {name, country, oa_id, n}
    names = defaultdict(set)  # ror -> set(别名 display_name)
    alias = defaultdict(lambda: defaultdict(int))  # raw_name -> ror -> count
    countries = set()
    n = 0
    t0 = time.time()

    with open(WORKS, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            for a in (d.get("work", {}).get("authorships") or []):
                for ix in (a.get("institutions") or []):
                    ror = bare(ix.get("ror"))
                    if not ror:
                        continue
                    cc = ix.get("country_code") or ""
                    nm = ix.get("display_name") or ""
                    oaid = bare(ix.get("id"))
                    rec = inst.get(ror)
                    if not rec:
                        inst[ror] = {"name": nm, "country": cc, "oa_id": oaid, "n": 1}
                    else:
                        rec["n"] += 1
                        if not rec["name"] and nm:
                            rec["name"] = nm
                        if not rec["country"] and cc:
                            rec["country"] = cc
                    if cc:
                        countries.add(cc)
                    if nm:
                        names[ror].add(nm)
                # 原始署名串 -> institution_ids
                for af in (a.get("affiliations") or []):
                    raw = (af.get("raw_affiliation_string") or "").strip()
                    # 把内嵌换行/回车/制表符替换为空格，避免破坏 TSV 行结构
                    raw = raw.replace("\r", " ").replace("\n", " ").replace("\t", " ")
                    raw = " ".join(raw.split())  # 折叠多余空白
                    if not raw:
                        continue
                    for iid in (af.get("institution_ids") or []):
                        # institution_ids 可能是 OpenAlex I ID，需转成 ror
                        oaid = bare(iid)
                        # 反查：先假设 OAI ID，稍后用 oa_id->ror 映射
                        alias[raw][oaid] += 1
            n += 1
            if n % 200000 == 0:
                print(f"  已扫 {n} 行, 机构 {len(inst)}, 别名 {len(alias)}, {time.time()-t0:.0f}s", flush=True)

    print(f"扫描完成: {n} 行, 用时 {time.time()-t0:.0f}s")

    # 建立 oa_id -> ror 映射，把别名的 OAI ID 转成 ROR
    oa2ror = {v["oa_id"]: k for k, v in inst.items() if v["oa_id"]}
    alias_ror = defaultdict(lambda: defaultdict(int))
    unresolved = 0
    for raw, idmap in alias.items():
        for oaid, cnt in idmap.items():
            ror = oa2ror.get(oaid)
            if ror:
                alias_ror[raw][ror] += cnt
            else:
                unresolved += 1

    # 写机构表
    with open(DATA / "_institutions.tsv", "w", encoding="utf-8") as f:
        for ror, v in inst.items():
            # 取最长名称作 canonical（更完整）
            cands = names.get(ror, {v["name"]})
            canon = max(cands, key=len) if cands else v["name"]
            f.write(f"{ror}\t{canon}\t{v['country']}\t{v['oa_id']}\t{v['n']}\n")

    # 写别名表（一个 raw 串可能对应多个 ROR，取出现最多的）
    n_alias = 0
    with open(DATA / "_aliases.tsv", "w", encoding="utf-8") as f:
        for raw, rormap in alias_ror.items():
            if not rormap:
                continue
            top_ror = max(rormap, key=rormap.get)
            f.write(f"{raw}\t{top_ror}\t{rormap[top_ror]}\n")
            n_alias += 1

    with open(DATA / "_countries.txt", "w", encoding="utf-8") as f:
        for cc in sorted(countries):
            f.write(cc + "\n")

    print(f"抽出: 机构 {len(inst)}, 别名 {n_alias}, 国家 {len(countries)}")
    print(f"别名中 OAI ID 无法映射到 ROR: {unresolved} 条(多为机构未在institutions列表出现)")
    print(f"输出: _institutions.tsv, _aliases.tsv, _countries.txt")


if __name__ == "__main__":
    main()
