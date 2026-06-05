#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘助手安装器 — 调用 engine.install_blocks() 统一板块定义

用法：双击运行，或 python install.py
"""
import os, sys, json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from utils import load_config, save_config, log
from engine import install_blocks

print("=" * 60)
print("通达信涨停板块复盘 - 安装器 V3")
print("=" * 60)

# ── 1. 查找/确认通达信路径 ──
print("\n【步骤1】查找通达信安装路径...")

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
                except:
                    pass
        except:
            pass
except:
    pass

tdx_path = None
for p in tdx_paths:
    if os.path.exists(p):
        tdx_path = p
        break

if not tdx_path:
    tdx_path = input("  未找到通达信，请手动输入安装路径（如 C:\\new_tdx）: ").strip()
    if not os.path.exists(tdx_path):
        print("  路径不存在！")
        input("按回车退出...")
        sys.exit(1)

print(f"  通达信路径: {tdx_path}")

# ── 2. 配置保存 ──
cfg = load_config()
cfg['tdxDir'] = tdx_path
cfg['blocknewDir'] = os.path.join(tdx_path, 'T0002', 'blocknew')
save_config(cfg)

# ── 3. 安装板块 ──
print("\n【步骤2】安装板块...")
install_blocks(cfg, callback=lambda m: print('  ' + m))

# ── 4. 创建定时任务 ──
print("\n【步骤3】创建定时任务...")
try:
    import subprocess
    python_exe = sys.executable

    task_name = "TongDaXin_ZT_Update"
    app_py = os.path.join(BASE_DIR, 'app.py')

    ps_script = f"""
$action = New-ScheduledTaskAction -Execute "{python_exe}" -Argument "\\"{app_py}\\" --update"
$trigger = New-ScheduledTaskTrigger -Daily -At "16:30"
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
        print(f"  定时任务已创建: {task_name}")
        print(f"  每天 16:30 自动运行")
    else:
        print(f"  定时任务创建失败: {result.stderr[:200]}")

    try: os.remove(ps_file)
    except: pass

except Exception as e:
    print(f"  定时任务创建异常: {e}")

# ── 5. 首次运行 ──
print("\n【步骤4】首次运行数据更新...")
from engine import run_engine
logs = run_engine(cfg)
for l in logs:
    print('  ' + l)

print("\n" + "=" * 60)
print("安装完成！重启通达信查看板块")
print("=" * 60)
input("\n按回车退出...")
