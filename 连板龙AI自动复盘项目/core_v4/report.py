#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日复盘报告 — HTML 报告生成"""
import os, sys, json, time
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from utils import http_get, load_json, save_json, DATA_DIR

REPORT_DIR = BASE_DIR / 'reports'
HISTORY_DIR = DATA_DIR / 'concept_history'
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(HISTORY_DIR, exist_ok=True)

from engine import fetch_limit_up, fetch_eastmoney, calc_board_days

THS = {'Referer': 'https://www.10jqka.com.cn/'}


def get_concepts():
    url = 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=200&po=1&np=1&fltt=2&invt=2&fs=m:90+t:2&fields=f12,f14,f3,f20'
    status, body = http_get(url)
    if status != 200:
        return []
    try:
        r = []
        for i in json.loads(body).get('data', {}).get('diff', []):
            if isinstance(i, dict):
                p = i.get('f3', 0)
                r.append({
                    'code': i.get('f12', ''), 'name': i.get('f14', ''),
                    'pct': p / 100 if abs(p) > 10 else p,
                    'amount': i.get('f20', 0)
                })
        return r
    except:
        return []


def get_members(bk):
    url = 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=300&po=1&np=1&fltt=2&invt=2&fs=b:%s&fields=f12' % bk
    status, body = http_get(url, timeout=10)
    if status != 200:
        return []
    try:
        return [str(i.get('f12', '')).zfill(6) for i in json.loads(body).get('data', {}).get('diff', []) if isinstance(i, dict)]
    except:
        return []


def collect_news():
    r = {}
    status, body = http_get('https://finance.sina.com.cn/')
    if status == 200:
        import re
        kw = re.findall(r'[一-龥]{2,10}(?:概念|板块|大涨)', body)
        r['sina'] = list(set(kw))[:15]
    url = 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1&fltt=2&invt=2&fs=m:90+t:3&fields=f12,f14,f3'
    status2, body2 = http_get(url)
    if status2 == 200:
        try:
            r['top_concepts'] = [i.get('f14', '') for i in json.loads(body2).get('data', {}).get('diff', []) if isinstance(i, dict)]
        except:
            pass
    return r


def save_concept_history(date_str, concept_data):
    fp = HISTORY_DIR / ('%s.json' % date_str)
    save_json(fp, {'date': date_str, 'concepts': concept_data})


def load_concept_history(days=10):
    files = sorted(HISTORY_DIR.glob('*.json'), reverse=True)[:days]
    result = []
    for fp in files:
        try:
            result.append(load_json(fp))
        except:
            pass
    return result[::-1]


def generate_html(date_str, analyzed, all_zt_codes, lianban, shouban_concepts, concept_history, news_data):
    date_display = '%s-%s-%s' % (date_str[:4], date_str[4:6], date_str[6:8])
    zt_count = len(all_zt_codes)
    lb_count = len(lianban)
    now_str = datetime.now().strftime('%H:%M:%S')

    # 生命周期
    life_cycle = '无数据'; life_detail = ''
    if analyzed:
        t = analyzed[0]
        ck = sum([t['zt_count'] >= 5, t['max_board'] >= 4, len(t['tiers']) >= 2])
        if ck >= 3:
            life_cycle = '爆发期'; life_detail = '主升阶段，梯队完整'
        elif ck >= 2:
            life_cycle = '发酵期'; life_detail = '资金开始关注'
        else:
            life_cycle = '分歧/退潮期'; life_detail = '热度下降'

    top_html = ''
    if analyzed:
        t = analyzed[0]
        tr = ''
        for b in sorted(t['tiers'].keys(), reverse=True):
            tr += ('<div style="margin:4px 0 0 20px;">'
                   '<span style="display:inline-block;width:60px;background:#e94560;color:#fff;text-align:center;border-radius:4px;font-size:13px;font-weight:bold;">%d板</span> %s</div>') % (b, ' '.join(t['tiers'][b]))
        if t['shouban_count'] > 0:
            tr += '<div style="margin:4px 0 0 20px;"><span style="display:inline-block;width:60px;background:#666;color:#fff;text-align:center;border-radius:4px;font-size:13px;">首板</span> %d只</div>' % t['shouban_count']
        amt = '%.0f亿' % (t['sector_amt'] / 1e8) if t['sector_amt'] else '未知'
        top_html = (
            '<div style="background:#16213e;border-radius:12px;padding:20px;margin:16px 0;">'
            '<h3 style="color:#e94560;">🔥 热度第1概念：%s</h3>'
            '<p style="font-size:28px;font-weight:bold;color:#f85149;">%d只涨停</p>'
            '<p>最高板：<span style="color:#e3b341;font-size:20px;">%d板</span>（%s） | 板块成交：%s</p>'
            '<h4 style="color:#c9d1d9;">梯队</h4>%s</div>') % (t['name'], t['zt_count'], t['max_board'], t['max_board_stock'], amt, tr)

    sr = ''
    if analyzed:
        t = analyzed[0]
        for lb, vl, sd, ok in [
            ('涨停数', str(t['zt_count']) + '只', '>=5只', t['zt_count'] >= 5),
            ('龙头', str(t['max_board']) + '板', '>=4板', t['max_board'] >= 4),
            ('梯队', str(len(t['tiers'])) + '层', '>=2层', len(t['tiers']) >= 2)
        ]:
            sr += '<tr><td>%s</td><td style="text-align:center;">%s</td><td style="text-align:center;color:#8b949e;">%s</td><td style="text-align:center;">%s</td></tr>' % (lb, vl, sd, 'Y' if ok else 'N')

    life_html = (
        '<div style="background:#1a1a2e;border-radius:12px;padding:20px;margin:16px 0;">'
        '<h3 style="color:#e3b341;">📈 生命周期</h3>'
        '<div style="font-size:24px;font-weight:bold;text-align:center;">%s</div>'
        '<p style="text-align:center;color:#8b949e;">%s</p>'
        '<table style="width:100%%;"><tr><th>维度</th><th>值</th><th>标准</th><th>达标</th></tr>%s</table></div>') % (life_cycle, life_detail, sr)

    fa_html = ''
    if shouban_concepts:
        fa_html = (
            '<div style="background:#1a1a2e;border-radius:12px;padding:20px;margin:16px 0;">'
            '<h3 style="color:#58a6ff;">🌱 首板发酵监测（新热点苗头）</h3>'
            '<table style="width:100%%;font-size:13px;"><tr><th>概念</th><th>首板数</th><th>首板股</th><th>有无高标</th><th>判断</th></tr>')
        for item in shouban_concepts[:8]:
            has_leader = '有' if item.get('has_leader') else '无'
            judge = '可能发酵' if item['count'] >= 3 else '观察' if item['count'] >= 2 else '零星'
            fa_html += '<tr><td>%s</td><td style="text-align:center;color:#f85149;">%d</td><td style="font-size:12px;">%s</td><td style="text-align:center;">%s</td><td>%s</td></tr>' % (item['name'], item['count'], ' '.join(item['names'][:5]), has_leader, judge)
        fa_html += '</table></div>'

    hist_html = ''
    if concept_history:
        hist_html = '<div style="background:#1a1a2e;border-radius:12px;padding:20px;margin:16px 0;"><h3 style="color:#3fb950;">📊 龙头概念涨停趋势</h3>'
        all_concept_names = []
        if analyzed:
            for t in analyzed[:3]:
                all_concept_names.append(t['name'])
        hist_html += '<table style="width:100%%;font-size:12px;"><tr><th>日期</th>'
        for nm in all_concept_names:
            hist_html += '<th>%s</th>' % nm
        hist_html += '</tr>'
        for h in concept_history[-7:]:
            hd = h.get('date', '')
            hist_html += '<tr><td style="color:#8b949e;">%s</td>' % hd[4:] if len(hd) >= 8 else ''
            for nm in all_concept_names:
                val = h.get('concepts', {}).get(nm, {}).get('zt_count', '-')
                hist_html += '<td style="text-align:center;">%s</td>' % str(val)
            hist_html += '</tr>'
        hist_html += '</table></div>'

    multi_html = (
        '<div style="background:#1a1a2e;border-radius:12px;padding:20px;margin:16px 0;">'
        '<h3 style="color:#58a6ff;">🏷️ 多概念轮动</h3>'
        '<table style="width:100%%;font-size:13px;"><tr><th>概念</th><th>涨停</th><th>最高板</th><th>板块涨幅</th></tr>')
    for i, t in enumerate(analyzed[:6]):
        icon = '🌟' if i == 0 else ' '
        pct_c = '#f85149' if t['sector_pct'] > 0 else '#58a6ff'
        pct_s = ('%+.2f' % t['sector_pct']) + '%'
        multi_html += '<tr><td>%s %s</td><td style="text-align:center;color:#f85149;">%d</td><td style="text-align:center;color:#e3b341;">%d板</td><td style="text-align:center;color:%s;">%s</td></tr>' % (icon, t['name'], t['zt_count'], t['max_board'], pct_c, pct_s)
    multi_html += '</table></div>'

    news_html = ''
    if news_data:
        news_html = '<div style="background:#1a1a2e;border-radius:12px;padding:20px;margin:16px 0;"><h3 style="color:#8b949e;">📰 市场解读（多信源）</h3>'
        if 'sina' in news_data and news_data['sina']:
            news_html += '<p style="font-size:12px;color:#8b949e;">新浪财经热点：%s</p>' % ' '.join(news_data['sina'][:10])
        if 'top_concepts' in news_data and news_data['top_concepts']:
            news_html += '<p style="font-size:12px;color:#8b949e;">东方财富热门概念：%s</p>' % ' '.join(news_data['top_concepts'])
        news_html += '</div>'

    css = (
        '*{margin:0;padding:0;box-sizing:border-box;}'
        'body{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#0d1117;color:#c9d1d9;padding:20px;}'
        '.container{max-width:900px;margin:0 auto;}'
        '.header{background:linear-gradient(135deg,#1a1a2e,#16213e,#0f3460);border-radius:16px;padding:30px;text-align:center;margin-bottom:20px;}'
        '.header h1{font-size:28px;color:#e94560;}'
        '.header .date{font-size:16px;color:#8b949e;margin-top:8px;}'
        '.summary{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:16px 0;}'
        '.summary-item{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;text-align:center;}'
        '.summary-value{font-size:24px;font-weight:bold;}'
        '.summary-label{font-size:12px;color:#8b949e;margin-top:4px;}'
        'table{width:100%%;border-collapse:collapse;font-size:13px;}'
        'td,th{padding:6px 8px;border-bottom:1px solid #21262d;}'
        'h3{margin-bottom:12px;}'
    )

    html_parts = [
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        '<title>复盘助手 每日复盘 ' + date_display + '</title><style>' + css + '</style></head><body><div class="container">'
        '<div class="header"><h1>复盘助手 · 每日复盘</h1><div class="date">' + date_display + ' | 生成时间: ' + now_str + '</div></div>'
        '<div class="summary">',
        '<div class="summary-item"><div class="summary-value" style="color:#f85149;">%d</div><div class="summary-label">涨停</div></div>' % zt_count,
        '<div class="summary-item"><div class="summary-value" style="color:#58a6ff;">0</div><div class="summary-label">跌停</div></div>',
        '<div class="summary-item"><div class="summary-value" style="color:#e3b341;">%d</div><div class="summary-label">连板</div></div>' % lb_count,
        '<div class="summary-item"><div class="summary-value" style="color:#3fb950;">0</div><div class="summary-label">龙虎榜</div></div>',
        '<div class="summary-item"><div class="summary-value" style="color:#e94560;">%s</div><div class="summary-label">生命周期</div></div>' % life_cycle,
        '</div>',
        top_html, life_html, fa_html, hist_html, multi_html, news_html,
        '</div></body></html>',
    ]
    html = '\n'.join([str(p) for p in html_parts])
    return html


def main():
    import sys
    date_str = sys.argv[1] if len(sys.argv) > 1 else ''
    latest_fp = DATA_DIR / 'latest.json'
    if latest_fp.exists():
        latest = load_json(latest_fp)
    else:
        latest = {}
    date_str = date_str or latest.get('date', datetime.now().strftime('%Y%m%d'))
    print('生成复盘报告: %s' % date_str)

    up_stocks = fetch_limit_up(date_str)
    if not up_stocks:
        html = generate_html(date_str, [], set(), [], [], [], {})
        fp = REPORT_DIR / ('report_%s.html' % date_str)
        with open(fp, 'w', encoding='utf-8') as f:
            f.write(html)
        os.startfile(str(fp))
        return

    days = calc_board_days(up_stocks, date_str)
    all_codes = {str(s.get('code', '')).zfill(6) for s in up_stocks}
    lianban = [{'code': str(s.get('code', '')).zfill(6), 'name': s.get('name', ''), 'board': days.get(str(s.get('code', '')).zfill(6), 1)}
               for s in up_stocks if days.get(str(s.get('code', '')).zfill(6), 1) >= 2]
    shouban_codes = {str(s.get('code', '')).zfill(6) for s in up_stocks if days.get(str(s.get('code', '')).zfill(6), 1) == 1}

    concepts_info = get_concepts()
    time.sleep(0.3)

    stocks_concept_hits = {}
    shouban_concept_hits = defaultdict(list)

    for ci in concepts_info[:120]:
        members = get_members(ci['code'])
        time.sleep(0.05)
        mset = set(members)
        hits = mset & all_codes
        if hits:
            stocks_concept_hits[ci['name']] = {'stocks': [{'code': c, 'name': ''} for c in hits], 'count': len(hits)}
        sb_hits = mset & shouban_codes
        if sb_hits:
            sb_names = []
            for s in up_stocks:
                c = str(s.get('code', '')).zfill(6)
                if c in sb_hits:
                    sb_names.append(s.get('name', ''))
            shouban_concept_hits[ci['name']] = {'codes': sb_hits, 'names': sb_names, 'count': len(sb_hits)}

    analyzed = []
    for cn, data in stocks_concept_hits.items():
        zc = data['count']
        if zc == 0:
            continue
        sp = 0; sa = 0
        for ci in concepts_info:
            if ci['name'] == cn:
                sp = ci['pct']; sa = ci['amount']; break
        mb = 0; ms = ''
        for s in lianban:
            if s['code'] in {s2['code'] for s2 in data['stocks']} and s['board'] > mb:
                mb = s['board']; ms = s['name']
        tiers = {}
        for s in lianban:
            if s['code'] in {s2['code'] for s2 in data['stocks']}:
                b = s['board']
                if b not in tiers:
                    tiers[b] = []
                tiers[b].append(s['name'])
        analyzed.append({
            'name': cn, 'zt_count': zc, 'max_board': mb, 'max_board_stock': ms,
            'sector_pct': sp, 'sector_amt': sa, 'tiers': tiers,
            'shouban_count': zc - sum(len(v) for v in tiers.values())
        })

    analyzed.sort(key=lambda x: (x['zt_count'], x['max_board']), reverse=True)

    sb_list = sorted([
        {'name': k, 'count': v['count'], 'names': v['names'],
         'has_leader': any(str(s.get('code', '')).zfill(6) in v['codes'] for s in lianban)}
        for k, v in shouban_concept_hits.items() if v['count'] >= 2
    ], key=lambda x: x['count'], reverse=True)

    concept_snapshot = {}
    for t in analyzed[:10]:
        concept_snapshot[t['name']] = {'zt_count': t['zt_count'], 'max_board': t['max_board']}
    save_concept_history(date_str, concept_snapshot)

    concept_history = load_concept_history(10)
    news_data = collect_news()

    html = generate_html(date_str, analyzed, all_codes, lianban, sb_list, concept_history, news_data)
    fp = REPORT_DIR / ('report_%s.html' % date_str)
    with open(fp, 'w', encoding='utf-8') as f:
        f.write(html)
    print('报告已保存: %s' % fp)
    os.startfile(str(fp))


if __name__ == '__main__':
    main()
