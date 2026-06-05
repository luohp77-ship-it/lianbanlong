#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复牌助手 - 通达信自动复盘桌面应用（兼容入口）

所有逻辑实现在 utils.py / engine.py / app.py 中，本文件仅为兼容入口。
不再重复实现，确保与 engine.py 逻辑完全一致。

用法:
  python app_tdx.py           启动桌面APP
  python app_tdx.py --update  命令行静默更新
  python app_tdx.py --install 命令行安装板块
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

# ── 委托至共享模块 ──────────────────────────────────
# engine.py 定义 SECTORS（唯一真源，含概念板块14个）
# utils.py 提供 http_get/log/config/文件操作
# app.py 提供桌面 GUI（FupanApp）

from utils import (
    load_config, save_config, log, http_get, http_get_json,
    DATA_DIR, LOG_DIR, BASE_DIR as _,
    stock_to_blk, write_blk, backup_file,
    get_limit_pct, is_trading_day, get_latest_trading_day,
    clean_old_logs, dedup_list,
)

from engine import (
    SECTORS, run_engine, install_blocks,
    fetch_limit_up, fetch_lhb, fetch_eastmoney,
    calc_board_days,
)

from app import FupanApp


def main():
    """等同于 app.main()"""
    import app as _app
    _app.main()


if __name__ == '__main__':
    main()
