#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘引擎：数据获取 → 连板计算 → 概念分析 → 板块写入"""
import os, json, shutil, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from utils import (
    http_get, log, load_config, save_json, load_json,
    write_blk, backup_file, is_trading_day, get_latest_trading_day,
    clean_old_logs, DATA_DIR
)
from concept_analyzer import analyze_concepts, get_top3_mutual

THS = {'Referer': 'https://www.10jqka.com.cn/'}

# ═══ 板块定义（唯一真源） ═══
SECTORS = [
    ('ZFB',    '昨涨停',     0x9026),
    ('SB',     '首板',       0x9027),
    ('2LB',    '2连板',      0x9028),
    ('3LB',    '3连板',      0x9029),
    ('4LB',    '4连板',      0x902A),
    ('5BYS',   '5板以上',     0x902B),
    ('SYLB',   '所有连板',    0x902C),
    ('LHBQ20', '龙虎榜前20',  0x902E),
    ('DT',     '今跌停',      0x902F),
    ('ZBQ',    '曾涨停',      0x9030),
    ('ZDT',    '曾跌停',      0x9031),
    ('RDGN',   '主线',        0x9032),
    ('HYRD',   '次线',        0x9033),
    ('RDGN3',  '潜在',        0x9034),
]

# ═══ 数据获取 ═══
def fetch_limit_up(date_str):
    """同花顺涨停池"""
    url = ('https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool'
           '?date=%s&type=all&page=1&limit=200' % date_str)
    data = http_get(url, THS)
    if data[0] != 200:
        return []
    try:
        d = json.loads(data[1])
        if d.get('status_code') == 0:
            return d.get('data', {}).get('info', [])
    except:
        pass
    return []

def fetch_lhb(date_str):
    """龙虎榜（ClawHub）"""
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
    except:
        pass
    return []

def fetch_eastmoney(date_str):
    """东方财富 push2ex：封涨停/跌停/曾涨停/曾跌停"""
    url = ('https://push2ex.eastmoney.com/getAllStockChanges?'
           'type=4,8,16,32&ut=7eea3edcaed734bea9cbfc24409ed989'
           '&pageindex=0&pagesize=500&dpt=wzchanges')
    data = http_get(url)
    if data[0] != 200:
        return {'zting': [], 'dting': [], 'zhaban': [], 'cengdting': []}

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

        result = {'zting': [], 'dting': [], 'zhaban': [], 'cengdting': [],
                   'st_zting': []}  # 同花顺API漏掉的ST涨停股（含名称）
        seen_zt, seen_dt = set(), set()

        for code, recs in records.items():
            recs.sort(key=lambda x: x['time'])
            last_type = recs[-1]['type']
            last_fd = recs[-1]['fengdan']
            name = recs[-1]['name']

            if last_type == 4 and last_fd > 0 and code not in seen_zt:
                seen_zt.add(code)
                result['zting'].append(code)
                # 额外收集ST涨停股（带名称，供合并到同花顺数据）
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
    except:
        pass
    return {'zting': [], 'dting': [], 'zhaban': [], 'cengdting': []}


# ═══ 连板天数计算：同花顺历史API（非ST）+ boards缓存（ST） ═══
def _save_today_boards(date_str, boards_dict):
    """保存今日连板数据"""
    fp = DATA_DIR / 'boards' / ('%s.json' % date_str)
    save_json(fp, {
        'date': date_str,
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'boards': boards_dict
    })

def _fetch_ths_day_codes(date_str):
    """获取某日同花顺涨停池中的所有股票代码（不含ST）"""
    url = ('https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool'
           '?date=%s&type=all&page=1&limit=200' % date_str)
    s, b = http_get(url, THS, timeout=10)
    if s != 200:
        return set()
    try:
        import json
        info = json.loads(b).get('data', {}).get('info', [])
        return {str(item.get('code', '')).zfill(6) for item in info}
    except:
        return set()

def calc_board_days(today_stocks, target_date):
    """
    连板计算：同花顺历史API + boards JSON缓存

    非ST股票：直接查同花顺历史涨停池 —— 权威数据源，不计算不推断
    ST股票  ：从boards JSON继承昨日连板数据（首次运行的首板，明日自动延续）

    规则设计：
    - 同花顺API含非ST涨停股，支持查任意历史日期，是最可靠的数据源
    - ST股只能通过boards缓存继承（东财不支持查历史）
    """
    # 收集近7个交易日的历史同花顺涨停池
    dt = datetime.strptime(target_date, '%Y%m%d')
    hist_ths = {}  # {date: {code, ...}}
    for i in range(1, 8):
        prev = dt - timedelta(days=i)
        prev_str = prev.strftime('%Y%m%d')
        codes = _fetch_ths_day_codes(prev_str)
        if len(codes) >= 20:
            hist_ths[prev_str] = codes

    # 加载昨日ST连板缓存
    yesterday_boards = {}
    for i in range(1, 8):
        prev = (dt - timedelta(days=i)).strftime('%Y%m%d')
        fp = DATA_DIR / 'boards' / ('%s.json' % prev)
        data = load_json(fp)
        if data and data.get('boards'):
            yesterday_boards = data['boards']
            break

    result = {}
    for s in today_stocks:
        code = str(s.get('code', '')).zfill(6)
        name = s.get('name', '')
        is_st = name.startswith(('ST', '*ST'))

        if is_st:
            # ST股：从boards缓存继承
            if code in yesterday_boards:
                result[code] = yesterday_boards[code] + 1
            else:
                result[code] = 1
        else:
            # 非ST股：查同花顺历史API
            days = 1
            for d in sorted(hist_ths.keys(), reverse=True):
                if code in hist_ths[d]:
                    days += 1
                else:
                    break
            result[code] = days

    return result


# ═══ 微信推送 ═══
def _push_wechat(config, d, summary):
    key = config.get('wechatKey', '')
    if not config.get('enableWechat') or not key:
        return False
    try:
        title = '复盘助手 %s 收盘复盘' % d
        content = ('涨停%d只 | 首板%d | 连板%d | 最高%d板\n'
                   '跌停%d只 | 曾涨停%d只 | 曾跌停%d只\n'
                   '龙虎榜前20: %d只') % summary
        push_url = ('https://sctapi.ftqq.com/%s.send?title=%s&desp=%s' %
                    (key, urllib.request.quote(title), urllib.request.quote(content)))
        http_get(push_url, timeout=10)
        return True
    except:
        return False


# ═══ 主引擎 ═══
def run_engine(config=None, callback=None):
    """复盘引擎主流程"""
    from utils import _get_logger
    logger = _get_logger()
    logger.set_callback(callback)
    logger.logs = []

    if config is None:
        config = load_config()

    blocknew_dir = config.get('blocknewDir', 'C:/new_tdx/T0002/blocknew')
    tdx_dir = config.get('tdxDir', 'C:/new_tdx')
    os.makedirs(blocknew_dir, exist_ok=True)
    os.makedirs(DATA_DIR / 'boards', exist_ok=True)

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

    log('--- 复盘引擎 目标日期: %s ---' % d)

    # ── 1. 获取涨停数据 ──
    log('  获取涨停(同花顺)...')
    up_stocks = fetch_limit_up(d)
    if not up_stocks:
        log('  [WARN] 无涨停数据，保留旧板块')
        _stale_recovery(blocknew_dir, d)
        return logger.logs
    log('  涨停: %d只' % len(up_stocks))
    if len(up_stocks) < 5:
        log('  [WARN] 涨停数异常偏少(%d只)，可能数据不完整' % len(up_stocks))

    # ── 1b. 获取东财数据（含ST涨停补充+封板/跌停/炸板） ──
    em = fetch_eastmoney(d)
    st_extra = em.get('st_zting', [])
    if st_extra:
        existing_codes = {str(s.get('code', '')).zfill(6) for s in up_stocks}
        added = 0
        for st in st_extra:
            code = str(st.get('code', '')).zfill(6)
            if code not in existing_codes:
                up_stocks.append(st)
                existing_codes.add(code)
                added += 1
        log('  补充ST涨停: %d只, 合并后涨停总数: %d只' % (added, len(up_stocks)))

    # ── 2. 连板天数计算 ──
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
            if n == 2: lb2.append(code)
            elif n == 3: lb3.append(code)
            elif n == 4: lb4.append(code)
            elif n >= 5: lb5.append(code)

    max_board = max(days.values()) if days else 0
    log('  首板:%d  2板:%d  3板:%d  4板:%d  5板+:%d  最高:%d板' % (
        len(sb), len(lb2), len(lb3), len(lb4), len(lb5), max_board))

    # 保存今日连板数据
    _save_today_boards(d, days)

    # ── 3. 龙虎榜 ──
    log('  获取龙虎榜(ClawHub)...')
    lhb = fetch_lhb(d)
    log('  龙虎榜前20: %d只' % (len(lhb) if lhb else 0))

    # ── 4. 东方财富数据（已在1b获取，直接输出） ──
    log('  涨停(东财):%d  跌停:%d  曾涨停:%d  曾跌停:%d' % (
        len(em['zting']), len(em['dting']), len(em['zhaban']), len(em['cengdting'])))

    # ── 5. 概念分析（综合评分重排） ──
    log('  概念分析...')
    limit_up_codes = {str(s.get('code', '')).zfill(6) for s in up_stocks}
    concept_result = analyze_concepts(limit_up_codes, callback=log)
    all_ranked = concept_result.get('ranked', [])
    top3 = _rank_concepts_composite(all_ranked, limit_up_codes, days)

    top1_codes, top2_codes, top3_codes = [], [], []
    concept_names = []
    # 命名规则：gridtab.dat 字段仅 14 字节(GBK)，TDX 界面显示约 6 个汉字
    # 直接取概念名，不加前缀（从左到右的顺序已表达主次关系）
    for i, t in enumerate(top3[:3]):
        concept_name = t[0]
        cnt = t[1]
        codes = t[2]
        # 概念名最多取 6 个汉字
        # 前面加阿拉伯数字显示主次关系，如 "1消费电子"
        name = str(i + 1) + concept_name
        # 确保 GBK 编码 ≤14 字节
        while len(name.encode('gbk')) > 14 and len(name) > 1:
            name = name[:-1]
        concept_names.append(name)

        if i == 0:
            top1_codes = codes
            log('  概念TOP1 [%s]: %d只 (评分:%.1f)' % (name, cnt, t[6] if len(t) >= 7 else cnt))
        elif i == 1:
            top2_codes = codes
            log('  概念TOP2 [%s]: %d只' % (name, cnt))
        else:
            top3_codes = codes
            log('  概念TOP3 [%s]: %d只' % (name, cnt))
    if not top3:
        log('  概念: 无数据')

    # ── 6. 数据真实性校验（双环节） ──
    _validate_data(
        total_up=len(up_stocks),
        sb=sb, lb2=lb2, lb3=lb3, lb4=lb4, lb5=lb5, sylb=sylb,
        max_board=max_board,
        em=em, up_stocks=up_stocks,
        logger=logger
    )

    # ── 6b. 更新通达信配置文件（动态热点概念命名） ──
    # concept_names 已在概念分析环节构建（含主/次/潜在前缀）
    if concept_names:
        log('  动态命名: %s, %s, %s' % (
            concept_names[0] if len(concept_names) >= 1 else '-',
            concept_names[1] if len(concept_names) >= 2 else '-',
            concept_names[2] if len(concept_names) >= 3 else '-'))
    install_blocks(config, concept_names=concept_names)

    # ── 7. 写入板块文件（原子操作） ──
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

    # 同步 .blk 文件到 LastSync（TDX BlockCache=1 时从此目录读取）
    lastsync_dir = os.path.join(blocknew_dir, 'LastSync')
    if os.path.isdir(lastsync_dir):
        for fn, _, _ in SECTORS:
            src = os.path.join(blocknew_dir, fn + '.blk')
            dst = os.path.join(lastsync_dir, fn + '.blk')
            if os.path.exists(src):
                shutil.copy2(src, dst)
        log('  LastSync .blk 同步完成')

    # ── 8. 微信推送 ──
    summary = (len(up_stocks), len(sb), len(sylb), max_board,
               len(em['dting']), len(em['zhaban']), len(em['cengdting']),
               len(lhb) if lhb else 0)
    if _push_wechat(config, d, summary):
        log('  [OK] 微信推送成功')

    # ── 9. 保存最新数据 ──
    latest = {
        'up': len(up_stocks), 'sb': len(sb), 'lb': len(sylb),
        'down': len(em['dting']), 'zhaban': len(em['zhaban']),
        'lhb': len(lhb) if lhb else 0,
        'maxBoard': max_board,
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
        'concepts': [{'name': t[0], 'count': t[1]} for t in top3[:3]] if top3 else [],
    })

    log('--- 完成: %d涨停(%d首板/%d高标) 最高%d板 %d跌停 %d炸板 %d曾跌停 %d龙虎榜 ---' % summary)
    return logger.logs


def _rank_concepts_composite(ranked, limit_up_codes, board_data):
    """
    综合评分重排概念排名（不依赖 get_top3_mutual 的顺序）。

    评分维度与权重：
    - 涨停数 (40%)：涨停股在概念中的聚集度
    - 强度   (35%)：最高板×2 + 梯队数×3，反映概念纵深
    - 资金   (25%)：板块成交额，反映资金真实参与度

    评分公式（归一化后加权求和）：
        score = 涨停分×0.40 + 强度分×0.35 + 资金分×0.25

    Args:
        ranked: analyze_concepts 返回的 ranked 列表
                [(cnt, industry_name, codes, pct, amount), ...]
        limit_up_codes: 今日所有涨停股代码集合
        board_data: {code: board_days} 格式的连板数据

    Returns:
        get_top3_mutual 格式的 top3 列表
        [(概念名, 涨停数, 股票列表, 行业名, 涨跌幅, 成交额, 综合评分), ...]
    """
    if not ranked or not board_data:
        return get_top3_mutual(ranked or [])

    # 解析 ranked 数据（兼容新旧格式）
    parsed = []
    for item in ranked:
        cnt = item[0]
        name = item[1]
        codes = item[2]
        pct = item[3] if len(item) >= 4 else 0
        amount = item[4] if len(item) >= 5 else 0
        parsed.append((cnt, name, codes, pct, amount))

    if not parsed:
        return get_top3_mutual([])

    max_cnt = max(c for c, _, _, _, _ in parsed)
    max_amt = max(a for _, _, _, _, a in parsed) if parsed else 1

    # 为每个概念计算综合评分
    scored = []
    for cnt, name, codes, pct, amount in parsed:
        # 强度：从 board_data 查最高板和梯队数
        max_board = 0
        tiers = set()
        for c in codes:
            b = board_data.get(c, 1)
            max_board = max(max_board, b)
            if b >= 2:
                tiers.add(b)
        tier_count = len(tiers)
        strength = max_board * 2 + tier_count * 3

        # 归一化各维度（0-100）
        zt_norm = cnt / max_cnt * 100 if max_cnt else 0
        strength_norm = min(strength / 30 * 100, 100)  # 30=5板×2+6层梯队×3
        amt_norm = amount / max_amt * 100 if max_amt else 0

        # 综合评分
        score = zt_norm * 0.40 + strength_norm * 0.35 + amt_norm * 0.25
        scored.append((score, cnt, name, codes, pct, amount, max_board, tier_count))

    # 按综合评分降序排列
    scored.sort(key=lambda x: x[0], reverse=True)

    # 按映射后概念名合并（多个东方财富行业可能映射到同一概念，如电力+公用事业→电力改革）
    from concept_analyzer import load_concept_map as _lcm
    _cmap = _lcm()
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

    # 合并后的列表，按评分降序（涨停数以集合实际大小为准）
    deduped = [(len(v['codes']), v['name'], list(v['codes']), v['pct'], v['amount'])
               for v in merged.values()]
    deduped.sort(key=lambda x: merged_score.get(x[1], x[0]), reverse=True)

    # 互斥去重
    top3 = get_top3_mutual(deduped)

    # 评分注入
    result = []
    for t in top3:
        score = merged_score.get(t[0], t[1])  # t[0] = 映射后概念名
        result.append(t + (round(score, 1),))

    return result


def _validate_data(total_up, sb, lb2, lb3, lb4, lb5, sylb, max_board, em, up_stocks, logger):
    """
    双环节数据真实性校验（每次 run_engine 自动执行）

    ── 第一环节：内部一致性 ──
    R1: 所有连板 = 2连板 + 3连板 + 4连板 + 5板以上
    R2: 涨停总数 >= 首板 + 连板（昨封板 = 首板 + 所有连板）
    R3: 最高板数与分类一致（有4板股则最高板 >= 4）

    ── 第二环节：跨数据源交叉验证 ──
    R4: 东方财富封涨停数 <= 同花顺涨停数（东财仅统计封单>0的涨停，口径更严）
    R5: 同花顺涨停股中东财也标记为封涨停的覆盖率
    R6: 东方财富全接口数据完整性
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

    # R5: 同花顺涨停股中东财也标记为封涨停的覆盖率
    # 注意：两数据源口径不同——同花顺含所有收盘涨停股，东财仅含封单>0的封涨停
    # 正常覆盖率通常在 20%-60% 之间，低于 10% 才判定异常
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

    if ok:
        overlaps = len(ths_codes & set(em_zt)) if ths_codes and em_zt else 0
        cov = overlaps / max(total_up, 1) * 100 if total_up else 0
        logger.emit('[校验] 全部通过 | 同花顺%d只 东财封涨停%d只 覆盖率%.0f%%' % (
            total_up, len(em_zt), cov))
    else:
        logger.emit('[校验] 存在异常，请核查数据', level='CHECK')


def _stale_recovery(blocknew_dir, date_str):
    """API 失败时保留旧板块数据，仅标记 stale"""
    stale = {'stale': True, 'date': date_str,
             'time': datetime.now().strftime('%H:%M:%S'),
             'reason': 'API 无数据返回'}
    save_json(DATA_DIR / 'latest.json', stale)


# ═══ 板块安装 ═══
def _get_sector_name(fn, static_name, concept_names=None):
    """获取板块显示名：动态概念名优先，无则用内置名称"""
    if not concept_names:
        return static_name
    if fn == 'RDGN' and len(concept_names) >= 1:
        return concept_names[0]
    if fn == 'HYRD' and len(concept_names) >= 2:
        return concept_names[1]
    if fn == 'RDGN3' and len(concept_names) >= 3:
        return concept_names[2]
    return static_name

def install_blocks(config=None, callback=None, concept_names=None):
    """安装板块到通达信

    Args:
        config: 配置字典
        callback: 日志回调
        concept_names: 当天热点概念名列表，用于动态命名 RDGN/HYRD/RDGN3
    """
    from utils import _get_logger
    logger = _get_logger()
    # 不重置 logger.logs —— install_blocks 可能被 run_engine
    # 内部调用，重置会丢失 run_engine 的日志
    if callback and not logger.callback:
        logger.set_callback(callback)
    _logs_before = len(logger.logs)

    if config is None:
        config = load_config()

    tdx_dir = config.get('tdxDir', 'C:/new_tdx')
    blocknew_dir = os.path.join(tdx_dir, 'T0002', 'blocknew')
    os.makedirs(blocknew_dir, exist_ok=True)

    gridtab_path = os.path.join(tdx_dir, 'T0002', 'gridtab.dat')
    if os.path.exists(gridtab_path):
        backup_file(gridtab_path)
        log('  已备份 gridtab.dat')

    config['blocknewDir'] = blocknew_dir
    from utils import save_config as _save_cfg
    _save_cfg(config)

    log('--- 安装通达信复盘板块 ---')

    # 写入 blocknew.cfg（通达信二进制格式：120字节/条）
    #   字节 0-47:  板块名称 (GBK, 补零)
    #   字节 48-49: \0\0 (固定标记)
    #   字节 50-?:  文件名 (ASCII, 补零到120字节)
    cfg_path = os.path.join(blocknew_dir, 'blocknew.cfg')
    buf = bytearray()
    for fn, nm, _ in SECTORS:
        dn = _get_sector_name(fn, nm, concept_names)
        rec = bytearray(120)
        gbk = dn.encode('gbk')
        rec[0:len(gbk)] = gbk
        rec[48:50] = b'\x00\x00'          # 2字节标记
        ascii_fn = fn.encode('ascii')
        rec[50:50+len(ascii_fn)] = ascii_fn
        buf.extend(rec)
    buf.extend(b'\x00' * 48)               # 终结块（48字节全零）
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
        # 颜色全部归零，TDX 使用默认白色显示
        clr_buf.extend(rec)
    with open(clr_path, 'wb') as f:
        f.write(clr_buf)
    log('  blocknew.clr: %d个板块 (%d bytes)' % (len(SECTORS), len(clr_buf)))

    # 同步更新 LastSync（含 .blk 文件，TDX BlockCache=1 时从此目录读取）
    lastsync_dir = os.path.join(blocknew_dir, 'LastSync')
    if os.path.isdir(lastsync_dir):
        with open(os.path.join(lastsync_dir, 'blocknew.cfg'), 'wb') as f:
            f.write(buf)
        with open(os.path.join(lastsync_dir, 'blocknew.clr'), 'wb') as f:
            f.write(clr_buf)
        # 同步已有的 .blk 文件到 LastSync
        for fn, _, _ in SECTORS:
            src = os.path.join(blocknew_dir, fn + '.blk')
            dst = os.path.join(lastsync_dir, fn + '.blk')
            if os.path.exists(src):
                shutil.copy2(src, dst)
        log('  LastSync 已同步')

    # 写入 gridtab.dat（全量替换板块条目，保留非板块旧标签）
    import struct
    new_data = bytearray()
    # 按 RID 范围过滤为主（0x9026-0x9035 是我们的专用 RID 段）
    # 辅以静态名称过滤，清理早期版本残留的 RID=0x91B4 旧条目
    sector_rids = set(range(0x9026, 0x9035 + 1))
    sector_static_names = {nm for _, nm, _ in SECTORS}
    if os.path.exists(gridtab_path):
        with open(gridtab_path, 'rb') as f:
            base = f.read()
        for i in range(len(base) // 38):
            rec = base[i*38:(i+1)*38]
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
        rec[1:1+len(gbk)] = gbk
        rec[14:18] = b'\x11\x01\x00\x00'
        rec[18:20] = struct.pack('<H', rid)
        rec[30:34] = b'\xff\xff\xff\xff'
        new_data.extend(rec)

    with open(gridtab_path, 'wb') as f:
        f.write(bytes(new_data))
    log('  gridtab.dat: %d个标签' % (len(new_data) // 38))

    # 创建空 .blk 文件（0字节，避免通达信误解换行符）
    for fn, _, _ in SECTORS:
        blk_path = os.path.join(blocknew_dir, fn + '.blk')
        if not os.path.exists(blk_path):
            with open(blk_path, 'wb') as f:
                pass  # 创建0字节文件
    log('  %d个板块文件已就绪' % len(SECTORS))
    log('--- 安装完成，重启通达信查看板块 ---')

    return logger.logs[_logs_before:]
