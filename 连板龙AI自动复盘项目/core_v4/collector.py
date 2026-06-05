#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场信源采集 — 交叉分析热点持续性"""
import sys, os, json, ssl, urllib.request, re
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
SOURCE_DIR = BASE_DIR / 'sources'
os.makedirs(SOURCE_DIR, exist_ok=True)

def http_get(url, headers=None, timeout=15):
    try:
        hdrs = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        if headers: hdrs.update(headers)
        ctx = ssl._create_unverified_context()
        resp = urllib.request.urlopen(urllib.request.Request(url,headers=hdrs), timeout=timeout, context=ctx)
        return resp.status, resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        return 0, str(e)

def collect_news():
    """收集今日市场解读信息"""
    results = {}

    # 1. 新浪财经头条
    status, body = http_get('https://finance.sina.com.cn/')
    if status == 200:
        # 提取今日热点关键词
        keywords = re.findall(r'[一-龥]{2,10}(?:概念|板块|大涨|拉升|走强)', body)
        results['sina'] = list(set(keywords))[:20]

    # 2. 财联社
    status, body = http_get('https://www.cls.cn/telegraph')
    if status == 200:
        headlines = re.findall(r'[一-龥]{4,50}[\。\，]', body)
        results['cls'] = [h.strip() for h in headlines[:15] if len(h.strip()) > 6]

    # 3. 东方财富概念热度
    status, body = http_get('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1&fltt=2&invt=2&fs=m:90+t:3&fields=f12,f14,f3,f104')
    if status == 200:
        try:
            data = json.loads(body)
            concepts = []
            for item in data.get('data',{}).get('diff',[]):
                if isinstance(item, dict):
                    concepts.append(item.get('f14',''))
            results['eastmoney_top'] = concepts[:10]
        except: pass

    return results

def save_daily_snapshot(date_str, concept_data, news_data):
    """保存每日市场快照"""
    snapshot = {
        'date': date_str,
        'time': datetime.now().strftime('%H:%M:%S'),
        'concepts': concept_data,
        'news': news_data,
    }
    fp = SOURCE_DIR / ('snapshot_%s.json' % date_str)
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return fp

if __name__ == '__main__':
    news = collect_news()
    print('=== 今日市场信源采集 ===')
    for src, items in news.items():
        print('\n[%s]' % src)
        for item in items[:8]:
            print('  - %s' % item)
