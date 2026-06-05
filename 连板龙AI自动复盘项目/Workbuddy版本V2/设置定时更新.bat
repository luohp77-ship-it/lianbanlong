@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   复盘助手 - 设置定时更新
echo ========================================
echo.
echo   每天自动跑两次，无需任何操作：
echo     15:26  收盘更新（涨停+概念+板块写入）
echo     18:00  全量更新（补齐龙虎榜+生成Word报告）
echo.
echo   你只需像平时一样打开通达信
echo   15:26 之后重启软件，数据自动已刷新
echo.
echo   按任意键开始设置...
pause >nul

set SCRIPT_PATH="%~dp0app.py"

echo.
echo   [1/2] 创建收盘更新 15:26 ...
schtasks /Create /SC DAILY /TN "复盘助手_收盘更新" /TR "python %SCRIPT_PATH% --update" /ST 15:26 /F
echo   [2/2] 创建全量更新 18:00 ...
schtasks /Create /SC DAILY /TN "复盘助手_全量更新" /TR "python %SCRIPT_PATH% --update" /ST 18:00 /F

if %errorlevel% equ 0 (
    echo.
    echo   [√] 全部完成！
    echo       15:26 收盘更新 + 18:00 全量更新
    echo       管理: 任务计划程序 → 搜索"复盘助手"
) else (
    echo.
    echo   [×] 失败，请右键 → 以管理员身份运行
)
echo.
pause
