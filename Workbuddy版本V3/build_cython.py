#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘助手 V3.1.0 加密构建 — 第一阶段：Cython 编译核心模块为 .pyd

将核心算法编译为 C 扩展（.pyd），反编译后为汇编代码，AI 无法理解。

用法:
    python build_cython.py          编译核心模块
    python build_cython.py --clean  清理 .c / .pyd / __pycache__
"""
import os
import sys
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()

# 需要 Cython 编译的核心模块（保护算法逻辑）
CYTHON_MODULES = [
    'engine.py',            # 复盘引擎核心逻辑
    'concept_analyzer.py',  # 概念分析算法
    'word_report.py',       # Word报告生成逻辑
]


def check_cython():
    """检查 Cython 是否安装，未安装则自动安装。"""
    try:
        from Cython.Build import cythonize
        return True
    except ImportError:
        print('[安装] Cython ...')
        import subprocess
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'cython'])
        return True


def check_compiler():
    """检查 C 编译器是否可用。"""
    try:
        import distutils.ccompiler
        cc = distutils.ccompiler.new_compiler()
        return True
    except Exception:
        print('[错误] 未找到 C 编译器！')
        print('  - Visual Studio: 安装 "Desktop development with C++" 工作负载')
        print('  - 或安装 Microsoft Visual C++ Build Tools:')
        print('    https://visualstudio.microsoft.com/visual-cpp-build-tools/')
        return False


def compile_module(py_file):
    """将单个 .py 文件编译为 .pyd。
    
    流程：.py → Cython → .c → C编译器 → .pyd
    编译产物（.py → .c + .pyd）保存在模块所在目录。
    """
    from Cython.Build import cythonize
    from distutils.core import setup, Extension

    module_name = py_file.stem
    print(f'  [Cython] {py_file.name} → {module_name}.pyd ...', end=' ')

    # 编译为 C 扩展
    ext = Extension(
        module_name,
        sources=[str(py_file)],
    )

    # 临时切换到模块目录进行编译
    old_cwd = os.getcwd()
    os.chdir(str(py_file.parent))
    try:
        cythonize(
            [ext],
            compiler_directives={
                'language_level': 3,
                'boundscheck': False,
                'wraparound': False,
            },
            quiet=True,
        )
        setup(
            ext_modules=[ext],
            script_args=['build_ext', '--inplace'],
        )
        print('OK')
    finally:
        os.chdir(old_cwd)

    # 验证 .pyd 已生成
    pyd_path = py_file.parent / f'{module_name}.cp*-win_amd64.pyd'
    pyd_files = list(py_file.parent.glob(f'{module_name}.cp*-win_amd64.pyd'))
    if not pyd_files:
        pyd_files = list(py_file.parent.glob(f'{module_name}.*.pyd'))
    
    if pyd_files:
        # 重命名为统一的 .pyd 文件名
        target = py_file.parent / f'{module_name}.pyd'
        if pyd_files[0] != target:
            shutil.move(str(pyd_files[0]), str(target))
        print(f'    输出: {target.name} ({target.stat().st_size / 1024:.0f} KB)')


def clean():
    """清理编译产物。"""
    patterns = ['*.c', '*.pyd', '*.exp', '*.lib', '*.obj']
    for pat in patterns:
        for f in BASE_DIR.glob(pat):
            f.unlink()
            print(f'  删除: {f.name}')
    # 清理 build 目录
    build_dir = BASE_DIR / 'build'
    if build_dir.exists():
        shutil.rmtree(build_dir)
        print(f'  删除: build/')
    # 清理 __pycache__
    for cache in BASE_DIR.rglob('__pycache__'):
        shutil.rmtree(cache)
    print('清理完成')


def build_all():
    """编译所有核心模块。"""
    print('=' * 60)
    print('复盘助手 V3.1.0 — Cython 加密编译（核心算法 → .pyd）')
    print('=' * 60)

    if not check_cython():
        return False
    if not check_compiler():
        return False

    # 备份原始 .py 文件
    backup_dir = BASE_DIR / 'src_backup'
    os.makedirs(backup_dir, exist_ok=True)
    
    print('\n[备份] 原始 .py 文件 → src_backup/')
    for m in CYTHON_MODULES:
        src = BASE_DIR / m
        dst = backup_dir / m
        shutil.copy2(str(src), str(dst))
        print(f'  {m}')

    # 编译
    print('\n[编译] .py → .pyd')
    for m in CYTHON_MODULES:
        compile_module(BASE_DIR / m)

    print('\n' + '=' * 60)
    print('Cython 编译完成！')
    print(f'原始代码备份于: {backup_dir}')
    print()
    print('下一步: python build_pack.py   (PyArmor混淆 + PyInstaller打包)')
    print('=' * 60)
    return True


if __name__ == '__main__':
    if '--clean' in sys.argv:
        clean()
    else:
        build_all()
