#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘引擎 V4.0：数据获取 → 连板计算(TDX本地) → 概念分析(reason_type) → 板块写入 → Word报告

V4.0变更（订阅制+推送改造）：
- _push_wechat 改为从 license.py 读取推送凭证（不再依赖 config.json）
- 推送逻辑保留，配置源改为用户中心绑定的 token
V3.1.0变更（QA审核修复+安全加固）：
- SSL验证恢复：HTTPS默认验证证书，HTTP自动跳过（不再全局禁用SSL）
- blocknewDir 自动从 tdxDir 推导（防止用户手误导致路径不一致）
- 板块配置文件 + LastSync 改用 .tmp + os.replace 原子写入
- Word报告生成状态在总结行显示（报告:OK/失败）
V3.0.0变更（QA审核修复）：
- API异常增加日志追踪（fetch_limit_up/fetch_lhb/fetch_lhb_eastmoney/fetch_eastmoney/_fetch_ths_day_codes）
- 推送异常增加日志（_push_wechat）
- .sp看盘界面文件操作增加异常保护
- read_tdx_daily增加LRU缓存（溢价率计算性能优化）
V2.1.2变更（Bug修复）：
- 修复概念分析 TypeError：engine.py 调用 analyze_concepts 时传入不支持的 up_stocks 参数，
  改为调用 analyze_concepts_by_reason，概念分析/综合评分/涨停原因功能恢复。
V2.1变更（基于V2实测反馈）：
1. 概念分析改用 reason_type 分组（push2.eastmoney.com 不通）
2. DT(今跌停)改为包含所有触及跌停的股票（封跌停+曾跌停）
3. ST股显式追踪，确保在所有板块可见
4. ZFB2(昨涨停今)增加API降级数据源
5. 所属概念数据随概念分析一起修复
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
    set_tdx_dir, read_tdx_daily, calc_board_days_tdx, is_limit_up, is_limit_down,
    write_extern_user, set_column_green,
    calc_period_gain, set_code_name_map,
    DATA_DIR
)
from concept_analyzer import analyze_concepts_by_reason, get_top3_with_overlap, load_concept_map
from word_report import generate_word_report
from license import LicenseManager as _LicenseManager

THS = {'Referer': 'https://www.10jqka.com.cn/'}

# 概念持续追踪文件
CONCEPT_HISTORY_FILE = DATA_DIR / 'concept_history.json'

# ═══ 板块定义（V2：15个板块，移除ZJL）═══
SECTORS = [
    ('ZFB',    '总涨停',      0x9026),
    ('SB',     '首板',        0x9027),
    ('2LB',    '2连板',       0x9028),
    ('3LB',    '3连板',       0x9029),
    ('4LB',    '4连板',       0x902A),
    ('5BYS',   '5板以上',     0x902B),
    ('SYLB',   '所有连板',    0x902C),
    ('LHBQ20', '龙虎榜前20',  0x902E),
    ('DT',     '今跌停',      0x902F),
    ('ZDT',    '曾跌停',      0x9031),
    ('ZBQ',    '曾涨停',      0x9030),
    ('ZFB2',   '昨涨停今',    0x9036),
    ('RDGN',   '主线',        0x9032),  # 动态命名
    ('HYRD',   '次线',        0x9033),  # 动态命名
    ('RDGN3',  '潜在',        0x9034),  # 动态命名
    ('R20D',   '20日涨幅排名', 0x9039),
    ('R60D',   '60日涨幅排名', 0x903A),
]


# ═══ 数据获取 ═══
def fetch_limit_up(date_str):
    """同花顺涨停池（含涨停原因）。

    Args:
        date_str: 日期字符串 YYYYMMDD。

    Returns:
        涨停股信息列表，含 reason_type（涨停原因）字段。
        注意：同花顺不返回ST股，ST股需从东财补充。
    """
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
    except (json.JSONDecodeError, KeyError) as e:
        log('  同花顺涨停池JSON解析失败: %s' % str(e), level='WARN')
    return []


def fetch_lhb(date_str):
    """龙虎榜（ClawHub主源）。返回 (摘要列表, 详细数据字典)。

    摘要列表: [{code, name, net_buy}, ...]  按净买额降序前20
    详细字典: {code: {buy_total, sell_total, net_buy, brokers: {buy:[], sell:[]}}, ...}
    """
    fmt = '%s-%s-%s' % (date_str[:4], date_str[4:6], date_str[6:8])
    url = 'http://fffy520.gicp.net:8003/api/lhb/daily?date=%s' % fmt
    data = http_get(url, timeout=15)
    if data[0] != 200:
        return [], {}
    try:
        d = json.loads(data[1])
        if d.get('code') == 200:
            lst = d.get('data', [])
            detail = {}
            for item in lst:
                code = str(item.get('code', '')).zfill(6)
                # 保留 buy_total/sell_total/brokers 用于自定义列
                detail[code] = {
                    'code': code,
                    'name': item.get('name', ''),
                    'net_buy': float(item.get('net_buy', 0)),
                    'buy_total': float(item.get('buy_total', 0)),
                    'sell_total': float(item.get('sell_total', 0)),
                    'brokers': item.get('brokers', {}),
                }
            # 摘要列表（按净买额排序取前20，兼容原有板块写入）
            lst.sort(key=lambda x: float(x.get('net_buy', 0)), reverse=True)
            summary = [{'code': str(s.get('code', '')).zfill(6),
                        'name': s.get('name', ''),
                        'net_buy': float(s.get('net_buy', 0))} for s in lst[:20]]
            return summary, detail
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log('  ClawHub龙虎榜JSON解析失败: %s' % str(e), level='WARN')
    return [], {}


def fetch_lhb_eastmoney(date_str):
    """东方财富龙虎榜备选数据源。"""
    fmt = '%s-%s-%s' % (date_str[:4], date_str[4:6], date_str[6:8])
    url = ('https://data.eastmoney.com/DataCenter_V3/chart/LHBD.ashx'
           '?startdate=%s&enddate=%s&top=20&cb=' % (fmt, fmt))
    data = http_get(url, timeout=15)
    if data[0] != 200:
        return []
    try:
        raw = json.loads(data[1])
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
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log('  东财龙虎榜JSON解析失败: %s' % str(e), level='WARN')
        return []


# ── 龙虎榜自定义列工具函数 ──
def _abbreviate_broker(name):
    """缩写券商名称到6字以内。

    知名实体固定映射，其余去掉上市公司后缀后截断。
    """
    name = str(name).strip()
    for full, short in [('深股通专用', '深股通'), ('沪股通专用', '沪股通'),
                         ('机构专用', '机构')]:
        if full in name:
            return short
    for s in ['证券', '有限', '责任', '股份', '公司', '分公司', '营业部']:
        name = name.replace(s, '')
    return name[:6] if len(name) > 6 else name


def _broker_label(name):
    """返回券商类型标记：机构→[机] 港股通→[港] 其他→[游]"""
    if '机构专用' in name:
        return '[机]'
    if '深股通' in name or '沪股通' in name:
        return '[港]'
    return '[游]'


def _format_amount(val):
    """金额格式化：>=1亿显示X.X亿，否则X万（整数）。val单位=万元"""
    if abs(val) >= 10000:
        return '%.1f亿' % (val / 10000)
    return '%d万' % int(val)


def _format_amount_e(val):
    """金额格式化（亿单位）：显示X.X（1位小数），val单位=万元"""
    return '%.1f' % (abs(float(val)) / 10000)


def format_lhb_custom(lhb_item):
    """将单只股票的龙虎榜详细数据格式化为单列文本（数据号3）。

    格式: 净买：净额；简称1金额1/简称2金额2/简称3金额3
    - 净买入（>=0）→ 显示买入前3简称+买入额（亿）
    - 净卖出（<0） → 显示卖出前3简称+卖出额（亿）

    Args:
        lhb_item: fetch_lhb 返回的 detail 字典中某一只股票的数据。

    Returns:
        单个字符串，适合写入 extern_user.txt 数据号3。
    """
    net_buy = lhb_item.get('net_buy', 0)
    net_str = '净买：%s' % _format_amount(net_buy)

    brokers = lhb_item.get('brokers', {})
    if net_buy >= 0:
        # 净买入 → 显示买入前3：简称+买入额（亿）
        parts = []
        for b in brokers.get('buy', [])[:3]:
            name = _abbreviate_broker(b.get('name', ''))
            amt = _format_amount_e(b.get('buy', 0))
            parts.append('%s%s' % (name, amt))
        name_str = '/'.join(parts) if parts else '-'
    else:
        # 净卖出 → 显示卖出前3：简称+卖出额（亿，按卖出额降序）
        sell_sorted = sorted(brokers.get('sell', []),
                             key=lambda x: float(x.get('sell', 0)), reverse=True)[:3]
        parts = []
        for b in sell_sorted:
            name = _abbreviate_broker(b.get('name', ''))
            amt = _format_amount_e(b.get('sell', 0))
            parts.append('%s%s' % (name, amt))
        name_str = '/'.join(parts) if parts else '-'

    return '%s；%s' % (net_str, name_str)


def fetch_eastmoney(date_str):
    """东方财富 push2ex：封涨停/跌停/曾涨停/曾跌停/ST补充。

    确认规则：
    - 封单>0 确认有封板动作
    - 通过价格对比（TDX日线获取昨日收盘价）验证当前价是否确实在涨停/跌停价位
    - 排除收盘前回封无力或价格偏离的情况

    Args:
        date_str: 日期字符串 YYYYMMDD。

    Returns:
        {zting, dting, zhaban, cengdting, st_zting, all_dting} 字典。
        all_dting: 所有触及跌停的股票（封跌停+曾跌停合并）
    """
    url = ('https://push2ex.eastmoney.com/getAllStockChanges?'
           'type=4,8,16,32&ut=7eea3edcaed734bea9cbfc24409ed989'
           '&pageindex=0&pagesize=500&dpt=wzchanges')
    data = http_get(url)
    if data[0] != 200:
        return {'zting': [], 'dting': [], 'zhaban': [], 'cengdting': [],
                'st_zting': [], 'st_codes': set(), 'all_dting': []}

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

        result = {
            'zting': [], 'dting': [], 'zhaban': [], 'cengdting': [],
            'st_zting': [], 'st_codes': set(), 'all_dting': [],
        }
        seen_zt, seen_dt = set(), set()
        all_dt_set = set()  # 所有触及跌停（封跌停+曾跌停）

        for code, recs in records.items():
            recs.sort(key=lambda x: x['time'])
            last = recs[-1]
            last_type = last['type']
            last_fd = last['fengdan']
            last_price = last['price']
            name = last['name']
            is_st = name.startswith(('ST', '*ST'))

            if is_st:
                result['st_codes'].add(code)

            # 获取昨日收盘价用于价格验证
            daily = read_tdx_daily(code)
            prev_close = 0
            if daily and len(daily) >= 2:
                prev_close = daily[-2]['close']

            # 封涨停（须封板金额>0 + 价格验证）
            if last_type == 4 and last_fd > 0 and code not in seen_zt:
                if prev_close > 0 and not is_limit_up(code, last_price, prev_close, is_st):
                    continue  # 价格未到涨停位，跳过
                seen_zt.add(code)
                result['zting'].append(code)
                if is_st:
                    result['st_zting'].append({'code': code, 'name': name})

            # 封跌停（须封板金额>0 + 价格验证）
            if last_type == 8 and last_fd > 0 and code not in seen_dt:
                if prev_close > 0 and not is_limit_down(code, last_price, prev_close, is_st):
                    continue  # 价格未到跌停位，跳过
                seen_dt.add(code)
                result['dting'].append(code)
                all_dt_set.add(code)

            # 曾涨停（炸板）：曾经封涨停（封单>0），但最后状态变为曾涨停
            ever_closed_zt = any(r['type'] == 4 and r['fengdan'] > 0 for r in recs)
            if ever_closed_zt and last_type == 16:
                # 曾涨停也用价格验证：曾经封板时的价格是否真正到涨停位
                if prev_close > 0:
                    # 取曾经封板时的价格进行验证
                    zt_recs = [r for r in recs if r['type'] == 4 and r['fengdan'] > 0]
                    price_ok = any(is_limit_up(code, r['price'], prev_close, is_st) for r in zt_recs)
                    if not price_ok:
                        continue
                result['zhaban'].append(code)

            # 曾跌停：曾经封跌停（封单>0），但最后状态变为曾跌停
            ever_closed_dt = any(r['type'] == 8 and r['fengdan'] > 0 for r in recs)
            if ever_closed_dt and last_type == 32:
                if prev_close > 0:
                    dt_recs = [r for r in recs if r['type'] == 8 and r['fengdan'] > 0]
                    price_ok = any(is_limit_down(code, r['price'], prev_close, is_st) for r in dt_recs)
                    if not price_ok:
                        continue
                result['cengdting'].append(code)
                all_dt_set.add(code)

        # all_dting = 封跌停 + 曾跌停（所有今日触及跌停的股票）
        result['all_dting'] = list(all_dt_set)

        return result
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log('  东财push2ex数据解析失败: %s' % str(e), level='WARN')
    return {'zting': [], 'dting': [], 'zhaban': [], 'cengdting': [],
            'st_zting': [], 'st_codes': set(), 'all_dting': []}


# ═══ 连板天数计算（V2：TDX本地日线）═══
def _save_today_boards(date_str, boards_dict):
    """保存今日连板数据。"""
    fp = DATA_DIR / 'boards' / ('%s.json' % date_str)
    save_json(fp, {
        'date': date_str,
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'boards': boards_dict
    })


def _load_yesterday_boards(date_str):
    """加载最近可用日的boards缓存。"""
    dt = datetime.strptime(date_str, '%Y%m%d')
    for i in range(1, 8):
        prev = (dt - timedelta(days=i)).strftime('%Y%m%d')
        fp = DATA_DIR / 'boards' / ('%s.json' % prev)
        data = load_json(fp)
        if data and data.get('boards'):
            return data['boards']
    return {}


def _fetch_ths_day_codes(date_str):
    """获取某日同花顺涨停池中的所有股票代码（降级时使用）。"""
    url = ('https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool'
           '?date=%s&type=all&page=1&limit=200' % date_str)
    s, b = http_get(url, THS, timeout=10)
    if s != 200:
        return set()
    try:
        info = json.loads(b).get('data', {}).get('info', [])
        return {str(item.get('code', '')).zfill(6) for item in info}
    except (json.JSONDecodeError, KeyError) as e:
        log('  同花顺历史涨停池JSON解析失败: %s' % str(e), level='WARN')
        return set()


def calc_board_days(today_stocks, target_date):
    """V2连板天数计算。

    非ST股：读TDX本地日线 → 向前遍历 → 涨幅判断涨停 → 数连板天数
    ST股：从boards JSON缓存继承
    降级：TDX日线缺失时用同花顺历史API
    """
    yesterday_boards = _load_yesterday_boards(target_date)
    target_int = int(target_date)

    hist_ths = None
    result = {}
    degraded_codes = []

    for s in today_stocks:
        code = str(s.get('code', '')).zfill(6)
        name = s.get('name', '')
        is_st = name.startswith(('ST', '*ST'))

        # ST股：TDX日线验证（4.8%阈值），内部自动降级到boards继承
        if is_st:
            result[code] = calc_board_days_tdx(code, target_int, is_st=True,
                                               yesterday_boards=yesterday_boards)
            continue

        # 非ST：尝试TDX本地日线计算
        days = calc_board_days_tdx(code, target_int, is_st=False,
                                   yesterday_boards=None)
        if days is not None:
            result[code] = days
        else:
            degraded_codes.append(code)
            result[code] = 1

    # 降级处理
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


# ═══ 炸板率/溢价率计算 ═══
def _calc_premium_rate(yesterday_zt_codes, today_daily_data):
    """计算溢价率：昨日涨停股今日开盘平均涨幅。"""
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
    """计算炸板率。"""
    total = zting_count + zhaban_count
    return (zhaban_count / total * 100) if total > 0 else 0


# ═══ 微信推送（V4：从 license.dat 读取推送凭证）═══
def _push_wechat(config, d, summary):
    """微信推送（通过用户中心绑定的推送通道发送）。

    V4 不再从 config.json 读取 ServerChan SendKey，
    改为从 license.dat 读取 push_token（由用户中心绑定微信时写入）。

    Args:
        config: 配置字典（保留参数签名兼容，不再从中读取推送配置）。
        d: 日期字符串 YYYYMMDD。
        summary: 摘要元组 (涨停,首板,连板,最高板,跌停,曾涨停,曾跌停,龙虎榜,溢价率,炸板率)。

    Returns:
        是否推送成功。
    """
    try:
        lm = _LicenseManager()
        key = lm.get_push_token()
    except Exception:
        key = ''
    if not key:
        return False
    try:
        title = '连板龙 %s 收盘复盘' % d
        content = ('涨停%d只 | 首板%d | 连板%d | 最高%d板\n'
                   '跌停%d只 | 曾涨停%d只 | 曾跌停%d只\n'
                   '龙虎榜前20: %d只\n'
                   '溢价率%.1f%% | 炸板率%.0f%%') % summary
        push_url = ('https://sctapi.ftqq.com/%s.send?title=%s&desp=%s' %
                    (key, urllib.request.quote(title), urllib.request.quote(content)))
        http_get(push_url, timeout=10)
        return True
    except Exception as e:
        log('  微信推送失败: %s' % str(e), level='WARN')
        return False


# ═══ 写入验证 ═══
def _write_blk_verified(filepath, codes, sector_name, up_stock_names=None):
    """写入.blk文件并输出验证日志（含ST股统计）。"""
    n = write_blk(filepath, codes)
    st_count = 0
    st_names = []
    if up_stock_names:
        for c in codes:
            c6 = str(c).strip().zfill(6)
            nm = up_stock_names.get(c6, '')
            if nm.startswith(('ST', '*ST')):
                st_count += 1
                st_names.append(nm)
    if st_count > 0:
        log('  %s.blk: %d只 (含ST %d只: %s)' % (
            sector_name, n, st_count, ','.join(st_names[:5])))
    else:
        log('  %s.blk: %d只' % (sector_name, n))
    return n


# ═══ 主引擎 ═══
def run_engine(config=None, callback=None):
    """复盘引擎主流程（V2.1：9步）。

    步骤：
    1. 获取涨停(同花顺+ST补充)
    2. 连板天数计算(TDX本地日线)
    3. 龙虎榜(ClawHub+东财备选)
    4. 东财封板/跌停/炸板
    5. 概念分析(reason_type分组)
    6. 数据校验
    7. 安装板块配置(含动态命名)
    8. 写入.blk文件+extern_user.txt
    9. 推送+保存
    """
    from utils import _get_logger
    logger = _get_logger()
    logger.set_callback(callback)
    logger.logs = []

    if config is None:
        config = load_config()

    tdx_dir = config.get('tdxDir', 'C:\\new_tdx')
    blocknew_dir = os.path.join(tdx_dir, 'T0002', 'blocknew')  # 从 tdxDir 自动推导
    os.makedirs(blocknew_dir, exist_ok=True)
    os.makedirs(DATA_DIR / 'boards', exist_ok=True)
    os.makedirs(DATA_DIR / 'history', exist_ok=True)

    set_tdx_dir(tdx_dir)
    clean_old_logs()

    # ── 确定目标日期 ──
    today = datetime.now()
    if not is_trading_day(today):
        log('今日非交易日，使用最近交易日')
    target = get_latest_trading_day(today)
    d = target.strftime('%Y%m%d')

    if today.hour < 15 and today.date() == target.date():
        prev = today - timedelta(days=1)
        target = get_latest_trading_day(prev)
        d = target.strftime('%Y%m%d')

    log('--- 复盘引擎 V3.1 目标日期: %s ---' % d)

    # ── 1. 获取涨停数据（同花顺 + 东财ST补充）──
    log('  获取涨停(同花顺)...')
    up_stocks = fetch_limit_up(d)
    if not up_stocks:
        log('  [WARN] 无涨停数据，保留旧板块', level='WARN')
        _stale_recovery(blocknew_dir, d)
        return logger.logs
    log('  涨停(同花顺): %d只' % len(up_stocks))

    # 东财数据（含ST涨停补充+封板/跌停/炸板）
    em = fetch_eastmoney(d)

    # ST股补充：东财st_zting中的ST股，同花顺不返回的
    st_extra = em.get('st_zting', [])
    st_codes_from_em = em.get('st_codes', set())
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

    # 构建 {code: name} 映射
    code_name_map = {}
    for s in up_stocks:
        c = str(s.get('code', '')).zfill(6)
        code_name_map[c] = s.get('name', '')

    # 显式统计ST股
    st_in_up = [c for c, n in code_name_map.items() if n.startswith(('ST', '*ST'))]
    if st_in_up:
        log('  ST涨停股: %d只 (%s)' % (len(st_in_up), ','.join(st_in_up[:5])))

    # ── 2. 连板天数计算（TDX本地日线）──
    log('  连板天数计算(TDX本地日线)...')
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

    # ST股统计（保留在所有子板块中，仅做日志提示）
    sb_st = [c for c in sb if code_name_map.get(c, '').startswith(('ST', '*ST'))]
    lb_st = [c for c in sylb if code_name_map.get(c, '').startswith(('ST', '*ST'))]
    if sb_st or lb_st:
        log('  首板含ST:%d只(%s), 连板含ST:%d只(%s)' % (
            len(sb_st), ','.join(sb_st[:3]),
            len(lb_st), ','.join(lb_st[:3])))



    _save_today_boards(d, days)

    # 炸板率
    zhaban_rate = _calc_zhaban_rate(len(em.get('zhaban', [])), len(em.get('zting', [])))
    log('  炸板率: %.0f%%' % zhaban_rate)

    # 溢价率
    yesterday_boards = _load_yesterday_boards(d)
    yesterday_zt_codes = set(yesterday_boards.keys()) if yesterday_boards else set()
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

    # ── 3. 龙虎榜 ──
    log('  获取龙虎榜(ClawHub)...')
    log('  [提示] 龙虎榜数据通常18:00-20:00后才更新，在此之前可能为空', level='INFO')
    lhb, lhb_detail = fetch_lhb(d)
    lhb_source = 'ClawHub'
    if not lhb:
        log('  ClawHub无数据，尝试东财龙虎榜...', level='WARN')
        lhb = fetch_lhb_eastmoney(d)
        lhb_source = '东财'
        if lhb:
            log('  [降级] 龙虎榜使用东财数据源', level='WARN')
            # 东财无明细，用净买入生成基础数据供自定义列显示
            lhb_detail = {}
            for s in lhb:
                code = s.get('code', '')
                nb = s.get('net_buy', 0)
                if nb:
                    lhb_detail[code] = {
                        'code': code, 'name': s.get('name', ''),
                        'net_buy': nb, 'buy_total': nb if nb > 0 else 0,
                        'sell_total': -nb if nb < 0 else 0,
                        'brokers': {},
                    }
        else:
            lhb_detail = {}
    log('  龙虎榜前20: %d只 (%s), 详细%d只' % (
        len(lhb) if lhb else 0, lhb_source, len(lhb_detail)))

    # ── 4. 东方财富数据（封板/跌停/炸板）──
    all_dting = em.get('all_dting', [])
    log('  涨停(东财封板):%d  跌停(全部):%d  曾涨停:%d  曾跌停:%d' % (
        len(em['zting']), len(all_dting), len(em['zhaban']), len(em['cengdting'])))

    # ── 5. 概念分析（reason_type分组 + V4综合评分）──
    log('  概念分析(reason_type分组+V4综合评分)...')
    limit_up_codes = {str(s.get('code', '')).zfill(6) for s in up_stocks}
    concept_result = analyze_concepts_by_reason(up_stocks, callback=log)
    all_ranked = concept_result.get('ranked', [])
    history_data = _load_concept_history()
    top3 = _rank_concepts_composite(all_ranked, limit_up_codes, days,
                                    up_stocks=up_stocks, history_data=history_data)

    # 保存今日概念排名到持续追踪
    if all_ranked:
        _save_concept_history(d, all_ranked)

    top1_codes, top2_codes, top3_codes = [], [], []
    concept_names = []
    for i, t in enumerate(top3[:3]):
        concept_name = t[0]  # 返回格式: (概念名, 涨停数, 股票列表, ...)
        cnt = t[1]
        codes = t[2]
        # V4命名格式：{数字}{概念名}，总长≤6字符
        name_body = str(i + 1) + concept_name
        while len(name_body) > 6:
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

    # ── 6. 数据校验 ──
    _validate_data(
        total_up=len(up_stocks),
        sb=sb, lb2=lb2, lb3=lb3, lb4=lb4, lb5=lb5, sylb=sylb,
        max_board=max_board,
        em=em, up_stocks=up_stocks,
        board_days=days,
        logger=logger
    )

    # ── 7. 安装板块配置（含动态热点命名）──
    if concept_names:
        log('  动态命名: %s, %s, %s' % (
            concept_names[0] if len(concept_names) >= 1 else '-',
            concept_names[1] if len(concept_names) >= 2 else '-',
            concept_names[2] if len(concept_names) >= 3 else '-'))

    # 昨涨停今（ZFB2）代码提前获取，用于板块名计数
    zfb2_codes = list(yesterday_zt_codes) if yesterday_zt_codes else []
    if not zfb2_codes:
        log('  ZFB2: 无boards缓存，尝试同花顺API获取昨日涨停...', level='WARN')
        dt_obj = datetime.strptime(d, '%Y%m%d')
        for i in range(1, 8):
            prev = (dt_obj - timedelta(days=i)).strftime('%Y%m%d')
            prev_codes = _fetch_ths_day_codes(prev)
            if len(prev_codes) >= 20:
                zfb2_codes = list(prev_codes)
                log('  ZFB2: 从同花顺API获取 %s 涨停%d只' % (prev, len(zfb2_codes)))
                break

    zfb2_name = '昨涨停今'
    extended_names = list(concept_names)
    extended_names.append(zfb2_name)  # index 3 -> ZFB2

    counts = {
        'ZFB': len(up_stocks),
        'SB': len(sb),
        '2LB': len(lb2),
        '3LB': len(lb3),
        '4LB': len(lb4),
        '5BYS': len(lb5),
        'SYLB': len(sylb),
        'LHBQ20': len(lhb) if lhb else 0,
        'DT': len(all_dting),
        'ZBQ': len(em['zhaban']),
        'ZDT': len(em['cengdting']),
        'ZFB2': len(zfb2_codes),
    }

    # ── 7b. 阶段涨幅排名板块（近20/60交易日涨幅Top30，仅主板，不含新股）──
    set_code_name_map(code_name_map)
    d_int = int(d)
    log('  计算阶段涨幅排名（主板+非新股）...')
    r20d = calc_period_gain(tdx_dir, d_int, period_days=20, top_n=30, cutoff_days=90)
    r60d = calc_period_gain(tdx_dir, d_int, period_days=60, top_n=30, cutoff_days=90)
    r20d_codes = [x[0] for x in r20d]
    r60d_codes = [x[0] for x in r60d]
    counts['R20D'] = len(r20d_codes)
    counts['R60D'] = len(r60d_codes)
    log('  20日涨幅排名(R20D): %d只, 60日涨幅排名(R60D): %d只' % (len(r20d_codes), len(r60d_codes)))

    install_blocks(config, concept_names=extended_names, counts=counts)

    # 更新看盘界面热点名称
    _update_sp(tdx_dir, concept_names)

    # 构建涨停时间映射（用于排序，仅同花顺数据含此字段，东财ST补充数据不包含）
    first_time_map = {}
    for s in up_stocks:
        code = str(s.get('code', '')).zfill(6)
        ft = s.get('first_limit_up_time')
        if ft is not None and str(ft).isdigit():
            first_time_map[code] = int(ft)

    # ── 8. 写入板块文件+extern_user.txt ──
    log('  写入通达信板块...')

    # 排序规则：
    #   ZFB/SYLB/概念板块 → 按连板天数降序（高连板在前）
    #   SB/2LB/3LB/4LB/5BYS → 按涨停时间升序（早封板在前，无时间数据排末尾）
    zfb_codes = sorted(
        [str(s.get('code', '')).zfill(6) for s in up_stocks],
        key=lambda c: -days.get(c, 1))
    sb_sorted = sorted(sb, key=lambda c: first_time_map.get(c, 0xFFFFFFFF))
    lb2_sorted = sorted(lb2, key=lambda c: first_time_map.get(c, 0xFFFFFFFF))
    lb3_sorted = sorted(lb3, key=lambda c: first_time_map.get(c, 0xFFFFFFFF))
    lb4_sorted = sorted(lb4, key=lambda c: first_time_map.get(c, 0xFFFFFFFF))
    lb5_sorted = sorted(lb5, key=lambda c: first_time_map.get(c, 0xFFFFFFFF))
    sylb_sorted = sorted(sylb, key=lambda c: -days.get(c, 1))
    top1_sorted = sorted(top1_codes, key=lambda c: -days.get(c, 1))
    top2_sorted = sorted(top2_codes, key=lambda c: -days.get(c, 1))
    top3_sorted = sorted(top3_codes, key=lambda c: -days.get(c, 1))

    # 涨停类板块
    _write_blk_verified(os.path.join(blocknew_dir, 'ZFB.blk'),
                        zfb_codes, 'ZFB', code_name_map)
    _write_blk_verified(os.path.join(blocknew_dir, 'SB.blk'), sb_sorted, 'SB', code_name_map)
    _write_blk_verified(os.path.join(blocknew_dir, '2LB.blk'), lb2_sorted, '2LB', code_name_map)
    _write_blk_verified(os.path.join(blocknew_dir, '3LB.blk'), lb3_sorted, '3LB', code_name_map)
    _write_blk_verified(os.path.join(blocknew_dir, '4LB.blk'), lb4_sorted, '4LB', code_name_map)
    _write_blk_verified(os.path.join(blocknew_dir, '5BYS.blk'), lb5_sorted, '5BYS', code_name_map)
    _write_blk_verified(os.path.join(blocknew_dir, 'SYLB.blk'), sylb_sorted, 'SYLB', code_name_map)

    # 龙虎榜
    if lhb:
        _write_blk_verified(os.path.join(blocknew_dir, 'LHBQ20.blk'),
                            [str(s.get('code', '')).zfill(6) for s in lhb],
                            'LHBQ20', code_name_map)
    else:
        _write_blk_verified(os.path.join(blocknew_dir, 'LHBQ20.blk'), [], 'LHBQ20')

    # 跌停类：DT=今日所有触及跌停（封跌停+曾跌停），ZDT=仅曾跌停
    _write_blk_verified(os.path.join(blocknew_dir, 'DT.blk'),
                        all_dting, 'DT(全部跌停)', code_name_map)
    _write_blk_verified(os.path.join(blocknew_dir, 'ZBQ.blk'),
                        em['zhaban'], 'ZBQ', code_name_map)
    _write_blk_verified(os.path.join(blocknew_dir, 'ZDT.blk'),
                        em['cengdting'], 'ZDT', code_name_map)

    # 昨涨停今（ZFB2）
    _write_blk_verified(os.path.join(blocknew_dir, 'ZFB2.blk'),
                        zfb2_codes, 'ZFB2', code_name_map)

    # 概念板块（按连板天数降序）
    _write_blk_verified(os.path.join(blocknew_dir, 'RDGN.blk'),
                        top1_sorted, 'RDGN', code_name_map)
    _write_blk_verified(os.path.join(blocknew_dir, 'HYRD.blk'),
                        top2_sorted, 'HYRD', code_name_map)
    _write_blk_verified(os.path.join(blocknew_dir, 'RDGN3.blk'),
                        top3_sorted, 'RDGN3', code_name_map)

    # 20日/60日涨幅排名（90日新股过滤）
    _write_blk_verified(os.path.join(blocknew_dir, 'R20D.blk'),
                        r20d_codes, 'R20D', code_name_map)
    _write_blk_verified(os.path.join(blocknew_dir, 'R60D.blk'),
                        r60d_codes, 'R60D', code_name_map)

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
    # 构建涨停股的reason和concepts信息（V1方式：从all_ranked构建code_concepts）
    # 涨停原因：来自同花顺dataapi的up_stocks字段
    # 所属概念：来自概念分析ranked数据
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

    # 将 LHB 详细数据格式化为 write_extern_user 需要的格式
    lhb_formatted = {}
    if lhb_detail:
        for code, item in lhb_detail.items():
            text = format_lhb_custom(item)
            if text:
                lhb_formatted[code] = text

    n_extern = write_extern_user(tdx_dir, extern_stocks, code_concepts,
                                 lhb_detail=lhb_formatted if lhb_formatted else None)
    if n_extern:
        n_with_concept = sum(1 for s in extern_stocks if s['concepts'])
        n_with_reason = sum(1 for s in extern_stocks if s['reason'])
        n_with_lhb = len(lhb_formatted) if lhb_detail else 0
        log('  自定义列: %d只涨停股, %d只有原因, %d只有概念, %d只有龙虎榜' % (
            n_extern, n_with_reason, n_with_concept, n_with_lhb))

    set_column_green(tdx_dir)

    # ── 9. 推送+保存 ──
    summary = (len(up_stocks), len(sb), len(sylb), max_board,
               len(all_dting), len(em['zhaban']), len(em['cengdting']),
               len(lhb) if lhb else 0,
               premium_rate, zhaban_rate)
    if _push_wechat(config, d, summary):
        log('  [OK] 微信推送成功')

    latest = {
        'up': len(up_stocks), 'sb': len(sb), 'lb': len(sylb),
        'down': len(all_dting), 'zhaban': len(em['zhaban']),
        'lhb': len(lhb) if lhb else 0,
        'maxBoard': max_board,
        'premiumRate': round(premium_rate, 2),
        'zhabanRate': round(zhaban_rate, 1),
        'date': d, 'time': datetime.now().strftime('%H:%M:%S'),
        'concepts': [
            {'name': t[0], 'count': t[1]} for t in top3[:3]
        ] if top3 else [],
    }
    save_json(DATA_DIR / 'latest.json', latest)

    save_json(DATA_DIR / 'history' / ('%s.json' % d), {
        'date': d,
        'limitUp': len(up_stocks),
        'limitDown': len(all_dting),
        'maxBoard': max_board,
        'premiumRate': round(premium_rate, 2),
        'zhabanRate': round(zhaban_rate, 1),
        'concepts': [{'name': t[0], 'count': t[1]} for t in top3[:3]] if top3 else [],
    })

    # ── 10. 生成Word复盘报告 ──
    log('  生成Word复盘报告...')
    report_ok = True
    try:
        report_path = generate_word_report(
            date_str=d,
            up_stocks=up_stocks,
            board_days=days,
            em_data=em,
            lhb_detail=lhb_detail,
            concept_result=concept_result,
            premium_rate=premium_rate,
            zhaban_rate=zhaban_rate,
            code_name_map=code_name_map,
            callback=log,
            yesterday_zt_count=len(yesterday_zt_codes) if yesterday_zt_codes else 0,
        )
        log('  [OK] Word报告: %s' % report_path)
    except Exception as e:
        report_ok = False
        log('  [WARN] Word报告生成失败: %s' % str(e), level='WARN')

    log('--- 完成: %d涨停(%d首板/%d连板) 最高%d板 %d跌停 %d曾涨停 %d曾跌停 %d龙虎榜 溢价%.1f%% 炸板%.0f%% 报告:%s ---' % (
        len(up_stocks), len(sb), len(sylb), max_board,
        len(all_dting), len(em['zhaban']), len(em['cengdting']),
        len(lhb) if lhb else 0,
        premium_rate, zhaban_rate,
        'OK' if report_ok else '失败'))

    return logger.logs


# ═══ 概念持续追踪（跨日）═══
def _load_concept_history():
    """加载历史概念排名数据。"""
    data = load_json(CONCEPT_HISTORY_FILE)
    if data is None:
        return {'last_update': '', 'history': []}
    return data


def _save_concept_history(date_str, top_ranked):
    """保存今日概念TOP10排名到历史追踪（按映射后名称去重聚合）。"""
    history = _load_concept_history()
    _cmap = load_concept_map()
    # 按映射后名称聚合涨停数
    agg = {}
    for r in top_ranked:
        raw_name = r[1] if len(r) >= 2 else ''
        mapped = _cmap.get(raw_name, raw_name)
        cnt = r[0]
        if mapped in agg:
            agg[mapped] = agg[mapped] + cnt
        else:
            agg[mapped] = cnt
    top10 = [[k, v] for k, v in sorted(agg.items(), key=lambda x: -x[1])[:10]]
    entry = {'date': date_str, 'top10': top10}
    history['history'].append(entry)
    if len(history['history']) > 30:
        history['history'] = history['history'][-30:]
    history['last_update'] = date_str
    save_json(CONCEPT_HISTORY_FILE, history)
    log('  概念持续追踪: 已保存TOP10到 %s' % date_str)


# ═══ V4综合评分（6维度+分级门槛+持续性）═══
def _rank_concepts_composite(ranked, limit_up_codes, board_data,
                             up_stocks=None, history_data=None):
    """V4综合评分：6维度评分+分级门槛+持续性追踪。

    评分维度与权重：
    - 广度B (25%)：涨停股聚集度，5只即满分
    - 高度H (25%)：最高连板数，6板即满分
    - 梯队T (15%)：板数覆盖层数，3层即满分
    - 资金M (20%)：概念内个股当日成交总额，百分位排名
    - 持续性P (10%)：前N日该概念是否在TOP10中
    - 强度I (5%)：早盘涨停时间中位值（越早越好）

    Args:
        ranked: analyze_concepts 返回的 ranked 列表
            [(涨停数, 概念名, 股票列表, 涨跌幅, 成交额), ...]
        limit_up_codes: 今日所有涨停股代码集合
        board_data: {code: board_days} 格式的连板数据
        up_stocks: 原始涨停股列表（用于提取涨停时间等）
        history_data: 概念持续追踪数据

    Returns:
        [(概念名, 涨停数, 股票列表, 行业名, 涨跌幅, 成交额, 综合评分, 等级), ...]
        其中等级为 'S主线'/'A次线'/'B观察'/'C'
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

    # ---- 构建辅助映射 ----

    # 涨停时间映射 {code: first_limit_up_time_in_minutes}
    first_time_map = {}
    if up_stocks:
        for s in up_stocks:
            code = str(s.get('code', '')).zfill(6)
            ft = s.get('first_limit_up_time')
            if ft is not None and str(ft).isdigit():
                first_time_map[code] = int(ft)

    # 成交额映射 {code: 今日成交额(万元)}
    amount_map = {}
    for _, _, codes, _, _ in parsed:
        for c in codes:
            if c not in amount_map:
                daily = read_tdx_daily(c)
                if daily and len(daily) >= 1:
                    amount_map[c] = daily[-1].get('amount', 0)
                else:
                    amount_map[c] = 0

    # ---- 为每个概念计算6维度评分 ----

    scored = []
    for cnt, name, codes, pct, amount in parsed:
        # 高度/梯队
        max_board_val = 0
        tiers = set()
        for c in codes:
            b = board_data.get(c, 1)
            max_board_val = max(max_board_val, b)
            if b >= 2:
                tiers.add(b)
        tier_count = len(tiers)

        # 资金：概念内各股成交额之和
        total_amount = sum(amount_map.get(c, 0) for c in codes)

        # 强度：涨停时间中位值
        times = []
        for c in codes:
            t = first_time_map.get(c)
            if t is not None:
                times.append(t)
        if times:
            times.sort()
            median_time = times[len(times) // 2]
        else:
            median_time = None

        # ---- 各维度得分（满分100）----
        # 广度B：5只涨停即满分
        B = min(cnt / 5, 1.0) * 100
        # 高度H：6板即满分
        H = min(max_board_val / 6, 1.0) * 100
        # 梯队T：3层即满分
        T = min(tier_count / 3, 1.0) * 100
        # 强度I：9:00涨停=100分, 10:00=80分, 11:30=50分, 14:00=20分
        if median_time is not None:
            I = max(0, 100 - (median_time - 540) / 3)
        else:
            I = 0

        scored.append({
            'cnt': cnt, 'name': name, 'codes': codes,
            'pct': pct, 'amount': total_amount,
            'max_board_val': max_board_val, 'tier_count': tier_count,
            'B': B, 'H': H, 'T': T, 'I': I,
            'M_raw': total_amount,
        })

    # ---- 资金维度百分位排名 ----
    m_raws = [s['M_raw'] for s in scored]
    m_sorted = sorted(m_raws)
    m_count = len(m_sorted)
    for s in scored:
        if m_count <= 1 or m_sorted[-1] == 0:
            s['M'] = 0
        else:
            rank = sum(1 for m in m_sorted if m <= s['M_raw'])
            s['M'] = (rank - 1) / (m_count - 1) * 100

    # ---- 持续性维度 ----
    if history_data and history_data.get('history'):
        # 取最近3天历史
        recent = history_data['history'][-3:]
        _cmap = load_concept_map()  # 一次性加载
        for s in scored:
            raw_name = s['name']
            p_score = 0
            for day_entry in recent:
                top10 = day_entry.get('top10', [])
                for rank_idx, (h_name, h_cnt) in enumerate(top10):
                    h_mapped = _cmap.get(h_name, h_name)
                    r_mapped = _cmap.get(raw_name, raw_name)
                    if h_mapped == r_mapped:
                        p_score += (10 - rank_idx)  # TOP1=10, TOP10=1
            s['P'] = min(p_score * 10, 100)
    else:
        for s in scored:
            s['P'] = 0

    # ---- 综合评分 ----
    for s in scored:
        s['score'] = (s['B'] * 0.25 + s['H'] * 0.25 +
                      s['T'] * 0.15 + s['M'] * 0.20 +
                      s['P'] * 0.10 + s['I'] * 0.05)

    # 按评分降序
    scored.sort(key=lambda x: x['score'], reverse=True)

    # ---- 概念名合并 ----
    _cmap = load_concept_map()
    merged = {}
    for s in scored:
        raw_name = s['name']
        cname = _cmap.get(raw_name, raw_name)
        if cname not in merged:
            merged[cname] = {
                'codes': set(s['codes']),
                'cnt': s['cnt'], 'raw_name': raw_name,
                'B': s['B'], 'H': s['H'], 'T': s['T'],
                'M': s['M'], 'P': s['P'], 'I': s['I'],
                'amount': s['amount'],
                'score': s['score'],
            }
        else:
            m = merged[cname]
            m['codes'].update(s['codes'])
            m['cnt'] += s['cnt']
            m['amount'] += s['amount']
            m['B'] = max(m['B'], s['B'])
            m['H'] = max(m['H'], s['H'])
            m['T'] = max(m['T'], s['T'])
            m['M'] = max(m['M'], s['M'])
            m['P'] = max(m['P'], s['P'])
            m['I'] = max(m['I'], s['I'])
            m['score'] = max(m['score'], s['score'])

    # ---- 合并后重新评分（基于合并后的完整股票列表）----
    for cname, m in merged.items():
        codes = list(m['codes'])
        # 重新计算高度/梯队
        max_board_val = 0
        tiers = set()
        for c in codes:
            b = board_data.get(c, 1)
            max_board_val = max(max_board_val, b)
            if b >= 2:
                tiers.add(b)
        H = min(max_board_val / 6, 1.0) * 100
        T = min(len(tiers) / 3, 1.0) * 100
        # 广度用合并后的去重涨停数
        B = min(len(codes) / 5, 1.0) * 100
        m['B'] = B
        m['H'] = H
        m['T'] = T
        # 重算综合分
        m['score'] = (m['B'] * 0.25 + m['H'] * 0.25 +
                      m['T'] * 0.15 + m['M'] * 0.20 +
                      m['P'] * 0.10 + m['I'] * 0.05)

    # ---- 合并后M_score重新百分位排名 + 重算综合分 ----
    m_amounts = sorted([m['amount'] for cname, m in merged.items()])
    m_count = len(m_amounts)
    for cname, m in merged.items():
        if m_count <= 1 or m_amounts[-1] == 0:
            m['M'] = 0
        else:
            rank = sum(1 for a in m_amounts if a <= m['amount'])
            m['M'] = (rank - 1) / (m_count - 1) * 100
        m['score'] = (m['B'] * 0.25 + m['H'] * 0.25 +
                      m['T'] * 0.15 + m['M'] * 0.20 +
                      m['P'] * 0.10 + m['I'] * 0.05)

    # ---- 分级判定 ----
    for cname, m in merged.items():
        sc = m['score']
        if sc >= 70:
            m['grade'] = 'S'
            m['grade_label'] = '主线'
        elif sc >= 50:
            m['grade'] = 'A'
            m['grade_label'] = '次线'
        elif sc >= 30:
            m['grade'] = 'B'
            m['grade_label'] = '观察'
        else:
            m['grade'] = 'C'
            m['grade_label'] = ''

    # 按评分降序输出
    merged_list = sorted(merged.items(), key=lambda x: x[1]['score'], reverse=True)
    result = []
    for cname, m in merged_list:
        item = (cname, len(m['codes']), list(m['codes']), m['raw_name'],
                m.get('pct', 0), m['amount'], round(m['score'], 1), m['grade_label'])
        result.append(item)

    # 日志输出分级信息
    log('  V4评分结果:')
    for cname, m in merged_list[:5]:
        log('    %s | 分:%.1f | 涨停%d | 等级:%s' % (
            cname, m['score'], len(m['codes']),
            m['grade_label']))
    if len([m for _, m in merged_list if m['grade'] == 'C']) == len(merged_list):
        log('  [提示] 今日无强主线概念（全部C级），热点分散')

    return result


# ═══ 数据校验 ═══
def _validate_data(total_up, sb, lb2, lb3, lb4, lb5, sylb,
                   max_board, em, up_stocks, board_days, logger):
    """双环节数据真实性校验。"""
    ok = True
    sb_count, lb_count = len(sb), len(sylb)
    ths_codes = {str(s.get('code', '')).zfill(6) for s in (up_stocks or [])}

    lb_sum = len(lb2) + len(lb3) + len(lb4) + len(lb5)
    if lb_count != lb_sum:
        logger.emit('[校验-R1] 所有连板%d != %d+%d+%d+%d=%d' % (
            lb_count, len(lb2), len(lb3), len(lb4), len(lb5), lb_sum), level='CHECK')
        ok = False

    if total_up < sb_count + lb_count:
        logger.emit('[校验-R2] 涨停总数%d < 首板%d + 连板%d=%d' % (
            total_up, sb_count, lb_count, sb_count + lb_count), level='CHECK')
        ok = False

    implied_max = 0
    if lb5: implied_max = max(implied_max, 5)
    if lb4: implied_max = max(implied_max, 4)
    if lb3: implied_max = max(implied_max, 3)
    if lb2: implied_max = max(implied_max, 2)
    if max_board < implied_max:
        logger.emit('[校验-R3] 最高板%d < 分类隐含最高%d' % (max_board, implied_max), level='CHECK')
        ok = False

    em_zt = em.get('zting', []) if em else []
    if len(em_zt) > total_up:
        logger.emit('[校验-R4] 东财封涨停%d > 同花顺涨停%d' % (len(em_zt), total_up), level='CHECK')
        ok = False

    if ths_codes and em_zt:
        em_zt_set = set(em_zt)
        overlap = ths_codes & em_zt_set
        coverage = len(overlap) / max(len(ths_codes), 1) * 100
        if coverage < 10:
            logger.emit('[校验-R5] 数据源严重偏离：东财覆盖率仅%.0f%%（%d/%d）' % (
                coverage, len(overlap), len(ths_codes)), level='CHECK')
            ok = False
        elif coverage < 30:
            logger.emit('[校验-R5] 东财覆盖率较低：%.0f%%（%d/%d）' % (
                coverage, len(overlap), len(ths_codes)), level='WARN')

    total_em = len(em_zt) + len(em.get('dting', [])) + len(em.get('zhaban', [])) + len(em.get('cengdting', []))
    if total_em == 0:
        logger.emit('[校验-R6] 东财全接口无数据返回', level='WARN')

    # ST股校验
    st_in_up = [c for c in ths_codes if any(
        s.get('name', '').startswith(('ST', '*ST'))
        for s in (up_stocks or []) if str(s.get('code', '')).zfill(6) == c
    )]
    st_in_sb = [c for c in sb if any(
        s.get('name', '').startswith(('ST', '*ST'))
        for s in (up_stocks or []) if str(s.get('code', '')).zfill(6) == c
    )]
    if st_in_up and not st_in_sb:
        logger.emit('[校验-R7] ST股在涨停池但不在首板: %s' % ','.join(st_in_up[:5]), level='CHECK')
        ok = False

    if ok:
        overlaps = len(ths_codes & set(em_zt)) if ths_codes and em_zt else 0
        cov = overlaps / max(total_up, 1) * 100 if total_up else 0
        st_msg = ' ST:%d只' % len(st_in_up) if st_in_up else ''
        logger.emit('[校验] 全部通过 | 同花顺%d只 东财封涨停%d只 覆盖率%.0f%%%s' % (
            total_up, len(em_zt), cov, st_msg))
    else:
        logger.emit('[校验] 存在异常，请核查数据', level='CHECK')


def _stale_recovery(blocknew_dir, date_str):
    """API 失败时保留旧板块数据。"""
    stale = {'stale': True, 'date': date_str,
             'time': datetime.now().strftime('%H:%M:%S'),
             'reason': 'API 无数据返回'}
    save_json(DATA_DIR / 'latest.json', stale)


# ═══ 板块安装 ═══
def _get_sector_name(fn, static_name, concept_names=None, counts=None):
    """获取板块显示名（概念板块动态命名，非概念板块加当日涨停数）。"""
    if not concept_names:
        return static_name
    if fn == 'RDGN' and len(concept_names) >= 1:
        return concept_names[0]
    if fn == 'HYRD' and len(concept_names) >= 2:
        return concept_names[1]
    if fn == 'RDGN3' and len(concept_names) >= 3:
        return concept_names[2]
    if fn == 'ZFB2' and len(concept_names) >= 4:
        name = concept_names[3]
        if counts and fn in counts:
            name = name + str(counts[fn])
        return name
    # 非概念板块：追加当日涨停数（LHBQ20/R20D/R60D除外）
    if counts and fn in counts and fn not in ('LHBQ20', 'R20D', 'R60D'):
        return static_name + str(counts[fn])
    return static_name


def install_blocks(config=None, callback=None, concept_names=None, counts=None):
    """安装板块到通达信（V2：15个板块）。"""
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

    log('--- 安装通达信复盘板块(V2: %d个) ---' % len(SECTORS))

    # 写入 blocknew.cfg
    cfg_path = os.path.join(blocknew_dir, 'blocknew.cfg')
    buf = bytearray()
    for fn, nm, _ in SECTORS:
        dn = _get_sector_name(fn, nm, concept_names, counts)
        rec = bytearray(120)
        gbk = dn.encode('gbk')
        rec[0:len(gbk)] = gbk
        rec[48:50] = b'\x00\x00'
        ascii_fn = fn.encode('ascii')
        rec[50:50 + len(ascii_fn)] = ascii_fn
        buf.extend(rec)
    buf.extend(b'\x00' * 48)
    # 原子写入：先写临时文件，再 os.replace 替换（Windows 上为原子操作）
    cfg_tmp = cfg_path + '.tmp'
    with open(cfg_tmp, 'wb') as f:
        f.write(buf)
    os.replace(cfg_tmp, cfg_path)
    log('  blocknew.cfg: %d个板块 (%d bytes)' % (len(SECTORS), len(buf)))

    # 写入 blocknew.clr（原子写入）
    clr_path = os.path.join(blocknew_dir, 'blocknew.clr')
    clr_buf = bytearray()
    for fn, nm, _ in SECTORS:
        dn = _get_sector_name(fn, nm, concept_names, counts)
        rec = bytearray(320)
        gbk = dn.encode('gbk')
        rec[0:len(gbk)] = gbk
        clr_buf.extend(rec)
    clr_tmp = clr_path + '.tmp'
    with open(clr_tmp, 'wb') as f:
        f.write(clr_buf)
    os.replace(clr_tmp, clr_path)
    log('  blocknew.clr: %d个板块 (%d bytes)' % (len(SECTORS), len(clr_buf)))

    # 同步更新 LastSync
    lastsync_dir = os.path.join(blocknew_dir, 'LastSync')
    if os.path.isdir(lastsync_dir):
        ls_cfg = os.path.join(lastsync_dir, 'blocknew.cfg')
        ls_clr = os.path.join(lastsync_dir, 'blocknew.clr')
        with open(ls_cfg + '.tmp', 'wb') as f:
            f.write(buf)
        os.replace(ls_cfg + '.tmp', ls_cfg)
        with open(ls_clr + '.tmp', 'wb') as f:
            f.write(clr_buf)
        os.replace(ls_clr + '.tmp', ls_clr)
        for fn, _, _ in SECTORS:
            src = os.path.join(blocknew_dir, fn + '.blk')
            dst = os.path.join(lastsync_dir, fn + '.blk')
            if os.path.exists(src):
                shutil.copy2(src, dst)
        log('  LastSync 已同步')

    # 写入 gridtab.dat
    new_data = bytearray()
    sector_rids = {rid for _, _, rid in SECTORS}
    sector_rids.add(0x9025)  # 旧版昨涨停RID（历史残留）
    sector_static_names = {nm for _, nm, _ in SECTORS}
    if os.path.exists(gridtab_path):
        with open(gridtab_path, 'rb') as f:
            base = f.read()
        for i in range(len(base) // 38):
            rec = base[i * 38:(i + 1) * 38]
            rid = struct.unpack('<H', rec[18:20])[0]
            name = rec[1:14].rstrip(b'\x00').decode('gbk', errors='replace').strip()
            # 同时按RID和名称前缀匹配（处理"昨涨停72" vs "昨涨停"）
            is_ours = (rid in sector_rids or
                       any(name.startswith(sn) for sn in sector_static_names))
            if is_ours:
                continue
            new_data.extend(rec)

    for fn, nm, rid in SECTORS:
        dn = _get_sector_name(fn, nm, concept_names, counts)
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

    # 安装看盘界面 .sp 文件
    _install_sp(tdx_dir, blocknew_dir)

    log('--- 安装完成，重启通达信查看板块 ---')

    return logger.logs[_logs_before:]


# ═══ 看盘界面(.sp)安装与更新 ═══

SP_FILENAME = 'DBKPJM.sp'

# .sp文件中热点板块的STEP编号与BlockFlag映射
# STEP5=热点1(BlockFlag=11), STEP13=热点2(BlockFlag=12), STEP11=热点3(BlockFlag=13)
SP_HOT_STEPS = {
    5: 0,    # concept_names[0] -> RDGN
    13: 1,   # concept_names[1] -> HYRD
    11: 2,   # concept_names[2] -> RDGN3
}


def _install_sp(tdx_dir, blocknew_dir):
    """安装看盘界面.sp文件到T0002/pad目录（用户自定义版面）。"""
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), SP_FILENAME)
    if not os.path.exists(src):
        log('  [跳过] 未找到看盘界面模板 %s' % SP_FILENAME)
        return

    pad_dir = os.path.join(tdx_dir, 'T0002', 'pad')
    os.makedirs(pad_dir, exist_ok=True)
    dst = os.path.join(pad_dir, SP_FILENAME)
    shutil.copy2(src, dst)
    log('  看盘界面已安装: %s' % dst)


def _update_sp(tdx_dir, concept_names):
    """更新看盘界面中热点板块的UserBlockName。"""
    if not concept_names:
        return

    pad_dir = os.path.join(tdx_dir, 'T0002', 'pad')
    sp_path = os.path.join(pad_dir, SP_FILENAME)
    if not os.path.exists(sp_path):
        return

    try:
        # 读取.sp文件（GBK编码）
        with open(sp_path, 'r', encoding='gbk', errors='replace') as f:
            content = f.read()

        # 逐STEP替换UserBlockName
        import re
        updated = 0
        for step_num, name_idx in SP_HOT_STEPS.items():
            if name_idx >= len(concept_names):
                continue
            new_name = concept_names[name_idx]
            # 匹配 [STEPn] 后的 UserBlockName=xxx
            pattern = r'(\[STEP%d\].*?UserBlockName=)([^\r\n]+)' % step_num
            match = re.search(pattern, content, re.DOTALL)
            if match:
                old_val = match.group(2)
                content = content[:match.start(2)] + new_name + content[match.end(2):]
                log('  .sp STEP%d: %s -> %s' % (step_num, old_val, new_name))
                updated += 1

        if updated:
            with open(sp_path, 'w', encoding='gbk', errors='replace') as f:
                f.write(content)
            log('  看盘界面热点名称已更新(%d处)' % updated)
    except (OSError, IOError) as e:
        log('  .sp看盘界面更新失败(通达信可能正在使用): %s' % str(e), level='WARN')
