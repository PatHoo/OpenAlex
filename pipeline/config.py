"""OpenAlex 跑批配置：环境变量、路径、限速与重试参数。

只用标准库 + requests + openpyxl。所有敏感信息（API key）只从环境变量读取，
不写入代码或仓库文件。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "pipeline" / "data"
DEFAULT_BATCH = "batch_001"

SOURCE_XLSX = ROOT / "reference" / "Table-S1-career-2017.xlsx"
SOURCE_SHEET = "Career"

# 名单口径：Excel 表头 1 行 + 数据 105,026 行（合计 105,027 行，与方案一致）
EXPECTED_DATA_ROWS = 105_026


def api_key() -> str:
    """API key 只从环境变量读取，缺失时返回空串（走匿名/测试额度）。"""
    return os.environ.get("OPENALEX_API_KEY", "").strip()


def batch_dir(name: str = DEFAULT_BATCH) -> Path:
    p = DATA / name
    p.mkdir(parents=True, exist_ok=True)
    return p


# 限速：免费 key 10 req/s，新账号首 $1 按 $0.10/5min  pacing。
# 试跑阶段主动压到 8 req/s 并逐请求记录 429，避免触发新账号限速与日预算耗尽。
RATE_LIMIT_PER_SEC = float(os.environ.get("OA_RATE", "8"))
MAX_RETRIES = 5
BACKOFF_BASE = 1.5  # 秒，指数退避

# 请求超时（秒）
TIMEOUT = 30

# 单次 works 抓取每页条数（OpenAlex 上限 100）
PER_PAGE = 100
