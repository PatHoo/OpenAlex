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


# 限速：官方文档（help.openalex.org/api/authentication）现行口径为
# 匿名 10 req/s、免费 key/prepaid 30 req/s、年付 50 req/s；
# 2026-11-03 之后的新账号首个 $1 另受 $0.10/5min 节奏限制，提速也花不更快。
# 默认 30 req/s（免费 key / prepaid 上限）；实测遇到 429 可下调 OA_RATE。
# 新账号首个 $1 另受 $0.10/5min 节奏限制，提高速率不等于能更快花完额度。
RATE_LIMIT_PER_SEC = float(os.environ.get("OA_RATE", "30"))
MAX_RETRIES = 5
BACKOFF_BASE = 1.5  # 秒，指数退避

# 请求超时（秒）
TIMEOUT = 30

# 单次 works 抓取每页条数（OpenAlex 上限 100）
PER_PAGE = 100


# ---------- 运行环境：WSL / 原生 Linux ----------
# Windows 主机上 Python 通过 `wsl -e bash` 调 psql，PostgreSQL 看到的是 /mnt/c/...；
# 原生 Linux 云主机上直接调 psql，路径就是本地绝对路径。两者由这里统一切换。
def path_mode() -> str:
    """返回 'wsl' 或 'linux'。

    优先级：OA_PATH_MODE 显式设置 > WSL_DISTRO_NAME / Windows 自动识别 > linux。
    """
    mode = os.environ.get("OA_PATH_MODE", "").strip().lower()
    if mode in ("wsl", "wsl2"):
        return "wsl"
    if mode in ("linux", "native", "cloud"):
        return "linux"
    if mode:
        raise ValueError(f"OA_PATH_MODE 无效: {mode}（可选 wsl / linux）")
    if os.environ.get("WSL_DISTRO_NAME") or os.name == "nt":
        return "wsl"
    return "linux"


def pg_path(p: Path | str) -> str:
    """把本地路径转成 PostgreSQL 服务端可见的路径（供 psql -f / \\copy 使用）。

    WSL 模式下沿用原有转换：C:\\... -> /mnt/c/...；已经是 /mnt/c/ 开头的原样返回。
    Linux 模式下直接返回 POSIX 路径，不再拼接 /mnt/c。
    """
    s = str(p)
    if path_mode() == "linux":
        return s
    if s.startswith("/mnt/c/"):
        return s
    return "/mnt/c" + s.replace("C:\\", "/").replace("\\", "/")
