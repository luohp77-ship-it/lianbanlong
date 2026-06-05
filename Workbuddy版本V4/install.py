#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连板龙 V4.0.1 安装器
1. 17个板块（含R20D/R60D）
2. V4综合评分概念分析
3. 固定路径安装 + 桌面快捷方式
4. 自定义数据列设置

用法：双击运行，或 python install.py
"""
import os
import sys
import json
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from utils import load_config, save_config, log, set_column_green
from engine import install_blocks


INSTALL_DIR = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
                           '连板龙')


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


def install_to_fixed_path():
    """将程序安装/复制到固定目录。

    返回安装后的 app.py 路径（或 exe 路径）。
    """
    # 判断是否在 PyInstaller 打包的 exe 中运行
    is_bundled = getattr(sys, 'frozen', False)

    if is_bundled:
        # PyInstaller onefile 模式：复制 exe 到固定目录
        src_exe = sys.executable
        dst_exe = os.path.join(INSTALL_DIR, os.path.basename(src_exe))
        os.makedirs(INSTALL_DIR, exist_ok=True)
        if os.path.abspath(src_exe) != os.path.abspath(dst_exe):
            shutil.copy2(src_exe, dst_exe)
            print("  已复制到: %s" % dst_exe)
        return dst_exe
    else:
        # 源码模式：在 INSTALL_DIR 创建快捷方式/app.py 引用
        os.makedirs(INSTALL_DIR, exist_ok=True)
        # 复制必要的配置文件到安装目录
        for fn in ['config.json']:
            src = os.path.join(BASE_DIR, fn)
            dst = os.path.join(INSTALL_DIR, fn)
            if os.path.exists(src) and (not os.path.exists(dst) or
                                         os.path.getmtime(src) > os.path.getmtime(dst)):
                shutil.copy2(src, dst)
        # 创建 install_path.txt 让 app.py 知道源码位置
        with open(os.path.join(INSTALL_DIR, 'source_path.txt'), 'w') as f:
            f.write(BASE_DIR)
        print("  安装目录: %s" % INSTALL_DIR)
        return os.path.join(BASE_DIR, 'app.py')


def create_shortcut(target_path):
    """创建桌面快捷方式。"""
    try:
        import subprocess
        desktop = os.path.join(os.environ['USERPROFILE'], 'Desktop')
        shortcut_path = os.path.join(desktop, '连板龙.lnk')

        # 使用 PowerShell 创建快捷方式
        ps_script = '''
$ws = New-Object -ComObject WScript.Shell
$shortcut = $ws.CreateShortcut("%s")
$shortcut.TargetPath = "%s"
$shortcut.Description = "连板龙 V4.0.1 - 通达信自动复盘"
$shortcut.WorkingDirectory = "%s"
$shortcut.Save()
''' % (shortcut_path, target_path, INSTALL_DIR)

        ps_file = os.path.join(os.environ.get('TEMP', '/tmp'), 'fupan_shortcut.ps1')
        with open(ps_file, 'w', encoding='utf-8') as f:
            f.write(ps_script)

        subprocess.run(['powershell', '-ExecutionPolicy', 'Bypass', '-File', ps_file],
                       capture_output=True, timeout=30, shell=True)
        try:
            os.remove(ps_file)
        except OSError:
            pass
        print("  桌面快捷方式已创建: %s" % shortcut_path)
        return True
    except Exception as e:
        print("  桌面快捷方式创建失败: %s" % e)
        return False


def setup_scheduled_task(app_path):
    """创建或更新定时任务（每天 18:00）。"""
    try:
        import subprocess

        # 判断入口：是 exe 还是 python + app.py
        is_exe = app_path.lower().endswith('.exe')
        if is_exe:
            execute = app_path
            argument = '--update'
        else:
            execute = sys.executable  # python.exe
            argument = '"%s" --update' % app_path

        task_name = '连板龙_数据更新'

        ps_script = '''
$action = New-ScheduledTaskAction -Execute "{execute}" -Argument "{argument}"
$trigger = New-ScheduledTaskTrigger -Daily -At "18:00"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType Interactive
try {{
    Unregister-ScheduledTask -TaskName "{task_name}" -Confirm:$false -ErrorAction SilentlyContinue
}} catch {{}}
Register-ScheduledTask -TaskName "{task_name}" -Action $action -Trigger $trigger -Principal $principal -Force
'''.format(execute=execute, argument=argument, task_name=task_name)

        ps_file = os.path.join(os.environ.get('TEMP', '/tmp'), 'fupan_create_task.ps1')
        with open(ps_file, 'w', encoding='utf-8') as f:
            f.write(ps_script)

        result = subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-File', ps_file],
            capture_output=True, text=True, timeout=30, shell=True
        )
        if result.returncode == 0:
            print("  定时任务已创建: %s" % task_name)
            print("  每天 18:00 自动运行")
        else:
            print("  定时任务创建失败: %s" % result.stderr[:200])

        try:
            os.remove(ps_file)
        except OSError:
            pass

    except Exception as e:
        print("  定时任务创建异常: %s" % e)


def install():
    """执行完整安装流程。"""
    print("=" * 60)
    print("连板龙 V4.0.1 安装器")
    print("=" * 60)

    # ── 1. 固定路径安装 ──
    print("\n【步骤1】安装到固定目录...")
    app_path = install_to_fixed_path()

    # ── 2. 桌面快捷方式 ──
    print("\n【步骤2】创建桌面快捷方式...")
    create_shortcut(app_path)

    # ── 3. 查找通达信路径 ──
    print("\n【步骤3】查找通达信安装路径...")
    tdx_path = find_tdx_path()
    if not tdx_path:
        tdx_path = input("  未找到通达信，请手动输入安装路径（如 C:\\new_tdx）: ").strip()
        if not os.path.exists(tdx_path):
            print("  路径不存在！")
            input("按回车退出...")
            sys.exit(1)
    print("  通达信路径: %s" % tdx_path)

    # ── 4. 配置保存 ──
    cfg = load_config()
    cfg['tdxDir'] = tdx_path
    cfg['blocknewDir'] = os.path.join(tdx_path, 'T0002', 'blocknew')
    save_config(cfg)

    # ── 5. 安装板块 ──
    print("\n【步骤4】安装板块(V4: 17个)...")
    install_blocks(cfg, callback=lambda m: print('  ' + m))

    # ── 6. 设置自定义数据列颜色 ──
    print("\n【步骤5】设置自定义数据列颜色...")
    if set_column_green(tdx_path):
        print("  自定义列(涨停原因/所属概念/龙虎榜)颜色已设为浅灰")
        print("  重启通达信后生效")
    else:
        print("  提示: 需先在通达信中创建自定义数据列，颜色才能设置")

    # ── 7. 创建定时任务（指向固定路径） ──
    print("\n【步骤6】创建定时任务(18:00)...")
    setup_scheduled_task(app_path)

    # ── 8. 首次运行 ──
    print("\n【步骤7】首次运行数据更新...")
    from engine import run_engine
    logs = run_engine(cfg)
    for l in logs:
        print('  ' + l)

    # ── 9. 激活 ──
    print("\n【步骤8】授权激活...")
    from license import LicenseManager
    lm = LicenseManager()
    lic = lm.verify()
    if not lic.get('valid', False):
        if lic['type'] == 'none':
            trial = lm.start_trial(30)
            print('  30日试用已激活，到期日: %s' % trial['expires_at'])
        else:
            print('  当前状态: %s' % lic['message'])
            print('  如需购买正式版，请访问用户中心')
    else:
        print('  授权状态: %s，剩余 %d 天' % (lic['type'], lic['days_left']))

    print("\n" + "=" * 60)
    print("安装完成！")
    print("1. 重启通达信")
    print("2. 在板块列表中右键→刷新，即可看到自定义板块")
    print("3. 桌面快捷方式: 连板龙")
    print("4. 每日 18:00 自动更新")
    print("")
    print("【自定义数据列设置】（显示涨停原因、所属概念和龙虎榜）")
    print("  行情列表表头右键 → 选择自定义数据 → 勾选数据号1/2/3")
    print("=" * 60)


def uninstall():
    """卸载：删除安装目录 + 快捷方式 + 定时任务。"""
    print("卸载连板龙 V4.0.1...")
    try:
        import subprocess
        # 删除定时任务
        subprocess.run(['schtasks', '/Delete', '/TN', '连板龙_数据更新', '/F'],
                       capture_output=True, timeout=10, shell=True)
        print("  [√] 定时任务已删除")
    except Exception:
        pass

    # 删除安装目录
    if os.path.exists(INSTALL_DIR):
        try:
            shutil.rmtree(INSTALL_DIR)
            print("  [√] 安装目录已删除: %s" % INSTALL_DIR)
        except Exception as e:
            print("  [×] 删除安装目录失败: %s" % e)

    # 删除快捷方式
    try:
        desktop = os.path.join(os.environ['USERPROFILE'], 'Desktop')
        for lnk in ['连板龙.lnk', '连板龙.lnk']:
            p = os.path.join(desktop, lnk)
            if os.path.exists(p):
                os.remove(p)
                print("  [√] 快捷方式已删除: %s" % p)
    except Exception:
        pass

    print("卸载完成，按回车退出...")


if __name__ == '__main__':
    if '--uninstall' in sys.argv:
        uninstall()
    else:
        install()
        input("\n按回车退出...")
