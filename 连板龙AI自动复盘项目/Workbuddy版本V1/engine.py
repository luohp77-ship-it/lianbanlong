#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘引擎：数据获取 → 连板计算(TDX本地) → 概念分析(t:3) → 板块写入

V5核心变更：
1. 连板计算改用TDX本地日线（非ST），ST走boards继承
2. 概念分析改用t:3概念板块
3. 新增晋级率/昨涨停今表现板块
4. 龙虎榜双源降级(ClawHub+东财)
5. 定时任务统一18:30
"""
import os
import json
import shutil
import struct
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from utils import (
    http_get, http_get_json, log, load_config, save_config,
    save_json, load_json, write_blk, backup_file,
    is_trading_day, get_latest_trading_day, clean_old_logs,
    set_tdx_dir, read_tdx_daily, calc_board_days_tdx, is_limit_up,
    write_extern_user, set_column_green,
    DATA_DIR
)
from concept_analyzer import analyze_concepts, get_top3_with_overlap, load_concept_map

THS = {'Referer': 'https://www.10jqka.com.cn/'}

# ═══ 板块定义（V5：16个板块，新增ZJL和ZFB2）═══
SECTORS = [
    ('ZFB',    '昨涨停',      0x9026),
    ('SB',     '首板',        0x9027),
    ('2LB',    '2连板',       0x9028),
    ('3LB',    '3连板',       0x9029),
    ('4LB',    '4连板',       0x902A),
    ('5BYS',   '5板以上',     0x902B),
    ('SYLB',   '所有连板',    0x902C),
    ('LHBQ20', '龙虎榜前20',  0x902E),
    ('DT',     '今跌停',      0x902F),
    ('ZBQ',    '曾涨停',      0x9030),
    ('ZDT',    '曾跌停',      0x9031),
    ('RDGN',   '主线',        0x9032),  # 动态命名
    ('HYRD',   '次线',        0x9033),  # 动态命名
    ('RDGN3',  '潜在',        0x9034),  # 动态命名
    ('ZJL',    '晋级率',      0x9035),  # V5新增
    ('ZFB2',   '昨涨停今',    0x9036),  # V5新增
]


# ═══ 数据获取 ═══
def fetch_limit_up(date_str):
    """同花顺涨停池（含涨停原因）。

    Args:
        date_str: 日期字符串 YYYYMMDD。

    Returns:
        涨停股信息列表，含 reason_type（涨停原因）字段。
    """
    # field参数：请求reason_type等扩展字段，不加则只返回code/name等基础字段
    url = ('https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool'
           '?date=%s&type=all&page=1&limit=200'
           '&field=199112,10,9001,330323,330324,330325,9002,9003,331399,331400,331401'
           % date_str)
    data = http_get(url, THS)
    if data[0] != 200:
        return []
    try:
        d = json.loads(data[1])
        if d.get('status_code') == 0:
            return d.get('data', {}).get('info', [])
    except (json.JSONDecodeError, KeyError):
        pass
    return []


def fetch_lhb(date_str):
    """龙虎榜（ClawHub主源）。

    Args:
        date_str: 日期字符串 YYYYMMDD。

    Returns:
        龙虎榜前20股票列表。
    """
    fmt = '%s-%s-%s' % (date_str[:4], date_str[4:6], date_str[6:8])
    url = 'http://fffy520.gicp.net:8003/api/lhb/daily?date=%s' % fmt
    data = http_get(url, timeout=15)
    if data[0] != 200:
        return []
    try:
        d = json.loads(data[1])
        if d.get('code') == 200:
            lst = d.get('data', [])
            lst.sort(key=lambda x: float(x.get('net_buy', 0)), reverse=True)
            return lst[:20]
    except (json.JSONDecodeError, KeyError, ValueError):
        pass
    return []


def fetch_lhb_eastmoney(date_str):
    """V5新增：东方财富龙虎榜备选数据源。

    ClawHub超时/失败时自动使用。

    Args:
        date_str: 日期字符串 YYYYMMDD。

    Returns:
        龙虎榜前20股票列表。
    """
    fmt = '%s-%s-%s' % (date_str[:4], date_str[4:6], date_str[6:8])
    url = ('https://data.eastmoney.com/DataCenter_V3/chart/LHBD.ashx'
           '?startdate=%s&enddate=%s&top=20&cb=' % (fmt, fmt))
    data = http_get(url, timeout=15)
    if data[0] != 200:
        return []
    try:
        raw = json.loads(data[1])
        # 东财龙虎榜返回格式解析
        items = raw.get('data', [])
        if not items:
            return []
        result = []
        for item in items[:20]:
            code = str(item.get('SCode', '')).zfill(6)
            name = item.get('SName', '')
            net_buy = 0
            try:
                net_buy = float(item.get('Lxb', 0))
            except (ValueError, TypeError):
                pass
            result.append({
                'code': code,
                'name': name,
                'net_buy': net_buy,
            })
        result.sort(key=lambda x: x.get('net_buy', 0), reverse=True)
        return result[:20]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def fetch_eastmoney(date_str):
    """东方财富 push2ex：封涨停/跌停/曾涨停/曾跌停/ST补充。

    Args:
        date_str: 日期字符串 YYYYMMDD（当前仅用于日志）。

    Returns:
        {zting, dting, zhaban, cengdting, st_zting} 字典。
    """
    url = ('https://push2ex.eastmoney.com/getAllStockChanges?'
           'type=4,8,16,32&ut=7eea3edcaed734bea9cbfc24409ed989'
           '&pageindex=0&pagesize=500&dpt=wzchanges')
    data = http_get(url)
    if data[0] != 200:
        return {'zting': [], 'dting': [], 'zhaban': [], 'cengdting': [], 'st_zting': []}

    try:
        d = json.loads(data[1])
        allstock = d.get('data', {}).get('allstock', [])
        records = defaultdict(list)

        for s in allstock:
            t = s.get('t', 0)
            code = str(s.get('c', '')).zfill(6)
            name = s.get('n', '')
            tm = s.get('tm', 0)
            parts = (s.get('i', '') or '').split(',')
            try:
                price = float(parts[0]) if parts else 0
                fengdan = int(float(parts[1])) if len(parts) >= 2 and parts[1] else 0
            except (ValueError, IndexError):
                price, fengdan = 0, 0
            records[code].append({
                'type': t, 'time': tm, 'name': name,
                'price': price, 'fengdan': fengdan
            })

        result = {'zting': [], 'dting': [], 'zhaban': [], 'cengdting': [], 'st_zting': []}
        seen_zt, seen_dt = set(), set()

        for code, recs in records.items():
            recs.sort(key=lambda x: x['time'])
            last_type = recs[-1]['type']
            last_fd = recs[-1]['fengdan']
            name = recs[-1]['name']

            if last_type == 4 and last_fd > 0 and code not in seen_zt:
                seen_zt.add(code)
                result['zting'].append(code)
                if name.startswith(('ST', '*ST')):
                    result['st_zting'].append({'code': code, 'name': name})
            if last_type == 8 and last_fd > 0 and code not in seen_dt:
                seen_dt.add(code)
                result['dting'].append(code)

            ever_closed_zt = any(r['type'] == 4 and r['fengdan'] > 0 for r in recs)
            if ever_closed_zt and last_type == 16:
                result['zhaban'].append(code)

            ever_closed_dt = any(r['type'] == 8 and r['fengdan'] > 0 for r in recs)
            if ever_closed_dt and last_type == 32:
                result['cengdting'].append(code)

        return result
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    return {'zting': [], 'dting': [], 'zhaban': [], 'cengdting': [], 'st_zting': []}


# ═══ 连板天数计算（V5重写：TDX本地日线）═══
def _save_today_boards(date_str, boards_dict):
    """保存今日连板数据。

    Args:
        date_str: 日期字符串。
        boards_dict: {code: board_days} 字典。
    """
    fp = DATA_DIR / 'boards' / ('%s.json' % date_str)
    save_json(fp, {
        'date': date_str,
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'boards': boards_dict
    })


def _load_yesterday_boards(date_str):
    """加载最近可用日的boards缓存。

    Args:
        date_str: 当前日期字符串 YYYYMMDD。

    Returns:
        {code: board_days} 字典。
    """
    dt = datetime.strptime(date_str, '%Y%m%d')
    for i in range(1, 8):
        prev = (dt - timedelta(days=i)).strftime('%Y%m%d')
        fp = DATA_DIR / 'boards' / ('%s.json' % prev)
        data = load_json(fp)
        if data and data.get('boards'):
            return data['boards']
    return {}


def _fetch_ths_day_codes(date_str):
    """获取某日同花顺涨停池中的所有股票代码（降级时使用）。

    Args:
        date_str: 日期字符串 YYYYMMDD。

    Returns:
        股票代码集合。
    """
    url = ('https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool'
           '?date=%s&type=all&page=1&limit=200' % date_str)
    s, b = http_get(url, THS, timeout=10)
    if s != 200:
        return set()
    try:
        info = json.loads(b).get('data', {}).get('info', [])
        return {str(item.get('code', '')).zfill(6) for item in info}
    except (json.JSONDecodeError, KeyError):
        return set()


def calc_board_days(today_stocks, target_date):
    """V5核心重写：连板天数计算。

    非ST股：读TDX本地日线 → 向前遍历 → 涨幅判断涨停 → 数连板天数
    ST股：从boards JSON缓存继承
    降级：TDX日线缺失时用同花顺历史API

    Args:
        today_stocks: 今日涨停股列表 [{code, name, ...}, ...]。
        target_date: 目标日期字符串 YYYYMMDD。

    Returns:
        {code: board_days} 字典。
    """
    yesterday_boards = _load_yesterday_boards(target_date)
    target_int = int(target_date)

    # 预加载降级用的同花顺历史数据（仅在需要降级时才请求）
    hist_ths = None

    result = {}
    degraded_codes = []

    for s in today_stocks:
        code = str(s.get('code', '')).zfill(6)
        name = s.get('name', '')
        is_st = name.startswith(('ST', '*ST'))

        # ST股：boards JSON继承
        if is_st:
            if code in yesterday_boards:
                result[code] = yesterday_boards[code] + 1
            else:
                result[code] = 1
            continue

        # 非ST：尝试TDX本地日线计算
        days = calc_board_days_tdx(code, target_int, is_st=False,
                                   yesterday_boards=None)
        if days is not None:
            result[code] = days
        else:
            # TDX日线缺失，标记降级
            degraded_codes.append(code)
            result[code] = 1  # 临时赋值，后续降级计算覆盖

    # 降级处理：TDX日线缺失时走同花顺历史API
    if degraded_codes:
        log('  [降级] %d只股票TDX日线缺失，使用同花顺历史API' % len(degraded_codes),
            level='WARN')
        if hist_ths is None:
            hist_ths = {}
            dt = datetime.strptime(target_date, '%Y%m%d')
            for i in range(1, 8):
                prev = dt - timedelta(days=i)
                prev_str = prev.strftime('%Y%m%d')
                codes = _fetch_ths_day_codes(prev_str)
                if len(codes) >= 20:
                    hist_ths[prev_str] = codes

        for code in degraded_codes:
            days = 1
            for d in sorted(hist_ths.keys(), reverse=True):
                if code in hist_ths[d]:
                    days += 1
                else:
                    break
            result[code] = days
            log('  [降级] %s: 同花顺API计算=%d板' % (code, days), level='WARN')

    return result


# ═══ 晋级率/溢价率/炸板率计算（V5新增）═══
def _calc_promotion_rate(yesterday_boards, today_boards):
    """计算晋级率：昨日首板今日晋级2板的股票。

    晋级率 = 昨日首板今日2板数 / 昨日首板总数 × 100%

    Args:
        yesterday_boards: 昨日boards字典 {code: days}。
        today_boards: 今日boards字典 {code: days}。

    Returns:
        (晋级股票代码列表, 晋级率百分比, 昨日首板数, 今日晋级数)。
    """
    if not yesterday_boards or not today_boards:
        return [], 0, 0, 0

    # 昨日首板股
    yesterday_sb = {code for code, days in yesterday_boards.items() if days == 1}

    # 今日2板股中来自昨日首板的
    promoted = []
    for code in yesterday_sb:
        if code in today_boards and today_boards[code] >= 2:
            promoted.append(code)

    total_sb = len(yesterday_sb)
    promoted_count = len(promoted)
    rate = (promoted_count / total_sb * 100) if total_sb > 0 else 0

    return promoted, rate, total_sb, promoted_count


def _calc_premium_rate(yesterday_zt_codes, today_daily_data):
    """计算溢价率：昨日涨停股今日开盘平均涨幅。

    Args:
        yesterday_zt_codes: 昨日涨停股代码集合。
        today_daily_data: 今日日线数据 {code: {open, close, prev_close, ...}}。

    Returns:
        溢价率百分比。
    """
    if not yesterday_zt_codes or not today_daily_data:
        return 0

    pcts = []
    for code in yesterday_zt_codes:
        if code in today_daily_data:
            d = today_daily_data[code]
            prev_close = d.get('prev_close', 0)
            open_price = d.get('open', 0)
            if prev_close > 0:
                pct = (open_price - prev_close) / prev_close * 100
                pcts.append(pct)

    return sum(pcts) / len(pcts) if pcts else 0


def _calc_zhaban_rate(zhaban_count, zting_count):
    """计算炸板率。

    炸板率 = 今日炸板数 / (今日封涨停数 + 今日炸板数) × 100%

    Args:
        zhaban_count: 今日炸板数。
        zting_count: 今日封涨停数。

    Returns:
        炸板率百分比。
    """
    total = zting_count + zhaban_count
    return (zhaban_count / total * 100) if total > 0 else 0


# ═══ 微信推送 ═══
def _push_wechat(config, d, summary):
    """微信ServerChan推送。

    Args:
        config: 配置字典。
        d: 日期字符串。
        summary: (涨停, 首板, 连板, 最高板, 跌停, 曾涨停, 曾跌停, 龙虎榜,
                 晋级率, 溢价率, 炸板率) 元组。

    Returns:
        是否推送成功。
    """
    key = config.get('wechatKey', '')
    if not config.get('enableWechat') or not key:
        return False
    try:
        title = '复盘助手 %s 收盘复盘' % d
        content = ('涨停%d只 | 首板%d | 连板%d | 最高%d板\n'
                   '跌停%d只 | 曾涨停%d只 | 曾跌停%d只\n'
                   '龙虎榜前20: %d只\n'
                   '晋级率%.0f%% | 溢价率%.1f%% | 炸板率%.0f%%') % summary
        push_url = ('https://sctapi.ftqq.com/%s.send?title=%s&desp=%s' %
                    (key, urllib.request.quote(title), urllib.request.quote(content)))
        http_get(push_url, timeout=10)
        return True
    except Exception:
        return False


# ═══ TG Bot 推送 ═══
def _push_tg(config, d, summary):
    """V5：TG Bot推送，Token从config.json读取。

    Args:
        config: 配置字典。
        d: 日期字符串。
        summary: 同 _push_wechat 的元组。

    Returns:
        是否推送成功。
    """
    token = config.get('tgBotToken', '')
    if not config.get('enableTgBot') or not token:
        return False
    try:
        # 读取已注册的TG用户
        tg_users_file = Path(__file__).parent.resolve() / 'tg_users.json'
        users = load_json(tg_users_file) or {}
        if not users:
            return False

        content = ('📊 复盘助手 %s 收盘复盘\n\n'
                   '🔴 涨停%d只 | 首板%d | 连板%d | 最高%d板\n'
                   '🔵 跌停%d只 | 曾涨停%d只 | 曾跌停%d只\n'
                   '🟢 龙虎榜前20: %d只\n'
                   '📈 晋级率%.0f%% | 溢价率%.1f%% | 炸板率%.0f%%') % summary

        api = 'https://api.telegram.org/bot%s' % token
        for chat_id in users:
            url = '%s/sendMessage' % api
            params = urllib.parse.urlencode({
                'chat_id': chat_id,
                'text': content,
            }).encode()
            req = urllib.request.Request(url, data=params)
            req.add_header('Content-Type', 'application/x-www-form-urlencoded')
            urllib.request.urlopen(req, timeout=10)

        return True
    except Exception:
        return False


# ═══ 主引擎 ═══
def run_engine(config=None, callback=None):
    """复盘引擎主流程（V5：10步）。

    步骤：
    1. 获取涨停(同花顺)
    1b. 获取东财数据(ST补充+封板/跌停/炸板)
    2. 连板天数计算(V5: TDX本地日线)
    3. 龙虎榜(ClawHub+东财备选)
    4. 东财封板/跌停/炸板(已在1b)
    5. 概念分析(V5: t:3+综合评分+允许重叠)
    6. 双环节校验+V5确定性校验(R7)
    7. 安装板块配置(含动态命名)
    8. 写入.blk文件(含V5新增: 晋级率/昨涨停今)
    9. 推送(微信+TG)
    10. 保存latest.json+历史快照

    Args:
        config: 配置字典，默认自动加载。
        callback: 日志回调函数。

    Returns:
        日志行列表。
    """
    from utils import _get_logger
    logger = _get_logger()
    logger.set_callback(callback)
    logger.logs = []

    if config is None:
        config = load_config()

    blocknew_dir = config.get('blocknewDir', 'C:\\new_tdx\\T0002\\blocknew')
    tdx_dir = config.get('tdxDir', 'C:\\new_tdx')
    os.makedirs(blocknew_dir, exist_ok=True)
    os.makedirs(DATA_DIR / 'boards', exist_ok=True)
    os.makedirs(DATA_DIR / 'history', exist_ok=True)

    # 设置TDX目录（供本地日线读取使用）
    set_tdx_dir(tdx_dir)

    # ── 清理过期日志 ──
    clean_old_logs()

    # ── 确定目标日期 ──
    today = datetime.now()
    if not is_trading_day(today):
        log('今日非交易日，使用最近交易日')
    target = get_latest_trading_day(today)
    d = target.strftime('%Y%m%d')

    # 收盘前用前一交易日
    if today.hour < 15 and today.date() == target.date():
        prev = today - timedelta(days=1)
        target = get_latest_trading_day(prev)
        d = target.strftime('%Y%m%d')

    log('--- 复盘引擎 V5 目标日期: %s ---' % d)

    # ── 1. 获取涨停数据（同花顺）──
    log('  获取涨停(同花顺)...')
    up_stocks = fetch_limit_up(d)
    if not up_stocks:
        log('  [WARN] 无涨停数据，保留旧板块', level='WARN')
        _stale_recovery(blocknew_dir, d)
        return logger.logs
    log('  涨停: %d只' % len(up_stocks))
    if len(up_stocks) < 5:
        log('  [WARN] 涨停数异常偏少(%d只)，可能数据不完整' % len(up_stocks), level='WARN')

    # ── 1b. 获取东财数据（含ST涨停补充+封板/跌停/炸板）──
    em = fetch_eastmoney(d)
    st_extra = em.get('st_zting', [])
    if st_extra:
        existing_codes = {str(s.get('code', '')).zfill(6) for s in up_stocks}
        added = 0
        for st in st_extra:
            code = str(st.get('code', '')).zfill(6)
            if code not in existing_codes:
                up_stocks.append({'code': code, 'name': st.get('name', '')})
                existing_codes.add(code)
                added += 1
        log('  补充ST涨停: %d只, 合并后涨停总数: %d只' % (added, len(up_stocks)))

    # ── 2. 连板天数计算（V5：TDX本地日线）──
    log('  连板天数计算(V5: TDX本地日线)...')
    days = calc_board_days(up_stocks, d)
    sb, lb2, lb3, lb4, lb5, sylb = [], [], [], [], [], []
    all_codes = set()
    for s in up_stocks:
        code = str(s.get('code', '')).zfill(6)
        all_codes.add(code)
        n = days.get(code, 1)
        if n == 1:
            sb.append(code)
        else:
            sylb.append(code)
            if n == 2:
                lb2.append(code)
            elif n == 3:
                lb3.append(code)
            elif n == 4:
                lb4.append(code)
            elif n >= 5:
                lb5.append(code)

    max_board = max(days.values()) if days else 0
    log('  首板:%d  2板:%d  3板:%d  4板:%d  5板+:%d  最高:%d板' % (
        len(sb), len(lb2), len(lb3), len(lb4), len(lb5), max_board))

    # 保存今日连板数据
    _save_today_boards(d, days)

    # ── V5新增：晋级率/溢价率/炸板率计算 ──
    yesterday_boards = _load_yesterday_boards(d)

    # 晋级率
    promoted, promo_rate, total_yesterday_sb, promoted_count = _calc_promotion_rate(
        yesterday_boards, days)
    log('  晋级率: %.0f%% (%d/%d)' % (promo_rate, promoted_count, total_yesterday_sb))

    # 昨日涨停股今日表现
    yesterday_zt_codes = set(yesterday_boards.keys()) if yesterday_boards else set()

    # 炸板率
    zhaban_rate = _calc_zhaban_rate(len(em.get('zhaban', [])), len(em.get('zting', [])))
    log('  炸板率: %.0f%%' % zhaban_rate)

    # 溢价率（简化计算：基于TDX日线开盘价）
    premium_rate = 0
    if yesterday_zt_codes:
        pcts = []
        for code in yesterday_zt_codes:
            daily = read_tdx_daily(code)
            if daily and len(daily) >= 2:
                today_rec = daily[-1]
                prev_close = daily[-2]['close']
                open_price = today_rec['open']
                if prev_close > 0:
                    pcts.append((open_price - prev_close) / prev_close * 100)
        premium_rate = sum(pcts) / len(pcts) if pcts else 0
    log('  溢价率: %.1f%%' % premium_rate)

    # ── 3. 龙虎榜（ClawHub主源 + 东财备选降级）──
    log('  获取龙虎榜(ClawHub)...')
    lhb = fetch_lhb(d)
    lhb_source = 'ClawHub'
    if not lhb:
        log('  ClawHub无数据，尝试东财龙虎榜...', level='WARN')
        lhb = fetch_lhb_eastmoney(d)
        lhb_source = '东财'
        if lhb:
            log('  [降级] 龙虎榜使用东财数据源', level='WARN')
    log('  龙虎榜前20: %d只 (%s)' % (len(lhb) if lhb else 0, lhb_source))

    # ── 4. 东方财富数据（已在1b获取，直接输出）──
    log('  涨停(东财):%d  跌停:%d  曾涨停:%d  曾跌停:%d' % (
        len(em['zting']), len(em['dting']), len(em['zhaban']), len(em['cengdting'])))

    # ── 5. 概念分析（V5：t:3概念板块 + 综合评分重排 + 允许重叠）──
    log('  概念分析(V5: t:3概念板块)...')
    limit_up_codes = {str(s.get('code', '')).zfill(6) for s in up_stocks}
    concept_result = analyze_concepts(limit_up_codes, callback=log)
    all_ranked = concept_result.get('ranked', [])
    top3 = _rank_concepts_composite(all_ranked, limit_up_codes, days)

    top1_codes, top2_codes, top3_codes = [], [], []
    concept_names = []
    for i, t in enumerate(top3[:3]):
        concept_name = t[0]
        cnt = t[1]
        codes = t[2]
        # V5命名格式：{数字}{概念名}({涨停数})
        # 如 "1消费电子(8)"
        name_body = str(i + 1) + concept_name + '(%d)' % cnt
        # 确保 GBK 编码 ≤14 字节
        while len(name_body.encode('gbk')) > 14 and len(name_body) > 1:
            # 优先截断概念名部分，保留括号中的数字
            name_body = name_body[:-1]
        concept_names.append(name_body)

        if i == 0:
            top1_codes = codes
            log('  概念TOP1 [%s]: %d只 (评分:%.1f)' % (
                name_body, cnt, t[6] if len(t) >= 7 else cnt))
        elif i == 1:
            top2_codes = codes
            log('  概念TOP2 [%s]: %d只' % (name_body, cnt))
        else:
            top3_codes = codes
            log('  概念TOP3 [%s]: %d只' % (name_body, cnt))
    if not top3:
        log('  概念: 无数据')

    # ── 6. 数据真实性校验（双环节 + V5确定性校验R7）──
    _validate_data(
        total_up=len(up_stocks),
        sb=sb, lb2=lb2, lb3=lb3, lb4=lb4, lb5=lb5, sylb=sylb,
        max_board=max_board,
        em=em, up_stocks=up_stocks,
        board_days=days,
        logger=logger
    )

    # ── 7. 安装板块配置（含动态热点命名 + V5新增板块）──
    if concept_names:
        log('  动态命名: %s, %s, %s' % (
            concept_names[0] if len(concept_names) >= 1 else '-',
            concept_names[1] if len(concept_names) >= 2 else '-',
            concept_names[2] if len(concept_names) >= 3 else '-'))

    # 晋级率板块动态命名：如"晋级35%"
    promo_name = '晋级%d%%' % int(promo_rate) if promo_rate > 0 else '晋级0%'
    # 昨涨停今表现固定命名
    zfb2_name = '昨涨停今'

    # 构建完整concept_names含V5新增板块
    extended_names = list(concept_names)
    extended_names.append(promo_name)   # index 3 -> ZJL
    extended_names.append(zfb2_name)    # index 4 -> ZFB2

    install_blocks(config, concept_names=extended_names)

    # ── 8. 写入板块文件（原子操作，含V5新增）──
    log('  写入通达信板块...')
    write_blk(os.path.join(blocknew_dir, 'ZFB.blk'),    [s.get('code', '') for s in up_stocks])
    write_blk(os.path.join(blocknew_dir, 'SB.blk'),     sb)
    write_blk(os.path.join(blocknew_dir, '2LB.blk'),    lb2)
    write_blk(os.path.join(blocknew_dir, '3LB.blk'),    lb3)
    write_blk(os.path.join(blocknew_dir, '4LB.blk'),    lb4)
    write_blk(os.path.join(blocknew_dir, '5BYS.blk'),   lb5)
    write_blk(os.path.join(blocknew_dir, 'SYLB.blk'),   sylb)
    if lhb:
        write_blk(os.path.join(blocknew_dir, 'LHBQ20.blk'),
                  [s.get('code', '') for s in lhb])
    else:
        write_blk(os.path.join(blocknew_dir, 'LHBQ20.blk'), [])
    write_blk(os.path.join(blocknew_dir, 'DT.blk'),     em['dting'])
    write_blk(os.path.join(blocknew_dir, 'ZBQ.blk'),    em['zhaban'])
    write_blk(os.path.join(blocknew_dir, 'ZDT.blk'),    em['cengdting'])
    write_blk(os.path.join(blocknew_dir, 'RDGN.blk'),   top1_codes)
    write_blk(os.path.join(blocknew_dir, 'HYRD.blk'),   top2_codes)
    write_blk(os.path.join(blocknew_dir, 'RDGN3.blk'),  top3_codes)
    # V5新增板块
    write_blk(os.path.join(blocknew_dir, 'ZJL.blk'),    promoted)
    write_blk(os.path.join(blocknew_dir, 'ZFB2.blk'),   list(yesterday_zt_codes))

    # 同步 .blk 文件到 LastSync
    lastsync_dir = os.path.join(blocknew_dir, 'LastSync')
    if os.path.isdir(lastsync_dir):
        for fn, _, _ in SECTORS:
            src = os.path.join(blocknew_dir, fn + '.blk')
            dst = os.path.join(lastsync_dir, fn + '.blk')
            if os.path.exists(src):
                shutil.copy2(src, dst)
        log('  LastSync .blk 同步完成')

    # ── 8b. 写入 extern_user.txt（涨停原因+所属概念 → TDX自定义列）──
    log('  写入自定义数据列(涨停原因+所属概念)...')
    # 构建涨停股的reason和concepts信息
    # 涨停原因：来自同花顺dataapi的up_stocks字段
    # 所属概念：来自concept_result的ranked数据
    code_concepts = {}  # {code: [概念名1, 概念名2, ...]}
    for item in all_ranked:
        concept_name = item[1] if len(item) >= 2 else ''
        codes_in = item[2] if len(item) >= 3 else []
        for c in codes_in:
            if c not in code_concepts:
                code_concepts[c] = []
            if concept_name and concept_name not in code_concepts[c]:
                code_concepts[c].append(concept_name)

    extern_stocks = []
    for s in up_stocks:
        code = str(s.get('code', '')).zfill(6)
        # 涨停原因：同花顺dataapi的reason_type字段（需field参数才返回）
        reason = s.get('reason_type', '') or s.get('reason_brief', '') or s.get('reason', '')
        if not reason:
            reason = s.get('up_limit_desc', '') or s.get('up_limit_reason', '')
        # 截断过长原因
        if len(reason) > 40:
            reason = reason[:37] + '...'
        extern_stocks.append({
            'code': code,
            'reason': reason,
            'concepts': code_concepts.get(code, []),
        })
    n_extern = write_extern_user(tdx_dir, extern_stocks, code_concepts)
    if n_extern:
        n_with_concept = sum(1 for s in extern_stocks if s['concepts'])
        log('  自定义列: %d只涨停股, %d只有概念数据' % (n_extern, n_with_concept))

    # 尝试设置自定义列颜色（首次运行时diycol.dat可能还不存在，静默跳过）
    set_column_green(tdx_dir)

    # ── 9. 推送（微信 + TG）──
    summary = (len(up_stocks), len(sb), len(sylb), max_board,
               len(em['dting']), len(em['zhaban']), len(em['cengdting']),
               len(lhb) if lhb else 0,
               promo_rate, premium_rate, zhaban_rate)
    if _push_wechat(config, d, summary):
        log('  [OK] 微信推送成功')
    if _push_tg(config, d, summary):
        log('  [OK] TG Bot推送成功')

    # ── 10. 保存latest.json + 历史快照 ──
    latest = {
        'up': len(up_stocks), 'sb': len(sb), 'lb': len(sylb),
        'down': len(em['dting']), 'zhaban': len(em['zhaban']),
        'lhb': len(lhb) if lhb else 0,
        'maxBoard': max_board,
        'promoRate': round(promo_rate, 1),
        'premiumRate': round(premium_rate, 2),
        'zhabanRate': round(zhaban_rate, 1),
        'date': d, 'time': datetime.now().strftime('%H:%M:%S'),
        'concepts': [
            {'name': t[0], 'count': t[1]} for t in top3[:3]
        ] if top3 else [],
    }
    save_json(DATA_DIR / 'latest.json', latest)

    # 历史快照
    save_json(DATA_DIR / 'history' / ('%s.json' % d), {
        'date': d,
        'limitUp': len(up_stocks),
        'limitDown': len(em['dting']),
        'maxBoard': max_board,
        'promoRate': round(promo_rate, 1),
        'premiumRate': round(premium_rate, 2),
        'zhabanRate': round(zhaban_rate, 1),
        'concepts': [{'name': t[0], 'count': t[1]} for t in top3[:3]] if top3 else [],
    })

    log('--- 完成: %d涨停(%d首板/%d高标) 最高%d板 %d跌停 %d炸板 %d曾跌停 %d龙虎榜 晋级%.0f%% 溢价%.1f%% 炸板%.0f%% ---' % (
        len(up_stocks), len(sb), len(sylb), max_board,
        len(em['dting']), len(em['zhaban']), len(em['cengdting']),
        len(lhb) if lhb else 0,
        promo_rate, premium_rate, zhaban_rate))
    return logger.logs


# ═══ 综合评分 ═══
def _rank_concepts_composite(ranked, limit_up_codes, board_data):
    """综合评分重排概念排名（V5：允许重叠）。

    评分维度与权重：
    - 涨停数 (40%)：涨停股在概念中的聚集度
    - 强度   (35%)：最高板×2 + 梯队数×3，反映概念纵深
    - 资金   (25%)：板块成交额，反映资金真实参与度

    V5变更：概念来源改为t:3，允许重叠展示，不再互斥去重。

    Args:
        ranked: analyze_concepts 返回的 ranked 列表
        limit_up_codes: 今日所有涨停股代码集合
        board_data: {code: board_days} 格式的连板数据

    Returns:
        [(概念名, 涨停数, 股票列表, 行业名, 涨跌幅, 成交额, 综合评分), ...]
    """
    if not ranked or not board_data:
        return get_top3_with_overlap(ranked or [])

    # 解析 ranked 数据
    parsed = []
    for item in ranked:
        cnt = item[0]
        name = item[1]
        codes = item[2]
        pct = item[3] if len(item) >= 4 else 0
        amount = item[4] if len(item) >= 5 else 0
        parsed.append((cnt, name, codes, pct, amount))

    if not parsed:
        return get_top3_with_overlap([])

    max_cnt = max(c for c, _, _, _, _ in parsed)
    max_amt = max(a for _, _, _, _, a in parsed) if parsed else 1

    # 为每个概念计算综合评分
    scored = []
    for cnt, name, codes, pct, amount in parsed:
        max_board_val = 0
        tiers = set()
        for c in codes:
            b = board_data.get(c, 1)
            max_board_val = max(max_board_val, b)
            if b >= 2:
                tiers.add(b)
        tier_count = len(tiers)
        strength = max_board_val * 2 + tier_count * 3

        # 归一化各维度（0-100）
        zt_norm = cnt / max_cnt * 100 if max_cnt else 0
        strength_norm = min(strength / 30 * 100, 100)
        amt_norm = amount / max_amt * 100 if max_amt else 0

        # 综合评分
        score = zt_norm * 0.40 + strength_norm * 0.35 + amt_norm * 0.25
        scored.append((score, cnt, name, codes, pct, amount, max_board_val, tier_count))

    # 按综合评分降序排列
    scored.sort(key=lambda x: x[0], reverse=True)

    # V5：按映射后概念名合并（概念板块名大多可直接用）
    _cmap = load_concept_map()
    merged = {}
    merged_score = {}
    for score, cnt, name, codes, pct, amount, _, _ in scored:
        cname = _cmap.get(name, name)  # 映射后的概念名
        if cname not in merged:
            merged[cname] = {'codes': set(), 'pct': pct, 'amount': amount, 'name': name}
            merged_score[cname] = score
        else:
            merged_score[cname] = max(merged_score[cname], score)
        merged[cname]['codes'].update(codes)
        merged[cname]['pct'] = max(merged[cname]['pct'], pct)
        merged[cname]['amount'] += amount

    # 合并后的列表，按评分降序
    deduped = [(len(v['codes']), v['name'], list(v['codes']), v['pct'], v['amount'])
               for v in merged.values()]
    deduped.sort(key=lambda x: merged_score.get(x[1], x[0]), reverse=True)

    # V5：允许重叠，直接取TOP3
    top3 = get_top3_with_overlap(deduped)

    # 评分注入
    result = []
    for t in top3:
        score = merged_score.get(t[0], t[1])
        result.append(t + (round(score, 1),))

    return result


# ═══ 数据校验 ═══
def _validate_data(total_up, sb, lb2, lb3, lb4, lb5, sylb,
                   max_board, em, up_stocks, board_days, logger):
    """双环节数据真实性校验 + V5确定性校验(R7)。

    第一环节：内部一致性
    - R1: 所有连板 = 2板 + 3板 + 4板 + 5板以上
    - R2: 涨停总数 >= 首板 + 连板
    - R3: 最高板 >= 分类隐含最高板

    第二环节：跨数据源交叉验证
    - R4: 东方财富封涨停数 <= 同花顺涨停数
    - R5: 东财覆盖率合理性
    - R6: 东方财富全接口数据完整性

    V5新增：
    - R7: 连板数据确定性校验
    """
    ok = True
    sb_count, lb_count = len(sb), len(sylb)
    ths_codes = {str(s.get('code', '')).zfill(6) for s in (up_stocks or [])}

    # ═══ 第一环节 ═══
    # R1
    lb_sum = len(lb2) + len(lb3) + len(lb4) + len(lb5)
    if lb_count != lb_sum:
        logger.emit('[校验-R1] 所有连板%d != %d+%d+%d+%d=%d' % (
            lb_count, len(lb2), len(lb3), len(lb4), len(lb5), lb_sum), level='CHECK')
        ok = False

    # R2
    if total_up < sb_count + lb_count:
        logger.emit('[校验-R2] 涨停总数%d < 首板%d + 连板%d=%d' % (
            total_up, sb_count, lb_count, sb_count + lb_count), level='CHECK')
        ok = False

    # R3
    implied_max = 0
    if lb5: implied_max = max(implied_max, 5)
    if lb4: implied_max = max(implied_max, 4)
    if lb3: implied_max = max(implied_max, 3)
    if lb2: implied_max = max(implied_max, 2)
    if max_board < implied_max:
        logger.emit('[校验-R3] 最高板%d < 分类隐含最高%d' % (max_board, implied_max), level='CHECK')
        ok = False

    # ═══ 第二环节 ═══
    em_zt = em.get('zting', []) if em else []

    # R4
    if len(em_zt) > total_up:
        logger.emit('[校验-R4] 东财封涨停%d > 同花顺涨停%d' % (len(em_zt), total_up), level='CHECK')
        ok = False

    # R5
    if ths_codes and em_zt:
        em_zt_set = set(em_zt)
        overlap = ths_codes & em_zt_set
        coverage = len(overlap) / max(len(ths_codes), 1) * 100
        if coverage < 10:
            logger.emit('[校验-R5] 数据源严重偏离：东财覆盖率仅%.0f%%（%d/%d）' % (
                coverage, len(overlap), len(ths_codes)), level='CHECK')
            ok = False
        elif coverage < 30:
            logger.emit('[校验-R5] 东财覆盖率较低：%.0f%%（%d/%d），可能因封板率不足' % (
                coverage, len(overlap), len(ths_codes)), level='WARN')

    # R6
    total_em = len(em_zt) + len(em.get('dting', [])) + len(em.get('zhaban', [])) + len(em.get('cengdting', []))
    if total_em == 0:
        logger.emit('[校验-R6] 东财全接口无数据返回', level='WARN')

    # ═══ V5新增：R7 确定性校验 ═══
    # 检查是否有降级计算的股票（非TDX本地计算的）
    degraded_count = 0
    for code, board_val in board_days.items():
        if board_val == 1:
            # 可能是TDX缺失降级的，检查日线
            daily = read_tdx_daily(code)
            if not daily:
                degraded_count += 1
    if degraded_count > 0:
        logger.emit('[校验-R7] %d只股票使用了降级计算(非TDX本地)，连板结果可能不确定' % degraded_count, level='WARN')

    if ok:
        overlaps = len(ths_codes & set(em_zt)) if ths_codes and em_zt else 0
        cov = overlaps / max(total_up, 1) * 100 if total_up else 0
        logger.emit('[校验] 全部通过 | 同花顺%d只 东财封涨停%d只 覆盖率%.0f%%' % (
            total_up, len(em_zt), cov))
    else:
        logger.emit('[校验] 存在异常，请核查数据', level='CHECK')


def _stale_recovery(blocknew_dir, date_str):
    """API 失败时保留旧板块数据，仅标记 stale。"""
    stale = {'stale': True, 'date': date_str,
             'time': datetime.now().strftime('%H:%M:%S'),
             'reason': 'API 无数据返回'}
    save_json(DATA_DIR / 'latest.json', stale)


# ═══ 板块安装 ═══
def _get_sector_name(fn, static_name, concept_names=None):
    """获取板块显示名：动态概念名优先，无则用内置名称。

    V5新增：ZJL和ZFB2的动态命名。

    Args:
        fn: 板块文件名。
        static_name: 静态显示名。
        concept_names: 动态命名列表。

    Returns:
        板块显示名。
    """
    if not concept_names:
        return static_name
    if fn == 'RDGN' and len(concept_names) >= 1:
        return concept_names[0]
    if fn == 'HYRD' and len(concept_names) >= 2:
        return concept_names[1]
    if fn == 'RDGN3' and len(concept_names) >= 3:
        return concept_names[2]
    if fn == 'ZJL' and len(concept_names) >= 4:
        return concept_names[3]
    if fn == 'ZFB2' and len(concept_names) >= 5:
        return concept_names[4]
    return static_name


def install_blocks(config=None, callback=None, concept_names=None):
    """安装板块到通达信（V5：16个板块）。

    Args:
        config: 配置字典。
        callback: 日志回调。
        concept_names: 动态命名列表（含V5新增的晋级率和昨涨停今名称）。
    """
    from utils import _get_logger
    logger = _get_logger()
    if callback and not logger.callback:
        logger.set_callback(callback)
    _logs_before = len(logger.logs)

    if config is None:
        config = load_config()

    tdx_dir = config.get('tdxDir', 'C:\\new_tdx')
    blocknew_dir = os.path.join(tdx_dir, 'T0002', 'blocknew')
    os.makedirs(blocknew_dir, exist_ok=True)

    gridtab_path = os.path.join(tdx_dir, 'T0002', 'gridtab.dat')
    if os.path.exists(gridtab_path):
        backup_file(gridtab_path)
        log('  已备份 gridtab.dat')

    config['blocknewDir'] = blocknew_dir
    save_config(config)

    log('--- 安装通达信复盘板块(V5: 16个) ---')

    # 写入 blocknew.cfg（通达信二进制格式：120字节/条）
    cfg_path = os.path.join(blocknew_dir, 'blocknew.cfg')
    buf = bytearray()
    for fn, nm, _ in SECTORS:
        dn = _get_sector_name(fn, nm, concept_names)
        rec = bytearray(120)
        gbk = dn.encode('gbk')
        rec[0:len(gbk)] = gbk
        rec[48:50] = b'\x00\x00'
        ascii_fn = fn.encode('ascii')
        rec[50:50 + len(ascii_fn)] = ascii_fn
        buf.extend(rec)
    buf.extend(b'\x00' * 48)  # 终结块
    with open(cfg_path, 'wb') as f:
        f.write(buf)
    log('  blocknew.cfg: %d个板块 (%d bytes)' % (len(SECTORS), len(buf)))

    # 写入 blocknew.clr（通达信二进制格式：320字节/条）
    clr_path = os.path.join(blocknew_dir, 'blocknew.clr')
    clr_buf = bytearray()
    for fn, nm, _ in SECTORS:
        dn = _get_sector_name(fn, nm, concept_names)
        rec = bytearray(320)
        gbk = dn.encode('gbk')
        rec[0:len(gbk)] = gbk
        clr_buf.extend(rec)
    with open(clr_path, 'wb') as f:
        f.write(clr_buf)
    log('  blocknew.clr: %d个板块 (%d bytes)' % (len(SECTORS), len(clr_buf)))

    # 同步更新 LastSync
    lastsync_dir = os.path.join(blocknew_dir, 'LastSync')
    if os.path.isdir(lastsync_dir):
        with open(os.path.join(lastsync_dir, 'blocknew.cfg'), 'wb') as f:
            f.write(buf)
        with open(os.path.join(lastsync_dir, 'blocknew.clr'), 'wb') as f:
            f.write(clr_buf)
        for fn, _, _ in SECTORS:
            src = os.path.join(blocknew_dir, fn + '.blk')
            dst = os.path.join(lastsync_dir, fn + '.blk')
            if os.path.exists(src):
                shutil.copy2(src, dst)
        log('  LastSync 已同步')

    # 写入 gridtab.dat（全量替换板块条目，保留非板块旧标签）
    new_data = bytearray()
    sector_rids = set(range(0x9026, 0x9036 + 1))  # V5: 扩展到0x9036
    sector_static_names = {nm for _, nm, _ in SECTORS}
    if os.path.exists(gridtab_path):
        with open(gridtab_path, 'rb') as f:
            base = f.read()
        for i in range(len(base) // 38):
            rec = base[i * 38:(i + 1) * 38]
            rid = struct.unpack('<H', rec[18:20])[0]
            name = rec[1:14].rstrip(b'\x00').decode('gbk', errors='replace').strip()
            if rid in sector_rids or name in sector_static_names:
                continue
            new_data.extend(rec)

    for fn, nm, rid in SECTORS:
        dn = _get_sector_name(fn, nm, concept_names)
        rec = bytearray(38)
        rec[0] = 0x00
        gbk = dn.encode('gbk')
        rec[1:1 + len(gbk)] = gbk
        rec[14:18] = b'\x11\x01\x00\x00'
        rec[18:20] = struct.pack('<H', rid)
        rec[30:34] = b'\xff\xff\xff\xff'
        new_data.extend(rec)

    with open(gridtab_path, 'wb') as f:
        f.write(bytes(new_data))
    log('  gridtab.dat: %d个标签' % (len(new_data) // 38))

    # 创建空 .blk 文件
    for fn, _, _ in SECTORS:
        blk_path = os.path.join(blocknew_dir, fn + '.blk')
        if not os.path.exists(blk_path):
            with open(blk_path, 'wb') as f:
                pass
    log('  %d个板块文件已就绪' % len(SECTORS))
    log('--- 安装完成，重启通达信查看板块 ---')

    return logger.logs[_logs_before:]
