"""WP1 + WP2：作者身份检索 + 作品署名抓取（1,000 人试跑）。

- WP1：按 authfull 在 OpenAlex /authors 搜索候选，取 top-N，落 candidates.jsonl。
  消歧评分留到 WP1b（本试跑只保留候选，不自动接受，避免臆造匹配）。
- WP2：对每个候选作者用 /works?filter=authorships.author.id:X 抓全部作品，
  cursor 翻页（免费 key 才支持超过 1 万条时翻页），落 works.jsonl。
- 断点续跑：checkpoint.json 记录每个 src_id 的完成状态，重跑只处理未完成者。

输出（pipeline/data/batch_001/）：
  candidates.jsonl  每个 src_id 一行：{src_id, candidates:[...]}
  works.jsonl       每作品一行：{src_id, oa_author_id, work:{...}}
  checkpoint.json   {src_id: {"wp1":bool,"wp2":bool,"works_count":int}}
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import PER_PAGE, batch_dir  # noqa: E402
from oa_client import APIBudgetExhausted, OpenAlexClient  # noqa: E402

# 只取需要的字段，降低响应体与额度消耗
AUTHOR_SELECT = "id,display_name,orcid,works_count,cited_by_count,last_known_institutions,ids"
WORK_SELECT = (
    "id,doi,publication_year,type,cited_by_count,"
    "authorships,primary_location,title"
)


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def load_checkpoint(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(path: Path, cp: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def wp1_search(client: OpenAlexClient, src: dict, top_n: int = 5) -> list[dict]:
    name = src["authfull"]
    data = client.get(
        "authors",
        {
            "search": name,
            "per-page": top_n,
            "select": AUTHOR_SELECT,
        },
        purpose="wp1_author_search",
        scholar_src_id=src["src_id"],
    )
    if not data:
        return []
    out = []
    for i, a in enumerate(data.get("results", [])):
        out.append(
            {
                "rank": i + 1,
                "author_id": a.get("id"),
                "display_name": a.get("display_name"),
                "orcid": a.get("orcid"),
                "works_count": a.get("works_count"),
                "cited_by_count": a.get("cited_by_count"),
                "last_known_institutions": a.get("last_known_institutions"),
                "ids": a.get("ids"),
            }
        )
    return out


def wp2_fetch_works(client: OpenAlexClient, src: dict, author_id: str, max_works: int | None) -> list[dict]:
    """用 cursor 翻页抓某作者全部作品；返回规范化记录。"""
    # filter 值必须是裸 ID（A5109160404），不能是 https://openalex.org/A... 否则 400
    bare_id = author_id.rstrip("/").split("/")[-1]
    works: list[dict] = []
    cursor = "*"
    page = 0
    per_page = min(PER_PAGE, max_works) if max_works else PER_PAGE
    while True:
        params = {
            "filter": f"authorships.author.id:{bare_id}",
            "per-page": per_page,
            "cursor": cursor,
            "select": WORK_SELECT,
        }
        data = client.get(
            "works",
            params,
            purpose="wp2_works",
            scholar_src_id=src["src_id"],
        )
        if not data:
            break
        results = data.get("results", [])
        for w in results:
            works.append(
                {
                    "src_id": src["src_id"],
                    "oa_author_id": author_id,
                    "work": w,
                }
            )
        page += 1
        meta = data.get("meta", {})
        nxt = meta.get("next_cursor")
        if not results or not nxt:
            break
        if max_works and len(works) >= max_works:
            break
        cursor = nxt
        if page >= 500:  # 安全阀：单作者最多 500 页
            print(f"[WP2][WARN] src_id={src['src_id']} 达到 500 页上限，截断")
            break
    return works


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10, help="本次处理多少学者（试跑先用 10）")
    ap.add_argument("--top-n", type=int, default=5, help="WP1 每人保留几个候选")
    ap.add_argument("--max-works", type=int, default=None, help="每人最多抓多少作品（None=全部）")
    ap.add_argument("--stage", choices=["wp1", "wp2", "all"], default="all")
    args = ap.parse_args()

    d = batch_dir()
    subset = load_jsonl(d / "subset.jsonl")
    if not subset:
        print("[ERR] 请先运行 wp0_import.py 生成 subset.jsonl")
        return
    cp = load_checkpoint(d / "checkpoint.json")
    req_log = d / "requests.jsonl"
    client = OpenAlexClient(req_log)

    if client.key:
        rl = client.rate_limit()
        if rl:
            print(f"[AUTH] key 已设置；额度：{rl.get('rate_limit', {})}")
        else:
            print("[AUTH] key 已设置，但额度查询失败，继续尝试")
    else:
        print("[AUTH] 未设置 OPENALEX_API_KEY：只能用匿名共享额度，且 cursor 翻页受限")

    todo = [s for s in subset[: args.limit]]
    print(f"[RUN] stage={args.stage} scholars={len(todo)} top_n={args.top_n} max_works={args.max_works}")

    cand_path = d / "candidates.jsonl"
    works_path = d / "works.jsonl"

    exhausted = False
    for src in todo:
        sid = str(src["src_id"])
        state = cp.setdefault(sid, {"wp1": False, "wp2": False, "works_count": 0})

        # ---- WP1 ----
        if args.stage in ("wp1", "all") and not state["wp1"]:
            cands = wp1_search(client, src, args.top_n)
            append_jsonl(cand_path, [{"src_id": src["src_id"], "candidates": cands,
                                      "searched_at": datetime.now(timezone.utc).isoformat()}])
            state["wp1"] = True
            state["candidates"] = len(cands)
            save_checkpoint(d / "checkpoint.json", cp)
            print(f"[WP1] src_id={sid} {src['authfull'][:40]:40s} -> {len(cands)} candidates")
        elif state["wp1"]:
            print(f"[WP1] src_id={sid} 已完成，跳过")

        # ---- WP2（只对第一个候选抓作品；消歧确认后再扩展）----
        if args.stage in ("wp2", "all") and state["wp1"] and not state["wp2"]:
            cands = [c for c in load_jsonl(cand_path) if c["src_id"] == src["src_id"]]
            if not cands or not cands[-1]["candidates"]:
                state["wp2"] = True
                state["works_count"] = 0
                save_checkpoint(d / "checkpoint.json", cp)
                print(f"[WP2] src_id={sid} 无候选，标记完成")
                continue
            top = cands[-1]["candidates"][0]
            aid = top["author_id"]
            if not aid:
                state["wp2"] = True
                save_checkpoint(d / "checkpoint.json", cp)
                continue
            # 额度耗尽时 wp2_fetch_works 会抛异常：此时不写入半截 results，
            # 也不标记 wp2 完成，下次续跑从该学者重抓（代码内对 requests.jsonl
            # 已成功页有留痕，但不会把不完整数据落进 works.jsonl）
            try:
                works = wp2_fetch_works(client, src, aid, args.max_works)
            except APIBudgetExhausted as e:
                print(f"[STOP] src_id={sid} 遇额度/速率上限：{e}")
                print("[STOP] 当前学者未标记完成，未写入半截作品；进度已保存，重置后续跑即可")
                exhausted = True
                break
            append_jsonl(works_path, works)
            state["wp2"] = True
            state["works_count"] = len(works)
            state["oa_author_id"] = aid
            save_checkpoint(d / "checkpoint.json", cp)
            print(f"[WP2] src_id={sid} author={aid} -> {len(works)} works")
        elif state["wp2"]:
            print(f"[WP2] src_id={sid} 已完成（{state['works_count']} works），跳过")

    done = sum(1 for s in todo if cp.get(str(s["src_id"]), {}).get("wp2"))
    if exhausted:
        print(f"[PAUSED] 因额度/速率上限暂停：{done}/{len(todo)} 学者完成；请求总数 {client.request_count}")
    else:
        print(f"[DONE] {done}/{len(todo)} 学者完成；请求总数 {client.request_count}；留痕 {req_log}")


if __name__ == "__main__":
    main()
