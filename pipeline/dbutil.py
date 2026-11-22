"""WSL psql 调用封装。

关键点：不使用 `2>&1 | tail`。管道退出码是 tail 的，会把 psql 失败吞成成功，
导致"静默丢数"。这里直接检查 subprocess 的 returncode。
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


def wsl_path(p: Path) -> str:
    """Windows 路径 -> WSL 可见路径。"""
    return "/mnt/c" + str(p).replace("C:\\", "/").replace("\\", "/")


def run_psql(sql: str, *, name: str = "query", temp_dir: Path | None = None,
             verbose: bool = True, timeout: int = 900) -> str:
    """把 SQL 写入临时文件并在 WSL 里用 psql 执行；失败抛 RuntimeError。"""
    d = temp_dir or Path(os.environ.get("TEMP", "/tmp"))
    f = d / f"{name}_{os.getpid()}.sql"
    f.write_text(sql, encoding="utf-8")
    cmd = ["wsl", "-e", "bash", "-c",
           f'PASS=$(cat ~/.openalex_db_pass); PGPASSWORD="$PASS" psql -h 127.0.0.1 '
           f'-p 5432 -U postgres -d openalex -v ON_ERROR_STOP=1 '
           f'-f {shlex.quote(wsl_path(f))}']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    if verbose:
        print(out)
    if r.returncode != 0:
        raise RuntimeError(f"psql [{name}] 失败（退出码 {r.returncode}）：\n{out[-2000:]}")
    return out
