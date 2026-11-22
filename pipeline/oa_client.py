"""OpenAlex API 客户端：key 认证、限速、429 指数退避、请求留痕。

每次请求（含失败）都追加写入 JSONL，字段对齐 design/schema.sql 的 api_request 表，
后续可一键导入。绝不把 key 写入留痕文件或日志。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import requests

from config import (
    BACKOFF_BASE,
    MAX_RETRIES,
    RATE_LIMIT_PER_SEC,
    TIMEOUT,
    api_key,
)


class APIBudgetExhausted(RuntimeError):
    """429 重试耗尽：额度或速率上限，需停止跑批，避免把部分数据误标为完成。"""


class OpenAlexClient:
    def __init__(self, log_path: Path, batch_tag: str = "batch_001"):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "OpenAlex-mobility-pipeline/0.1 (research; contact via API key)",
                "Accept": "application/json",
            }
        )
        self.key = api_key()
        if self.key:
            # 官方支持两种传法；用 Authorization 头，避免 key 出现在 URL/日志中
            self.session.headers.update({"Authorization": f"Bearer {self.key}"})
        self.log_path = log_path
        self.batch_tag = batch_tag
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._min_interval = 1.0 / RATE_LIMIT_PER_SEC if RATE_LIMIT_PER_SEC > 0 else 0.0
        self.request_count = 0

    # ---------- 限速 ----------
    def _throttle(self) -> None:
        if not self._min_interval:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    # ---------- 留痕 ----------
    def _log(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self._lock, self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ---------- 核心请求 ----------
    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        purpose: str = "",
        scholar_src_id: int | None = None,
    ) -> dict[str, Any] | None:
        """GET 一个 OpenAlex 端点，返回 JSON；失败返回 None 并留痕。

        path 形如 'authors' 或 'works/W123'，不带 api_key（由 header 提供）。
        """
        params = dict(params or {})
        url = f"https://api.openalex.org/{path.lstrip('/')}"
        full_url = url + ("?" + urlencode(params) if params else "")
        endpoint = "/" + path.split("?")[0].strip("/")

        for attempt in range(MAX_RETRIES + 1):
            self._throttle()
            requested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            status: int | None = None
            body = b""
            error_code: str | None = None
            try:
                resp = self.session.get(url, params=params, timeout=TIMEOUT)
                status = resp.status_code
                body = resp.content
                if status == 200:
                    data = resp.json()
                    self.request_count += 1
                    self._log(
                        {
                            "batch": self.batch_tag,
                            "purpose": purpose,
                            "scholar_src_id": scholar_src_id,
                            "endpoint": endpoint,
                            "request_url": resp.url,
                            "http_method": "GET",
                            "http_status": status,
                            "requested_at": requested_at,
                            "response_sha256": hashlib.sha256(body).hexdigest(),
                            "byte_count": len(body),
                            "error_code": None,
                            "payload": {
                                "meta": data.get("meta") if isinstance(data, dict) else None,
                                "attempt": attempt,
                                "has_key": bool(self.key),
                            },
                        }
                    )
                    return data
                # 429 / 5xx 可重试
                if status in (403, 429) or 500 <= status < 600:
                    try:
                        j = resp.json()
                        error_code = j.get("code") or j.get("reason") or j.get("error")
                    except Exception:
                        error_code = None
                    if status == 429:
                        if error_code == "daily_budget":
                            # 额度耗尽时 OpenAlex 的 Retry-After 指向次日重置（约 22 小时）。
                            # 绝不能按它长睡挂起进程：wait=0 跳过 sleep，直接抛异常干净暂停，
                            # 由用户明天续跑。
                            wait = 0.0
                        else:
                            retry_after = resp.headers.get("Retry-After")
                            wait = float(retry_after) if retry_after else BACKOFF_BASE ** (attempt + 1)
                    elif status == 403 and error_code == "key_not_activated":
                        # 配置错误，重试无意义
                        self._log(
                            {
                                "batch": self.batch_tag,
                                "purpose": purpose,
                                "scholar_src_id": scholar_src_id,
                                "endpoint": endpoint,
                                "request_url": resp.url,
                                "http_method": "GET",
                                "http_status": status,
                                "requested_at": requested_at,
                                "response_sha256": hashlib.sha256(body).hexdigest(),
                                "byte_count": len(body),
                                "error_code": error_code,
                                "payload": {"message": "API key 未激活，请到邮箱点击激活链接"},
                            }
                        )
                        print("[FATAL] API key 未激活，请到邮箱点击激活链接后重试")
                        return None
                    else:
                        wait = BACKOFF_BASE ** (attempt + 1)
                    self._log(
                        {
                            "batch": self.batch_tag,
                            "purpose": purpose,
                            "scholar_src_id": scholar_src_id,
                            "endpoint": endpoint,
                            "request_url": resp.url,
                            "http_method": "GET",
                            "http_status": status,
                            "requested_at": requested_at,
                            "response_sha256": hashlib.sha256(body).hexdigest(),
                            "byte_count": len(body),
                            "error_code": error_code,
                            "payload": {"attempt": attempt, "retry_in": wait, "has_key": bool(self.key)},
                        }
                    )
                    # 额度耗尽（wait=0）立即抛异常，不 sleep、不重试；
                    # 其他 429 走正常退避重试，重试耗尽后再抛。
                    if status == 429 and error_code == "daily_budget":
                        raise APIBudgetExhausted("OpenAlex 当日额度已耗尽（daily_budget）")
                    if attempt < MAX_RETRIES:
                        time.sleep(wait)
                        continue
                    # 429 重试耗尽：抛异常让主循环停在当前学者，
                    # 避免把只抓到一半的 works 误标为 wp2 完成（下次续跑会补）
                    if status == 429:
                        raise APIBudgetExhausted(
                            "OpenAlex 429 重试耗尽，可能已触发速率限制"
                        )
                else:
                    # 4xx 非限流（如 404），记录后返回 None，不重试
                    self._log(
                        {
                            "batch": self.batch_tag,
                            "purpose": purpose,
                            "scholar_src_id": scholar_src_id,
                            "endpoint": endpoint,
                            "request_url": resp.url,
                            "http_method": "GET",
                            "http_status": status,
                            "requested_at": requested_at,
                            "response_sha256": hashlib.sha256(body).hexdigest(),
                            "byte_count": len(body),
                            "error_code": None,
                            "payload": {"attempt": attempt, "has_key": bool(self.key)},
                        }
                    )
                    return None
            except requests.RequestException as exc:
                error_code = type(exc).__name__
                self._log(
                    {
                        "batch": self.batch_tag,
                        "purpose": purpose,
                        "scholar_src_id": scholar_src_id,
                        "endpoint": endpoint,
                        "request_url": full_url,
                        "http_method": "GET",
                        "http_status": None,
                        "requested_at": requested_at,
                        "response_sha256": None,
                        "byte_count": None,
                        "error_code": error_code,
                        "payload": {"attempt": attempt, "message": str(exc)[:300]},
                    }
                )
                if attempt < MAX_RETRIES:
                    time.sleep(BACKOFF_BASE ** (attempt + 1))
                    continue
            break
        return None

    # ---------- 额度查询 ----------
    def rate_limit(self) -> dict[str, Any] | None:
        """查询当前 key 的额度；此端点免费，不计入预算。"""
        if not self.key:
            return None
        return self.get("rate-limit", purpose="rate_limit_check")
