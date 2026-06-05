#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通达信涨停板块 + 龙虎榜前20 自动更新脚本（Python版）
数据源：同花顺 dataapi（涨停池）+ ClawHub LHB API（龙虎榜）
用法：python tdx_zt_update.py [YYYYMMDD]

板块定义：
 - 首板 (SB.blk) = 1连板
 - 2连板 (2LB.blk) = 2连板
 - 3连板 (3LB.blk) = 3连板
 - 4连板 (4LB.blk) = 4连板
 - 5板以上 (5BYS.blk) = 5+连板
 - 所有连板 (SYLB.blk) = 2+连板
 - 昨封板 (ZFB.blk) = 目标日期涨停股
 - 昨日 (ZS.blk) = 目标日期涨停股（备用）
 - 龙虎榜前20 (LHBQ20.blk) = 龙虎榜净买入额前20
"""

import sys
import os
import json
import http.client
import urllib.parse
from datetime import datetime, timedelta

# 设置 stdout 为 UTF-8，避免 Windows GBK 编码错误
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except AttributeError:
    pass  # Python < 3.7 不支持

# ── 读取配置 ──────────────────────────────────────────────────────────────────
def get_config():
    # 命令行参数：argv[1] = 日期，argv[2] = TDX blocknew 目录
    if len(sys.argv) > 2 and sys.argv[2]:
        return sys.argv[2]
    # 读取同目录下的 config.json
    config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                if cfg.get('blocknewDir'):
                    return cfg['blocknewDir']
        except Exception:
            pass
    # 默认值
    return 'C:/new_tdx/T0002/blocknew'

TDX_DIR = get_config()

# ── 工具函数 ──────────────────────────────────────────────────────────────────
def http_get(url, headers=None, timeout=15):
    """简单的 HTTP GET，返回 (status_code, body_str)"""
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        path = parsed.path + ('?' + parsed.query if parsed.query else '')
        
        if parsed.scheme == 'https':
            conn = http.client.HTTPSConnection(host, port, timeout=timeout)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        
        req_headers = headers or {}
        if 'User-Agent' not in req_headers:
            req_headers['User-Agent'] = 'Mozilla/5.0'
        
        conn.request('GET', path, headers=req_headers)
        resp = conn.getresponse()
        body = resp.read().decode('utf-8', errors='replace')
        conn.close()
        return resp.status, body
    except Exception as e:
        print(f'  [ERROR] HTTP 请求失败: {e}')
        return 0, ''

def write_blk_file(filepath, stocks):
    """写入 .blk 文件，每行一个7位代码"""
    lines = []
    for s in stocks:
        code = str(s.get('code', '')).strip()
        # 补齐6位，加市场前缀
        code6 = code.zfill(6)
        # 判断市场：6开头=沪市(1)，0/3开头=深市(0)，8/4开头=北交所(3)
        if code6.startswith('6'):
            prefix = '1'
        elif code6.startswith(('0', '3')):
            prefix = '0'
        else:
            prefix = '3'
        lines.append(prefix + code6)
    
    content = '\n'.join(lines) + '\n'
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'  [OK] {os.path.basename(filepath)}: {len(lines)} 只')

def get_target_date():
    """获取目标日期（命令行参数或昨天）"""
    if len(sys.argv) > 1 and sys.argv[1]:
        return sys.argv[1]
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime('%Y%m%d')

# ── 获取涨停数据（同花顺） ──────────────────────────────────────────────────
def fetch_limit_up_data(date_str):
    """
    获取指定日期的涨停数据
    返回：[{code, name, is_again_limit, change_rate}, ...]
    """
    url = f'https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?date={date_str}&type=all&page=1&limit=200'
    headers = {
        'Referer': 'https://www.10jqka.com.cn/',
        'User-Agent': 'Mozilla/5.0',
    }
    status, body = http_get(url, headers)
    
    if status != 200 or not body:
        print(f'  [ERROR] 同花顺 API 请求失败 (status={status})')
        return []
    
    try:
        data = json.loads(body)
        if data.get('status_code') != 0:
            print(f'  [ERROR] 同花顺 API 返回错误: {data.get("status_msg")}')
            return []
        stocks = data.get('data', {}).get('info', [])
        print(f'  [OK] 获取到 {len(stocks)} 只涨停股 ({date_str})')
        return stocks
    except Exception as e:
        print(f'  [ERROR] 解析同花顺数据失败: {e}')
        return []

# ── 获取龙虎榜数据（ClawHub） ──────────────────────────────────────────────
def fetch_lhb_data(date_str):
    """
    获取龙虎榜数据
    返回：[{code, name, close, change, net_buy, reason}, ...] 按净买入额排序
    """
    url = f'http://fffy520.gicp.net:8003/api/lhb/daily?date={date_str}'
    status, body = http_get(url, timeout=15)
    
    if status != 200 or not body:
        print(f'  [ERROR] 龙虎榜 API 请求失败 (status={status})')
        return []
    
    try:
        data = json.loads(body)
        if data.get('code') == 200:
            lhb_list = data.get('data', [])
            # 按净买入额排序
            lhb_list.sort(key=lambda x: float(x.get('net_buy', 0)), reverse=True)
            print(f'  [OK] 获取到 {len(lhb_list)} 只龙虎榜股票')
            return lhb_list[:20]  # 只取前20
        else:
            print(f'  [ERROR] 龙虎榜 API 返回错误: {data.get("message")}')
            return []
    except Exception as e:
        print(f'  [ERROR] 解析龙虎榜数据失败: {e}')
        return []

# ── 计算连板天数 ──────────────────────────────────────────────────────────────
def calc_continuous_days(target_date, all_stocks_set):
    """
    计算每只股票的连板天数。
    每只股票独立向前递推，断了就停。
    只遍历已有数据的交易日，不跳过无数据的日期（防止周末真空导致连板虚增）。
    返回：{code: continuous_days}
    """
    target_stocks = all_stocks_set.get(target_date, [])
    if not target_stocks:
        return {}

    # 提取所有历史日期（不含目标日），按时间倒序排列
    hist_dates = sorted([d for d in all_stocks_set if d != target_date], reverse=True)

    # 预计算历史数据每天有哪些股票（加快查表）
    history = {}
    for d in hist_dates:
        history[d] = {str(s.get('code', '')).zfill(6) for s in all_stocks_set[d]}

    # 逐只股票独立计算连板天数
    continuous = {}
    for s in target_stocks:
        code = str(s.get('code', '')).zfill(6)
        days = 1  # 今天涨停至少算1天
        for d in hist_dates:
            prev_codes = history.get(d, set())
            if not prev_codes:
                continue  # 无数据的日期跳过（理论上不会触发）
            if code in prev_codes:
                days += 1
            else:
                break  # 连板断了，停止
        continuous[code] = days

    return continuous

# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    target_date = get_target_date()
    print('=' * 60)
    print(f'通达信涨停板块更新 - {target_date}')
    print('=' * 60)
    
    # 1. 获取目标日期涨停数据
    print('\n【步骤1】获取涨停数据...')
    target_stocks = fetch_limit_up_data(target_date)
    
    if not target_stocks:
        print('[ERROR] 未获取到涨停数据，退出')
        return
    
    # 2. 获取历史数据（计算连板天数）
    print('\n【步骤2】获取历史数据（计算连板天数）...')
    all_stocks = {target_date: target_stocks}
    current = datetime.strptime(target_date, '%Y%m%d')
    for i in range(1, 6):  # 往前查5天
        check_date = (current - timedelta(days=i)).strftime('%Y%m%d')
        stocks = fetch_limit_up_data(check_date)
        if stocks:
            all_stocks[check_date] = stocks
    
    # 3. 计算连板天数
    print('\n【步骤3】计算连板天数...')
    continuous = calc_continuous_days(target_date, all_stocks)
    print(f'  连板股票数: {len(continuous)}')
    
    # 4. 分类
    print('\n【步骤4】分类写入板块...')
    sb = []    # 首板
    lb2 = []   # 2连板
    lb3 = []   # 3连板
    lb4 = []   # 4连板
    lb5 = []   # 5板以上
    sylb = []  # 所有连板
    
    for s in target_stocks:
        code = str(s.get('code', '')).zfill(6)
        days = continuous.get(code, 1)
        
        if days == 1:
            sb.append(s)
        elif days == 2:
            lb2.append(s)
            sylb.append(s)
        elif days == 3:
            lb3.append(s)
            sylb.append(s)
        elif days == 4:
            lb4.append(s)
            sylb.append(s)
        elif days >= 5:
            lb5.append(s)
            sylb.append(s)
    
    # 5. 写入 .blk 文件
    print('\n【步骤5】写入 .blk 文件...')
    write_blk_file(os.path.join(TDX_DIR, 'SB.blk'), sb)
    write_blk_file(os.path.join(TDX_DIR, '2LB.blk'), lb2)
    write_blk_file(os.path.join(TDX_DIR, '3LB.blk'), lb3)
    write_blk_file(os.path.join(TDX_DIR, '4LB.blk'), lb4)
    write_blk_file(os.path.join(TDX_DIR, '5BYS.blk'), lb5)
    write_blk_file(os.path.join(TDX_DIR, 'SYLB.blk'), sylb)
    write_blk_file(os.path.join(TDX_DIR, 'ZFB.blk'), target_stocks)
    write_blk_file(os.path.join(TDX_DIR, 'ZS.blk'), target_stocks)
    
    # 6. 获取龙虎榜数据
    print('\n【步骤6】获取龙虎榜前20...')
    lhb_stocks = fetch_lhb_data(target_date)
    if lhb_stocks:
        write_blk_file(os.path.join(TDX_DIR, 'LHBQ20.blk'), lhb_stocks)
    
    # 完成
    print('\n' + '=' * 60)
    print('✅ 更新完成！')
    print('=' * 60)
    print(f'\n板块文件已写入: {TDX_DIR}')
    print('请重启通达信查看效果。\n')

if __name__ == '__main__':
    main()
