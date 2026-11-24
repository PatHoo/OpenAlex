"""1,000 人重跑执行脚本：双 key 轮换 / 断点续跑 / 成本上限 / 落库与分析。

设计要点
--------
1. 抓取阶段复用 wp12_fetch 的 WP1/WP2 函数与 checkpoint.json 语义，
   不另造断点格式；失败时停在当前学者，不写空候选、不写半截作品。
2. API key 只从环境变量读取：
   OPENALEX_API_KEY_1 / OPENALEX_API_KEY_2（推荐）或 OPENALEX_API_KEYS（逗号分隔）。
   遇到 429 daily_budget 自动切到下一把 key，从当前学者继续；两把都耗尽则干净暂停。
3. 成本闸门在请求发出前判断：search $0.001/次、list+filter $0.0001/次、
   rate-limit 免费。累计超过 OA_COST_CAP_USD（默认 2.20）立即暂停，
   账本 cost_ledger.json 跨续跑累计，不会因重跑清零。
4. --stage db 在写库前先 pg_dump 备份；落库/分析顺序与 pipeline/README.md 一致。

用法
----
  # 只抓取（推荐先跑这个，检查 works.jsonl 后再落库）
  OPENALEX_API_KEY_1=keyA OPENALEX_API_KEY_2=keyB OA_RATE=30 \
    python -u rerun_1000.py --stage fetch

  # 抓取 + 落库 + 分析 + 重导出交付物
  OPENALEX_API_KEY_1=keyA OPENALEX_API_KEY_2=keyB OA_RATE=30 \
    python -u rerun_1000.py --stage all

  # 只看会做什么，不发请求、不写库
  python rerun_1000.py --stage fetch --dry-run

  # 3 人冒烟（不花多少额度）
  OPENALEX_API_KEY_1=keyA python -u rerun_1000.py --stage fetch --limit 3

  # 从零重跑（先把旧 batch_001 整个改名备份，再重生成 subset）
  OPENALEX_API_KEY_1=keyA OPENALEX_API_KEY_2=keyB \
    python -u rerun_1000.py --stage fetch --fresh
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from config import batch_dir, path_mode, pg_path  # noqa: E402
from dbutil import psql_command  # noqa: E402
from oa_client import APIBudgetExhausted, OpenAlexClient  # noqa: E402
import wp12_fetch  # noqa: E402

# 官方计费（help.openalex.org，2026-11 口径）：
# /authors?search= 属 search $0.001/次；/works?filter= 属 list+filter $0.0001/次。
REQUEST_PRICES: dict[str, float] = {
    "wp1_author_search": 0.001,
    "wp2_works": 0.0001,
    "rate_limit_check": 0.0,
}
DEFAULT_COST_CAP = 2.20
PAGES_PER_SCHOLAR = 9.76  # 实测 9,759 页 / 1,000 人，仅用于 dry-run 估算

# 落库 + 分析 + 导出顺序（与 pipeline/README.md「运行顺序」一致）
# ("py", 脚本名) 走当前 Python；("sql", 种子文件) 走 psql -f，路径在 design/；
# ("batchsql", SQL 文件) 走 psql -f，路径在 batch_dir（WP0 生成的名单子集）。
# subset_source.sql 必须在 wp4_load_decisions 之前：scholar.src_id 外键引用
# source_scholar，空库若跳过这步会报 scholar_src_id_fkey（2026-11-24 实测踩坑）。
DB_STEPS: list[tuple[str, str]] = [
    ("batchsql", "subset_source.sql"),
    ("py", "wp3_import_works.py"),
    ("py", "wp4_analyze_candidates.py"),
    ("py", "wp4_disambiguate.py"),
    ("py", "wp4_load_decisions.py"),
    ("py", "wp5_import_authorship.py"),
    ("py", "wp6_extract_institutions.py"),
    ("py", "wp7_load_institutions.py"),
    ("py", "wp8_backfill_institution.py"),
    ("py", "wp9_build_panel.py"),
    ("py", "wp10_event_engine.py"),
    ("sql", "class_rule_85_seed.sql"),
    ("py", "wp11_classify.py"),
    ("sql", "cohort_did_seed.sql"),
    ("sql", "external_evidence_seed.sql"),
    ("sql", "did_robust_seed.sql"),
    ("py", "wp13_cohort_screen.py"),
    ("py", "wp15_external_evidence.py"),
    ("py", "wp14_did.py"),
    ("py", "wp16_did_robust.py"),
    ("py", "export_requests_sql.py"),
    ("py", "export_platform_snapshot.py"),
    ("py", "export_client_workbook.py"),
    ("py", "export_local_tools.py"),
]


class CostCapExceeded(RuntimeError):
    """累计成本将超过上限：在请求发出前抛出，保证不多花一分钱。"""


class AllKeysExhausted(RuntimeError):
    """所有 key 当日额度均耗尽：干净暂停，checkpoint 保留现场。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


# ---------- 成本账本 ----------
class CostLedger:
    """累计成本账本；优先读 cost_ledger.json，首次运行时从 requests.jsonl 反推。"""

    def __init__(self, path: Path, cap: float, spent: float = 0.0,
                 counts: dict[str, int] | None = None) -> None:
        self.path = path
        self.cap = cap
        self.spent = spent
        self.counts = counts or {k: 0 for k in REQUEST_PRICES}

    @classmethod
    def load(cls, path: Path, requests_log: Path, cap: float,
             *, persist: bool = True) -> "CostLedger":
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(path, cap, float(raw.get("spent_usd", 0.0)),
                       dict(raw.get("counts", {})))
        counts = {k: 0 for k in REQUEST_PRICES}
        if requests_log.exists():
            with requests_log.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # 只有 200 的成功请求计费；重试/429 不计费
                    if r.get("http_status") == 200:
                        p = r.get("purpose", "")
                        if p in counts:
                            counts[p] += 1
        spent = sum(counts[p] * price for p, price in REQUEST_PRICES.items())
        ledger = cls(path, cap, spent, counts)
        if persist:
            ledger.save()
        return ledger

    def check(self, purpose: str) -> None:
        price = REQUEST_PRICES.get(purpose, 0.0)
        if price > 0 and self.spent + price > self.cap + 1e-9:
            raise CostCapExceeded(
                f"成本上限 ${self.cap:.2f} 将达标（已用 ${self.spent:.4f} "
                f"+ 本次 ${price:.4f}）；提高 OA_COST_CAP_USD 后续跑"
            )

    def charge(self, purpose: str) -> None:
        price = REQUEST_PRICES.get(purpose, 0.0)
        if price <= 0:
            return
        self.spent += price
        self.counts[purpose] = self.counts.get(purpose, 0) + 1
        self.save()

    def save(self) -> None:
        payload = {
            "spent_usd": round(self.spent, 6),
            "counts": self.counts,
            "cap_usd": self.cap,
            "updated_at": utc_now(),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def summary(self) -> str:
        return (f"${self.spent:.4f}/${self.cap:.2f} "
                f"(search={self.counts.get('wp1_author_search', 0)}, "
                f"list={self.counts.get('wp2_works', 0)})")


# ---------- 带成本闸门的客户端 ----------
class BudgetedClient(OpenAlexClient):
    def __init__(self, key: str, log_path: Path, batch_tag: str, ledger: CostLedger) -> None:
        # OpenAlexClient 在构造时读 OPENALEX_API_KEY；临时设置后恢复，避免污染子进程
        old = os.environ.get("OPENALEX_API_KEY")
        os.environ["OPENALEX_API_KEY"] = key
        try:
            super().__init__(log_path, batch_tag)
        finally:
            if old is None:
                os.environ.pop("OPENALEX_API_KEY", None)
            else:
                os.environ["OPENALEX_API_KEY"] = old
        self.ledger = ledger

    def get(self, path: str, params: dict[str, Any] | None = None, *,
            purpose: str = "", scholar_src_id: int | None = None) -> dict[str, Any] | None:
        # 在请求发出前拦截，而不是事后记账——否则超限那一笔已经花出去了
        self.ledger.check(purpose)
        data = super().get(path, params, purpose=purpose, scholar_src_id=scholar_src_id)
        if data is not None:
            self.ledger.charge(purpose)
        return data


# ---------- key 管理 ----------
def load_keys() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for i in range(1, 9):
        v = os.environ.get(f"OPENALEX_API_KEY_{i}", "").strip()
        if v:
            out.append((f"KEY{i}", v))
    if not out:
        csv = os.environ.get("OPENALEX_API_KEYS", "")
        out = [(f"KEY{i + 1}", v.strip()) for i, v in enumerate(csv.split(",")) if v.strip()]
    seen: set[str] = set()
    uniq = []
    for label, key in out:
        if key not in seen:
            seen.add(key)
            uniq.append((label, key))
    return uniq


def pg_dump_command(out_path: Path) -> list[str]:
    args = [
        "pg_dump",
        "-h", os.environ.get("PGHOST", "127.0.0.1"),
        "-p", os.environ.get("PGPORT", "5432"),
        "-U", os.environ.get("PGUSER", "postgres"),
        "-d", os.environ.get("PGDATABASE", "openalex"),
        "-Fc", "-f", pg_path(out_path),
    ]
    if path_mode() == "wsl":
        password = os.environ.get("PGPASSWORD", "").strip()
        prefix = (f"PGPASSWORD={shlex.quote(password)} " if password
                  else "PGPASSWORD=$(cat ~/.openalex_db_pass) ")
        return ["wsl", "-e", "bash", "-c", prefix + shlex.join(args)]
    return args


# ---------- 抓取阶段 ----------
def backup_batch(d: Path) -> Path | None:
    if not d.exists():
        return None
    dest = d.parent / f"{d.name}_bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    d.rename(dest)
    print(f"[FRESH] 旧批次已改名备份：{dest}")
    return dest


def ensure_subset(d: Path, limit: int | None, dry_run: bool = False) -> list[dict]:
    sub = d / "subset.jsonl"
    if not sub.exists():
        if dry_run:
            print("[DRY-RUN] subset.jsonl 不存在；正式运行会先执行 wp0_import.py")
            return []
        print("[WP0] subset.jsonl 不存在，运行 wp0_import.py 生成")
        subprocess.run([sys.executable, "-u", str(HERE / "wp0_import.py")],
                       cwd=HERE, check=True)
    rows = wp12_fetch.load_jsonl(sub)
    return rows[:limit] if limit else rows


def process_scholar(client: BudgetedClient, src: dict, cp: dict, d: Path,
                    top_n: int, max_works: int | None) -> None:
    """与 wp12_fetch.main 的单学者语义完全一致；断点写在同一份 checkpoint。"""
    sid = str(src["src_id"])
    state = cp.setdefault(sid, {"wp1": False, "wp2": False, "works_count": 0})
    cp_path = d / "checkpoint.json"
    cand_path = d / "candidates.jsonl"
    works_path = d / "works.jsonl"

    if not state["wp1"]:
        cands = wp12_fetch.wp1_search(client, src, top_n)
        wp12_fetch.append_jsonl(cand_path, [{
            "src_id": src["src_id"],
            "candidates": cands,
            "searched_at": utc_now(),
        }])
        state["wp1"] = True
        state["candidates"] = len(cands)
        wp12_fetch.save_checkpoint(cp_path, cp)

    if state["wp2"]:
        return
    cands = [c for c in wp12_fetch.load_jsonl(cand_path) if c["src_id"] == src["src_id"]]
    if not cands or not cands[-1]["candidates"]:
        state["wp2"] = True
        state["works_count"] = 0
        wp12_fetch.save_checkpoint(cp_path, cp)
        return
    aid = cands[-1]["candidates"][0].get("author_id")
    if not aid:
        state["wp2"] = True
        wp12_fetch.save_checkpoint(cp_path, cp)
        return
    works = wp12_fetch.wp2_fetch_works(client, src, aid, max_works)
    wp12_fetch.append_jsonl(works_path, works)
    state["wp2"] = True
    state["works_count"] = len(works)
    state["oa_author_id"] = aid
    wp12_fetch.save_checkpoint(cp_path, cp)


def run_fetch(args: argparse.Namespace, ledger: CostLedger) -> int:
    d = HERE / "data" / args.batch if args.dry_run else batch_dir(args.batch)
    if args.fresh and not args.dry_run:
        backup_batch(d)
        d = batch_dir(args.batch)

    todo = ensure_subset(d, args.limit, args.dry_run)
    cp = wp12_fetch.load_checkpoint(d / "checkpoint.json")
    keys = load_keys()
    if not keys and not args.dry_run:
        print("[ERR] 请设置 OPENALEX_API_KEY_1 / OPENALEX_API_KEY_2（或 OPENALEX_API_KEYS）",
              file=sys.stderr)
        return 2

    remaining = [s for s in todo if not cp.get(str(s["src_id"]), {}).get("wp2")]
    todo_wp1 = [s for s in remaining if not cp.get(str(s["src_id"]), {}).get("wp1")]
    est = len(todo_wp1) * REQUEST_PRICES["wp1_author_search"] + \
        len(remaining) * PAGES_PER_SCHOLAR * REQUEST_PRICES["wp2_works"]
    print(f"[PLAN] 学者 {len(todo)} 人，待抓 {len(remaining)} 人（其中 WP1 待做 {len(todo_wp1)}）")
    key_text = ", ".join(f"{lb}={mask_key(k)}" for lb, k in keys) if keys else "未设置（正式运行前必须设置）"
    print(f"[PLAN] key: {key_text}")
    print(f"[PLAN] 成本账本: {ledger.summary()}；本次粗估 ${est:.2f}（含重试缓冲请留 10%）")
    if args.dry_run:
        print("[DRY-RUN] 未发出任何请求，未改动数据")
        return 0

    req_log = d / "requests.jsonl"
    clients: dict[int, BudgetedClient] = {}

    def client_for(i: int) -> BudgetedClient:
        if i not in clients:
            label, key = keys[i]
            print(f"[KEY] 切换到 {label} ({mask_key(key)})")
            clients[i] = BudgetedClient(key, req_log, args.batch, ledger)
            try:
                rl = clients[i].rate_limit()
                if rl and isinstance(rl.get("rate_limit"), dict):
                    print(f"[KEY] {label} 额度: {rl['rate_limit']}")
            except Exception as e:  # 额度查询免费，失败不应中断跑批
                print(f"[KEY] {label} 额度查询失败（继续）: {e}")
        return clients[i]

    key_i = 0
    started = time.monotonic()
    done = 0
    try:
        for n, src in enumerate(remaining, start=1):
            sid = str(src["src_id"])
            attempts = 0
            while True:
                try:
                    process_scholar(client_for(key_i), src, cp, d,
                                    args.top_n, args.max_works)
                    done += 1
                    break
                except APIBudgetExhausted as e:
                    print(f"[PAUSE] src_id={sid} 额度耗尽：{e}")
                    if key_i + 1 >= len(keys):
                        raise AllKeysExhausted("所有 key 当日额度均耗尽") from e
                    key_i += 1
                    attempts += 1
                    continue
                except wp12_fetch.APIFetchFailed as e:
                    # 网络/非限流失败：轮换一次 key 再试；仍失败则暂停，现场保留
                    print(f"[WARN] src_id={sid} 请求失败：{e}")
                    if key_i + 1 >= len(keys) or attempts >= len(keys):
                        raise
                    key_i += 1
                    attempts += 1
                    continue
            if n % 25 == 0 or n == len(remaining):
                elapsed = time.monotonic() - started
                print(f"[PROGRESS] {n}/{len(remaining)} 学者 | {ledger.summary()} "
                      f"| 用时 {elapsed / 60:.1f} min | {elapsed / max(n, 1):.1f} s/人",
                      flush=True)
    except (AllKeysExhausted, CostCapExceeded, wp12_fetch.APIFetchFailed) as e:
        print(f"[PAUSED] {type(e).__name__}: {e}", file=sys.stderr)
        print(f"[PAUSED] 已完成本轮 {done} 人；断点在 {d / 'checkpoint.json'}，"
              f"成本账本 {ledger.path}；修复后续跑同一条命令即可", file=sys.stderr)
        return 3

    print(f"[DONE] 本轮完成 {done}/{len(remaining)} 人；成本 {ledger.summary()}")
    print(f"[DONE] works.jsonl={d / 'works.jsonl'}；checkpoint={d / 'checkpoint.json'}")
    return 0


# ---------- 落库 / 分析 / 导出阶段 ----------
def export_mapping_files(batch: str) -> None:
    """WP5 依赖的两个映射文件此前靠手工从库导出、从未进仓库。

    空库重跑时若缺失会在 wp5 load_maps 阶段 FileNotFoundError。这里在落库前
    从 identity_map / work 重建，保证 --stage db 从头跑也能自愈。
      _scholar_map.txt: src_id|oa_author_id|scholar_id
      _work_map.txt:    work_id|openalex_id
    """
    d = batch_dir(batch)
    smap = d / "_scholar_map.txt"
    wmap = d / "_work_map.txt"
    if smap.exists() and wmap.exists():
        return
    from dbutil import query_psql

    if not smap.exists():
        rows = query_psql(
            "SELECT s.src_id || '|' || im.external_id || '|' || s.scholar_id "
            "FROM identity_map im JOIN scholar s USING (scholar_id) "
            "WHERE im.scheme = 'openalex';")
        smap.write_text("\n".join(rows.splitlines()) + "\n"
                        if rows else "", encoding="utf-8")
        print(f"[EXPORT] {smap.name}: {len(smap.read_text(encoding='utf-8').splitlines())} 行")
    if not wmap.exists():
        rows = query_psql("SELECT work_id || '|' || openalex_id FROM work;")
        wmap.write_text("\n".join(rows.splitlines()) + "\n"
                        if rows else "", encoding="utf-8")
        print(f"[EXPORT] {wmap.name}: {len(wmap.read_text(encoding='utf-8').splitlines())} 行")


def run_db(args: argparse.Namespace) -> int:
    if args.dry_run:
        print("[DRY-RUN] 将按顺序执行：")
        for kind, name in DB_STEPS:
            label = {"py": "python ", "sql": "psql -f design/",
                     "batchsql": "psql -f batch/"}[kind]
            print(f"  {label}{name}")
        return 0

    backup_dir = ROOT / "pipeline" / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dump = backup_dir / f"openalex_{datetime.now().strftime('%Y%m%d_%H%M%S')}.dump"
    if not args.skip_db_backup:
        print(f"[BACKUP] pg_dump -> {dump}")
        subprocess.run(pg_dump_command(dump), check=True)
    else:
        print("[BACKUP] 已按 --skip-db-backup 跳过（不建议）")

    print("[EXPORT] 校验 WP5 依赖的映射文件", flush=True)
    export_mapping_files(args.batch)

    for kind, name in DB_STEPS:
        if kind == "py":
            print(f"\n[STEP] python {name}", flush=True)
            r = subprocess.run([sys.executable, "-u", str(HERE / name)], cwd=HERE)
        elif kind == "batchsql":
            print(f"\n[STEP] psql -f batch/{name}", flush=True)
            r = subprocess.run(psql_command(sql_file=batch_dir(args.batch) / name), cwd=HERE)
        else:
            print(f"\n[STEP] psql -f design/{name}", flush=True)
            r = subprocess.run(psql_command(sql_file=ROOT / "design" / name), cwd=HERE)
        if r.returncode != 0:
            print(f"[FATAL] {name} 退出码 {r.returncode}，后续步骤未执行；"
                  f"备份在 {dump}", file=sys.stderr)
            return r.returncode
    print(f"\n[DB DONE] 落库、分析、导出完成；备份在 {dump}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="1,000 人重跑：双 key 轮换 / 断点续跑 / 成本上限")
    ap.add_argument("--stage", choices=["fetch", "db", "all"], default="fetch")
    ap.add_argument("--batch", default="batch_001")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 人（冒烟用）")
    ap.add_argument("--top-n", type=int, default=5)
    ap.add_argument("--max-works", type=int, default=None)
    ap.add_argument("--fresh", action="store_true",
                    help="先把旧 batch 目录改名备份，再从 WP0 重新生成子集")
    ap.add_argument("--cost-cap", type=float, default=None,
                    help="美元；默认取 OA_COST_CAP_USD，未设则 2.20")
    ap.add_argument("--skip-db-backup", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cap = args.cost_cap
    if cap is None:
        try:
            cap = float(os.environ.get("OA_COST_CAP_USD", str(DEFAULT_COST_CAP)))
        except ValueError:
            print("[ERR] OA_COST_CAP_USD 不是数字", file=sys.stderr)
            return 2
    d = HERE / "data" / args.batch if args.dry_run else batch_dir(args.batch)
    if args.dry_run:
        # dry-run 不写账本、不建目录；成本按当前 requests.jsonl 只读推算
        ledger = CostLedger.load(d / "cost_ledger.json", d / "requests.jsonl",
                                 cap, persist=False)
    else:
        ledger = CostLedger.load(d / "cost_ledger.json", d / "requests.jsonl", cap)

    if args.stage in ("fetch", "all"):
        rc = run_fetch(args, ledger)
        if rc != 0 or args.stage == "fetch":
            return rc
    return run_db(args)


if __name__ == "__main__":
    raise SystemExit(main())
