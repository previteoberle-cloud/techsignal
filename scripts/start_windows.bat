@echo off
REM TechSignal 信息采集工作台：Windows 一键启动
chcp 65001 >nul
REM 第一次运行会创建虚拟环境并安装依赖（需要已安装 Python 3.11 或以上，并勾选 Add to PATH）。
cd /d "%~dp0\.."
if not exist .venv\Scripts\python.exe (
    echo 正在创建虚拟环境 .venv ...
    python -m venv .venv || goto :error
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -e ".[ui]" || goto :error
)
echo 正在启动工作台，浏览器打开 http://localhost:8501 ...
.venv\Scripts\python.exe -m techsignal.workbench
goto :eof
:error
echo 启动失败：请确认已安装 Python 3.11+，并在命令行中可以运行 python。
pause
