#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""概念分析：概念板块排名(t:3)、映射表查询、综合评分、允许重叠展示"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from utils import http_get, load_json, log

STANDALONE_DIR = Path(__file__).parent.resolve() / 'standalone'


# ═══ 加载概念映射表 ═══
def load_concept_map():
    """加载概念映射表（V5：主要用于超长概念名截断/简化）。

    Returns:
        概念映射字典 {原始名: 简化名}。
    """
    fp = STANDALONE_DIR / 'concept_map.json'
    data = load_json(fp)
    return data if data else {}


# ═══ 获取所有概念板块（V5：t:3概念板块，~300个）═══
def _fetch_all_concepts():
    """获取东方财富所有概念板块 (t=3)，含涨跌幅和成交额。

    V5变更：从 t:2（行业板块~90个）改为 t:3（概念板块~300个）。

    Returns:
        列表 [(板块代码, 板块名, 涨跌幅%, 成交额), ...]。
    """
    url = ('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=500&po=1&np=1'
           '&fltt=2&invt=2&fs=m:90+t:3&fields=f12,f14,f3,f20')
    data = http_get(url)
    if data[0] != 200:
        return []
    try:
        items = json.loads(data[1]).get('data', {}).get('diff', [])
        result = []
        for i in items:
            if isinstance(i, dict):
                pct = i.get('f3', 0)
                pct = pct / 100 if abs(pct) > 10 else pct
                result.append((
                    i.get('f12', ''),          # 板块代码
                    i.get('f14', ''),          # 板块名
                    pct,                       # 涨跌幅(%)
                    i.get('f20', 0) or 0       # 成交额
                ))
        return result
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


# ═══ 获取板块成分股 ═══
def _fetch_concept_members(bk_code):
    """获取某概念板块的成分股代码列表。

    Args:
        bk_code: 板块代码。

    Returns:
        6位股票代码列表。
    """
    url = ('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=300&po=1&np=1'
           '&fltt=2&invt=2&fs=b:%s&fields=f12' % bk_code)
    data = http_get(url, timeout=10)
    if data[0] != 200:
        return []
    try:
        items = json.loads(data[1]).get('data', {}).get('diff', [])
        return [str(i.get('f12', '')).zfill(6) for i in items if isinstance(i, dict)]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


# ═══ 概念排名（并行请求）═══
def rank_concepts(limit_up_codes, max_workers=8):
    """全量计算：统计每个概念板块的涨停数、涨跌幅、成交额。

    V5变更：使用 t:3 概念板块（~300个），并发数8。

    Args:
        limit_up_codes: 今日涨停股代码集合。
        max_workers: 并发线程数。

    Returns:
        [(涨停数, 概念名, 股票列表, 涨跌幅, 成交额), ...] 降序排列。
    """
    if not limit_up_codes:
        return []

    limit_up_set = set(limit_up_codes)
    sectors = _fetch_all_concepts()
    if not sectors:
        return []

    hits = []

    def _check_concept(bk_code, name, pct, amount):
        """检查一个概念板块与涨停股的重叠。"""
        try:
            members = _fetch_concept_members(bk_code)
            overlap = [c for c in members if c in limit_up_set]
            if overlap:
                return (len(overlap), name, overlap, pct, amount)
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_check_concept, bk, nm, p, a): (bk, nm)
            for bk, nm, p, a in sectors
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                hits.append(result)

    hits.sort(key=lambda x: x[0], reverse=True)
    return hits


# ═══ V5新增：允许重叠的TOP3展示 ═══
def get_top3_with_overlap(ranked_concepts):
    """V5概念展示：允许重叠，同一股票可出现在多个概念板块。

    V4是互斥去重（次线/潜在板块寒酸），V5允许重叠展示全量。

    Args:
        ranked_concepts: rank_concepts 返回的排名列表
            [(涨停数, 概念名, 股票列表, 涨跌幅, 成交额), ...]

    Returns:
        [(概念名, 涨停数, 股票列表, 原概念名, 涨跌幅, 成交额), ...]
        允许重叠，不做去重。
    """
    result = []
    concept_map = load_concept_map()

    for item in ranked_concepts[:3]:
        cnt = item[0]
        industry_name = item[1]
        codes = item[2]
        pct = item[3] if len(item) >= 4 else 0
        amount = item[4] if len(item) >= 5 else 0

        # V5：概念板块名大多可直接用，映射表仅用于超长名截断/简化
        concept_name = concept_map.get(industry_name, industry_name)

        # 允许重叠：直接使用全量股票列表，不做互斥去重
        result.append((concept_name, cnt, codes, industry_name, pct, amount))

    return result


# ═══ 完整概念分析 ═══
def analyze_concepts(limit_up_codes, callback=None):
    """完整概念分析流程。

    V5变更：
    1. 使用 t:3 概念板块
    2. 允许重叠展示（不再互斥去重）

    Args:
        limit_up_codes: 今日涨停股代码集合。
        callback: 日志回调函数。

    Returns:
        {
            'ranked': [(cnt, concept, codes, pct, amount), ...],
            'top3': [(concept_name, cnt, codes, concept, pct, amount), ...]
        }
    """
    if callback:
        callback('  概念分析: 获取概念板块(t:3)...')

    ranked = rank_concepts(limit_up_codes)

    if callback:
        callback('  概念分析: 排名完成, TOP3: %s' % (
            ', '.join('%s(%d只)' % (r[1], r[0]) for r in ranked[:3])))

    # V5：允许重叠
    top3 = get_top3_with_overlap(ranked)

    return {
        'ranked': ranked,
        'top3': top3,
    }
