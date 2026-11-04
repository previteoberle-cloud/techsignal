@echo off
setlocal EnableExtensions
REM TechSignal 信息采集工作台：Windows 启动脚本
REM - 需要 Python 3.11 或以上（python.org 安装时勾选 "Add python.exe to PATH"，或已安装 py 启动器）
REM - 第一次运行：在项目目录下创建 .venv 并安装依赖；之后直接启动
REM - 只监听本机 127.0.0.1；采集数据保存在 %USERPROFILE%\.techsignal\collector，本脚本不会改动或删除数据
chcp 65001 >nul

cd /d "%~dp0.." || (echo [错误] 无法进入项目目录："%~dp0.." & goto :fail)

set "VENV_PY=.venv\Scripts\python.exe"
set "MARKER=.venv\techsignal-installed.txt"

if exist "%VENV_PY%" if exist "%MARKER%" goto :check

REM ---- 查找系统 Python（优先 py 启动器，其次 python）----
set "PY="
where py >nul 2>nul && py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [错误] 没有找到 Python。请从 https://www.python.org/downloads/ 安装 Python 3.11 或以上，
    echo        安装时勾选 "Add python.exe to PATH"，然后重新运行本脚本。
    goto :fail
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo [错误] Python 版本过低，需要 3.11 或以上。当前版本：
    %PY% --version
    goto :fail
)

if not exist "%VENV_PY%" (
    echo 正在创建虚拟环境 .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (echo [错误] 创建虚拟环境失败。 & goto :fail)
)
echo 正在安装依赖（第一次需要几分钟）...
"%VENV_PY%" -m pip install --upgrade pip >nul
"%VENV_PY%" -m pip install -e ".[ui]"
if errorlevel 1 (
    echo [错误] 安装依赖失败，工作台没有启动。请检查网络后重新运行本脚本。
    goto :fail
)
echo ok> "%MARKER%"

:check
"%VENV_PY%" -c "import streamlit, techsignal.collector" >nul 2>nul
if errorlevel 1 (
    echo [提示] 依赖不完整，正在重新安装 ...
    del "%MARKER%" >nul 2>nul
    "%VENV_PY%" -m pip install -e ".[ui]"
    if errorlevel 1 (echo [错误] 安装依赖失败，工作台没有启动。 & goto :fail)
    echo ok> "%MARKER%"
)

echo 正在启动工作台：浏览器打开 http://localhost:8501 （若该端口已被占用，会自动换用下一个端口，以命令行显示为准）
echo 关闭本窗口即可停止工作台。
"%VENV_PY%" -m techsignal.workbench
if errorlevel 1 (echo [错误] 工作台异常退出。 & goto :fail)
exit /b 0

:fail
echo.
pause
exit /b 1
