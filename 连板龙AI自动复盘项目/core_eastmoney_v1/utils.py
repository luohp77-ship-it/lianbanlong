#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具层：HTTP请求(3次重试)、日志(自动清理)、配置读写、文件操作"""
import os, sys, json, ssl, time, struct, urllib.request
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
    "tdxDir": "C:/new_tdx",
    "blocknewDir": "C:/new_tdx/T0002/blocknew",
    "enableWechat": False,
    "wechatKey": "",
}

# ═══ HTTP 请求（3次重试 + 指数退避）═══
def http_get(url, headers=None, timeout=15, retries=3):
    """HTTP GET 带重试，返回 (status_code, body_string)"""
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
    """HTTP GET 返回 JSON 解析结果"""
    status, body = http_get(url, headers, timeout, retries)
    if status == 200:
        try:
            return json.loads(body)
        except:
            pass
    return None


# ═══ 日志 ═══
_cached_logger = None

def _get_logger():
    """获取一个可配置的 logger 实例"""
    global _cached_logger
    if _cached_logger is None:
        _cached_logger = Logger()
    return _cached_logger

class Logger:
    """日志管理器，支持回调函数"""
    def __init__(self):
        self.callback = None
        self.logs = []

    def set_callback(self, cb):
        self.callback = cb

    def emit(self, msg, level='INFO'):
        ts = datetime.now().strftime('%H:%M:%S')
        line = '[%s] [%s] %s' % (ts, level, msg)
        # 写入文件
        fpath = LOG_DIR / ('%s.log' % datetime.now().strftime('%Y%m%d'))
        try:
            with open(fpath, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except:
            pass
        self.logs.append(line)
        if self.callback:
            self.callback(line)
        return line

def log(msg, level='INFO'):
    return _get_logger().emit(msg, level)

def clean_old_logs(days=30):
    """清理 days 天前的日志文件"""
    cutoff = datetime.now() - timedelta(days=days)
    try:
        for f in LOG_DIR.glob('*.log'):
            if f.stat().st_mtime < cutoff.timestamp():
                f.unlink()
    except:
        pass


# ═══ 配置文件 ═══
def load_config():
    if not CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    except:
        return dict(DEFAULT_CONFIG)

def save_config(cfg):
    tmp = str(CONFIG_FILE) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(CONFIG_FILE))


# ═══ JSON 文件读写（原子写入） ═══
def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return None

def save_json(filepath, data):
    """原子写入：先写 .tmp，再 rename"""
    tmp = str(filepath) + '.tmp'
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(filepath))


# ═══ 板块文件操作 ═══
def stock_to_blk(code):
    """股票代码 → 通达信 .blk 文件编码"""
    c = str(code).strip().zfill(6)
    if c.startswith('6'):
        return '1' + c
    elif c.startswith(('0', '3')):
        return '0' + c
    else:
        return '3' + c

def write_blk(filepath, codes):
    """原子写入 .blk 文件"""
    seen = []
    for c in codes:
        s = stock_to_blk(c)
        if s not in seen:
            seen.append(s)
    # 空板块写入0字节文件，避免通达信误解 CRLF 为股票代码
    if not seen:
        content = ''
    else:
        content = '\r\n'.join(seen)  # 不添加尾部\r\n，匹配原生格式
    tmp = filepath + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(content.encode('ascii'))
    os.replace(tmp, filepath)
    return len(seen)

def backup_file(filepath):
    """创建时间戳备份，返回备份路径"""
    if not os.path.exists(filepath):
        return None
    ts = datetime.now().strftime('%Y%m%d%H%M%S')
    backup = filepath + '.bak.' + ts
    import shutil
    shutil.copy2(filepath, backup)
    return backup


# ═══ 涨跌幅标准 ═══
def get_limit_pct(code, is_st=False):
    """按市场前缀返回涨跌停幅度"""
    c = str(code).strip().zfill(6)
    if c.startswith('30'):    # 创业板
        return 0.20
    elif c.startswith('68'):  # 科创板
        return 0.20
    elif c.startswith('8'):   # 北交所
        return 0.30
    else:                      # 主板 (60xxxx, 00xxxx)
        return 0.05 if is_st else 0.10


# ═══ TDX 日线数据读取 ═══
TDX_DIR = None  # 由 engine.py 在运行时设置

def set_tdx_dir(tdx_path):
    global TDX_DIR
    TDX_DIR = tdx_path

def read_tdx_daily(code):
    """读取通达信日线数据，返回 [{date, open, high, low, close, amount, volume}]"""
    if not TDX_DIR:
        return []
    market = 'sz' if code.startswith(('0', '3')) else 'sh'
    fp = os.path.join(TDX_DIR, 'vipdoc', market, 'lday', '%s%s.day' % (market, code))
    if not os.path.exists(fp):
        return []
    try:
        with open(fp, 'rb') as f:
            data = f.read()
    except:
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


# ═══ 交易日历 ═══
_holidays_cache = None

def _load_holidays():
    global _holidays_cache
    if _holidays_cache is not None:
        return _holidays_cache
    fp = STANDALONE_DIR / 'holidays.json'
    data = load_json(fp)
    _holidays_cache = set(data) if data else set()
    return _holidays_cache

def is_trading_day(dt=None):
    """判断是否为交易日（周末 + 法定假日）"""
    if dt is None:
        dt = datetime.now()
    if dt.weekday() >= 5:
        return False
    date_str = dt.strftime('%Y-%m-%d')
    return date_str not in _load_holidays()

def get_latest_trading_day(dt=None):
    """获取最近一个交易日"""
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
    seen = set()
    return [x for x in lst if not (x in seen or seen.add(x))]
