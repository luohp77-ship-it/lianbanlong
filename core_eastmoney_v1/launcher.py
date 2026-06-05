#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘助手启动器 — 替换通达信快捷方式
检测是否已更新 → 未更新则自动运行复盘 → 启动通达信
"""
import sys, os, subprocess, json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from utils import load_config, log, DATA_DIR

LOG_FILE = BASE_DIR / 'logs' / 'launcher.log'
APP_PY = BASE_DIR / 'app.py'

def _log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = '[%s] %s' % (ts, msg)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')
    print(line)

def need_update():
    """检查今天是否已更新过"""
    today = datetime.now().strftime('%Y%m%d')
    latest_fp = DATA_DIR / 'latest.json'
    if not latest_fp.exists():
        return True
    try:
        data = json.load(open(latest_fp, 'r', encoding='utf-8'))
        if data.get('stale'):
            return True
        return data.get('date') != today
    except:
        return True

def main():
    _log('启动器运行...')
    config = load_config()
    tdx_dir = config.get('tdxDir', 'C:/new_tdx')
    tdx_exe = os.path.join(tdx_dir, 'TdxW.exe')

    if need_update():
        _log('  检测到需要更新，运行复盘引擎...')
        try:
            result = subprocess.run(
                [sys.executable, str(APP_PY), '--update'],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                _log('  复盘引擎更新成功')
            else:
                _log('  复盘引擎返回非零: %s' % (result.stderr[:200] if result.stderr else 'unknown'))
        except subprocess.TimeoutExpired:
            _log('  复盘引擎超时，继续启动通达信')
        except Exception as e:
            _log('  复盘引擎错误: %s' % str(e)[:100])
    else:
        _log('  今天已更新，直接启动通达信')

    if os.path.exists(tdx_exe):
        _log('  启动通达信: %s' % tdx_exe)
        subprocess.Popen([tdx_exe])
    else:
        _log('  错误: 未找到通达信 %s' % tdx_exe)
        input('按回车退出...')

if __name__ == '__main__':
    main()
