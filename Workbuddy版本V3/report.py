#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日复盘报告 V3.0.0 — HTML 报告生成
1. 概念数据从主流程传入，不再独立请求API
2. 增加晋级率/溢价率/炸板率展示
3. 概念来源从t:2改为t:3
"""
import os
import sys
import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from utils import load_json, save_json, DATA_DIR

REPORT_DIR = BASE_DIR / 'reports'
HISTORY_DIR = DATA_DIR / 'concept_history'
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(HISTORY_DIR, exist_ok=True)


def generate_html(latest_data, board_data=None, concept_data=None):
    """生成HTML复盘报告。

    V5变更：概念数据从主流程传入，不再独立请求API。

    Args:
        latest_data: latest.json 数据字典。
        board_data: 连板数据（可选，用于详情展示）。
        concept_data: 概念分析结果（可选，V5从主流程传入）。

    Returns:
        HTML字符串。
    """
    d = latest_data.get('date', datetime.now().strftime('%Y%m%d'))
    date_display = '%s-%s-%s' % (d[:4], d[4:6], d[6:8]) if len(d) >= 8 else d
    now_str = datetime.now().strftime('%H:%M:%S')

    zt_count = latest_data.get('up', 0)
    lb_count = latest_data.get('lb', 0)
    dt_count = latest_data.get('down', 0)
    zhaban_count = latest_data.get('zhaban', 0)
    lhb_count = latest_data.get('lhb', 0)
    max_board = latest_data.get('maxBoard', 0)
    promo_rate = latest_data.get('promoRate', 0)
    premium_rate = latest_data.get('premiumRate', 0)
    zhaban_rate = latest_data.get('zhabanRate', 0)
    concepts = latest_data.get('concepts', [])

    # 生命周期判断
    life_cycle = '无数据'
    life_detail = ''
    if zt_count > 0:
        ck = sum([zt_count >= 5, max_board >= 4, lb_count >= 3])
        if ck >= 3:
            life_cycle = '爆发期'
            life_detail = '主升阶段，梯队完整'
        elif ck >= 2:
            life_cycle = '发酵期'
            life_detail = '资金开始关注'
        else:
            life_cycle = '分歧/退潮期'
            life_detail = '热度下降'

    # 概念展示
    concept_html = ''
    if concepts:
        concept_html = (
            '<div style="background:#16213e;border-radius:12px;padding:20px;margin:16px 0;">'
            '<h3 style="color:#e94560;">🔥 热点概念 (t:3概念板块)</h3>')
        for i, c in enumerate(concepts[:6]):
            icon = '🌟' if i == 0 else '  '
            concept_html += (
                '<div style="margin:8px 0;font-size:16px;">'
                '%s <span style="color:#f85149;font-weight:bold;">%s</span>'
                ' <span style="color:#8b949e;">%d只涨停</span></div>'
            ) % (icon, c.get('name', ''), c.get('count', 0))
        concept_html += '</div>'

    # 情绪指标（V5新增）
    emotion_html = (
        '<div style="background:#1a1a2e;border-radius:12px;padding:20px;margin:16px 0;">'
        '<h3 style="color:#e3b341;">📈 市场情绪指标</h3>'
        '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:16px;">'
        '<div style="text-align:center;">'
        '<div style="font-size:28px;font-weight:bold;color:#ff6b6b;">%.0f%%</div>'
        '<div style="color:#8b949e;margin-top:4px;">晋级率</div>'
        '<div style="font-size:11px;color:#666;">昨日首板→今日2板</div>'
        '</div>'
        '<div style="text-align:center;">'
        '<div style="font-size:28px;font-weight:bold;color:#ffd93d;">%.1f%%</div>'
        '<div style="color:#8b949e;margin-top:4px;">溢价率</div>'
        '<div style="font-size:11px;color:#666;">昨涨停今开盘涨幅</div>'
        '</div>'
        '<div style="text-align:center;">'
        '<div style="font-size:28px;font-weight:bold;color:#6bcb77;">%.0f%%</div>'
        '<div style="color:#8b949e;margin-top:4px;">炸板率</div>'
        '<div style="font-size:11px;color:#666;">今日炸板/(封板+炸板)</div>'
        '</div>'
        '</div></div>'
    ) % (promo_rate, premium_rate, zhaban_rate)

    # 生命周期
    life_html = (
        '<div style="background:#1a1a2e;border-radius:12px;padding:20px;margin:16px 0;">'
        '<h3 style="color:#e3b341;">📈 生命周期</h3>'
        '<div style="font-size:24px;font-weight:bold;text-align:center;">%s</div>'
        '<p style="text-align:center;color:#8b949e;">%s</p>'
        '<table style="width:100%%;"><tr><th>维度</th><th>值</th><th>标准</th><th>达标</th></tr>'
        '<tr><td>涨停数</td><td style="text-align:center;">%d只</td>'
        '<td style="text-align:center;color:#8b949e;">≥5只</td>'
        '<td style="text-align:center;">%s</td></tr>'
        '<tr><td>最高板</td><td style="text-align:center;">%d板</td>'
        '<td style="text-align:center;color:#8b949e;">≥4板</td>'
        '<td style="text-align:center;">%s</td></tr>'
        '<tr><td>连板数</td><td style="text-align:center;">%d只</td>'
        '<td style="text-align:center;color:#8b949e;">≥3只</td>'
        '<td style="text-align:center;">%s</td></tr>'
        '</table></div>'
    ) % (life_cycle, life_detail,
         zt_count, 'Y' if zt_count >= 5 else 'N',
         max_board, 'Y' if max_board >= 4 else 'N',
         lb_count, 'Y' if lb_count >= 3 else 'N')

    # 免责声明
    disclaimer_html = (
        '<div style="background:#1a1a2e;border-radius:12px;padding:16px;margin:16px 0;'
        'font-size:11px;color:#8b949e;">'
        '⚠️ 免责声明：本软件为数据整理工具，仅提供公开市场数据的自动分类与展示功能。'
        '不提供任何投资建议、不推荐任何股票、不预测市场走势。'
        '晋级率、溢价率、炸板率等指标仅为历史数据统计，不构成投资建议。'
        '用户基于本软件做出的任何投资决策，均由用户自行承担风险。'
        '股市有风险，投资需谨慎。</div>'
    )

    css = (
        '*{margin:0;padding:0;box-sizing:border-box;}'
        'body{font-family:-apple-system,"Microsoft YaHei",sans-serif;'
        'background:#0d1117;color:#c9d1d9;padding:20px;}'
        '.container{max-width:900px;margin:0 auto;}'
        '.header{background:linear-gradient(135deg,#1a1a2e,#16213e,#0f3460);'
        'border-radius:16px;padding:30px;text-align:center;margin-bottom:20px;}'
        '.header h1{font-size:28px;color:#e94560;}'
        '.header .date{font-size:16px;color:#8b949e;margin-top:8px;}'
        '.summary{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:16px 0;}'
        '.summary-item{background:#161b22;border:1px solid #30363d;'
        'border-radius:8px;padding:12px;text-align:center;}'
        '.summary-value{font-size:24px;font-weight:bold;}'
        '.summary-label{font-size:12px;color:#8b949e;margin-top:4px;}'
        'table{width:100%%;border-collapse:collapse;font-size:13px;}'
        'td,th{padding:6px 8px;border-bottom:1px solid #21262d;}'
        'h3{margin-bottom:12px;}'
    )

    html_parts = [
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">',
        '<title>复盘助手 V3 每日复盘 ' + date_display + '</title>',
        '<style>' + css + '</style></head><body><div class="container">',
        '<div class="header"><h1>复盘助手 V3 · 每日复盘</h1>',
        '<div class="date">' + date_display + ' | 生成时间: ' + now_str + '</div></div>',
        '<div class="summary">',
        '<div class="summary-item"><div class="summary-value" style="color:#f85149;">%d</div>'
        '<div class="summary-label">涨停</div></div>' % zt_count,
        '<div class="summary-item"><div class="summary-value" style="color:#58a6ff;">%d</div>'
        '<div class="summary-label">跌停</div></div>' % dt_count,
        '<div class="summary-item"><div class="summary-value" style="color:#e3b341;">%d</div>'
        '<div class="summary-label">连板</div></div>' % lb_count,
        '<div class="summary-item"><div class="summary-value" style="color:#3fb950;">%d</div>'
        '<div class="summary-label">龙虎榜</div></div>' % lhb_count,
        '<div class="summary-item"><div class="summary-value" style="color:#e94560;">%s</div>'
        '<div class="summary-label">生命周期</div></div>' % life_cycle,
        '</div>',
        emotion_html,
        concept_html,
        life_html,
        disclaimer_html,
        '</div></body></html>',
    ]
    html = '\n'.join(html_parts)
    return html


def save_concept_history(date_str, concept_data):
    """保存概念历史快照。

    Args:
        date_str: 日期字符串。
        concept_data: 概念数据字典。
    """
    fp = HISTORY_DIR / ('%s.json' % date_str)
    save_json(fp, {'date': date_str, 'concepts': concept_data})


def load_concept_history(days=10):
    """加载最近N天的概念历史。

    Args:
        days: 加载天数。

    Returns:
        历史数据列表。
    """
    files = sorted(HISTORY_DIR.glob('*.json'), reverse=True)[:days]
    result = []
    for fp in files:
        try:
            result.append(load_json(fp))
        except (json.JSONDecodeError, OSError):
            pass
    return result[::-1]


def main():
    """报告生成入口。"""
    date_str = sys.argv[1] if len(sys.argv) > 1 else ''
    latest_fp = DATA_DIR / 'latest.json'
    if latest_fp.exists():
        latest = load_json(latest_fp)
    else:
        latest = {}
    date_str = date_str or latest.get('date', datetime.now().strftime('%Y%m%d'))
    print('生成复盘报告: %s' % date_str)

    # V5：概念数据从主流程传入（latest.json），不再独立请求API
    html = generate_html(latest)
    fp = REPORT_DIR / ('report_%s.html' % date_str)
    with open(fp, 'w', encoding='utf-8') as f:
        f.write(html)
    print('报告已保存: %s' % fp)

    # Windows下自动打开
    try:
        os.startfile(str(fp))
    except (OSError, AttributeError):
        pass


if __name__ == '__main__':
    main()
