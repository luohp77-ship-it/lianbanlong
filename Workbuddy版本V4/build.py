#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连板龙 V3.0.0 PyInstaller 打包脚本

用法:
  python build.py          打包为 exe
  python build.py --clean  清理构建文件
"""
import os
import sys
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
DIST_DIR = BASE_DIR.parent / 'dist'

BUILD_FILES = [
    'app.py', 'engine.py', 'utils.py', 'concept_analyzer.py',
    'install.py', 'report.py', 'config.json', 'version.json',
]
STANDALONE_FILES = [
    'standalone/concept_map.json',
    'standalone/holidays.json',
]


def clean():
    """清理构建产物。"""
    for d in [DIST_DIR, BASE_DIR / 'build', BASE_DIR / '__pycache__']:
        if os.path.exists(d):
            shutil.rmtree(d)
    for spec in BASE_DIR.glob('*.spec'):
        spec.unlink()
    print('清理完成')


def build():
    """PyInstaller 打包。"""
    try:
        import PyInstaller.__main__
    except ImportError:
        print('正在安装 PyInstaller...')
        import subprocess
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyinstaller'])
        import PyInstaller.__main__

    os.makedirs(DIST_DIR, exist_ok=True)

    # 收集 standalone 文件
    standalone_src = BASE_DIR / 'standalone'
    standalone_dst = DIST_DIR / 'standalone'
    if standalone_dst.exists():
        shutil.rmtree(standalone_dst)
    shutil.copytree(standalone_src, standalone_dst)
    shutil.copy(BASE_DIR / 'config.json', DIST_DIR / 'config.json')

    # 打包 engine.exe（核心引擎，无 GUI）
    print('打包 engine.exe ...')
    PyInstaller.__main__.run([
        '--name=engine',
        '--onefile',
        '--console',
        '--distpath=' + str(DIST_DIR),
        '--workpath=' + str(BASE_DIR / 'build' / 'engine'),
        '--specpath=' + str(BASE_DIR / 'build'),
        '--add-data=%s;standalone' % str(standalone_src),
        '--hidden-import=engine',
        '--hidden-import=utils',
        '--hidden-import=concept_analyzer',
        '--hidden-import=license',
        '--hidden-import=word_report',
        str(BASE_DIR / 'engine.py'),
    ])

    # 打包 app.exe（桌面 APP）
    print('打包 app.exe ...')
    PyInstaller.__main__.run([
        '--name=连板龙V3',
        '--onefile',
        '--windowed',
        '--distpath=' + str(DIST_DIR),
        '--workpath=' + str(BASE_DIR / 'build' / 'app'),
        '--specpath=' + str(BASE_DIR / 'build'),
        '--add-data=%s;standalone' % str(standalone_src),
        '--hidden-import=tkinter',
        '--hidden-import=engine',
        '--hidden-import=utils',
        '--hidden-import=concept_analyzer',
        '--hidden-import=license',
        '--hidden-import=word_report',
        str(BASE_DIR / 'app.py'),
    ])

    # 打包 install.exe
    print('打包 install.exe ...')
    PyInstaller.__main__.run([
        '--name=install',
        '--onefile',
        '--console',
        '--distpath=' + str(DIST_DIR),
        '--workpath=' + str(BASE_DIR / 'build' / 'install'),
        '--specpath=' + str(BASE_DIR / 'build'),
        '--add-data=%s;standalone' % str(standalone_src),
        '--hidden-import=engine',
        '--hidden-import=utils',
        '--hidden-import=concept_analyzer',
        '--hidden-import=license',
        '--hidden-import=word_report',
        str(BASE_DIR / 'install.py'),
    ])

    # 复制依赖文件
    for f in ['config.json', 'version.json']:
        src = BASE_DIR / f
        if src.exists():
            shutil.copy(src, DIST_DIR / f)

    print('\n打包完成！输出目录: %s' % DIST_DIR)
    print('文件列表:')
    for f in sorted(DIST_DIR.iterdir()):
        if f.is_file():
            size_mb = f.stat().st_size / (1024 * 1024)
            print('  %s (%.1f MB)' % (f.name, size_mb))


if __name__ == '__main__':
    if '--clean' in sys.argv:
        clean()
    else:
        build()
