#!/usr/bin/env python3
"""WP4 身份消歧定案：候选信号评分 + 分级 + 定案清单。

规则（可解释、可复核）：
  A 级 —— 可自动建 scholar + identity_map(match_level=A)：
     姓名匹配 且 机构匹配 且 引用倍数∈[0.5,20] 且 无第二个候选同时满足姓名+机构
  B 级 —— 建映射但 match_level=B，待人工复核：
     姓名匹配 但 机构不匹配/引用倍数异常；或 唯一候选+姓名匹配但机构信号缺失
  挂起 —— 不建映射，保留 JSONL 待人工：
     源名单重复行 / 姓名不匹配 / 首候选无信号 / 多个候选都强匹配（真歧义）

三人种子(src 17/18/29)已有人 Tunis A 级映射，标注 existing_seed 跳过。

只产出定案清单 JSONL，不写数据库。落库由后续步骤执行。
"""
import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

DATA = Path(__file__).parent / "data" / "batch_001"
SEED_SIDS = {17, 18, 29}
RATIO_LO, RATIO_HI = 0.5, 20.0


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def toks(s: str) -> list:
    return [norm(t) for t in re.split(r"[^A-Za-zÀ-ÿ0-9]+", s or "") if t]


def name_match(src_name: str, cand_name: str) -> bool:
    """首字母级匹配：源每个 token 都能在候选里找到同首字母 token。
    容忍 Jr./III 后缀、中间名缩写、变音符号。"""
    a, b = toks(src_name), toks(cand_name)
    if not a or not b:
        return False
    # 姓必须一致：源姓(逗号前) 与 候选末 token 同首字母
    src_surname = (src_name or "").split(",")[0]
    sa = toks(src_surname)
    if sa and not any(sa[0][0] == y[0] for y in b):
        return False
    return all(any(x[0] == y[0] for y in b) for x in a if len(x) > 0)


def inst_match(src_inst: str, cand_insts: list) -> bool:
    src = norm(src_inst)
    if not src:
        return False
    for inst in cand_insts or []:
        cname = norm(inst.get("display_name", ""))
        if not cname:
            continue
        if src[:12] and (src[:12] in cname or cname[:12] in src):
            return True
    return False


def ratio_of(src: dict, cand: dict):
    s, c = src.get("citations"), cand.get("cited_by_count")
    return (c / s) if (s and c) else None


def bare(aid: str) -> str:
    return (aid or "").rstrip("/").split("/")[-1]


def classify(src: dict, cands: list):
    """返回 (level, signals, reason)。"""
    sig = {"n_cand": len(cands)}
    if not cands:
        return "HOLD", sig, "无候选"

    top = cands[0]
    nm = name_match(src.get("authfull", ""), top.get("display_name", ""))
    im = inst_match(src.get("inst_name", ""), top.get("last_known_institutions", []))
    orcid = bool(top.get("orcid"))
    ratio = ratio_of(src, top)
    sig.update(name=nm, inst=im, orcid=orcid,
               ratio=round(ratio, 2) if ratio else None,
               top_id=bare(top.get("author_id")))

    ratio_ok = ratio is not None and RATIO_LO <= ratio <= RATIO_HI

    # 真歧义：多个候选同时满足姓名+机构，且引用量级接近（排除同名小号）
    top_cited = top.get("cited_by_count") or 0
    strong = [c for c in cands
              if name_match(src.get("authfull", ""), c.get("display_name", ""))
              and inst_match(src.get("inst_name", ""), c.get("last_known_institutions", []))
              and (c.get("cited_by_count") or 0) >= top_cited * 0.2]
    sig["n_strong"] = len(strong)
    if len(strong) > 1:
        return "HOLD", sig, f"真歧义:{len(strong)}个候选强匹配且量级接近"

    if not nm:
        return "HOLD", sig, "首候选姓名不匹配"

    # A 级：姓名 + 机构 + 引用倍数合理 + 无竞争强候选
    if nm and im and ratio_ok:
        return "A", sig, "姓名+机构+引用倍数三项一致"

    # B 级：姓名匹配，但机构或引用信号不全/异常
    if nm and not im and ratio_ok and orcid:
        return "B", sig, "姓名+ORCID匹配,机构未在候选列名中"
    if nm and im and not ratio_ok:
        return "B", sig, "姓名+机构匹配,但引用倍数异常"
    if len(cands) == 1 and nm:
        return "B", sig, "唯一候选且姓名匹配,机构/引用信号不足"

    return "HOLD", sig, "信号不足,需人工"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DATA / "identity_decisions.jsonl"))
    args = ap.parse_args()

    src, cands = {}, {}
    for l in open(DATA / "source_scholar.jsonl", encoding="utf-8"):
        d = json.loads(l)
        src[d["src_id"]] = d
    for l in open(DATA / "candidates.jsonl", encoding="utf-8"):
        d = json.loads(l)
        cands[d["src_id"]] = d["candidates"]

    # 检测源名单重复行：同一 OpenAlex ID 被多人列为第一候选
    by_top = defaultdict(list)
    for sid, cs in cands.items():
        if cs:
            by_top[bare(cs[0].get("author_id"))].append(sid)
    dup_groups = {aid: sids for aid, sids in by_top.items() if len(sids) > 1}
    dup_sids = {s for sids in dup_groups.values() for s in sids}

    decisions = []
    # 只处理有候选的 1000 人（src_scholar.jsonl 是全量 105026 人名单）
    for sid in sorted(cands):
        rec = {"src_id": sid, "authfull": src.get(sid, {}).get("authfull"),
               "source_inst": src.get(sid, {}).get("inst_name")}
        if sid in SEED_SIDS:
            rec.update(level="SEED", reason="已有人 Tunis A 级映射,跳过")
        elif sid in dup_sids:
            aid = bare(cands[sid][0].get("author_id")) if cands.get(sid) else None
            rec.update(level="DUP", reason=f"源名单重复行(同OpenAlex ID {aid})",
                       signals={"top_id": aid})
        else:
            level, sig, reason = classify(src.get(sid, {}), cands.get(sid, []))
            rec.update(level=level, reason=reason, signals=sig,
                       top_candidates=[{"id": bare(c.get("author_id")),
                                        "name": c.get("display_name"),
                                        "works": c.get("works_count"),
                                        "cited": c.get("cited_by_count")}
                                       for c in (cands.get(sid) or [])[:5]])
        decisions.append(rec)

    with open(args.out, "w", encoding="utf-8") as f:
        for d in decisions:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    cnt = Counter(d["level"] for d in decisions)
    print(f"定案清单: {args.out}")
    print(f"分级统计: {dict(cnt)}")
    print(f"  A级(自动): {cnt['A']}  B级(复核): {cnt['B']}  "
          f"挂起: {cnt['HOLD']}  源重复: {cnt['DUP']}  种子: {cnt['SEED']}")
    print("\n重复行分组(需决定合并/保留):")
    for aid, sids in dup_groups.items():
        print(f"  {aid}: src {sids} — {[src[s]['authfull'] for s in sids]}")
    for lv in ("A", "B", "HOLD"):
        print(f"\n=== {lv} 级样例 ===")
        for d in [x for x in decisions if x["level"] == lv][:8]:
            print(f"  src{d['src_id']:>4} {str(d['authfull'])[:26]:26s} "
                  f"-> {str(d.get('signals',{}).get('top_id','')):14s} | {d['reason']}")


if __name__ == "__main__":
    main()
