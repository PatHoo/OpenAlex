#!/usr/bin/env python3
"""查看 OpenAlex API 额度 + 本地 1,000 人跑批进度。

只读：rate-limit 端点免费、不计入预算；不写任何文件、不回显 key。

用法（WSL）：
  python3 /mnt/c/Users/Administrator/Desktop/OpenAlex/design/check_quota.py

云主机或 env 在别处时：
  OA_ENV_DIR=/etc/openalex OPENALEX_ROOT=/srv/openalex python3 check_quota.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

RATE_URL = "https://api.openalex.org/rate-limit"
DEFAULT_ENV_DIR = "/home/v/.openalex_env"


def parse_env_file(path: Path) -> dict[str, str]:
    """读 shell 风格的 KEY=value（不 source，避免执行文件内容）。"""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def collect_keys(env_dir: Path) -> list[tuple[str, str]]:
    """环境变量优先，api.env 补齐；编号 key 优先于 OPENALEX_API_KEYS。"""
    merged = dict(os.environ)
    for k, v in parse_env_file(env_dir / "api.env").items():
        if k.startswith("OPENALEX_API_KEY") and not merged.get(k):
            merged[k] = v
    keys: list[tuple[str, str]] = []
    for i in range(1, 9):
        v = merged.get(f"OPENALEX_API_KEY_{i}", "").strip()
        if v:
            keys.append((f"KEY{i}", v))
    if not keys:
        csv = merged.get("OPENALEX_API_KEYS", "")
        keys = [(f"KEY{i + 1}", v.strip())
                for i, v in enumerate(csv.split(",")) if v.strip()]
    return keys


def mask(key: str) -> str:
    return key[:4] + "****" + key[-4:] if len(key) > 8 else "***"


def format_reset(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (dt + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S") + " 北京时间"
    except ValueError:
        return str(iso)


def query_quota(key: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(
        RATE_URL,
        headers={"Authorization": f"Bearer {key}",
                 "User-Agent": "openalex-quota-check/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def show_local_progress(root: Path) -> None:
    d = root / "pipeline" / "data" / "batch_001"
    print("\n=== 本地跑批进度 ===")
    ledger_path = d / "cost_ledger.json"
    data: dict = {}
    if ledger_path.exists():
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        spent = float(data.get("spent_usd", 0.0))
        cap = float(data.get("cap_usd", 0.0))
        print(f"成本账本   : ${spent:.4f} / ${cap:.2f}"
              f"（剩余 ${max(0.0, cap - spent):.4f}）")
        c = data.get("counts", {})
        print(f"计费请求   : search={c.get('wp1_author_search', 0)}  "
              f"list={c.get('wp2_works', 0)}  "
              f"免费查询={c.get('rate_limit_check', 0)}")
    else:
        print("成本账本   : 尚未生成")

    log = d / "requests.jsonl"
    if log.exists():
        with log.open(encoding="utf-8") as f:
            n = sum(1 for line in f if line.strip())
        print(f"请求日志   : {n} 条")
    else:
        print("请求日志   : 尚未生成")

    cp = d / "checkpoint.json"
    done = 0
    if cp.exists():
        raw = json.loads(cp.read_text(encoding="utf-8"))
        done = sum(1 for v in raw.values()
                   if isinstance(v, dict) and v.get("wp2"))
    print(f"已完成学者 : {done} / 1000")
    if done and data:
        spent = float(data.get("spent_usd", 0.0))
        cap = float(data.get("cap_usd", 0.0))
        per = spent / done
        remain = max(0.0, cap - spent)
        print(f"按当前单价 : 剩余额度约可再跑 {int(remain / per) if per else 0} 人"
              f"（人均 ${per:.4f}）")


def main() -> int:
    ap = argparse.ArgumentParser(description="查看 OpenAlex 额度与跑批进度")
    ap.add_argument("--env-dir", default=os.environ.get("OA_ENV_DIR", DEFAULT_ENV_DIR),
                    help=f"凭据目录，默认 {DEFAULT_ENV_DIR}")
    ap.add_argument("--root", default=os.environ.get(
        "OPENALEX_ROOT", str(Path(__file__).resolve().parent.parent)),
        help="项目根目录，默认脚本上一级")
    ap.add_argument("--timeout", type=int, default=15, help="单把 key 查询超时秒数")
    args = ap.parse_args()

    keys = collect_keys(Path(args.env_dir))
    if not keys:
        print(f"[ERR] 没有找到 API key：检查 {args.env_dir}/api.env 或环境变量",
              file=sys.stderr)
        return 2

    print("=== OpenAlex 实时额度 ===")
    for label, key in keys:
        try:
            d = query_quota(key, args.timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            print(f"[{label} {mask(key)}] HTTP {e.code}: {body}")
            continue
        except Exception as e:  # noqa: BLE001 - 网络异常只提示，不影响其他 key
            print(f"[{label} {mask(key)}] 查询失败：{type(e).__name__}: {e}")
            continue

        rl = d.get("rate_limit", {})
        budget = rl.get("daily_budget_usd")
        used = rl.get("daily_used_usd")
        remain = rl.get("daily_remaining_usd")
        prepaid = rl.get("prepaid_remaining_usd", 0)
        print(f"[{label} {mask(key)}] {d.get('api_key', '')}")
        print(f"  每日额度   : ${remain} / ${budget} 剩余（已用 ${used}）")
        if prepaid:
            print(f"  预付余额   : ${prepaid}")
        print(f"  重置时间   : {format_reset(rl.get('resets_at'))}")
        limits = rl.get("per_second_limits", {})
        print(f"  每秒上限   : {limits.get('all_requests', '?')} req/s"
              f"（新账号首 $1 按 $0.10/5min 释放）")

    show_local_progress(Path(args.root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
