#!/usr/bin/env python3
"""WP4 消歧定案：候选信号分布分析（只读，不写库）。

对 1000 人的候选数据做信号统计，为定案阈值提供依据：
- 候选数量分布
- 姓名归一化匹配（忽略变音符号/大小写/姓序）
- 机构名关键词匹配
- 引用数量级（2017 源 vs OpenAlex 当前）
- ORCID 覆盖
"""
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

DATA = Path(__file__).parent / "data" / "batch_001"


def norm(s: str) -> str:
    """归一化：去变音符号、小写、只留字母数字。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def name_tokens(authfull: str, display: str):
    """把 'Grätzel, Michael' 和 'Michaël Grätzel' 都拆成 token 集合。"""
    return set(t for t in re.split(r"[^a-zA-ZÀ-ÿ]+", authfull or "") if len(t) > 1), \
           set(t for t in re.split(r"[^a-zA-ZÀ-ÿ]+", display or "") if len(t) > 1)


def name_match(src_authfull: str, cand_display: str) -> bool:
    """姓名归一化后 token 集合相等（忽略姓序与变音符号）。"""
    a, b = name_tokens(src_authfull, cand_display)
    na = set(norm(t) for t in a)
    nb = set(norm(t) for t in b)
    if not na or not nb:
        return False
    return na == nb


def inst_match(src_inst: str, cand_insts: list) -> bool:
    """机构匹配：源机构名归一化后，其显著词在任一候选机构名中出现。"""
    src = norm(src_inst)
    if not src:
        return False
    for inst in cand_insts or []:
        cname = norm(inst.get("display_name", ""))
        if not cname:
            continue
        # 取源机构名前 12 个字符做包含判断（足以覆盖 EPFL/Max Planck 等）
        key = src[:12]
        if key and (key in cname or cname[:12] in src):
            return True
        # ROR 相同也算（这里源无 ROR，仅示例）
    return False


def main():
    candidates = {}
    for line in open(DATA / "candidates.jsonl", encoding="utf-8"):
        d = json.loads(line)
        candidates[d["src_id"]] = d
    source = {}
    for line in open(DATA / "source_scholar.jsonl", encoding="utf-8"):
        d = json.loads(line)
        source[d["src_id"]] = d

    n = len(candidates)
    cand_dist = Counter(len(c["candidates"]) for c in candidates.values())

    name_ok = inst_ok = both_ok = orcid1 = 0
    ratio_bucket = Counter()
    unclear = []

    for sid, c in candidates.items():
        s = source.get(sid, {})
        cands = c["candidates"]
        if not cands:
            unclear.append((sid, "no_candidate"))
            continue
        top = cands[0]
        nm = name_match(s.get("authfull", ""), top.get("display_name", ""))
        im = inst_match(s.get("inst_name", ""), top.get("last_known_institutions", []))
        if nm:
            name_ok += 1
        if im:
            inst_ok += 1
        if nm and im:
            both_ok += 1
        if top.get("orcid"):
            orcid1 += 1
        src_cit = s.get("citations")
        cand_cit = top.get("cited_by_count")
        if src_cit and cand_cit:
            r = cand_cit / src_cit
            ratio_bucket["<1" if r < 1 else "1-2" if r < 2 else "2-4" if r < 4 else "4-8" if r < 8 else ">=8"] += 1
        # 多候选且第一候选姓名/机构都不匹配 -> 需复核
        if len(cands) > 1 and not nm and not im:
            unclear.append((sid, "multi_no_signal", len(cands)))

    print(f"候选人数: {n}")
    print(f"每人候选数分布: {dict(sorted(cand_dist.items()))}")
    print(f"第一候选姓名归一化匹配: {name_ok}/{n} ({name_ok/n:.1%})")
    print(f"第一候选机构关键词匹配: {inst_ok}/{n} ({inst_ok/n:.1%})")
    print(f"两项都匹配(强匹配候选): {both_ok}/{n} ({both_ok/n:.1%})")
    print(f"第一候选有ORCID: {orcid1}/{n} ({orcid1/n:.1%})")
    print(f"引用倍数(OpenAlex当前/2017源)分布: {dict(ratio_bucket)}")
    print(f"多候选且首候选无姓名/机构信号: {sum(1 for u in unclear if u[1]=='multi_no_signal')}")
    print(f"无候选: {sum(1 for u in unclear if u[1]=='no_candidate')}")

    def bare(aid):
        return (aid or "").rstrip("/").split("/")[-1]

    # 回测：三人种子的已知正确答案（人工确认的 A 级映射）
    known = {17: "A5015077264", 18: "A5014987686", 29: "A5067142016"}
    print("\n=== 三人种子回测（已知正确答案是否=第一候选）===")
    for sid, kid in known.items():
        cands = candidates.get(sid, {}).get("candidates", [])
        ranks = [c["rank"] for c in cands if bare(c["author_id"]) == kid]
        top = bare(cands[0]["author_id"]) if cands else "(none)"
        print(f"src {sid}: 已知 {kid}, 候选rank={ranks}, 第一候选={top}, "
              f"{'命中' if ranks and ranks[0] == 1 else '未命中!'}")

    # 冲突：不同学者的第一候选是否撞同一个 OpenAlex ID
    first_ids = [bare(c["candidates"][0]["author_id"])
                 for c in candidates.values() if c["candidates"]]
    dup = {k: v for k, v in Counter(first_ids).items() if v > 1}
    print(f"\n第一候选ID重复(会撞identity_map唯一索引): {len(dup)} 个")
    for k, v in list(dup.items())[:10]:
        print("  ", k, "被", v, "人列为第一候选")

    # 姓名不匹配样例（看是格式问题还是真的错配）
    name_fail = []
    for sid, c in candidates.items():
        if not c["candidates"]:
            continue
        s = source.get(sid, {})
        top = c["candidates"][0]
        if not name_match(s.get("authfull", ""), top.get("display_name", "")):
            name_fail.append((sid, s.get("authfull"), top.get("display_name")))
    print(f"\n姓名不匹配样例(共{len(name_fail)}, 前12):")
    for sid, a, b in name_fail[:12]:
        print(f"  {sid}: 源[{a}] vs 首候选[{b}]")

    print("\n模糊样例(前15):")
    for u in unclear[:15]:
        print("  ", u)


if __name__ == "__main__":
    main()
