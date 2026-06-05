#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具层：HTTP请求(3次重试)、日志(自动清理)、配置读写、文件操作、TDX日线读取"""
import os
import sys
import json
import ssl
import time
import struct
import shutil
import urllib.request
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

# 区分 PyInstaller 打包模式和源码模式
# PyInstaller onefile 模式：sys.executable 指向 exe 实际路径
# 源码模式：__file__ 指向当前文件
if getattr(sys, 'frozen', False):
    # 打包为 exe 时，配置文件放在 exe 同级目录
    _exe_dir = Path(sys.executable).parent.resolve()
    BASE_DIR = _exe_dir
else:
    BASE_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = BASE_DIR / 'config.json'
DATA_DIR = BASE_DIR / 'data'
LOG_DIR = BASE_DIR / 'logs'
STANDALONE_DIR = BASE_DIR / 'standalone'

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

DEFAULT_CONFIG = {
    "tdxDir": "C:\\new_tdx",
    "blocknewDir": "C:\\new_tdx\\T0002\\blocknew",
}


# ═══ HTTP 请求（3次重试 + 指数退避）═══
def http_get(url, headers=None, timeout=15, retries=3, verify_ssl=True):
    """HTTP GET 带重试，返回 (status_code, body_string)。

    Args:
        url: 请求URL。
        headers: 额外HTTP头。
        timeout: 超时秒数。
        retries: 最大重试次数。
        verify_ssl: 是否验证SSL证书，默认 True（V3.1新增）。

    Returns:
        (status_code, body_string) 元组。
    """
    hdrs = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    if headers:
        hdrs.update(headers)

    # 仅对 HTTPS 使用 SSL 策略；HTTP 无需 SSL 上下文
    is_https = url.startswith('https://')
    if is_https and verify_ssl:
        ctx = ssl.create_default_context()
    elif is_https:
        ctx = ssl._create_unverified_context()
    else:
        ctx = None

    last_error = ''
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            kwargs = {'timeout': timeout}
            if ctx is not None:
                kwargs['context'] = ctx
            resp = urllib.request.urlopen(req, **kwargs)
            return resp.status, resp.read().decode('utf-8', errors='replace')
        except Exception as e:
            last_error = str(e)
            if attempt < retries - 1:
                wait = 1 * (2 ** attempt)
                time.sleep(wait)
    return 0, last_error


def http_get_json(url, headers=None, timeout=15, retries=3):
    """HTTP GET 返回 JSON 解析结果。

    Args:
        url: 请求URL。
        headers: 额外HTTP头。
        timeout: 超时秒数。
        retries: 最大重试次数。

    Returns:
        解析后的字典，失败返回 None。
    """
    status, body = http_get(url, headers, timeout, retries)
    if status == 200:
        try:
            return json.loads(body)
        except (json.JSONDecodeError, ValueError):
            pass
    return None


# ═══ 日志 ═══
_cached_logger = None


def _get_logger():
    """获取全局 Logger 单例。"""
    global _cached_logger
    if _cached_logger is None:
        _cached_logger = Logger()
    return _cached_logger


class Logger:
    """日志管理器，支持回调函数和文件写入。"""

    def __init__(self):
        self.callback = None
        self.logs = []

    def set_callback(self, cb):
        """设置日志回调函数。"""
        self.callback = cb

    def emit(self, msg, level='INFO'):
        """输出一条日志。

        Args:
            msg: 日志内容。
            level: 日志级别。

        Returns:
            格式化后的日志行。
        """
        ts = datetime.now().strftime('%H:%M:%S')
        line = '[%s] [%s] %s' % (ts, level, msg)
        fpath = LOG_DIR / ('%s.log' % datetime.now().strftime('%Y%m%d'))
        try:
            with open(fpath, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass
        self.logs.append(line)
        if self.callback:
            self.callback(line)
        return line


def log(msg, level='INFO'):
    """输出日志（全局便捷函数）。"""
    return _get_logger().emit(msg, level)


def clean_old_logs(days=30):
    """清理 days 天前的日志文件。

    Args:
        days: 保留天数。
    """
    cutoff = datetime.now() - timedelta(days=days)
    try:
        for f in LOG_DIR.glob('*.log'):
            if f.stat().st_mtime < cutoff.timestamp():
                f.unlink()
    except OSError:
        pass


# ═══ 配置文件（原子写入）═══
def load_config():
    """加载配置文件，缺失时创建默认配置。

    Returns:
        配置字典。
    """
    if not CONFIG_FILE.exists():
        save_config(dict(DEFAULT_CONFIG))
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    """原子写入配置文件。

    Args:
        cfg: 配置字典。
    """
    tmp = str(CONFIG_FILE) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(CONFIG_FILE))


# ═══ JSON 文件读写（原子写入）═══
def load_json(filepath):
    """读取 JSON 文件。

    Args:
        filepath: 文件路径。

    Returns:
        解析后的对象，失败返回 None。
    """
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_json(filepath, data):
    """原子写入 JSON 文件：先写 .tmp，再 rename。

    Args:
        filepath: 目标文件路径。
        data: 要序列化的数据。
    """
    tmp = str(filepath) + '.tmp'
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(filepath))


# ═══ 板块文件操作 ═══
def stock_to_blk(code):
    """股票代码 → 通达信 .blk 文件编码。

    Args:
        code: 6位股票代码。

    Returns:
        市场前缀+代码的7位字符串。
    """
    c = str(code).strip().zfill(6)
    if c.startswith('6'):
        return '1' + c
    elif c.startswith(('0', '3')):
        return '0' + c
    else:
        return '3' + c


def write_blk(filepath, codes):
    """原子写入 .blk 文件。

    Args:
        filepath: .blk 文件路径。
        codes: 股票代码列表。

    Returns:
        写入的股票数量。
    """
    seen = []
    for c in codes:
        s = stock_to_blk(c)
        if s not in seen:
            seen.append(s)
    # 空板块写入0字节文件，避免通达信误解 CRLF 为股票代码
    if not seen:
        content = ''
    else:
        content = '\r\n'.join(seen)
    tmp = filepath + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(content.encode('ascii'))
    os.replace(tmp, filepath)
    return len(seen)


def backup_file(filepath):
    """创建时间戳备份。

    Args:
        filepath: 要备份的文件路径。

    Returns:
        备份文件路径，原文件不存在返回 None。
    """
    if not os.path.exists(filepath):
        return None
    ts = datetime.now().strftime('%Y%m%d%H%M%S')
    backup = filepath + '.bak.' + ts
    shutil.copy2(filepath, backup)
    return backup


# ═══ 涨跌幅标准 ═══
def get_limit_pct(code, is_st=False):
    """按市场前缀返回涨跌停幅度。

    Args:
        code: 6位股票代码。
        is_st: 是否ST股。

    Returns:
        涨跌幅比例（如0.10表示10%）。
    """
    c = str(code).strip().zfill(6)
    if c.startswith('30'):    # 创业板
        return 0.20
    elif c.startswith('68'):  # 科创板
        return 0.20
    elif c.startswith('8'):   # 北交所
        return 0.30
    else:                      # 主板 (60xxxx, 00xxxx)
        return 0.05 if is_st else 0.10


def is_limit_up(code, close, prev_close, is_st=False):
    """判断是否涨停（V5本地计算核心函数）。

    涨停阈值：主板≥9.8%，创业板/科创板≥19.5%，ST≥4.8%
    留容差避免四舍五入导致误判。

    Args:
        code: 6位股票代码。
        close: 当日收盘价。
        prev_close: 前一日收盘价。
        is_st: 是否ST股。

    Returns:
        是否涨停。
    """
    if prev_close <= 0:
        return False
    pct = (close - prev_close) / prev_close
    c = str(code).strip().zfill(6)
    if is_st:
        return pct >= 0.048
    elif c.startswith('30') or c.startswith('68'):  # 创业板/科创板
        return pct >= 0.195
    else:  # 主板
        return pct >= 0.098


def is_limit_down(code, close, prev_close, is_st=False):
    """判断是否跌停。

    跌停阈值：主板≤-9.8%，创业板/科创板≤-19.5%，ST≤-4.8%
    留容差避免四舍五入导致误判。

    Args:
        code: 6位股票代码。
        close: 当日收盘价。
        prev_close: 前一日收盘价。
        is_st: 是否ST股。

    Returns:
        是否跌停。
    """
    if prev_close <= 0:
        return False
    pct = (close - prev_close) / prev_close
    c = str(code).strip().zfill(6)
    if is_st:
        return pct <= -0.048
    elif c.startswith('30') or c.startswith('68'):  # 创业板/科创板
        return pct <= -0.195
    else:  # 主板
        return pct <= -0.098


# ═══ TDX 日线数据读取 ═══
TDX_DIR = None  # 由 engine.py 在运行时设置


def set_tdx_dir(tdx_path):
    """设置通达信安装目录。

    Args:
        tdx_path: 通达信目录路径。
    """
    global TDX_DIR
    TDX_DIR = tdx_path


@lru_cache(maxsize=256)
def read_tdx_daily(code):
    """读取通达信本地日线数据（32字节/条，带LRU缓存）。

    文件路径: vipdoc/{market}/lday/{market}{code}.day
    格式：日期(4B)+开(4B)+高(4B)+低(4B)+收(4B)+额(4B float)+量(4B)+保留(4B)

    Args:
        code: 6位股票代码。

    Returns:
        日线记录列表 [{date, open, high, low, close, amount, volume}]，
        失败返回空列表。
    """
    if not TDX_DIR:
        return []
    c = str(code).strip().zfill(6)
    if c.startswith(('6', '68')):
        market = 'sh'
    else:
        market = 'sz'
    fp = os.path.join(TDX_DIR, 'vipdoc', market, 'lday', '%s%s.day' % (market, c))
    if not os.path.exists(fp):
        return []
    try:
        with open(fp, 'rb') as f:
            data = f.read()
    except OSError:
        return []
    recs = []
    for i in range(len(data) // 32):
        rec = data[i * 32:(i + 1) * 32]
        dt = struct.unpack('I', rec[0:4])[0]
        open_p = struct.unpack('I', rec[4:8])[0] / 100
        high = struct.unpack('I', rec[8:12])[0] / 100
        low = struct.unpack('I', rec[12:16])[0] / 100
        close = struct.unpack('I', rec[16:20])[0] / 100
        amount = struct.unpack('f', rec[20:24])[0]
        volume = struct.unpack('I', rec[24:28])[0]
        recs.append({
            'date': dt, 'open': open_p, 'high': high,
            'low': low, 'close': close, 'amount': amount, 'volume': volume
        })
    return recs


def calc_board_days_tdx(code, target_date, is_st=False, yesterday_boards=None):
    """V5核心：通过TDX本地日线计算连板天数。

    算法：从目标日期向前遍历日线，连续涨停则连板+1，
    遇到非涨停则中断。ST股优先TDX验证（4.8%阈值），无日线时降级boards继承。

    Args:
        code: 6位股票代码。
        target_date: 目标日期，格式YYYYMMDD(int)。
        is_st: 是否ST股。
        yesterday_boards: 昨日boards缓存字典{code: days}。

    Returns:
        连板天数（最小为1），非ST股TDX无数据返回None。
    """
    # 读TDX本地日线
    daily = read_tdx_daily(code)
    if not daily:
        # ST股：TDX无数据，降级到boards继承
        if is_st:
            if yesterday_boards and code in yesterday_boards:
                return yesterday_boards[code] + 1
            return 1
        return None  # 非ST降级信号

    # 从最新记录往前找目标日期
    target_int = int(target_date) if isinstance(target_date, str) else target_date
    start_idx = -1
    for i in range(len(daily) - 1, -1, -1):
        if daily[i]['date'] == target_int:
            start_idx = i
            break

    if start_idx < 0:
        # ST股：日线中无目标日期，降级到boards继承
        if is_st:
            if yesterday_boards and code in yesterday_boards:
                return yesterday_boards[code] + 1
            return 1
        return None

    # 向前遍历计算连板天数（ST用4.8%阈值，非ST用正常阈值）
    days = 0
    for i in range(start_idx, -1, -1):
        close = daily[i]['close']
        if i == 0:
            # 第一条记录无法判断涨停，算1天
            days += 1
            break
        prev_close = daily[i - 1]['close']
        if is_limit_up(code, close, prev_close, is_st=is_st):
            days += 1
        else:
            break

    return max(days, 1)


# ═══ 通达信路径自动检测 ═══

def detect_tdx_path():
    """自动检测通达信安装路径。

    扫描常见路径 + 注册表反安装信息，返回第一个找到的路径，
    未找到返回 None。

    Returns:
        通达信根目录路径（如 C:\\new_tdx），或 None。
    """
    # 常见安装路径（按优先级排列）
    common_paths = [
        r"C:\new_tdx64",
        r"C:\new_tdx",
        r"D:\new_tdx64",
        r"D:\new_tdx",
        r"E:\new_tdx64",
        r"E:\new_tdx",
        r"C:\tdx",
        r"D:\tdx",
        r"C:\tdx64",
        r"D:\tdx64",
        r"C:\TdxClaw",
    ]

    # 1. 扫描常见路径（检查 TdxW.exe 存在）
    for p in common_paths:
        if os.path.isdir(p):
            exe = os.path.join(p, 'TdxW.exe')
            if os.path.isfile(exe):
                return p

    # 2. 注册表查找（通达信安装器通常会写注册表）
    try:
        import winreg
        # HKEY_LOCAL_MACHINE 卸载信息
        for scope in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
            try:
                key = winreg.OpenKey(scope,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
                for i in range(200):
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        subkey = winreg.OpenKey(key, subkey_name)
                        try:
                            name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                            if any(kw in name for kw in ('通达信', 'TongDaXin', 'tdx', 'TDX')):
                                path = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                                if path and os.path.isdir(path):
                                    return path
                        except WindowsError:
                            pass
                        finally:
                            winreg.CloseKey(subkey)
                    except WindowsError:
                        break
                winreg.CloseKey(key)
            except WindowsError:
                pass
    except ImportError:
        pass

    return None


def check_day_writedisk(tdx_dir):
    """检查并修复通达信 Day_WriteDisk 配置。

    如果 Day_WriteDisk=0，自动改为 1 并返回 True，
    否则返回 False。

    Args:
        tdx_dir: 通达信根目录路径。

    Returns:
        是否修复了配置。
    """
    if not tdx_dir:
        return False
    user_ini = os.path.join(tdx_dir, 'T0002', 'user.ini')
    if not os.path.isfile(user_ini):
        return False
    try:
        with open(user_ini, 'r', encoding='gbk', errors='replace') as f:
            content = f.read()
        if 'Day_WriteDisk=0' not in content:
            return False
        content = content.replace('Day_WriteDisk=0', 'Day_WriteDisk=1')
        with open(user_ini, 'w', encoding='gbk', errors='replace') as f:
            f.write(content)
        return True
    except (OSError, IOError):
        return False


# ═══ 阶段涨幅排名 ═══

def calc_period_gain(tdx_dir, target_date, period_days=20, top_n=30, cutoff_days=60):
    """计算主板股票近N个交易日涨幅排名（含新股过滤）。

    筛选规则：
    - 仅主板：沪市主板(60xxxx)、深市主板(000xxx~003xxx)
    - 排除创业板(30xxxx)、科创板(68xxxx)、北交所(8xxxxx)
    - 排除ST股（名称含ST）
    - 排除新股：TDX日线首条记录距今 < cutoff_days 个自然日

    Args:
        tdx_dir: 通达信安装目录。
        target_date: 目标日期 YYYYMMDD (int)。
        period_days: 回溯交易日数（默认20）。
        top_n: 返回前N名（默认30）。
        cutoff_days: 新股过滤天数（默认60，新板块用90）。

    Returns:
        [(code, name, gain_pct, close, days_ago_close), ...]
        按涨幅降序排列。
    """
    import re
    from datetime import datetime

    if not tdx_dir or not os.path.isdir(tdx_dir):
        return []

    td = str(target_date)
    cutoff_calendar = int((datetime.strptime(td, '%Y%m%d') -
                           timedelta(days=cutoff_days)).strftime('%Y%m%d'))

    results = []

    for market in ('sh', 'sz'):
        lday_dir = os.path.join(tdx_dir, 'vipdoc', market, 'lday')
        if not os.path.isdir(lday_dir):
            continue
        for fname in os.listdir(lday_dir):
            if not fname.endswith('.day'):
                continue
            # fname: sh600000.day / sz000001.day
            code = fname[2:8]
            if not code.isdigit() or len(code) != 6:
                continue

            # --- 主板筛选 ---
            if market == 'sh':
                if not code.startswith('6'):
                    continue
                if code.startswith('68'):  # 科创板排除
                    continue
            else:  # sz
                if not (code.startswith('000') or code.startswith('001') or
                        code.startswith('002') or code.startswith('003')):
                    continue
                if code.startswith('30'):  # 创业板排除
                    continue

            # --- 读取日线 ---
            recs = read_tdx_daily(code)
            if not recs or len(recs) < period_days + 1:
                continue

            # --- 新股过滤：首条记录距今需 >= 60个自然日 ---
            first_date = recs[0]['date']
            if first_date >= cutoff_calendar:
                continue

            # --- ST 过滤 ---
            name = _code_name_map.get(code, '') if '_code_name_map' in dir() else ''
            if name and (name.startswith('ST') or name.startswith('*ST')):
                continue

            # --- 计算涨幅 ---
            latest = recs[-1]
            ago = recs[-period_days - 1]
            if ago['close'] <= 0:
                continue
            gain = (latest['close'] - ago['close']) / ago['close'] * 100

            results.append((code, gain, latest['close'], ago['close']))

    results.sort(key=lambda x: -x[1])
    return results[:top_n]


_code_name_map = {}  # 代码→名称缓存（运行时由 engine 填充）


def set_code_name_map(mapping):
    """设置股票代码→名称映射缓存。
    Args:
        mapping: {code: name} 字典。
    """
    global _code_name_map
    _code_name_map = mapping


# ═══ 交易日历 ═══
_holidays_cache = None


def _load_holidays():
    """加载并缓存节假日列表。"""
    global _holidays_cache
    if _holidays_cache is not None:
        return _holidays_cache
    fp = STANDALONE_DIR / 'holidays.json'
    data = load_json(fp)
    _holidays_cache = set(data) if data else set()
    return _holidays_cache


def is_trading_day(dt=None):
    """判断是否为交易日（排除周末和法定假日）。

    Args:
        dt: 日期对象，默认为当前时间。

    Returns:
        是否为交易日。
    """
    if dt is None:
        dt = datetime.now()
    if dt.weekday() >= 5:
        return False
    date_str = dt.strftime('%Y-%m-%d')
    return date_str not in _load_holidays()


def get_latest_trading_day(dt=None):
    """获取最近一个交易日。

    Args:
        dt: 起始日期，默认为当前时间。

    Returns:
        最近的交易日 datetime 对象。
    """
    if dt is None:
        dt = datetime.now()
    d = dt
    for _ in range(10):
        if is_trading_day(d):
            return d
        d = d - timedelta(days=1)
    return d


# ═══ 列表去重 ═══
def dedup_list(lst):
    """保序去重。

    Args:
        lst: 输入列表。

    Returns:
        去重后的列表。
    """
    seen = set()
    return [x for x in lst if not (x in seen or seen.add(x))]


# ═══ 自定义数据列：extern_user.txt 写入 ═══
def get_market_prefix(code):
    """股票代码 → 通达信市场前缀（用于extern_user.txt格式）。

    Args:
        code: 6位股票代码。

    Returns:
        '1'（沪市）、'0'（深市/北交所/创业板）。
    """
    c = str(code).strip().zfill(6)
    if c.startswith('6'):
        return '1'
    # 0开头(深主板)、3开头(创业板)、8/9开头(北交所) → 均用前缀0
    return '0'


def write_extern_user(tdx_dir, stocks, concept_map, lhb_detail=None,
                      lhb_sell_codes=None):
    """将涨停原因+所属概念+龙虎榜明细写入通达信 extern_user.txt。

    格式：market|code|data_id|value|color_flag
    - 数据号1：涨停原因（来自同花顺dataapi）
    - 数据号2：所属概念（概念分析TOP4，用"/"连接）
    - 数据号3：龙虎榜（合计+买入前3+卖出前3名称），颜色由diycol.dat列默认色决定

    Args:
        tdx_dir: 通达信安装目录。
        stocks: 涨停股列表，每个元素需含 code/name/reason/concepts 字段。
        concept_map: 概念→股票映射 {code: [概念名列表]}（备选）。
        lhb_detail: 龙虎榜格式化数据 {code: text_string}（可选）。
        lhb_sell_codes: 预留参数，暂不使用。

    Returns:
        写入的股票数量。
    """
    if not tdx_dir:
        return 0
    # 若 tdx_zt_monitor.py 正在运行则终止它（它会持续覆盖 signals 路径导致龙虎榜数据丢失）
    try:
        import psutil
        for p in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmd = ' '.join(p.info['cmdline'] or []) if p.info['cmdline'] else ''
                if 'tdx_zt_monitor' in cmd.lower():
                    p.terminate()
                    p.wait(timeout=3)
                    log('  [OK] 已终止冲突的监控进程 tdx_zt_monitor.py (PID=%d)' % p.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                pass
    except ImportError:
        pass
    # 通达信不同版本可能从两个路径读取，因此同时写入
    paths = [
        os.path.join(tdx_dir, 'T0002', 'extern_user.txt'),
        os.path.join(tdx_dir, 'T0002', 'signals', 'extern_user.txt'),
    ]

    lines = []
    written_codes = set()
    for s in stocks:
        code = str(s.get('code', '')).zfill(6)
        market = get_market_prefix(code)
        written_codes.add(code)

        # 涨停原因（数据号1）：取reason字段，超40字截断
        reason = s.get('reason', '') or s.get('reason_brief', '')
        if len(reason) > 40:
            reason = reason[:37] + '...'
        lines.append('%s|%s|1|%s|0' % (market, code, reason))

        # 所属概念（数据号2）：取concepts字段，最多4个用"/"连接
        concepts = s.get('concepts', [])
        if not concepts and code in concept_map:
            concepts = concept_map[code][:4]
        concept_text = '/'.join(concepts[:4])
        lines.append('%s|%s|2|%s|0' % (market, code, concept_text))

        # 龙虎榜明细（数据号3）：V2.1.3精简为单列，颜色由diycol.dat列默认色决定
        if lhb_detail and code in lhb_detail:
            val = lhb_detail[code]
            if isinstance(val, str) and val:
                lines.append('%s|%s|3|%s|0' % (market, code, val))

    # 为未涨停但上龙虎榜的股票也写入 LHB 数据（仅数据号3）
    if lhb_detail:
        for code, val in lhb_detail.items():
            if code in written_codes:
                continue
            if not (isinstance(val, str) and val):
                continue
            market = get_market_prefix(code)
            lines.append('%s|%s|3|%s|0' % (market, code, val))

    try:
        content = '\n'.join(lines) + '\n'
        for filepath in paths:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            tmp = filepath + '.tmp'
            with open(tmp, 'w', encoding='gbk', errors='ignore') as f:
                f.write(content)
            os.replace(tmp, filepath)
        lhb_n = len(lhb_detail) if lhb_detail else 0
        log('  extern_user.txt: %d只 (涨停原因+所属概念%s)' %
            (len(stocks), ', +龙虎榜%d只' % lhb_n if lhb_n else ''))
        return len(stocks)
    except OSError as e:
        log('  [WARN] extern_user.txt写入失败: %s' % e, level='WARN')
        return 0


def set_column_green(tdx_dir):
    """将通达信自定义数据列（数据号1-3）的字体颜色设为浅灰（暖白）。
    原理：修改 T0002/diycol.dat 记录的偏移0x1c处的颜色字段。
    颜色格式：COLORREF = RGB(192,192,192) = 0x00C0C0C0。

    Args:
        tdx_dir: 通达信安装目录。

    Returns:
        是否成功设置。
    """
    import struct as _struct
    if not tdx_dir:
        return False
    # 检测通达信是否在运行，警告但不阻止（用户可能在复盘后再开TDX）
    try:
        import psutil  # type: ignore
        tdx_names = {'tdxw', 'xiadan', 't0002', 'tdx'}
        for p in psutil.process_iter(['name']):
            if p.info['name'] and any(t in p.info['name'].lower() for t in tdx_names):
                log('  [提示] 通达信正在运行；写入diycol.dat后需重启TDX才能看到颜色变化',
                    level='WARN')
                break
    except ImportError:
        pass
    diycol = os.path.join(tdx_dir, 'T0002', 'diycol.dat')
    RECORD_SIZE = 150
    LIGHT_GRAY = 0x00C0C0C0  # RGB(192,192,192) 暖白/浅灰，深色背景上清晰不刺眼

    # 需要确保存在的数据号定义（首次运行自动创建）
    NEW_DEFS = {
        1: '涨停原因',
        2: '所属概念',
        3: '龙虎榜',
    }
    ALL_IDS = {1, 2, 3}

    # Step 1: 读取现有记录（若文件不存在则创建空数组）
    data = bytearray()
    if os.path.isfile(diycol):
        with open(diycol, 'rb') as f:
            data = bytearray(f.read())

    existing_ids = set()
    for i in range(len(data) // RECORD_SIZE):
        off = i * RECORD_SIZE
        if off + 4 <= len(data):
            d_id = _struct.unpack_from('<I', data, off + 4)[0]
            if d_id > 0:
                existing_ids.add(d_id)

    # Step 2: 为缺失的数据号创建新记录
    has_new = False
    for did, name in NEW_DEFS.items():
        if did not in existing_ids:
            rec = bytearray(RECORD_SIZE)
            # byte 4-7: data_id (uint32 LE)
            _struct.pack_into('<I', rec, 4, did)
            # byte 8-23: name (GBK encoded, null-padded)
            name_bytes = name.encode('gbk', errors='ignore')[:16]
            rec[8:8 + len(name_bytes)] = name_bytes
            # byte 0x1c (28): color = white
            if did in ALL_IDS:
                _struct.pack_into('<I', rec, 0x1c, LIGHT_GRAY)
            data.extend(rec)
            has_new = True
            log('  [自动创建] 自定义列 数据号%d: %s' % (did, name))
    # 更新 byte0（通达信用它判断记录数，必须始终与记录数一致）
    data[0] = len(data) // RECORD_SIZE
    log("  [byte0] 已更新记录数: %d" % data[0])

    # Step 3: 为所有数据号1-3设置浅灰
    n = 0
    for i in range(len(data) // RECORD_SIZE):
        off = i * RECORD_SIZE
        d_id = _struct.unpack_from('<I', data, off + 4)[0]
        if d_id in ALL_IDS:
            _struct.pack_into('<I', data, off + 0x1c, LIGHT_GRAY)
            n += 1

    if n:
        tmp = diycol + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, diycol)
        log('  自定义列颜色已设为浅灰: %d条记录 (数据号1-3)' % n)
        return True

    log('  diycol.dat中未找到数据号1-3')
    return False
