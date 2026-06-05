#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""概念分析 V2：基于同花顺 reason_type 的概念分组（不再依赖 push2.eastmoney.com）

V2核心变更：
1. 移除 push2.eastmoney.com 依赖（该API不通）
2. 使用同花顺 limit_up_pool 返回的 reason_type 字段做概念分组
3. reason_type 格式："概念1+概念2+概念3"，用"+"分隔
4. 100%覆盖率（同花顺返回的所有涨停股都有 reason_type）
5. 保留 push2 逻辑作为可选（如果API恢复可用）
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

from utils import http_get, load_json, log

STANDALONE_DIR = Path(__file__).parent.resolve() / 'data'


# ═══ 加载概念映射表 ═══
def load_concept_map():
    """加载概念映射表（主要用于超长概念名截断/简化）。"""
    fp = Path(__file__).parent.resolve() / 'standalone' / 'concept_map.json'
    data = load_json(fp)
    return data if data else {}


# ═══ 噪声概念过滤列表（财报事件、个股事件等非持续性题材）═══
FILTER_WORDS = [
    '增长', '预增', '预亏', '预盈', '扭亏',       # 财务
    '季报', '年报', '半年报', '快报', '预告',
    '股权转让', '资产重组', '资产剥离',            # 重组
    '股东增持', '股东减持', '股东变更',
    '中标', '签约', '合同', '订单',                 # 公告
    '摘帽', '更名',                                 # 状态变更
    '回购', '分红', '送转', '派息',                 # 财务操作
    '超跌反弹', '高送转', '填权', '除权',           # 市场描述
    '新股', '次新股', '开板',                       # 次新
    '定增', '增发', '配股', '债转股',               # 融资
    '复牌', '停牌',                                 # 交易状态
]


def _is_noise_concept(concept_name):
    """判断概念是否为噪声概念（财报事件、个股事件等非持续性题材）。"""
    for w in FILTER_WORDS:
        if w in concept_name:
            return True
    return False


# ═══ V4核心：基于 reason_type 的概念分析（含噪声过滤）═══
def analyze_concepts_by_reason(up_stocks, callback=None):
    """基于同花顺 reason_type 的概念分组分析。

    从涨停股的 reason_type 字段提取概念标签，按出现频次排名。
    reason_type 格式："概念1+概念2+概念3"
    V4变更：增加噪声过滤，剔除财报事件/个股事件等非持续性概念。

    Args:
        up_stocks: 涨停股列表，每个元素需含 code/name/reason_type 字段。
        callback: 日志回调函数。

    Returns:
        {
            'ranked': [(cnt, concept, codes, pct, amount), ...],
            'top3': [(concept_name, cnt, codes, concept, pct, amount), ...],
            'code_concepts': {code: [概念名列表]},
            'filtered_count': int,  # 被过滤掉的噪声概念数量
        }
    """
    if callback:
        callback('  概念分析: 基于 reason_type 分组(含噪声过滤)...')

    concept_counter = Counter()
    concept_stocks = defaultdict(list)  # concept -> [code]
    code_concepts = defaultdict(list)   # code -> [concept]
    filtered_count = 0

    for s in up_stocks:
        code = str(s.get('code', '')).zfill(6)
        reason = s.get('reason_type', '') or s.get('reason', '') or ''
        if not reason:
            continue

        # 按 "+" 分割概念
        parts = [p.strip() for p in reason.split('+') if p.strip()]
        for part in parts:
            # V4：噪声过滤
            if _is_noise_concept(part):
                filtered_count += 1
                continue
            concept_counter[part] += 1
            concept_stocks[part].append(code)
            code_concepts[code].append(part)

    # 按出现次数排名
    ranked = []
    for concept, count in concept_counter.most_common():
        codes = concept_stocks[concept]
        # 去重保持顺序
        seen = set()
        unique_codes = []
        for c in codes:
            if c not in seen:
                seen.add(c)
                unique_codes.append(c)
        ranked.append((count, concept, unique_codes, 0, 0))

    if callback:
        if ranked:
            top_names = ', '.join('%s(%d只)' % (r[1], r[0]) for r in ranked[:3])
            callback('  概念分析: 完成, TOP3: %s (过滤%d个噪声标签)' % (top_names, filtered_count))
        else:
            callback('  概念分析: reason_type 无数据', level='WARN')

    return {
        'ranked': ranked,
        'top3': get_top3_with_overlap(ranked),
        'code_concepts': dict(code_concepts),
        'filtered_count': filtered_count,
    }



# ═══ 允许重叠的TOP3展示 ═══
def get_top3_with_overlap(ranked_concepts):
    """概念展示：允许重叠，同一股票可出现在多个概念板块。"""
    result = []
    concept_map = load_concept_map()

    for item in ranked_concepts[:3]:
        cnt = item[0]
        industry_name = item[1]
        codes = item[2]
        pct = item[3] if len(item) >= 4 else 0
        amount = item[4] if len(item) >= 5 else 0

        concept_name = concept_map.get(industry_name, industry_name)
        result.append((concept_name, cnt, codes, industry_name, pct, amount))

    return result


# ═══ 以下为旧版 push2 API（保留但不再默认使用）═══
def _fetch_all_concepts(fs_param='m:90+t:3'):
    """获取东方财富板块列表（push2 API，可能不通）。"""
    url = ('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=500&po=1&np=1'
           '&fltt=2&invt=2&fs=%s&fields=f12,f14,f3,f20' % fs_param)
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
                    i.get('f12', ''),
                    i.get('f14', ''),
                    pct,
                    i.get('f20', 0) or 0
                ))
        return result
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def _fetch_concept_members(bk_code):
    """获取某概念板块的成分股代码列表。"""
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


def rank_concepts(limit_up_codes, max_workers=8, fs_param='m:90+t:3'):
    """push2 概念排名（保留接口，实际可能因API不通返回空）。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not limit_up_codes:
        return []

    limit_up_set = set(limit_up_codes)
    sectors = _fetch_all_concepts(fs_param)
    if not sectors:
        return []

    hits = []

    def _check_concept(bk_code, name, pct, amount):
        try:
            members = _fetch_concept_members(bk_code)
            overlap = [c for c in members if c in limit_up_set]
            if overlap:
                return (len(overlap), name, overlap, pct, amount)
        except Exception as e:
            log('  概念扫描[%s]异常: %s' % (name, e), level='WARN')
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


def analyze_concepts(limit_up_codes, callback=None):
    """push2 概念分析（保留接口，API不通时返回空）。"""
    if callback:
        callback('  概念分析: 获取概念板块(t:3)...')

    ranked = rank_concepts(limit_up_codes, fs_param='m:90+t:3')

    if not ranked and callback:
        callback('  概念分析: t:3无数据，尝试t:2...', level='WARN')
        ranked = rank_concepts(limit_up_codes, fs_param='m:90+t:2')

    if callback:
        if ranked:
            top_names = ', '.join('%s(%d只)' % (r[1], r[0]) for r in ranked[:3])
            callback('  概念分析: 排名完成, TOP3: %s' % top_names)
        else:
            callback('  概念分析: push2 API无数据', level='WARN')

    top3 = get_top3_with_overlap(ranked)

    return {
        'ranked': ranked,
        'top3': top3,
    }


# （load_concept_map 已在上方第22行定义，此处删除重复定义）