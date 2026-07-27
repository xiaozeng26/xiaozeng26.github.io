@echo off
chcp 65001 >nul
REM ============================================
REM  配置 Windows 任务计划器 - 每天20:00运行
REM  需要管理员权限运行
REM ============================================

echo ============================================
echo   配置每日博客自动生成任务
echo   运行时间: 每天 20:00
echo ============================================
echo.

set TASK_NAME=AutoBlogDaily
set BAT_PATH=%~dp0..\run_blog.bat

REM 检查批处理文件是否存在
if not exist "%BAT_PATH%" (
    echo [错误] 找不到 run_blog.bat: %BAT_PATH%
    pause
    exit /b 1
)

echo [1/3] 删除旧任务（如果存在）...
schtasks /delete /tn "%TASK_NAME%" /f 2>nul

echo [2/3] 创建新的计划任务...
REM /sc DAILY: 每天运行
REM /st 20:00: 晚上8点
REM /f: 强制创建，不提示
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "%BAT_PATH%" ^
    /sc DAILY ^
    /st 20:00 ^
    /ru "%USERNAME%" ^
    /f

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [失败] 任务创建失败！请尝试以管理员身份运行此脚本。
    echo 手动创建方法：
    echo   1. 打开 "任务计划程序" (taskschd.msc)
    echo   2. 点击 "创建基本任务"
    echo   3. 名称: %TASK_NAME%
    echo   4. 触发器: 每天, 20:00
    echo   5. 操作: 启动程序 "%BAT_PATH%"
    pause
    exit /b 1
)

echo [3/3] 验证任务...
schtasks /query /tn "%TASK_NAME%" /fo LIST | findstr /C:"TaskName" /C:"Schedule" /C:"Start Time"

echo.
echo ============================================
echo   ✅ 任务配置成功！
echo   任务名称: %TASK_NAME%
echo   运行时间: 每天 20:00
echo   运行脚本: %BAT_PATH%
echo ============================================
echo.
echo 测试运行:
echo   schtasks /run /tn "%TASK_NAME%"
echo 查看任务:
echo   schtasks /query /tn "%TASK_NAME%"
echo 删除任务:
echo   schtasks /delete /tn "%TASK_NAME%" /f
echo ============================================
pause
