@echo off
chcp 65001 >nul
title 通达信 涨停原因+所属概念 监控
echo ================================================
echo  通达信 涨停原因+所属概念 自动监控
echo  数据源：同花顺 zzshare + 东方财富概念缓存
echo  按 Ctrl+C 停止
echo ================================================
cd /d "%~dp0"
python tdx_zt_monitor.py
pause
