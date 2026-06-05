#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘助手 V2.1.2 安装器
1. 15个板块（移除晋级率ZJL）
2. 概念分析t:3→t:2降级
3. 自定义数据列设置

用法：双击运行，或 python install.py
"""
import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from utils import load_config, save_config, log, set_column_green
from engine import install_blocks


def find_tdx_path():
    """查找通达信安装路径。"""
    tdx_paths = [
        r"C:\new_tdx", r"D:\new_tdx", r"E:\new_tdx",
    ]

    try:
        import winreg
        for root in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
            try:
                key = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
                for i in range(100):
                    try:
                        subkey = winreg.EnumKey(key, i)
                        sub = winreg.OpenKey(key, subkey)
                        name = winreg.QueryValueEx(sub, "DisplayName")[0]
                        if "通达信" in name or "TongDaXin" in name:
                            path = winreg.QueryValueEx(sub, "InstallLocation")[0]
                            tdx_paths.insert(0, path)
                            break
                    except OSError:
                        pass
            except OSError:
                pass
    except ImportError:
        pass

    for p in tdx_paths:
        if os.path.exists(p):
            return p
    return None


def install():
    """执行完整安装流程。"""
    print("=" * 60)
    print("通达信涨停板块复盘 - 安装器 V2")
    print("=" * 60)

    # ── 1. 查找/确认通达信路径 ──
    print("\n【步骤1】查找通达信安装路径...")

    tdx_path = find_tdx_path()

    if not tdx_path:
        tdx_path = input("  未找到通达信，请手动输入安装路径（如 C:\\new_tdx）: ").strip()
        if not os.path.exists(tdx_path):
            print("  路径不存在！")
            input("按回车退出...")
            sys.exit(1)

    print("  通达信路径: %s" % tdx_path)

    # ── 2. 配置保存 ──
    cfg = load_config()
    cfg['tdxDir'] = tdx_path
    cfg['blocknewDir'] = os.path.join(tdx_path, 'T0002', 'blocknew')
    save_config(cfg)

    # ── 3. 安装板块 ──
    print("\n【步骤2】安装板块(V2: 15个)...")
    install_blocks(cfg, callback=lambda m: print('  ' + m))

    # ── 3b. 设置自定义数据列颜色 ──
    print("\n【步骤2b】设置自定义数据列颜色...")
    if set_column_green(tdx_path):
        print("  自定义列(涨停原因/所属概念)颜色已设为荧光绿")
        print("  重启通达信后生效")
    else:
        print("  提示: 需先在通达信中创建自定义数据列，颜色才能设置")
        print("  （首次运行复盘后自动再尝试）")

    # ── 4. 创建定时任务 ──
    print("\n【步骤3】创建定时任务(18:30)...")
    try:
        import subprocess
        python_exe = sys.executable

        task_name = "TongDaXin_ZT_Update"
        app_py = os.path.join(BASE_DIR, 'app.py')

        ps_script = f"""
$action = New-ScheduledTaskAction -Execute "{python_exe}" -Argument "\\"{app_py}\\" --update"
$trigger = New-ScheduledTaskTrigger -Daily -At "18:30"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType Interactive
Register-ScheduledTask -TaskName "{task_name}" -Action $action -Trigger $trigger -Principal $principal -Force
"""
        ps_file = os.path.join(os.environ["TEMP"], "fupan_create_task.ps1")
        with open(ps_file, "w", encoding="utf-8") as f:
            f.write(ps_script)

        result = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", ps_file],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            print("  定时任务已创建: %s" % task_name)
            print("  每天 18:30 自动运行")
        else:
            print("  定时任务创建失败: %s" % result.stderr[:200])

        try:
            os.remove(ps_file)
        except OSError:
            pass

    except Exception as e:
        print("  定时任务创建异常: %s" % e)

    # ── 5. 首次运行 ──
    print("\n【步骤4】首次运行数据更新...")
    from engine import run_engine
    logs = run_engine(cfg)
    for l in logs:
        print('  ' + l)

    print("\n" + "=" * 60)
    print("安装完成！")
    print("1. 重启通达信")
    print("2. 在板块列表中右键→刷新，即可看到自定义板块")
    print("3. 如未看到板块，请确认通达信路径正确")
    print("")
    print("【自定义数据列设置】（显示涨停原因和所属概念）")
    print("  功能 → 公式系统 → 自定义数据管理器")
    print("    - 新建 数据号:1 名称:涨停原因")
    print("    - 新建 数据号:2 名称:所属概念")
    print("  行情列表表头右键 → 选择自定义数据 → 勾选两列")
    print("  （每次复盘自动写入数据，无需手动刷新）")
    print("=" * 60)


if __name__ == '__main__':
    install()
    input("\n按回车退出...")
