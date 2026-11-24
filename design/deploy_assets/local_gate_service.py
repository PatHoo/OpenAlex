#!/usr/bin/env python3
"""本地模拟门禁服务（对应生产 gate_service.py）。

复用生产版的 sharecode / Cookie 校验算法（HMAC-SHA256，Cookie 只放摘要不放明文），
适配本地 HTTP：Cookie 不带 Secure；下载不跳 COS/CDN，而是由网关直接读本地文件。

监听 127.0.0.1:8765，只由本地 nginx（auth_request / 下载内部跳转）调用，不对公网开放。
敏感值从环境变量读取（本地由 gate.env 加载），不写进代码、不进 Git。
"""
import hashlib
import hmac
import html
import mimetypes
import os
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SHARECODE = os.environ["SHARECODE"]
COOKIE_SECRET = os.environ["COOKIE_SECRET"].encode()
COOKIE_NAME = "oa_access"
COOKIE_TTL = 86400
EXPECTED = hmac.new(COOKIE_SECRET, SHARECODE.encode(), hashlib.sha256).hexdigest()

# 本地直出文件；生产版 gate_service.py 这里改成 302 到 COS/CDN 签名 URL
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/home/v/openalex-sim/www/web/download"))

ALLOWED_DOWNLOADS = {
    "新三学者基线与增量对照数据_数据库复现版.xlsx",
    "学者流动分析成果_1000人试跑版.xlsx",
}

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>第二期 DEMO · 分享码验证</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;
background:#0f172a;color:#e2e8f0;font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.box{width:min(420px,90vw);background:#1e293b;padding:30px;border-radius:12px;
box-shadow:0 20px 50px rgba(0,0,0,.4)}h1{margin:0 0 6px;font-size:20px}
p{margin:0 0 22px;color:#94a3b8;font-size:13px}
input,button{width:100%;padding:11px 14px;border-radius:7px;border:1px solid #475569;
background:#0f172a;color:#e2e8f0;font-size:15px}
input:focus{outline:none;border-color:#3b82f6}
button{margin-top:14px;background:#2563eb;border:none;font-weight:600;cursor:pointer}
button:hover{background:#1d4ed8}
.tip{margin-top:16px;color:#64748b;font-size:12px}
</style></head><body>
<div class="box">
  <h1>OpenAlex 科研分析平台</h1>
  <p>第二期 DEMO · 请输入分享码继续</p>
  <form method="get" action="/login">
    <input type="hidden" name="next" value="__NEXT__">
    <input type="text" name="sharecode" placeholder="分享码" autofocus required autocomplete="off">
    <button type="submit">进入平台</button>
  </form>
  <div class="tip">本地测试分享码：<b>LocalTest2026</b></div>
</div></body></html>"""


def send(handler, code, body=b"", headers=None):
    handler.send_response(code)
    for k, v in (headers or {}).items():
        handler.send_header(k, v)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenAlexLocalGate/1.0"

    def authed(self):
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        morsel = jar.get(COOKIE_NAME)
        return bool(morsel) and hmac.compare_digest(morsel.value, EXPECTED)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/auth":
            send(self, 200 if self.authed() else 401,
                 headers={"Cache-Control": "no-store"})
            return

        if path == "/login":
            q = urllib.parse.parse_qs(parsed.query)
            code = q.get("sharecode", [""])[0]
            target = q.get("next", ["/web/index.html"])[0]
            if not target.startswith("/") or target.startswith("//"):
                target = "/web/index.html"
            # 未带 sharecode：显示输入页（生产版靠外部系统带码，本地补一个便于测试）
            if not code:
                page = LOGIN_PAGE.replace("__NEXT__", html.escape(target, quote=True))
                send(self, 200, page.encode("utf-8"),
                     {"Content-Type": "text/html; charset=utf-8",
                      "Cache-Control": "no-store"})
                return
            if not hmac.compare_digest(code, SHARECODE):
                send(self, 403, b"invalid sharecode\n",
                     {"Content-Type": "text/plain; charset=utf-8",
                      "Cache-Control": "no-store"})
                return
            # 本地 HTTP：不带 Secure，否则浏览器不会回传 Cookie
            cookie = "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" % (
                COOKIE_NAME, EXPECTED, COOKIE_TTL)
            send(self, 302, headers={
                "Set-Cookie": cookie,
                "Location": target,
                "Cache-Control": "no-store",
            })
            return

        if path == "/logout":
            send(self, 302, headers={
                "Set-Cookie": "%s=; Path=/; Max-Age=0" % COOKIE_NAME,
                "Location": "/login",
            })
            return

        if path.startswith("/web/download/"):
            if not self.authed():
                send(self, 401, headers={"Cache-Control": "no-store"})
                return
            name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            if name not in ALLOWED_DOWNLOADS:
                send(self, 404, b"not found\n",
                     {"Content-Type": "text/plain; charset=utf-8"})
                return
            target = DOWNLOAD_DIR / name
            if not target.is_file():
                send(self, 404, b"not found\n",
                     {"Content-Type": "text/plain; charset=utf-8"})
                return
            data = target.read_bytes()
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            send(self, 200, data, {
                "Content-Type": ctype,
                "Content-Disposition": 'attachment; filename*=UTF-8\'\'%s' % urllib.parse.quote(name),
                "Cache-Control": "no-store",
            })
            return

        send(self, 404, b"not found\n",
             {"Content-Type": "text/plain; charset=utf-8"})

    def log_message(self, fmt, *args):
        # 不记录任何请求，避免 sharecode / Cookie 落盘
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
