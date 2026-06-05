#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复盘助手 东方财富版 — 启动入口

本版本基于复盘助手 V4 架构适配 东方财富 交易软件。
核心逻辑（数据获取、连板计算、概念分析）与通达信版完全一致。
仅板块文件写入路径和安装配置不同。

⚠️ 本版本为结构适配版，需在 东方财富 实机环境验证。
"""
import sys
from pathlib import Path

# 加入当前目录到sys.path
BASE_DIR = Path(__file__).parent.resolve()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from platforms import get_platform, EastMoneyPlatform
from engine import run_engine, install_blocks
from utils import load_config, save_config


def setup_platform():
    """初始化东方财富平台配置"""
    plat = EastMoneyPlatform()
    cfg = plat.load_config()
    detected = plat.detect_path()

    if detected:
        print('  检测到东方财富安装路径: %s' % detected)
        if cfg.get('eastmoneyDir') != detected:
            cfg['eastmoneyDir'] = detected
            plat.save_config(cfg)
    else:
        print('  [WARN] 未检测到东方财富安装路径')
        print('  请在 config.json 中设置 eastmoneyDir')

    return plat, cfg


def main():
    print('=' * 60)
    print('复盘助手 - 东方财富版')
    print('=' * 60)
    print()

    plat, cfg = setup_platform()

    if len(sys.argv) > 1:
        if sys.argv[1] == '--update':
            print('运行数据更新...')
            run_engine(cfg)
            return
        elif sys.argv[1] == '--install':
            print('安装板块配置...')
            install_blocks(cfg)
            return
        elif sys.argv[1] == '--detect':
            path = plat.detect_path()
            if path:
                print('✅ 检测到东方财富: %s' % path)
            else:
                print('❌ 未检测到东方财富')
            return

    print('用法:')
    print('  python app_eastmoney.py             查看信息')
    print('  python app_eastmoney.py --update    更新数据')
    print('  python app_eastmoney.py --install   安装配置')
    print('  python app_eastmoney.py --detect    检测路径')
    print()


if __name__ == '__main__':
    main()
