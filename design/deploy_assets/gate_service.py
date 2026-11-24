#!/usr/bin/env python3
"""OpenAlex Demo 门禁服务：sharecode 签发 Cookie + 校验 + 生成 COS/CDN 签名下载 URL。

仅用 Python 标准库，监听 127.0.0.1:8765，由 nginx auth_request / proxy_pass 调用。
敏感值全部从环境变量读取（/etc/openalex/gate.env），不写入本文件、不进 Git。
"""

import hashlib
import hmac
import html
import os
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SHARECODE = os.environ["SHARECODE"]
COOKIE_SECRET = os.environ["COOKIE_SECRET"].encode()
COS_SECRET_ID = os.environ["COS_SECRET_ID"]
COS_SECRET_KEY = os.environ["COS_SECRET_KEY"].encode()
COS_REGION = os.environ["COS_REGION"]
CDN_HOST = os.environ["CDN_HOST"]
COS_PREFIX = os.environ.get("COS_PREFIX", "openalex").strip("/")

COOKIE_NAME = "oa_access"
COOKIE_TTL = 86400
URL_TTL = 3600

# Cookie 里只放 HMAC 值，不放明文分享码
EXPECTED = hmac.new(COOKIE_SECRET, SHARECODE.encode(), hashlib.sha256).hexdigest()

# 只允许下载这两份交付工作簿，避免路径穿越
ALLOWED = {
    "新三学者基线与增量对照数据_数据库复现版.xlsx",
    "学者流动分析成果_1000人试跑版.xlsx",
}

# 裸 /login?next=...（如客户直接访问 /web/ 被 302 过来）没有 sharecode 参数，
# 此时显示分享码输入页，避免直接 403 invalid sharecode。
# 生产环境的分享码仍由项目负责人通过内部渠道分发，页面不预填、不展示。
LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenAlex 科研分析平台 · 分享码验证</title>
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
</style></head><body>
<div class="box">
  <h1>OpenAlex 科研分析平台</h1>
  <p>请输入项目负责人提供的分享码继续</p>
  <form method="get" action="/login">
    <input type="hidden" name="next" value="__NEXT__">
    <input type="text" name="sharecode" placeholder="分享码" autofocus required autocomplete="off">
    <button type="submit">进入平台</button>
  </form>
</div></body></html>"""


def signed_download_url(key):
    """生成 COS V5 签名 URL，指向 CDN 域名（CDN 缓存键需忽略 q-sign-* 参数）。"""
    now = int(time.time())
    key_time = "%d;%d" % (now - 60, now + URL_TTL)
    uri = "/" + urllib.parse.quote(key, safe="/")
    sign_key = hmac.new(COS_SECRET_KEY, key_time.encode(), hashlib.sha1).hexdigest()
    http_string = "get\n%s\n\n\n" % uri
    string_to_sign = (
        "sha1\n%s\n%s\n" % (key_time, hashlib.sha1(http_string.encode()).hexdigest())
    )
    signature = hmac.new(sign_key.encode(), string_to_sign.encode(), hashlib.sha1).hexdigest()
    auth = (
        "q-sign-algorithm=sha1&q-ak=%s&q-sign-time=%s&q-key-time=%s"
        "&q-header-list=&q-url-param-list=&q-signature=%s"
        % (COS_SECRET_ID, key_time, key_time, signature)
    )
    return "https://%s%s?%s" % (CDN_HOST, uri, auth)


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenAlexGate/1.0"

    def cookie_value(self):
        raw = self.headers.get("Cookie", "")
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return None
        morsel = jar.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def authed(self):
        value = self.cookie_value()
        return bool(value) and hmac.compare_digest(value, EXPECTED)

    def send_result(self, code, body=b"", headers=None):
        self.send_response(code)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/auth":
            self.send_result(
                200 if self.authed() else 401,
                headers={"Cache-Control": "no-store"},
            )
            return

        if path == "/login":
            query = urllib.parse.parse_qs(parsed.query)
            code = query.get("sharecode", [""])[0]
            target = query.get("next", ["/web/platform.html"])[0]
            # 只允许站内跳转，避免开放重定向
            if not target.startswith("/") or target.startswith("//"):
                target = "/web/platform.html"
            # 未带 sharecode：显示输入页而非直接 403（开放重定向防护已做，转义 next）
            if not code:
                page = LOGIN_PAGE.replace("__NEXT__", html.escape(target, quote=True))
                self.send_result(
                    200, page.encode("utf-8"),
                    {"Content-Type": "text/html; charset=utf-8",
                     "Cache-Control": "no-store"},
                )
                return
            if not hmac.compare_digest(code, SHARECODE):
                self.send_result(
                    403,
                    b"invalid sharecode\n",
                    {
                        "Content-Type": "text/plain; charset=utf-8",
                        "Cache-Control": "no-store",
                    },
                )
                return
            cookie = "%s=%s; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=%d" % (
                COOKIE_NAME,
                EXPECTED,
                COOKIE_TTL,
            )
            self.send_result(
                302,
                headers={
                    "Set-Cookie": cookie,
                    "Location": target,
                    "Cache-Control": "no-store",
                },
            )
            return

        if path == "/logout":
            self.send_result(
                302,
                headers={
                    "Set-Cookie": "%s=; Path=/; Max-Age=0" % COOKIE_NAME,
                    "Location": "/login",
                },
            )
            return

        if path.startswith("/web/download/"):
            if not self.authed():
                self.send_result(401, headers={"Cache-Control": "no-store"})
                return
            name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            if name not in ALLOWED:
                self.send_result(
                    404,
                    b"not found\n",
                    {"Content-Type": "text/plain; charset=utf-8"},
                )
                return
            key = "%s/%s" % (COS_PREFIX, name) if COS_PREFIX else name
            self.send_result(
                302,
                headers={
                    "Location": signed_download_url(key),
                    "Cache-Control": "no-store",
                },
            )
            return

        self.send_result(
            404,
            b"not found\n",
            {"Content-Type": "text/plain; charset=utf-8"},
        )

    def log_message(self, fmt, *args):
        # 门禁服务不记录任何请求，避免敏感值落盘；访问统计看 nginx
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
