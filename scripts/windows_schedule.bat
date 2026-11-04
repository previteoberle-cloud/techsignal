@echo off
setlocal EnableExtensions
REM TechSignal 定时采集（Windows 任务计划程序）。默认不创建任何计划任务，必须由你手动执行 enable。
REM 用法（在项目目录或任意位置执行均可）：
REM   scripts\windows_schedule.bat enable 6     每 6 小时采集一次全部任务（小时数 1-24，默认 6）
REM   scripts\windows_schedule.bat status       查看计划任务状态
REM   scripts\windows_schedule.bat run          立即运行一次（与计划任务执行的命令相同）
REM   scripts\windows_schedule.bat disable      删除计划任务（不删除任何采集数据）
REM 运行结果写入 %USERPROFILE%\.techsignal\collector\logs\collect.log
chcp 65001 >nul

set "TASK=TechSignal-Collector"
cd /d "%~dp0.." || (echo [错误] 无法进入项目目录 & exit /b 1)
set "ROOT=%CD%"
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"
set "VENV_PYW=%ROOT%\.venv\Scripts\pythonw.exe"

set "CMD=%~1"
if "%CMD%"=="" goto :usage

if /i "%CMD%"=="disable" goto :disable
if /i "%CMD%"=="status" goto :status

if not exist "%VENV_PY%" (
    echo [错误] 还没有安装：请先运行 scripts\start_windows.bat 完成安装。
    exit /b 1
)
if /i "%CMD%"=="run" goto :run
if /i "%CMD%"=="enable" goto :prepare
goto :usage

REM 注意：不在括号块内使用 %errorlevel%（cmd 会在执行前展开，得到旧值）
:disable
schtasks /Delete /TN "%TASK%" /F
exit /b

:status
schtasks /Query /TN "%TASK%" /V /FO LIST
exit /b

:run
"%VENV_PY%" -m techsignal.collector collect-all
exit /b

:prepare
set "HOURS=%~2"
if "%HOURS%"=="" set "HOURS=6"

:enable
echo %HOURS%| findstr /r "^[0-9][0-9]*$" >nul || (echo [错误] 小时数必须是 1-24 的整数 & exit /b 1)
if %HOURS% LSS 1 (echo [错误] 小时数必须是 1-24 的整数 & exit /b 1)
if %HOURS% GTR 24 (echo [错误] 小时数必须是 1-24 的整数 & exit /b 1)
REM pythonw 不弹出命令行窗口；结果只写日志
set "RUNNER=%VENV_PYW%"
if not exist "%RUNNER%" set "RUNNER=%VENV_PY%"
schtasks /Create /TN "%TASK%" /SC HOURLY /MO %HOURS% /F /TR "\"%RUNNER%\" -m techsignal.collector collect-all"
if errorlevel 1 (
    echo [错误] 创建计划任务失败。项目路径过长时可把项目放到较短的路径（例如 C:\TechSignal）后重试。
    exit /b 1
)
echo 已启用：每 %HOURS% 小时采集一次全部任务（仅在当前用户登录时运行）。
echo 关闭：scripts\windows_schedule.bat disable
exit /b 0

:usage
echo 用法：scripts\windows_schedule.bat enable [小时数] ^| status ^| run ^| disable
exit /b 1
