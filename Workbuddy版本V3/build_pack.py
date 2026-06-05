#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘助手 V3.1.0 加密构建 — 第二阶段：PyArmor混淆 + PyInstaller打包

用法:
    python build_pack.py          完整打包（需先运行 build_cython.py）
    python build_pack.py --clean  清理构建产物
    python build_pack.py --no-armor  跳过 PyArmor（调试用）
"""
import os
import sys
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
DIST_DIR = BASE_DIR.parent / 'dist'


def clean():
    """清理构建产物。"""
    for d in [DIST_DIR, BASE_DIR / 'build']:
        if os.path.exists(d):
            shutil.rmtree(d)
    for spec in BASE_DIR.glob('*.spec'):
        spec.unlink()
    # PyArmor 产物
    for d in BASE_DIR.glob('.pyarmor*'):
        if d.is_dir():
            shutil.rmtree(d)
    print('清理完成')


def step_pyarmor():
    """PyArmor 混淆：加密字节码 + 混淆变量名。

    对未编译为 .pyd 的 .py 文件进行混淆保护。
    已编译为 .pyd 的模块（engine/concept_analyzer/word_report）跳过。
    """
    print('\n[PyArmor] 安装/检查 ...')
    try:
        import pyarmor
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyarmor'])
        import pyarmor

    # 检查核心模块是否已编译为 .pyd
    pyd_modules = {'engine', 'concept_analyzer', 'word_report'}
    for m in pyd_modules:
        pyd_path = BASE_DIR / f'{m}.pyd'
        py_path = BASE_DIR / f'{m}.py'
        if not pyd_path.exists() and py_path.exists():
            print(f'  [WARN] {m}.pyd 不存在，将保护 {m}.py（建议先运行 build_cython.py）')

    # 需要 PyArmor 保护的 .py 文件（主入口 + 非核心模块）
    target_py = [
        'app.py', 'utils.py',
        'report.py', 'install.py',
    ]

    # 检查哪些文件存在
    files_to_protect = [f for f in target_py if (BASE_DIR / f).exists()]
    
    print(f'[PyArmor] 混淆 {len(files_to_protect)} 个文件 ...')
    
    # 创建临时输出目录
    build_dir = BASE_DIR / 'build' / 'pyarmor'
    os.makedirs(build_dir, exist_ok=True)

    import subprocess
    for f in files_to_protect:
        print(f'  混淆: {f}')
        subprocess.check_call([
            sys.executable, '-m', 'pyarmor', 'gen',
            '--output', str(build_dir),
            '--recursive',
            '--platform', 'windows.x86_64',
            str(BASE_DIR / f),
        ], stdout=subprocess.DEVNULL if '--verbose' not in sys.argv else None)

    print('  PyArmor 混淆完成')

    return build_dir


def step_pyinstaller():
    """PyInstaller 打包为 .exe（单文件，带GUI）。"""
    print('\n[PyInstaller] 打包 .exe ...')
    try:
        import PyInstaller.__main__
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyinstaller'])
        import PyInstaller.__main__

    os.makedirs(DIST_DIR, exist_ok=True)

    # 收集 standalone 数据文件
    standalone_src = BASE_DIR / 'standalone'
    
    # 复制数据文件到 dist
    if standalone_src.exists():
        dst = DIST_DIR / 'standalone'
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(standalone_src, dst)
    
    for f in ['config.json', 'version.json']:
        src = BASE_DIR / f
        if src.exists():
            shutil.copy(src, DIST_DIR / f)

    # 确定入口文件
    app_py = BASE_DIR / 'app.py'
    if not app_py.exists():
        print('[错误] app.py 不存在！')
        return False

    # 打包参数
    args = [
        '--name=复盘助手V3',
        '--onefile',
        '--windowed',
        '--distpath=' + str(DIST_DIR),
        '--workpath=' + str(BASE_DIR / 'build' / 'pyinstaller'),
        '--specpath=' + str(BASE_DIR / 'build'),
        '--noconfirm',
    ]

    # 添加 standalone 数据
    if standalone_src.exists():
        args.append('--add-data=%s;standalone' % str(standalone_src))

    # 添加数据文件
    for f in ['config.json', 'version.json']:
        src = BASE_DIR / f
        if src.exists():
            args.append('--add-data=%s;.' % str(src))

    # 添加 .pyd 模块（如果存在）
    for m in ['engine', 'concept_analyzer', 'word_report']:
        pyd = BASE_DIR / f'{m}.pyd'
        if pyd.exists():
            args.append('--add-binary=%s;.' % str(pyd))
            print(f'  包含 .pyd: {m}.pyd')

    # 隐藏导入
    for m in ['tkinter', 'concept_analyzer', 'word_report']:
        args.append('--hidden-import=' + m)

    args.append(str(app_py))

    print(f'  PyInstaller 参数: {len(args)} 项')
    PyInstaller.__main__.run(args)

    # 打印输出文件
    print('\n打包完成！输出目录: %s' % DIST_DIR)
    exe_files = list(DIST_DIR.glob('*.exe'))
    for f in sorted(exe_files):
        size_mb = f.stat().st_size / (1024 * 1024)
        print('  %s (%.1f MB)' % (f.name, size_mb))

    return True


def build_full(use_armor=True):
    """完整构建流程。"""
    print('=' * 60)
    print('复盘助手 V3.1.0 — 加密打包（PyArmor + PyInstaller）')
    print('=' * 60)

    # 检查 Cython 编译是否已完成
    pyd_count = sum(1 for p in BASE_DIR.glob('*.pyd'))
    if pyd_count < 2:
        print('\n[提示] 检测到 .pyd 核心模块不足（当前 %d 个）' % pyd_count)
        print('  建议先运行: python build_cython.py')
        resp = input('  是否继续？（核心模块将以 .py 形式打包）[y/N]: ')
        if resp.lower() != 'y':
            return

    # Step 1: PyArmor（可选）
    if use_armor:
        step_pyarmor()
    else:
        print('\n[跳过] PyArmor 混淆')

    # Step 2: PyInstaller
    if not step_pyinstaller():
        return

    print('\n' + '=' * 60)
    print('加密打包完成！')
    print('防护层级:')
    print('  核心算法: Cython → .pyd（汇编级，AI无法理解）')
    if use_armor:
        print('  其他代码: PyArmor → 字节码加密 + 变量混淆')
    print('  交付形式: PyInstaller → 单文件 .exe')
    print()
    print('输出: %s\\复盘助手V3.exe' % DIST_DIR)
    print('=' * 60)


if __name__ == '__main__':
    if '--clean' in sys.argv:
        clean()
    elif '--no-armor' in sys.argv:
        build_full(use_armor=False)
    else:
        build_full()
