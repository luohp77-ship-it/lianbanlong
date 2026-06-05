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
from pathlib import Path

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
    "enableWechat": False,
    "wechatKey": "",
    "enableTgBot": False,
    "tgBotToken": "",
}


# ═══ HTTP 请求（3次重试 + 指数退避）═══
def http_get(url, headers=None, timeout=15, retries=3):
    """HTTP GET 带重试，返回 (status_code, body_string)。

    Args:
        url: 请求URL。
        headers: 额外HTTP头。
        timeout: 超时秒数。
        retries: 最大重试次数。

    Returns:
        (status_code, body_string) 元组。
    """
    hdrs = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    if headers:
        hdrs.update(headers)

    last_error = ''
    for attempt in range(retries):
        try:
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request(url, headers=hdrs)
            resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
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


# ═══ TDX 日线数据读取 ═══
TDX_DIR = None  # 由 engine.py 在运行时设置


def set_tdx_dir(tdx_path):
    """设置通达信安装目录。

    Args:
        tdx_path: 通达信目录路径。
    """
    global TDX_DIR
    TDX_DIR = tdx_path


def read_tdx_daily(code):
    """读取通达信本地日线数据（32字节/条）。

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
    遇到非涨停则中断。ST股走boards JSON继承。

    Args:
        code: 6位股票代码。
        target_date: 目标日期，格式YYYYMMDD(int)。
        is_st: 是否ST股。
        yesterday_boards: 昨日boards缓存字典{code: days}。

    Returns:
        连板天数（最小为1）。
    """
    # ST股：从boards JSON继承
    if is_st:
        if yesterday_boards and code in yesterday_boards:
            return yesterday_boards[code] + 1
        return 1

    # 非ST：读TDX本地日线
    daily = read_tdx_daily(code)
    if not daily:
        return None  # 降级信号：TDX日线缺失

    # 从最新记录往前找目标日期
    target_int = int(target_date) if isinstance(target_date, str) else target_date
    start_idx = -1
    for i in range(len(daily) - 1, -1, -1):
        if daily[i]['date'] == target_int:
            start_idx = i
            break

    if start_idx < 0:
        return None  # 日线中无目标日期数据

    # 向前遍历计算连板天数
    days = 0
    for i in range(start_idx, -1, -1):
        close = daily[i]['close']
        if i == 0:
            # 第一条记录无法判断涨停，算1天
            days += 1
            break
        prev_close = daily[i - 1]['close']
        if is_limit_up(code, close, prev_close, is_st=False):
            days += 1
        else:
            break

    return max(days, 1)


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
        '1'（沪市）、'0'（深市）、'3'（其他）。
    """
    c = str(code).strip().zfill(6)
    if c.startswith('6'):
        return '1'
    elif c.startswith(('0', '3')):
        return '0'
    return '3'


def write_extern_user(tdx_dir, stocks, concept_map):
    """将涨停原因+所属概念写入通达信 extern_user.txt。

    格式：market|code|data_id|value|0
    - 数据号1：涨停原因（来自同花顺dataapi）
    - 数据号2：所属概念（来自t:3概念分析，TOP4概念用" | "连接）

    Args:
        tdx_dir: 通达信安装目录。
        stocks: 涨停股列表，每个元素需含 code/name/reason/concepts 字段。
            reason: 涨停原因字符串。
            concepts: 概念名列表（最多4个）。
        concept_map: 概念→股票映射 {code: [概念名列表]}（备选，stocks已有concepts则不用）。

    Returns:
        写入的股票数量。
    """
    if not tdx_dir:
        return 0
    signals_dir = os.path.join(tdx_dir, 'T0002', 'signals')
    os.makedirs(signals_dir, exist_ok=True)
    filepath = os.path.join(signals_dir, 'extern_user.txt')

    lines = []
    for s in stocks:
        code = str(s.get('code', '')).zfill(6)
        market = get_market_prefix(code)

        # 涨停原因（数据号1）：取reason字段，超40字截断
        reason = s.get('reason', '') or s.get('reason_brief', '')
        if len(reason) > 40:
            reason = reason[:37] + '...'
        lines.append('%s|%s|1|%s|0' % (market, code, reason))

        # 所属概念（数据号2）：取concepts字段，最多4个用"/"连接
        # 注意：不使用"|"分隔，否则与extern_user.txt的字段分隔符冲突
        concepts = s.get('concepts', [])
        if not concepts and code in concept_map:
            concepts = concept_map[code][:4]
        concept_text = '/'.join(concepts[:4])
        lines.append('%s|%s|2|%s|0' % (market, code, concept_text))

    try:
        tmp = filepath + '.tmp'
        with open(tmp, 'w', encoding='gbk', errors='ignore') as f:
            f.write('\n'.join(lines) + '\n')
        os.replace(tmp, filepath)
        log('  extern_user.txt: %d只 (涨停原因+所属概念)' % len(stocks))
        return len(stocks)
    except OSError as e:
        log('  [WARN] extern_user.txt写入失败: %s' % e, level='WARN')
        return 0


def set_column_green(tdx_dir):
    """将通达信自定义数据列（数据号1、2）的字体颜色设为荧光绿。

    原理：修改 T0002/diycol.dat 中每条150字节记录的偏移0x1c处的颜色字段
    颜色格式：COLORREF = RGB(0,255,0) = 0x0000FF00

    Args:
        tdx_dir: 通达信安装目录。

    Returns:
        是否成功设置。
    """
    import struct as _struct
    if not tdx_dir:
        return False
    diycol = os.path.join(tdx_dir, 'T0002', 'diycol.dat')
    if not os.path.isfile(diycol):
        log('  diycol.dat 不存在，跳过颜色设置（首次运行后可用）')
        return False
    try:
        with open(diycol, 'rb') as f:
            data = bytearray(f.read())
        RECORD_SIZE = 150
        COLOR_OFFSET = 0x1c
        GREEN = 0x0000FF00  # RGB(0,255,0)
        n = 0
        for i in range(len(data) // RECORD_SIZE):
            offset = i * RECORD_SIZE
            d_id = _struct.unpack_from('<I', data, offset + 4)[0]
            if d_id in (1, 2):
                _struct.pack_into('<I', data, offset + COLOR_OFFSET, GREEN)
                n += 1
        if n:
            tmp = diycol + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, diycol)
            log('  自定义列颜色已设为荧光绿: %d条记录' % n)
            return True
        log('  diycol.dat中未找到数据号1/2（需先在通达信中创建自定义列）')
        return False
    except Exception as e:
        log('  [WARN] 颜色设置失败: %s' % e, level='WARN')
        return False
