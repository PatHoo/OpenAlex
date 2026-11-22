@echo off
chcp 65001 >nul
title OpenAlex 学者作品自助抓取工具
cd /d "%~dp0"

echo.
echo  ============================================
echo    OpenAlex 学者作品自助抓取工具
echo  ============================================
echo.

rem 1. 创建/复用虚拟环境
if not exist ".venv\Scripts\python.exe" (
    echo  [1/3] 正在创建虚拟环境 .venv ...
    python -m venv .venv
)

rem 2. 安装依赖（幂等）
echo  [2/3] 正在安装依赖 ...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt --index-url https://pypi.org/simple

rem 3. 打开浏览器并启动服务
echo  [3/3] 启动服务：http://127.0.0.1:8765
echo        关闭本窗口即停止服务。
echo.
start "" http://127.0.0.1:8765

".venv\Scripts\python.exe" app.py

pause
