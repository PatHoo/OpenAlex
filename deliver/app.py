"""客户自助抓取网页应用（Flask 版）。

启动：`python app.py`（默认 http://127.0.0.1:8765）

REST 接口（与旧 http.server 版保持一致，前端无需改协议）：
- GET  /api/state            抓取进度快照
- POST /api/key              设置 API key（仅存内存，可选写本地缓存）
- POST /api/list             上传/粘贴名单 或 选预置名单子集
- POST /api/run              启动抓取（后台线程）
- POST /api/stop             手动停止
- GET  /api/export           导出结果 JSONL / 请求留痕 / 断点
- GET  /                     返回前端页面

安全边界：
- 仅监听 127.0.0.1，不对外暴露。
- key 只在内存（除非显式勾选"保存到本机"），不写请求留痕、不写日志。
- 所有抓取逻辑在 fetch_service.FetchRunner，可独立单测。
"""
from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file

from fetch_service import FetchRunner, RunState, load_preset_subset, parse_name_list

ROOT = Path(__file__).resolve().parent
DEFAULT_WORKDIR = ROOT / "data" / "self_serve"
KEY_CACHE = ROOT / ".self_serve_key"  # 可选本地缓存（仅当用户勾选保存）

app = Flask(__name__)

state = RunState()
runner: FetchRunner | None = None
runner_thread: threading.Thread | None = None
_key: str = ""
WORKDIR: Path = DEFAULT_WORKDIR


# ---- key 管理（仅内存 + 可选缓存文件）----
def _cached_key() -> str:
    if KEY_CACHE.exists():
        return KEY_CACHE.read_text(encoding="utf-8").strip()
    return ""


def _set_key(k: str, persist: bool) -> None:
    global _key
    _key = k.strip()
    if persist:
        KEY_CACHE.write_text(_key, encoding="utf-8")
    elif KEY_CACHE.exists():
        KEY_CACHE.unlink()


# ---- 页面 ----
@app.get("/")
def index():
    return send_file(ROOT / "self_serve.html")


@app.get("/api/state")
def api_state():
    snap = state.snapshot()
    snap["results"] = list(state.results)  # 前端预览已完成学者摘要
    return jsonify({"state": snap, "key_set": bool(_key or _cached_key())})


@app.post("/api/key")
def api_key():
    body = request.get_json(silent=True) or {}
    k = body.get("key", "")
    persist = bool(body.get("persist"))
    _set_key(k, persist)
    return jsonify({"ok": True, "key_set": bool(k)})


@app.post("/api/list")
def api_list():
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "preset")
    try:
        if mode == "paste":
            names = parse_name_list(body.get("text", ""))
        elif mode == "preset":
            limit = int(body.get("limit", 10))
            offset = int(body.get("offset", 0))
            names = load_preset_subset(limit, offset)
        else:
            return jsonify({"error": "unknown mode"}), 400
        if not names:
            return jsonify({"error": "名单为空"}), 400
        (WORKDIR / "roster.json").write_text(
            json.dumps(names, ensure_ascii=False), encoding="utf-8"
        )
        return jsonify({"ok": True, "count": len(names), "sample": names[:3]})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@app.post("/api/run")
def api_run():
    global runner, runner_thread
    if runner_thread and runner_thread.is_alive():
        return jsonify({"error": "已有抓取在进行中"}), 409
    roster = WORKDIR / "roster.json"
    if not roster.exists():
        return jsonify({"error": "请先加载名单"}), 400
    names = json.loads(roster.read_text(encoding="utf-8"))

    body = request.get_json(silent=True) or {}
    top_n = int(body.get("top_n", 5))
    max_works = body.get("max_works") or None
    if max_works is not None:
        max_works = int(max_works)

    runner = FetchRunner(WORKDIR, state, _key or _cached_key())
    runner_thread = threading.Thread(
        target=runner.run, args=(names, top_n, max_works), daemon=True
    )
    runner_thread.start()
    return jsonify({"ok": True, "total": len(names)})


@app.post("/api/stop")
def api_stop():
    if runner:
        runner.stop()
    return jsonify({"ok": True})


@app.get("/api/export")
def api_export():
    which = request.args.get("kind", "works")
    mapping = {
        "works": WORKDIR / "works.jsonl",
        "candidates": WORKDIR / "candidates.jsonl",
        "requests": WORKDIR / "requests.jsonl",
        "checkpoint": WORKDIR / "checkpoint.json",
    }
    p = mapping.get(which)
    if not p or not p.exists():
        return jsonify({"error": "无此结果文件"}), 404
    return send_file(p, as_attachment=True, download_name=p.name)


def main() -> None:
    global WORKDIR
    ap = argparse.ArgumentParser(description="客户自助抓取本地服务（Flask）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--batch",
        default=None,
        help="数据输出 batch 名（相对本工具 data/，如 batch_001 或 self_serve）。"
             "默认 self_serve；指定 batch_001 则与下游 WP3 分析链同目录，抓完可直接跑分析。",
    )
    args = ap.parse_args()

    if args.batch:
        WORKDIR = ROOT / "data" / args.batch
    WORKDIR.mkdir(parents=True, exist_ok=True)

    print(f"[self-serve] 已启动：http://{args.host}:{args.port}")
    print(f"[self-serve] 数据目录：{WORKDIR}")
    # Flask 开发服务器单线程即可（后台抓取在独立线程，用 threaded=True 保证并发响应）
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
