"""psql 调用封装：统一 WSL / 原生 Linux 两种运行模式。

关键点：
- 不使用 `2>&1 | tail`。管道退出码是 tail 的，会把 psql 失败吞成成功，
  导致"静默丢数"。需要截断输出时用 run_psql(..., tail=N)，仍检查 returncode。
- 路径转换与 psql 启动方式统一由 config.path_mode() 决定：
  WSL 走 `wsl -e bash -c`，原生 Linux 直接调用 psql。
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from config import path_mode, pg_path

# 兼容旧调用名；新代码直接用 pg_path
wsl_path = pg_path

DB_HOST = os.environ.get("PGHOST", "127.0.0.1")
DB_PORT = os.environ.get("PGPORT", "5432")
DB_USER = os.environ.get("PGUSER", "postgres")
DB_NAME = os.environ.get("PGDATABASE", "openalex")


def _connect_args() -> list[str]:
    return [
        "-h", os.environ.get("PGHOST", DB_HOST),
        "-p", os.environ.get("PGPORT", DB_PORT),
        "-U", os.environ.get("PGUSER", DB_USER),
        "-d", os.environ.get("PGDATABASE", DB_NAME),
    ]


def psql_command(*, sql_file: Path | None = None, sql: str | None = None) -> list[str]:
    """构造可直接 subprocess.run 的 psql 命令（含 WSL 包装）。"""
    args = ["psql", *_connect_args(), "-v", "ON_ERROR_STOP=1"]
    if sql_file is not None:
        args += ["-f", pg_path(sql_file)]
    else:
        args += ["-c", sql or ""]

    if path_mode() == "wsl":
        password = os.environ.get("PGPASSWORD", "").strip()
        if password:
            prefix = "PGPASSWORD=%s " % shlex.quote(password)
        else:
            prefix = 'PGPASSWORD=$(cat ~/.openalex_db_pass) '
        return ["wsl", "-e", "bash", "-c", prefix + shlex.join(args)]
    return args


def run_psql(sql: str, *, name: str = "query", temp_dir: Path | None = None,
             verbose: bool = True, timeout: int = 900,
             tail: int | None = None) -> str:
    """把 SQL 写入临时文件并执行；失败抛 RuntimeError，绝不用管道吞退出码。

    tail 只影响打印行数，不影响 returncode 检查。
    """
    d = temp_dir or Path(os.environ.get("TEMP", "/tmp"))
    f = d / f"{name}_{os.getpid()}.sql"
    f.write_text(sql, encoding="utf-8")
    cmd = psql_command(sql_file=f)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    if verbose:
        lines = out.splitlines()
        print("\n".join(lines[-tail:]) if tail and len(lines) > tail else out)
    if r.returncode != 0:
        raise RuntimeError(f"psql [{name}] 失败（退出码 {r.returncode}）：\n{out[-2000:]}")
    return out


def query_psql(sql: str, *, timeout: int = 900) -> str:
    """执行单条查询（-c）并返回 strip 后的 stdout；失败抛 RuntimeError。"""
    cmd = psql_command(sql=sql)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        err = (r.stderr or "") + (r.stdout or "")
        raise RuntimeError(f"psql 查询失败（退出码 {r.returncode}）：\n{err[-2000:]}")
    return (r.stdout or "").strip()
