#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
平台适配层 — 统一管理不同交易软件的路径和存储机制

支持平台：
  - tdx    通达信（含券商定制版）  ✅ 已验证
  - eastmoney  东方财富            ⚠️ 逻辑完整，需真机测试
  - dzh    大智慧                ❌ 待开发

使用方法：
  from platforms import get_platform
  plat = get_platform()        # 自动检测
  plat = get_platform('eastmoney')  # 手动指定
  print(plat.name, plat.data_dir, plat.blocknew_dir)
"""
import os, sys, json
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = BASE_DIR / 'config.json'

# ═══════════════════════════════════════
#  平台定义
# ═══════════════════════════════════════

class PlatformBase:
    """平台基类"""
    def __init__(self, platform_id, name):
        self.id = platform_id      # 短标识: tdx / eastmoney / dzh
        self.name = name           # 中文名
        self._config = {}          # 配置缓存

    def load_config(self):
        """加载配置文件（含自动检测默认路径）"""
        if self._config:
            return self._config
        cfg = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            except:
                pass
        # 填充默认值
        defaults = self.default_config()
        for k, v in defaults.items():
            cfg.setdefault(k, v)
        self._config = cfg
        return cfg

    def save_config(self, cfg):
        """保存配置"""
        tmp = str(CONFIG_FILE) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(CONFIG_FILE))
        self._config = cfg

    def default_config(self):
        """各平台覆盖此方法返回默认配置"""
        raise NotImplementedError

    def detect_path(self):
        """自动检测安装路径，返回路径或None"""
        raise NotImplementedError

    @property
    def data_dir(self):
        """数据目录"""
        raise NotImplementedError

    @property
    def blocknew_dir(self):
        """板块文件目录"""
        raise NotImplementedError

    def get_gridtab_path(self):
        """标签栏配置路径"""
        raise NotImplementedError

    def get_tdx_exe_path(self):
        """主程序路径"""
        raise NotImplementedError


class TdxPlatform(PlatformBase):
    """通达信平台"""
    def __init__(self):
        super().__init__('tdx', '通达信')

    def default_config(self):
        return {
            "platform": "tdx",
            "tdxDir": "C:/new_tdx",
            "blocknewDir": "C:/new_tdx/T0002/blocknew",
            "enableWechat": False,
            "wechatKey": "",
        }

    def detect_path(self):
        """自动检测通达信安装路径"""
        candidates = [
            "C:/new_tdx", "D:/new_tdx", "E:/new_tdx",
            "C:/TdxW", "D:/TdxW",
        ]
        # 尝试注册表
        try:
            import winreg
            for root in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                try:
                    key = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
                    for i in range(100):
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            sub = winreg.OpenKey(key, subkey_name)
                            name = winreg.QueryValueEx(sub, "DisplayName")[0]
                            if "通达信" in name or "Tdx" in name or "TongDaXin" in name:
                                path = winreg.QueryValueEx(sub, "InstallLocation")[0]
                                if path and os.path.exists(path):
                                    return path
                        except:
                            pass
                except:
                    pass
        except:
            pass
        # 找TdxW.exe
        for p in candidates:
            if os.path.exists(os.path.join(p, "TdxW.exe")):
                return p
        return None

    @property
    def data_dir(self):
        cfg = self.load_config()
        tdx = cfg.get('tdxDir', 'C:/new_tdx')
        return tdx

    @property
    def blocknew_dir(self):
        cfg = self.load_config()
        return cfg.get('blocknewDir', os.path.join(cfg.get('tdxDir', 'C:/new_tdx'), 'T0002', 'blocknew'))

    def get_gridtab_path(self):
        return os.path.join(self.data_dir, 'T0002', 'gridtab.dat')

    def get_tdx_exe_path(self):
        return os.path.join(self.data_dir, 'TdxW.exe')


class EastMoneyPlatform(PlatformBase):
    """东方财富平台

    注意：东方财富的自选股/板块机制与通达信不同。
    本适配器实现了路径和基本结构的适配，但真机测试等待实机环境验证。
    """
    def __init__(self):
        super().__init__('eastmoney', '东方财富')

    def default_config(self):
        return {
            "platform": "eastmoney",
            "eastmoneyDir": "E:/东方财富",
            "blocknewDir": "",  # 运行时自动拼接
            "enableWechat": False,
            "wechatKey": "",
        }

    def detect_path(self):
        """自动检测东方财富安装路径"""
        candidates = [
            "E:/东方财富", "D:/东方财富", "C:/东方财富",
            "C:/Program Files/东方财富",
            "C:/Program Files (x86)/东方财富",
        ]
        # 找主程序
        for p in candidates:
            exe = os.path.join(p, "eastmoney.exe")
            if os.path.exists(exe):
                return p
            # 也可能是其他名称
            for fname in ["eastmoney", "EM", "dfcf", "东方财富"]:
                for ext in [".exe", ".EXE"]:
                    exe = os.path.join(p, fname + ext)
                    if os.path.exists(exe):
                        return p
        return None

    @property
    def data_dir(self):
        cfg = self.load_config()
        return cfg.get('eastmoneyDir', 'E:/东方财富')

    @property
    def blocknew_dir(self):
        """
        东方财富的自选股数据目录（非通达信T0002/blocknew格式）
        需要根据实际安装版本确认路径：
        - 老版本: 安装目录/jydata/自选股/
        - 新版本: 安装目录/user/
        """
        cfg = self.load_config()
        cached = cfg.get('blocknewDir', '')
        if cached:
            return cached
        base = cfg.get('eastmoneyDir', 'E:/东方财富')
        # 尝试多种可能的目录
        candidates = [
            os.path.join(base, 'jydata', '自选股'),
            os.path.join(base, 'jyqy'),
            os.path.join(base, 'block'),
            base,
        ]
        for p in candidates:
            if os.path.exists(p):
                config['blocknewDir'] = p if False else None
                return p
        return base

    def get_gridtab_path(self):
        """东方财富无gridtab概念，返回自选股配置文件"""
        return os.path.join(self.data_dir, 'jyqy', 'mystock.cfg')

    def get_tdx_exe_path(self):
        cfg = self.load_config()
        base = cfg.get('eastmoneyDir', 'E:/东方财富')
        for fname in ["eastmoney.exe", "EM.exe", "dfcf.exe", "东方财富.exe"]:
            exe = os.path.join(base, fname)
            if os.path.exists(exe):
                return exe
        return os.path.join(base, "eastmoney.exe")


class DzhPlatform(PlatformBase):
    """大智慧平台（待开发）"""
    def __init__(self):
        super().__init__('dzh', '大智慧')

    def default_config(self):
        return {
            "platform": "dzh",
            "dzhDir": "C:/dzh",
            "enableWechat": False,
            "wechatKey": "",
        }

    def detect_path(self):
        return None

    @property
    def data_dir(self):
        cfg = self.load_config()
        return cfg.get('dzhDir', 'C:/dzh')

    @property
    def blocknew_dir(self):
        return os.path.join(self.data_dir, 'USERDATA', 'block')

    def get_gridtab_path(self):
        return os.path.join(self.data_dir, 'USERDATA', 'block.cfg')

    def get_tdx_exe_path(self):
        return os.path.join(self.data_dir, 'dzh.exe')


# ═══════════════════════════════════════
#  平台注册表
# ═══════════════════════════════════════

_PLATFORMS = {
    'tdx': TdxPlatform(),
    'eastmoney': EastMoneyPlatform(),
    'dzh': DzhPlatform(),
}


def get_platform(platform_id=None):
    """
    获取平台实例。

    Args:
        platform_id: 'tdx' / 'eastmoney' / 'dzh' / None（自动检测）

    Returns:
        PlatformBase 子类实例
    """
    if platform_id:
        plat = _PLATFORMS.get(platform_id)
        if plat:
            return plat
        raise ValueError("未知平台: %s，可选: tdx, eastmoney, dzh" % platform_id)

    # 自动检测
    # 1. 先读config.json中的配置
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            pid = cfg.get('platform', '')
            if pid in _PLATFORMS:
                return _PLATFORMS[pid]
        except:
            pass

    # 2. 按优先级自动检测
    for pid in ['tdx', 'eastmoney', 'dzh']:
        plat = _PLATFORMS[pid]
        path = plat.detect_path()
        if path:
            return plat

    # 3. 默认通达信
    return _PLATFORMS['tdx']


def list_platforms():
    """列出所有支持的平台"""
    return {pid: plat.name for pid, plat in _PLATFORMS.items()}
