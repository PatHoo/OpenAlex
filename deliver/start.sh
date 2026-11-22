#!/usr/bin/env bash
# OpenAlex 学者作品自助抓取工具 —— Linux / macOS 一键启动
# 自动创建虚拟环境、安装依赖、启动服务并打开浏览器。
set -e

cd "$(dirname "$0")"

# 1. 创建/复用虚拟环境
if [ ! -d ".venv" ]; then
    echo "[1/3] 正在创建虚拟环境 .venv ..."
    python3 -m venv .venv
fi
source .venv/bin/activate

# 2. 安装依赖（幂等：已装则秒过）
# 默认用官方 PyPI；若网络慢，可改成国内镜像：
#   pip install -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo "[2/3] 正在安装依赖 ..."
pip install -q -r requirements.txt --index-url https://pypi.org/simple

# 3. 打开浏览器并启动服务
echo "[3/3] 启动服务：http://127.0.0.1:8765"
echo "      关闭本窗口或按 Ctrl+C 即停止服务。"
echo

# 后台打开浏览器（macOS 用 open，Linux 用 xdg-open）
( sleep 1
  if command -v xdg-open >/dev/null 2>&1; then xdg-open http://127.0.0.1:8765
  elif command -v open >/dev/null 2>&1; then open http://127.0.0.1:8765
  fi ) &

python app.py
