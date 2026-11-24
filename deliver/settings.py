"""自助抓取工具的最小配置（替代 pipeline 内部 config.py）。

只用标准库。API key 只从环境变量读取，不写入代码或仓库文件。
本文件独立于主分析链，客户拿到交付包即可直接运行，无需 PostgreSQL 等内部依赖。
"""
from __future__ import annotations

import os
from pathlib import Path

# 交付包根目录（本文件所在目录）
ROOT = Path(__file__).resolve().parent

# 预置名单（Elsevier 2017 高被引，105,026 名学者）
PRESET_XLSX = ROOT / "preset" / "Table-S1-career-2017.xlsx"
PRESET_SHEET = "Career"

# 限速：免费 key / prepaid 官方上限 30 req/s；实测遇 429 可下调 OA_RATE。
RATE_LIMIT_PER_SEC = float(os.environ.get("OA_RATE", "30"))
MAX_RETRIES = 5
BACKOFF_BASE = 1.5  # 秒，指数退避

# 请求超时（秒）
TIMEOUT = 30

# 单次 works 抓取每页条数（OpenAlex 上限 100）
PER_PAGE = 100


def api_key() -> str:
    """API key 只从环境变量读取，缺失时返回空串（走匿名/测试额度）。

    注：自助工具里 key 主要由网页输入并仅存内存，此函数仅作兜底。
    """
    return os.environ.get("OPENALEX_API_KEY", "").strip()
