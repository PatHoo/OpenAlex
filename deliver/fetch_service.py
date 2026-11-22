"""客户自助抓取服务层：把 WP1+WP2 抓取封装成可被本地网页调用的状态机。

设计目标（成本转移给客户）：
- key 由客户在网页输入，仅存内存（+可选本地缓存文件），绝不写入请求留痕或日志。
- 支持两类名单：客户自传（CSV/Excel 姓名列），或预置 105,027 名单（按排名/引用选子集）。
- 断点续跑、额度耗尽干净暂停、请求留痕，逻辑复用 oa_client / wp12_fetch 的既有实现。

本模块不依赖 Flask，纯标准库（http.server + threading），客户 pip install requests 即可跑。
"""
from __future__ import annotations

import csv
import io
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests  # 唯一的第三方依赖，oa_client 已用

from oa_client import APIBudgetExhausted, OpenAlexClient

# 与 wp12 保持一致，抓取字段与翻页逻辑复用
try:
    from wp12_fetch import (  # type: ignore
        APIFetchFailed,
        AUTHOR_SELECT,
        WORK_SELECT,
        wp1_search,
        wp2_fetch_works,
    )
except Exception:  # 防御：即使 import 失败也能独立运行
    AUTHOR_SELECT = "id,display_name,orcid,works_count,cited_by_count,last_known_institutions,ids"
    WORK_SELECT = "id,doi,publication_year,type,cited_by_count,authorships,primary_location,title"

    class APIFetchFailed(RuntimeError):
        pass


# 预置名单：交付包 preset/Table-S1-career-2017.xlsx，含 105,026 名学者
from settings import PRESET_XLSX  # noqa: E402


@dataclass
class RunState:
    """一次抓取运行的内存状态（并发安全）。"""

    status: str = "idle"  # idle | running | paused | done | error
    total: int = 0
    done: int = 0
    requests: int = 0
    works: int = 0
    current: str = ""
    message: str = ""
    halted_reason: str = ""
    started_at: str = ""
    finished_at: str = ""
    results: list[dict] = field(default_factory=list)  # 已完成学者摘要，供前端预览
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "status": self.status,
                "total": self.total,
                "done": self.done,
                "requests": self.requests,
                "works": self.works,
                "current": self.current,
                "message": self.message,
                "halted_reason": self.halted_reason,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }


def parse_name_list(text: str) -> list[dict]:
    """解析客户粘贴/上传的名单：CSV 或「姓名」每行一个。

    返回 [{src_id, authfull}]，src_id 从 1 起（对外编号口径 = source_row）。
    兼容表头行（含 name/authfull/姓名 之一则跳过）。
    """
    rows: list[str] = []
    sample = text.lstrip("\ufeff")
    lines = [ln for ln in sample.splitlines() if ln.strip()]
    if not lines:
        return []

    # 有表头（含 name/authfull/姓名/fullname 之一）→ 按 CSV 解析，取姓名列
    first = lines[0].strip().lower()
    header_hit = any(h in first for h in ("name", "authfull", "姓名", "fullname"))
    if header_hit:
        reader = csv.reader(io.StringIO(sample))
        parsed = list(reader)
        header = [str(h).strip().lower() for h in parsed[0]]
        col = next(
            (i for i, h in enumerate(header) if h in {"name", "authfull", "姓名", "fullname"}),
            0,
        )
        for r in parsed[1:]:
            if len(r) > col and r[col].strip():
                rows.append(r[col].strip())
    else:
        # 无表头：默认「每行一个姓名」。姓名里含逗号（如 "Zhang, Wei"）时整行视为一个姓名，
        # 不按 CSV 拆分（除非一行有 ≥2 个逗号且出现引号，才按 CSV 处理）。
        rows = lines

    out = []
    for i, name in enumerate(rows, start=1):
        out.append({"src_id": i, "authfull": name})
    return out


def load_preset_subset(limit: int, offset: int = 0, by: str = "rank") -> list[dict]:
    """从预置 105,026 名单取子集（按 rank 升序 = 引用最高优先）。返回 [{src_id, authfull}]。

    src_id 用 source_row（从 1 起）作为对外编号；抓取内部仍可映射回 Excel 行号。
    """
    import openpyxl  # 仅在用到预置名单时 import

    wb = openpyxl.load_workbook(PRESET_XLSX, read_only=True, data_only=True)
    ws = wb["Career"]
    header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    name_idx = header.index("authfull") if "authfull" in header else 0
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[name_idx] is None or str(row[name_idx]).strip() == "":
            continue
        rows.append(str(row[name_idx]).strip())
    wb.close()

    subset = rows[offset : offset + limit]
    return [{"src_id": offset + i + 1, "authfull": n} for i, n in enumerate(subset)]


class FetchRunner:
    """抓取执行器：给定名单 + key，串行抓取并更新进度。"""

    def __init__(self, workdir: Path, state: RunState, key: str = ""):
        self.workdir = workdir
        self.state = state
        self.key = key
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def _client(self) -> OpenAlexClient:
        req_log = self.workdir / "requests.jsonl"
        # 用环境变量兜底；key 只在此处使用，不写日志
        client = OpenAlexClient(req_log)
        if self.key:
            client.key = self.key
            client.session.headers.update({"Authorization": f"Bearer {self.key}"})
        return client

    def run(self, names: list[dict], top_n: int = 5, max_works: int | None = None) -> None:
        """串行抓取；额度耗尽或失败时干净暂停（不把半截标完成）。"""
        state = self.state
        with state.lock:
            state.status = "running"
            state.total = len(names)
            state.done = 0
            state.requests = 0
            state.works = 0
            state.started_at = datetime.now(timezone.utc).isoformat()
            state.halted_reason = ""
            state.results = []

        client = self._client()
        # 断点：读既有 checkpoint
        cp_path = self.workdir / "checkpoint.json"
        cp: dict[str, dict] = {}
        if cp_path.exists():
            cp = json.loads(cp_path.read_text(encoding="utf-8"))

        cand_path = self.workdir / "candidates.jsonl"
        works_path = self.workdir / "works.jsonl"

        for src in names:
            if self._stop.is_set():
                state.message = "已手动停止"
                break
            sid = str(src["src_id"])
            cps = cp.setdefault(sid, {"wp1": False, "wp2": False, "works_count": 0})
            if cps.get("wp2"):
                with state.lock:
                    state.done += 1
                continue

            with state.lock:
                state.current = src["authfull"]

            # WP1
            if not cps.get("wp1"):
                try:
                    cands = wp1_search(client, src, top_n)
                except (APIBudgetExhausted, APIFetchFailed) as e:
                    with state.lock:
                        state.halted_reason = str(e)
                    break
                _append(cand_path, [{"src_id": src["src_id"], "candidates": cands}])
                cps["wp1"] = True
                cps["candidates"] = len(cands)
                _save(cp_path, cp)
            else:
                cands = [c["candidates"] for c in _load(cand_path) if c["src_id"] == src["src_id"]]
                cands = cands[-1] if cands else []

            # WP2（对第一个候选抓作品）
            works: list[dict] = []
            if cps["wp1"] and not cps["wp2"]:
                top = cands[0] if cands else {}
                aid = top.get("author_id")
                if not aid:
                    cps["wp2"] = True
                    cps["works_count"] = 0
                    _save(cp_path, cp)
                else:
                    try:
                        works = wp2_fetch_works(client, src, aid, max_works)
                    except (APIBudgetExhausted, APIFetchFailed) as e:
                        with state.lock:
                            state.halted_reason = str(e)
                        break
                    _append(works_path, works)
                    cps["wp2"] = True
                    cps["works_count"] = len(works)
                    cps["oa_author_id"] = aid
                    _save(cp_path, cp)

            with state.lock:
                state.done += 1
                state.works += len(works)
                state.requests = client.request_count
                state.results.append(
                    {
                        "src_id": src["src_id"],
                        "authfull": src["authfull"],
                        "works": len(works),
                        "candidates": cps.get("candidates", 0),
                    }
                )

        with state.lock:
            state.requests = client.request_count
            state.finished_at = datetime.now(timezone.utc).isoformat()
            if state.halted_reason:
                state.status = "paused"
                state.message = f"已暂停：{state.halted_reason}"
            elif self._stop.is_set():
                state.status = "paused"
            else:
                state.status = "done"
                state.message = f"完成 {state.done}/{state.total} 名学者"


# ---- 小工具 ----
def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _append(path: Path, records: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _save(path: Path, cp: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
