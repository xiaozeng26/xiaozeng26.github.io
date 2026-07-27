@echo off
chcp 65001 >nul
REM ============================================
REM  每日博客自动生成 & 发布
REM  由 Windows 任务计划器每天20:00触发
REM ============================================

setlocal

REM 设置日志目录
set LOG_DIR=%~dp0auto_blog\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM 设置 Python 路径 (根据实际安装位置调整)
set PYTHON=D:\Python\Python3.14\python.exe

REM 如果上面的路径不存在，尝试从 PATH 中查找
if not exist "%PYTHON%" set PYTHON=python

REM 运行
echo [%date% %time%] 启动每日博客生成 >> "%LOG_DIR%\scheduler.log"
"%PYTHON%" "%~dp0auto_blog\run_daily.py" >> "%LOG_DIR%\scheduler.log" 2>&1
echo [%date% %time%] 完成, 返回码: %ERRORLEVEL% >> "%LOG_DIR%\scheduler.log"

endlocal
exit /b 0
