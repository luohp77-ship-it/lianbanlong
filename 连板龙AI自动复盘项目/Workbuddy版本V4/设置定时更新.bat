@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   复盘助手 - 设置定时更新
echo ========================================
echo.
echo   每天 18:00 自动全量更新（涨停+概念+龙虎榜+Word报告）
echo.
echo   请先确认本机 Python 路径：
echo     推荐：E:\hermes-agent\venv\Scripts\python.exe
echo.
echo   如果路径不同，请手动修改本 BAT 文件中的 PYTHON_PATH 变量
echo.
echo   按任意键开始设置...
pause >nul

set PYTHON_PATH=E:\hermes-agent\venv\Scripts\python.exe
set SCRIPT_PATH=%~dp0app.py

echo.
echo   [1/1] 创建每日更新 18:00 ...
%PYTHON_PATH% -c "import subprocess; subprocess.run('schtasks /Create /SC DAILY /TN 复盘助手_数据更新 /TR \"%PYTHON_PATH% %SCRIPT_PATH% --update\" /ST 18:00 /F', shell=True)"

if %errorlevel% equ 0 (
    echo.
    echo   [√] 完成！
    echo       每日 18:00 自动更新
    echo       管理: 任务计划程序 → 搜索"复盘助手_数据更新"
) else (
    echo.
    echo   [×] 失败，请右键 → 以管理员身份运行
)
echo.
pause
