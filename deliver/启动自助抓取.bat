@echo off
chcp 65001 >nul
title OpenAlex 学者作品自助抓取工具
cd /d "%~dp0"

echo.
echo  ============================================
echo    OpenAlex 学者作品自助抓取工具
echo  ============================================
echo.

rem 1. 校验/创建虚拟环境（能 import flask 才算可用，否则重建）
set "NEED_VENV=0"
if not exist ".venv\Scripts\python.exe" set "NEED_VENV=1"
if "%NEED_VENV%"=="0" (
    ".venv\Scripts\python.exe" -c "import flask" >nul 2>&1
    if errorlevel 1 set "NEED_VENV=1"
)
if "%NEED_VENV%"=="1" (
    echo  [1/3] 虚拟环境缺失或不可用，正在重建 .venv ...
    if exist ".venv" rmdir /s /q ".venv"
    python -m venv .venv
)

rem 2. 安装依赖（已装则跳过）
".venv\Scripts\python.exe" -c "import flask, requests, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo  [2/3] 正在安装依赖 ...
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt --index-url https://pypi.org/simple
) else (
    echo  [2/3] 依赖已就绪，跳过安装。
)

rem 3. 打开浏览器并启动服务
echo  [3/3] 启动服务：http://127.0.0.1:8765
echo        关闭本窗口即停止服务。
echo.
start "" http://127.0.0.1:8765

".venv\Scripts\python.exe" app.py

pause
