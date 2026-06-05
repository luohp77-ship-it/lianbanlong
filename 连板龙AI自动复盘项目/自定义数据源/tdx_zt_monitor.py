#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通达信涨停原因+所属概念 监控脚本 v5.0
=====================================
功能：
  每分钟从同花顺（zzshare）获取涨停股数据，写入两只自定义列：
    - 数据号 1：涨停原因（"AI PC+印制电路板"）
    - 数据号 2：所属概念（"CPO概念 | 光通信模块 | PCB"）
  两个维度互补，帮助判断个股走势逻辑。

数据源：
  - 涨停原因：同花顺 zzshare API（100% 覆盖）
  - 所属概念：东方财富板块成分股缓存（覆盖约 50%）
安装要求：pip install zzshare

使用前准备（仅一次）：
  1. 通达信 -> 功能 -> 公式系统 -> 自定义数据管理器
     - 新建 数据号:1 名称:涨停原因
     - 新建 数据号:2 名称:所属概念
  2. 行情列表表头右键 -> 选择自定义数据 -> 勾选两列
  3. python tdx_zt_monitor.py
"""

import os
import sys
import time
import logging
import json
from datetime import datetime, date
from pathlib import Path

# ============================================================
# 配置区
# ============================================================
TDX_PATH = ""                              # 通达信安装路径，留空自动检测
DATA_ID_REASON = "1"                       # 涨停原因 数据号
DATA_ID_CONCEPT = "2"                      # 所属概念 数据号
UPDATE_INTERVAL = 60                       # 更新间隔（秒）
CACHE_FILE = Path(__file__).parent / "tdx_concept_cache.json"  # 概念缓存
MAX_CONCEPTS = 4                           # 最多展示几个概念
LOG_FILE = Path(__file__).parent / "tdx_zt_monitor.log"

# ============================================================
# 日志
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ============================================================
# 路径检测
# ============================================================
def find_tdx_path():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\TdxTdx")
        path, _ = winreg.QueryValueEx(key, "InstallPath")
        if path and os.path.isdir(os.path.join(path, "T0002")):
            return path
    except Exception:
        pass
    for p in ["C:/new_tdx", "D:/new_tdx", "C:/zd_zhonj", "D:/zd_zhonj"]:
        if os.path.isdir(p) and os.path.isdir(os.path.join(p, "T0002")):
            return p
    return ""


def get_market_prefix(code):
    code = code.zfill(6)
    if code.startswith("6"):
        return "1"
    elif code.startswith(("0", "3")):
        return "0"
    return "3"


def format_reason(short_desc, full_reason):
    if not full_reason:
        return short_desc or ""
    parts = full_reason.split("；")
    brief = parts[0].strip()
    if len(brief) > 40:
        brief = brief[:37] + "..."
    return brief


def set_column_green():
    """
    将通达信自定义数据列（数据号1、2）的字体颜色设为荧光绿
    原理：修改 T0002/diycol.dat 中每条150字节记录的偏移0x1c处的颜色字段
    颜色格式：COLORREF = RGB(0,255,0) = 0x0000FF00
    """
    import struct
    tdx = TDX_PATH or find_tdx_path()
    if not tdx:
        return False

    diycol = os.path.join(tdx, "T0002", "diycol.dat")
    if not os.path.isfile(diycol):
        log.warning(f"diycol.dat 不存在: {diycol}")
        return False

    try:
        with open(diycol, "rb") as f:
            data = bytearray(f.read())

        RECORD_SIZE = 150
        COLOR_OFFSET = 0x1c
        GREEN = 0x0000FF00  # RGB(0,255,0)

        n = 0
        for i in range(len(data) // RECORD_SIZE):
            offset = i * RECORD_SIZE
            d_id = struct.unpack_from("<I", data, offset + 4)[0]
            if d_id in (1, 2):
                struct.pack_into("<I", data, offset + COLOR_OFFSET, GREEN)
                n += 1

        if n:
            with open(diycol, "wb") as f:
                f.write(data)
            log.info(f"列颜色已设为荧光绿: {n} 条记录")
            return True
        return False
    except Exception as e:
        log.debug(f"设置颜色失败: {e}")
        return False


# ============================================================
# 数据获取：涨停原因（zzshare）
# ============================================================
def fetch_limit_up_data():
    from zzshare.client import DataApi

    api = DataApi()
    today = datetime.now().strftime("%Y%m%d")

    result = api.review_uplimit_reason(date1=today)
    if not result or not isinstance(result, list):
        return []

    stocks = []
    seen_codes = set()

    for group in result:
        stock_list = group.get("stocks", [])
        plate_name = group.get("plate_name", "")

        for s in stock_list:
            code = str(s.get("stock_code", "")).zfill(6)
            if code in seen_codes:
                continue
            seen_codes.add(code)

            full_reason = s.get("reason", "")
            short_desc = s.get("up_limit_desc", "")
            brief = format_reason(short_desc, full_reason)

            if len(brief) < 4 and plate_name:
                brief = short_desc + " " + plate_name

            stocks.append({
                "code": code,
                "name": s.get("stock_name", ""),
                "reason_brief": brief,
                "keep_times": s.get("up_limit_keep_times", ""),
                "up_time": s.get("up_limit_time", ""),
            })

    return stocks


# ============================================================
# 数据获取：所属概念（本地缓存）
# ============================================================
# 概念数据兜底：缓存失败时保留上一次的有效数据
_concept_backup = {}  # {code: concept_text}
_BACKUP_FILE = Path(__file__).parent / ".concept_backup.json"


def _save_concept_backup(concept_map):
    """持久化保存概念数据兜底文件"""
    try:
        with open(str(_BACKUP_FILE), "w", encoding="utf-8") as f:
            json.dump(concept_map, f, ensure_ascii=False)
    except Exception:
        pass


def _load_concept_backup():
    """从兜底文件恢复概念数据"""
    try:
        if _BACKUP_FILE.is_file():
            with open(str(_BACKUP_FILE), "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def load_concept_cache():
    global _concept_backup

    # 1. 尝试加载当日缓存
    if os.path.isfile(str(CACHE_FILE)):
        try:
            with open(str(CACHE_FILE), "r", encoding="utf-8") as f:
                c = json.load(f)
            if c.get("cache_date") == date.today().isoformat():
                stock_map = c.get("stock_map", {})
                if stock_map:
                    # 更新兜底数据
                    _concept_backup = {
                        code: " | ".join(
                            x[0] for x in sorted(concepts, key=lambda x: x[1], reverse=True)[:MAX_CONCEPTS]
                        )
                        for code, concepts in stock_map.items()
                    }
                    _save_concept_backup(_concept_backup)
                    return stock_map
            log.info("概念缓存过期")
        except Exception as e:
            log.warning(f"概念缓存读取失败: {e}")

    # 2. 降级：使用兜底数据
    if not _concept_backup:
        _concept_backup.update(_load_concept_backup())
    if _concept_backup:
        log.info(f"概念缓存降级: 使用兜底数据 ({len(_concept_backup)} 只)")
        return _concept_backup

    return {}


def get_concept_text(code, stock_map):
    """
    从缓存中取个股的概念归属，格式化为字符串
    支持两种格式：
      - 标准格式: {code: [(概念名, 涨幅), ...]}
      - 兜底格式: {code: "概念1 | 概念2 | ..."}
    返回: e.g. "CPO概念 | 光通信模块 | PCB"
    """
    val = stock_map.get(code)
    if not val:
        return ""

    # 兜底格式：已经是字符串
    if isinstance(val, str):
        return val

    # 标准格式：列表 of (name, pct)
    if isinstance(val, list) and val:
        concepts = sorted(val, key=lambda x: x[1], reverse=True)
        return " | ".join(c[0] for c in concepts[:MAX_CONCEPTS])

    return ""


# ============================================================
# 写入通达信
# ============================================================
def write_extern_user(stocks, stock_concept_map):
    global TDX_PATH
    if not TDX_PATH:
        TDX_PATH = find_tdx_path()
    if not TDX_PATH:
        log.error("未找到通达信路径")
        return False

    signals_dir = os.path.join(TDX_PATH, "T0002", "signals")
    os.makedirs(signals_dir, exist_ok=True)
    filepath = os.path.join(signals_dir, "extern_user.txt")

    lines = []
    n_with_concept = 0

    for s in stocks:
        code = s["code"].zfill(6)
        market = get_market_prefix(code)

        # 数据号 1: 涨停原因
        lines.append(f"{market}|{code}|{DATA_ID_REASON}|{s['reason_brief']}|0")

        # 数据号 2: 所属概念
        concept_text = get_concept_text(s["code"], stock_concept_map)
        if concept_text:
            n_with_concept += 1
        lines.append(f"{market}|{code}|{DATA_ID_CONCEPT}|{concept_text}|0")

    try:
        with open(filepath, "w", encoding="gbk", errors="ignore") as f:
            f.write("\n".join(lines) + "\n")
        log.info(f"写入 {len(stocks)} 只, {n_with_concept} 只有概念 -> {filepath}"
                 f"  [数据号1:涨停原因 数据号2:所属概念]")
        return True
    except Exception as e:
        log.error(f"写入失败: {e}")
        return False


# ============================================================
# 主流程
# ============================================================
def main():
    global TDX_PATH

    TDX_PATH = TDX_PATH or find_tdx_path()

    print("=" * 60)
    print("  通达信涨停原因+所属概念 监控 v5.0")
    print(f"  数据源: 同花顺(zzshare) + 东财概念缓存")
    print(f"  通达信: {TDX_PATH or '[FAIL]'}")
    print(f"  数据号1: 涨停原因  数据号2: 所属概念")
    print(f"  刷新: {UPDATE_INTERVAL}s")
    print("=" * 60)

    if not TDX_PATH:
        log.error("未找到通达信，请手动配置 TDX_PATH")
        return

    os.makedirs(os.path.join(TDX_PATH, "T0002", "signals"), exist_ok=True)

    # 首次提示
    extern_file = os.path.join(TDX_PATH, "T0002", "signals", "extern_user.txt")
    if not os.path.isfile(extern_file) or os.path.getsize(extern_file) == 0:
        print("-" * 60)
        print("首次运行提醒：")
        print("  1. 通达信 -> 功能 -> 公式系统 -> 自定义数据管理器")
        print("     - 新建 数据号:1 名称:涨停原因")
        print("     - 新建 数据号:2 名称:所属概念")
        print("  2. 行情列表表头右键 -> 选择自定义数据 -> 勾选两列")
        print("-" * 60)

    # 校验 zzshare
    try:
        import zzshare
        log.info(f"zzshare 就绪")
    except ImportError:
        log.error("未安装 zzshare：pip install zzshare")
        return

    # 设置列颜色为荧光绿（修改 diycol.dat）
    if set_column_green():
        log.info("如需生效请重启通达信")
    else:
        log.info("颜色设置失败，可手动设置：表头右键 -> 列属性 -> 字体颜色")

    # 主循环
    while True:
        try:
            # 1. 获取涨停数据
            stocks = fetch_limit_up_data()
            if not stocks:
                log.info("无涨停数据（可能是非交易时间）")
                time.sleep(UPDATE_INTERVAL)
                continue

            log.info(f"涨停 {len(stocks)} 只")

            # 2. 加载概念缓存
            concept_map = load_concept_cache() or {}
            if concept_map:
                n_hit = sum(1 for s in stocks if s["code"] in concept_map)
                log.info(f"概念缓存: {len(concept_map)} 只, 命中 {n_hit}/{len(stocks)}")
            else:
                log.info("概念缓存: 无有效数据（仅显示涨停原因）")

            # 3. 写入通达信
            write_extern_user(stocks, concept_map)

            # 4. 打印示例
            for s in stocks[:8]:
                concept = get_concept_text(s["code"], concept_map)
                c_tag = f" 概念: {concept}" if concept else ""
                log.info(f"  {s['code']} {s['name']} {s['up_time']}"
                         f" 连板{s['keep_times']}"
                         f" -> {s['reason_brief']}{c_tag}")

            time.sleep(UPDATE_INTERVAL)

        except KeyboardInterrupt:
            log.info("停止")
            break
        except Exception as e:
            log.error(f"异常: {e}")
            time.sleep(UPDATE_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
