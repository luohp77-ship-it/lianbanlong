#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""边缘场景测试：空数据、损坏文件、首次运行"""
import os, sys, json, time
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from utils import (
    http_get, load_config, save_json, load_json, write_blk, backup_file,
    get_limit_pct, is_trading_day, get_latest_trading_day, dedup_list,
    stock_to_blk, save_config, DATA_DIR
)
from engine import calc_board_days, _save_today_boards

passed = 0
failed = 0

def check(name, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print('[PASS] %s' % name)
    else:
        failed += 1
        print('[FAIL] %s %s' % (name, detail))

# ===== 1. 涨跌幅标准测试 =====
print('\n--- 涨跌幅标准 ---')
check('沪主板 10%', get_limit_pct('600001') == 0.10)
check('深主板 10%', get_limit_pct('000001') == 0.10)
check('创业板 20%', get_limit_pct('300001') == 0.20)
check('科创板 20%', get_limit_pct('688001') == 0.20)
check('北交所 30%', get_limit_pct('800001') == 0.30)
check('ST 5%', get_limit_pct('600001', is_st=True) == 0.05)
check('创业板 ST 20%', get_limit_pct('300001', is_st=True) == 0.20)
check('短代码 0001', get_limit_pct('0001') == 0.10)
check('空字符串', get_limit_pct('') == 0.10)

# ===== 2. 交易日判断 =====
print('\n--- 交易日判断 ---')
from datetime import datetime, timedelta
saturday = datetime(2026, 5, 30)  # Saturday
sunday = datetime(2026, 5, 31)    # Sunday
monday = datetime(2026, 6, 1)     # Monday
check('周六非交易日', not is_trading_day(saturday))
check('周日非交易日', not is_trading_day(sunday))
check('周一可能是交易日', is_trading_day(monday) == True)  # depends on holidays
check('get_latest 退回周五', get_latest_trading_day(saturday).strftime('%Y%m%d') == '20260529')

# ===== 3. stock_to_blk 编码 =====
print('\n--- blk 编码 ---')
check('沪市 1+code', stock_to_blk('600001') == '1600001')
check('深市 0+code', stock_to_blk('000001') == '0000001')
check('创业板 0+code', stock_to_blk('300001') == '0300001')
check('北交 3+code', stock_to_blk('800001') == '3800001')
check('不全6位补全', stock_to_blk('1') == '0000001')

# ===== 4. 连板天数（同花顺历史API）=====
print('\n--- 连板天数 ---')

# 用真实股票测试calc_board_days
stocks = [{'code': '000539', 'name': '粤电力A'}]
result = calc_board_days(stocks, '20260601')
check('粤电力A连板>=1', result.get('000539', 0) >= 1, 'got: %s' % result)

# ===== 5. 原子写入 =====
print('\n--- 原子写入 ---')
test_blk = str(BASE_DIR / 'data' / '_test.blk')
write_blk(test_blk, ['600001', '000001', '300001'])
check('.blk 文件存在', os.path.exists(test_blk))
with open(test_blk, 'r', encoding='utf-8') as f:
    content = f.read()
check('内容正确', '1600001' in content and '0000001' in content and '0300001' in content)
check('无 .tmp 残留', not os.path.exists(test_blk + '.tmp'))
os.remove(test_blk)

# 空列表
write_blk(test_blk, [])
check('空列表写入0字节', os.path.exists(test_blk) and os.path.getsize(test_blk) == 0)
os.remove(test_blk)

# test save_json
test_json = str(BASE_DIR / 'data' / '_test.json')
save_json(test_json, {'a': 1})
check('JSON 存在', os.path.exists(test_json))
check('无 .tmp 残留', not os.path.exists(test_json + '.tmp'))
d = load_json(test_json)
check('JSON 内容正确', d and d.get('a') == 1)
os.remove(test_json)

# ===== 6. backup_file =====
print('\n--- 文件备份 ---')
test_file = str(BASE_DIR / 'data' / '_test_backup.txt')
with open(test_file, 'w') as f:
    f.write('test')
bp = backup_file(test_file)
check('备份创建成功', bp is not None and os.path.exists(bp))
check('备份文件名格式', '.bak.20' in str(bp) if bp else False)
os.remove(test_file)
if bp:
    os.remove(bp)

# ===== 7. config 读写 =====
print('\n--- 配置文件 ---')
cfg = load_config()
check('config 加载成功', cfg is not None and 'tdxDir' in cfg)
check('默认值填充', 'enableWechat' in cfg and 'wechatKey' in cfg)

# ===== 8. 去重 =====
print('\n--- 列表去重 ---')
check('基本去重', dedup_list([1, 2, 2, 3, 1]) == [1, 2, 3])
check('空列表', dedup_list([]) == [])
check('无重复', dedup_list([1, 2, 3]) == [1, 2, 3])

# ===== 9. 损坏 JSON 容错 =====
print('\n--- 容错测试 ---')
corrupt = str(BASE_DIR / 'data' / '_corrupt.json')
with open(corrupt, 'w') as f:
    f.write('{not valid json]')
result = load_json(corrupt)
check('损坏 JSON 返回 None', result is None)
os.remove(corrupt)

missing = str(BASE_DIR / 'data' / '_nonexistent.json')
result2 = load_json(missing)
check('不存在文件返回 None', result2 is None)

# ===== 10. save_config 原子性 =====
print('\n--- 配置原子保存 ---')
cfg_test = load_config()
cfg_test['test'] = 'test_value'
save_config(cfg_test)
check('save 后无 .tmp', not os.path.exists(str(BASE_DIR / 'config.json') + '.tmp'))
cfg2 = load_config()
check('读回正确', cfg2.get('test') == 'test_value')
# 清理
del cfg_test['test']
save_config(cfg_test)

# ===== 总结 =====
print('\n' + '=' * 50)
total = passed + failed
print('[RESULT] %d/%d passed (%d failed)' % (passed, total, failed))
if failed > 0:
    print('[WARN] %d tests FAILED - need investigation' % failed)
else:
    print('[OK] All edge case tests passed!')
