"""本地网页抓取工具的服务端入口：`python fetch_server.py`

起一个本地 HTTP 服务，供客户浏览器打开 self-serve 页面：
- GET  /api/state            抓取进度快照
- POST /api/key              设置 API key（仅存内存，可选写本地缓存）
- POST /api/list             上传/粘贴名单 或 选预置名单子集
- POST /api/run              启动抓取（后台线程）
- POST /api/stop             手动停止
- GET  /api/export           导出结果 JSONL / 请求留痕 / 断点
- GET  /                     返回 self-serve 页面

安全边界：
- 仅监听 127.0.0.1，不对外暴露。
- key 只在内存（除非显式勾选"保存到本机"），不写请求留痕、不写日志。
- 所有抓取逻辑在 fetch_service.FetchRunner，可独立单测。
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fetch_service import (
    FetchRunner,
    RunState,
    load_preset_subset,
    parse_name_list,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_WORKDIR = ROOT / "data" / "self_serve"

KEY_CACHE = ROOT / ".self_serve_key"  # 可选本地缓存（仅当用户勾选保存）

state = RunState()
runner: FetchRunner | None = None
runner_thread: threading.Thread | None = None
_key: str = ""

# 工作目录由启动参数决定；存到模块级变量，供 Handler 读取
WORKDIR = DEFAULT_WORKDIR


def _cached_key() -> str:
    if KEY_CACHE.exists():
        return KEY_CACHE.read_text(encoding="utf-8").strip()
    return ""


def set_key(k: str, persist: bool) -> None:
    global _key
    _key = k.strip()
    if persist:
        KEY_CACHE.write_text(_key, encoding="utf-8")
    elif KEY_CACHE.exists():
        KEY_CACHE.unlink()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        return json.loads(raw) if raw else {}

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            html = (ROOT / "self_serve.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path == "/api/state":
            self._json(200, {"state": state.snapshot(), "key_set": bool(_key or _cached_key())})
            return
        if path == "/api/export":
            self._do_export()
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_body()
        if path == "/api/key":
            k = body.get("key", "")
            persist = bool(body.get("persist"))
            set_key(k, persist)
            self._json(200, {"ok": True, "key_set": bool(k)})
            return
        if path == "/api/list":
            self._do_list(body)
            return
        if path == "/api/run":
            self._do_run(body)
            return
        if path == "/api/stop":
            self._do_stop()
            return
        self._json(404, {"error": "not found"})

    # ---- 业务 ----
    def _do_list(self, body: dict) -> None:
        mode = body.get("mode", "preset")
        try:
            if mode == "paste":
                text = body.get("text", "")
                names = parse_name_list(text)
            elif mode == "preset":
                limit = int(body.get("limit", 10))
                offset = int(body.get("offset", 0))
                names = load_preset_subset(limit, offset)
            else:
                self._json(400, {"error": "unknown mode"})
                return
            if not names:
                self._json(400, {"error": "名单为空"})
                return
            # 保存名单供 run 使用
            (WORKDIR / "roster.json").write_text(
                json.dumps(names, ensure_ascii=False), encoding="utf-8"
            )
            self._json(200, {"ok": True, "count": len(names), "sample": names[:3]})
        except Exception as e:  # noqa: BLE001
            self._json(500, {"error": str(e)})

    def _do_run(self, body: dict) -> None:
        global runner, runner_thread
        if runner_thread and runner_thread.is_alive():
            self._json(409, {"error": "已有抓取在进行中"})
            return
        roster = WORKDIR / "roster.json"
        if not roster.exists():
            self._json(400, {"error": "请先加载名单"})
            return
        names = json.loads(roster.read_text(encoding="utf-8"))
        top_n = int(body.get("top_n", 5))
        max_works = body.get("max_works") or None
        if max_works is not None:
            max_works = int(max_works)

        runner = FetchRunner(WORKDIR, state, _key or _cached_key())
        runner_thread = threading.Thread(
            target=runner.run, args=(names, top_n, max_works), daemon=True
        )
        runner_thread.start()
        self._json(200, {"ok": True, "total": len(names)})

    def _do_stop(self) -> None:
        if runner:
            runner.stop()
        self._json(200, {"ok": True})

    def _do_export(self) -> None:
        which = parse_qs(urlparse(self.path).query).get("kind", ["works"])[0]
        mapping = {
            "works": WORKDIR / "works.jsonl",
            "candidates": WORKDIR / "candidates.jsonl",
            "requests": WORKDIR / "requests.jsonl",
            "checkpoint": WORKDIR / "checkpoint.json",
        }
        p = mapping.get(which)
        if not p or not p.exists():
            self._json(404, {"error": "无此结果文件"})
            return
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header(
            "Content-Disposition", f'attachment; filename="{p.name}"'
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    global WORKDIR
    ap = argparse.ArgumentParser(description="客户自助抓取本地服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--batch",
        default=None,
        help="数据输出 batch 名（相对 pipeline/data/，如 batch_001 或 self_serve）。"
             "默认 self_serve；指定 batch_001 则与下游 WP3 分析链同目录，抓完可直接跑分析。",
    )
    args = ap.parse_args()

    if args.batch:
        WORKDIR = ROOT / "data" / args.batch
    WORKDIR.mkdir(parents=True, exist_ok=True)

    server = HTTPServer((args.host, args.port), Handler)
    print(f"[self-serve] 已启动：http://{args.host}:{args.port}")
    print(f"[self-serve] 数据目录：{WORKDIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[self-serve] 已停止")


if __name__ == "__main__":
    main()
