#!/usr/bin/env bash
# OpenAlex 学者作品自助抓取工具 —— Linux / macOS 一键启动
# 自动创建/校验虚拟环境、安装依赖、启动服务并打开浏览器。
set -e

cd "$(dirname "$0")"

PY=".venv/bin/python"

# 1. 校验虚拟环境是否真正可用（能 import flask 才算数）
#    兼容两种情况：venv 不存在，或 venv 是"空壳"（软链指向系统 python、依赖没装进去）。
need_venv=0
if [ ! -x "$PY" ]; then
    need_venv=1
elif ! "$PY" -c "import flask" >/dev/null 2>&1; then
    need_venv=1
fi

if [ "$need_venv" = "1" ]; then
    echo "[1/3] 虚拟环境缺失或不可用，正在重建 .venv ..."
    rm -rf .venv
    python3 -m venv .venv
fi

# 2. 安装依赖（幂等：已装则秒过）
#    默认用官方 PyPI；若网络慢，可改成国内镜像：
#   pip install -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if ! "$PY" -c "import flask, requests, openpyxl" >/dev/null 2>&1; then
    echo "[2/3] 正在安装依赖 ..."
    "$PY" -m pip install -q -r requirements.txt --index-url https://pypi.org/simple
else
    echo "[2/3] 依赖已就绪，跳过安装。"
fi

# 3. 打开浏览器并启动服务
echo "[3/3] 启动服务：http://127.0.0.1:8765"
echo "      关闭本窗口或按 Ctrl+C 即停止服务。"
echo

# 后台打开浏览器（macOS 用 open，Linux 用 xdg-open）
( sleep 1
  if command -v xdg-open >/dev/null 2>&1; then xdg-open http://127.0.0.1:8765
  elif command -v open >/dev/null 2>&1; then open http://127.0.0.1:8765
  fi ) &

# 用 venv 内的 python 显式启动，避免依赖 PATH 上的系统 python（可能没装 flask）
"$PY" app.py
