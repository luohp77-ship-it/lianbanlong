@echo off
REM 通达信涨停复盘 - 手动测试运行
REM 双击此文件运行一次复盘（不限时间，随时可测）

cd /d "%~dp0"
python tdx_zt_update.py
pause
