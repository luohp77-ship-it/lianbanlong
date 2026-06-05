#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""概念分析：行业排名、映射表查询、板块生成、TOP3 互斥展示"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from utils import http_get, load_json, log

STANDALONE_DIR = Path(__file__).parent.resolve() / 'standalone'

# ═══ 加载概念映射表 ═══
def load_concept_map():
    fp = STANDALONE_DIR / 'concept_map.json'
    data = load_json(fp)
    return data if data else {}

# ═══ 获取所有行业板块 ═══
def _fetch_all_sectors():
    """获取东方财富所有行业板块 (t=2)，含涨跌幅和成交额"""
    url = 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=200&po=1&np=1&fltt=2&invt=2&fs=m:90+t:2&fields=f12,f14,f3,f20'
    data = http_get(url)
    if data[0] != 200:
        return []
    try:
        items = json.loads(data[1]).get('data', {}).get('diff', [])
        result = []
        for i in items:
            if isinstance(i, dict):
                pct = i.get('f3', 0)
                # 东方财富返回的涨跌幅可能已经是百分比或需除以100
                pct = pct / 100 if abs(pct) > 10 else pct
                result.append((
                    i.get('f12', ''),          # 板块代码
                    i.get('f14', ''),          # 板块名
                    pct,                       # 涨跌幅(%)
                    i.get('f20', 0) or 0       # 成交额
                ))
        return result
    except:
        return []

# ═══ 获取板块成分股 ═══
def _fetch_sector_members(bk_code):
    """获取某板块的成分股代码列表"""
    url = 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=300&po=1&np=1&fltt=2&invt=2&fs=b:%s&fields=f12' % bk_code
    data = http_get(url, timeout=10)
    if data[0] != 200:
        return []
    try:
        items = json.loads(data[1]).get('data', {}).get('diff', [])
        return [str(i.get('f12', '')).zfill(6) for i in items if isinstance(i, dict)]
    except:
        return []

# ═══ 概念排名（并行请求） ═══
def rank_concepts(limit_up_codes, max_workers=8):
    """
    全量计算：统计每个概念的涨停数、板块涨跌幅、成交额
    返回 [(涨停数, 行业名, 股票列表, 涨跌幅, 成交额), ...] 降序排列
    """
    if not limit_up_codes:
        return []

    limit_up_set = set(limit_up_codes)
    sectors = _fetch_all_sectors()
    if not sectors:
        return []

    hits = []

    def _check_sector(bk_code, name, pct, amount):
        try:
            members = _fetch_sector_members(bk_code)
            overlap = [c for c in members if c in limit_up_set]
            if overlap:
                return (len(overlap), name, overlap, pct, amount)
        except:
            pass
        return None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_check_sector, bk, nm, p, a): (bk, nm) for bk, nm, p, a in sectors}
        for future in as_completed(futures):
            result = future.result()
            if result:
                hits.append(result)

    hits.sort(key=lambda x: x[0], reverse=True)
    return hits


# ═══ TOP3 互斥展示 ═══
def get_top3_mutual(ranked_concepts):
    """
    排名用全量，展示用互斥：
    主线 = TOP1 全部涨停股
    次线 = TOP2 涨停股 - 主线中的
    潜在 = TOP3 涨停股 - 主线/次线中的
    返回 [(概念名, 涨停数, 股票列表, 行业名, 涨跌幅, 成交额), ...]
    """
    result = []
    used = set()

    for i, item in enumerate(ranked_concepts[:3]):
        cnt, industry_name, codes = item[0], item[1], item[2]
        # 兼容新旧格式：旧版3字段，新版5字段(含pct, amount)
        pct = item[3] if len(item) >= 4 else 0
        amount = item[4] if len(item) >= 5 else 0

        concept_map = load_concept_map()
        concept_name = concept_map.get(industry_name, industry_name)

        if i == 0:
            result.append((concept_name, cnt, codes, industry_name, pct, amount))
            used.update(codes)
        elif i == 1:
            remaining = [c for c in codes if c not in used]
            if remaining:
                result.append((concept_name, len(remaining), remaining, industry_name, pct, amount))
                used.update(remaining)
        else:
            remaining = [c for c in codes if c not in used]
            if remaining:
                result.append((concept_name, len(remaining), remaining, industry_name, pct, amount))

    return result


# ═══ 完整概念分析 ═══
def analyze_concepts(limit_up_codes, callback=None):
    """
    完整概念分析流程：
    1. 全量排名所有行业 → 取 TOP3
    2. 映射为市场公认概念名
    3. 互斥写入板块（同股不同板）
    返回 {
        'ranked': [(cnt, industry, codes), ...],
        'top3': [(concept_name, cnt, codes, industry), ...]
    }
    """
    if callback:
        callback('  概念分析: 获取行业板块...')

    ranked = rank_concepts(limit_up_codes)

    if callback:
        callback('  概念分析: 排名完成, TOP3: %s' % (
            ', '.join('%s(%d只)' % (r[1], r[0]) for r in ranked[:3])))

    top3 = get_top3_mutual(ranked)

    return {
        'ranked': ranked,
        'top3': top3,
    }
