#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连板龙 V4.0 - 通达信自动复盘桌面应用

V4.0变更（订阅制 + 授权系统）：
- 按年订阅制 + 30日试用（RSA签名验证）
- 用户中心扫码绑定微信（推送不再依赖 ServerChan）
- 固定路径安装 + 桌面快捷方式
- 清理废弃微信推送配置
- 更新频次调整为每日18:00单次

用法:
  python app.py           启动桌面APP
  python app.py --update  命令行静默更新
  python app.py --install 命令行安装板块
  python app.py --activate LICENSE-XXXX-XXXX-XXXX-XXXX  激活
"""
import sys
import os
import json
import subprocess
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext
except ImportError:
    tk = None

BASE_DIR = Path(__file__).parent.resolve()
from utils import load_config, save_config, log, DATA_DIR, LOG_DIR, clean_old_logs
from engine import run_engine, install_blocks
from license import LicenseManager, ActivationError


class FupanApp:
    """连板龙 V4.0 桌面应用。"""

    def __init__(self):
        self.config = load_config()
        self.lm = LicenseManager()
        self.root = tk.Tk()
        self.root.title('连板龙 V4 - 通达信自动复盘')
        self.root.geometry('720x580')
        self.root.minsize(640, 500)
        self.root.configure(bg='#f0f2f5')
        self.running = False
        self._build_ui()
        self._license_init()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    def _build_ui(self):
        """构建UI界面。"""
        # 顶部标题栏
        hdr = tk.Frame(self.root, bg='#1a1a2e', height=50)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)
        tk.Label(hdr, text='连板龙 V4',
                 font=('Microsoft YaHei', 16, 'bold'),
                 fg='#e94560', bg='#1a1a2e').pack(side='left', padx=20)
        # 授权状态标签
        self.license_label = tk.Label(hdr, text='',
                                      font=('Microsoft YaHei', 9),
                                      fg='#8b949e', bg='#1a1a2e')
        self.license_label.pack(side='left', padx=5)
        self.status = tk.Label(hdr, text='就绪',
                               font=('Microsoft YaHei', 10),
                               fg='#8b949e', bg='#1a1a2e')
        self.status.pack(side='right', padx=20)

        # 主区域
        main = tk.Frame(self.root, bg='#f0f2f5')
        main.pack(fill='both', expand=True, padx=16, pady=10)

        # 数据卡片（V5：8个卡片）
        cards = tk.Frame(main, bg='#f0f2f5')
        cards.pack(fill='x', pady=(0, 10))
        self.mv = {}
        card_defs = [
            ('涨停', 'up', '#f85149'),
            ('跌停', 'down', '#58a6ff'),
            ('最高板', 'max', '#e3b341'),
            ('曾涨停', 'zb', '#da3633'),
            ('龙虎榜', 'lhb', '#3fb950'),
            ('晋级率', 'promo', '#ff6b6b'),
            ('溢价率', 'premium', '#ffd93d'),
            ('炸板率', 'zhaban', '#6bcb77'),
        ]
        for txt, key, color in card_defs:
            c = tk.Frame(cards, bg='white', highlightbackground='#ddd',
                         highlightthickness=1, padx=6, pady=4)
            c.pack(side='left', expand=True, fill='x', padx=2)
            tk.Label(c, text=txt, font=('Microsoft YaHei', 8), fg='#666').pack()
            v = tk.Label(c, text='-', font=('Microsoft YaHei', 16, 'bold'), fg=color)
            v.pack()
            self.mv[key] = v

        # 日志区
        lf = tk.LabelFrame(main, text='运行日志',
                           font=('Microsoft YaHei', 10),
                           bg='#f0f2f5', padx=8, pady=8)
        lf.pack(fill='both', expand=True)
        self.logbox = scrolledtext.ScrolledText(
            lf, height=12, font=('Consolas', 10),
            bg='#1a1a2e', fg='#e0e0e0', insertbackground='white',
            relief='flat', borderwidth=0)
        self.logbox.pack(fill='both', expand=True)

        # 按钮栏
        btns = tk.Frame(main, bg='#f0f2f5')
        btns.pack(fill='x', pady=(10, 0))
        self.btn_update = self._mkbtn(btns, '立即更新', '#238636', self._cmd_update)
        self.btn_update.pack(side='left', padx=3)
        self._mkbtn(btns, '安装板块', '#0f3460', self._cmd_install).pack(side='left', padx=3)
        self._mkbtn(btns, '激活', '#e94560', self._open_activate).pack(side='left', padx=3)
        self._mkbtn(btns, '设置', '#21262d', self._open_settings).pack(side='right', padx=3)
        self._mkbtn(btns, '打开通达信', '#e94560', self._open_tdx).pack(side='right', padx=3)

    def _mkbtn(self, parent, text, color, cmd):
        """创建按钮。"""
        return tk.Button(parent, text=text,
                         font=('Microsoft YaHei', 11, 'bold'),
                         bg=color, fg='white', padx=14, pady=7,
                         bd=0, cursor='hand2', command=cmd)

    def _rlog(self, msg):
        """日志输出到文本框。"""
        self.logbox.insert('end', msg + '\n')
        self.logbox.see('end')

    def _open_tdx(self):
        """打开通达信。"""
        exe = os.path.join(self.config.get('tdxDir', 'C:\\new_tdx'), 'TdxW.exe')
        if os.path.exists(exe):
            subprocess.Popen([exe])
        else:
            messagebox.showerror('错误', '未找到通达信: ' + exe)

    def _cmd_update(self):
        """执行更新。"""
        if self.running:
            return
        self.running = True
        self.btn_update.config(state='disabled', text='更新中...')
        self.logbox.delete('1.0', 'end')
        self.status.config(text='更新中...', fg='#e3b341')

        def worker():
            try:
                cfg = load_config()
                run_engine(cfg, callback=lambda m: self.root.after(0, lambda: self._rlog(m)))
                self.root.after(0, self._refresh)
                self.root.after(0, lambda: self.status.config(text='完成', fg='#3fb950'))
            except Exception as e:
                self.root.after(0, lambda: self._rlog('[错误] 更新异常: %s' % str(e)))
                self.root.after(0, lambda: self.status.config(text='失败', fg='#f85149'))
            finally:
                self.root.after(0, lambda: self.btn_update.config(state='normal', text='立即更新'))
                self.root.after(0, lambda: setattr(self, 'running', False))

        threading.Thread(target=worker, daemon=True).start()

    def _license_init(self):
        """检查授权状态，未激活则开始试用或提示激活。"""
        result = self.lm.verify()
        if result['valid']:
            self._update_license_label()
            return

        # 首次运行：自动创建30天试用
        if result['type'] == 'none':
            trial = self.lm.start_trial(30)
            self._rlog('[授权] 30日试用已激活，到期日: %s' % trial['expires_at'])
            self._update_license_label()
            return

        # 已过期
        self._rlog('[授权] %s' % result['message'])
        self._update_license_label()
        if result['days_left'] < -7:
            self._rlog('[授权] 授权已过期超过7天，请续费')

    def _update_license_label(self):
        """更新顶部授权状态显示。"""
        result = self.lm.verify()
        if result['valid']:
            days = result['days_left']
            lic_type = '试用' if result['type'] == 'trial' else '正式'
            color = '#e3b341' if result['type'] == 'trial' else '#3fb950'
            text = '%s·剩%d天' % (lic_type, days)
            if days <= 7:
                color = '#f85149'
                text = '%s·剩%d天(将到期)' % (lic_type, days)
            self.license_label.config(text=text, fg=color)
        else:
            self.license_label.config(text='未激活', fg='#f85149')

    def _open_activate(self):
        """打开激活对话框。"""
        win = tk.Toplevel(self.root)
        win.title('激活')
        win.geometry('480x240')
        win.configure(bg='#f0f2f5')
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        f = tk.Frame(win, bg='white', padx=24, pady=20,
                     highlightbackground='#ddd', highlightthickness=1)
        f.pack(fill='both', expand=True, padx=16, pady=12)

        tk.Label(f, text='输入激活码',
                 font=('Microsoft YaHei', 12, 'bold'),
                 anchor='w').pack(anchor='w', pady=(0, 12))

        code_var = tk.StringVar()
        tk.Entry(f, textvariable=code_var, width=40,
                 font=('Microsoft YaHei', 11)).pack(fill='x', pady=8)
        tk.Label(f, text='格式: LICENSE-XXXX-XXXX-XXXX-XXXX',
                 font=('Microsoft YaHei', 9), fg='#666').pack(anchor='w')

        msg_var = tk.StringVar()
        tk.Label(f, textvariable=msg_var,
                 font=('Microsoft YaHei', 9), fg='#f85149').pack(anchor='w', pady=4)

        def do_activate():
            code = code_var.get().strip()
            if not code:
                msg_var.set('请输入激活码')
                return
            try:
                result = self.lm.activate(code)
                messagebox.showinfo('激活成功',
                    '激活码有效，到期日: %s\n重启应用后生效' % result['expires_at'])
                self._update_license_label()
                win.destroy()
            except ActivationError as e:
                msg_var.set(str(e))

        bf = tk.Frame(win, bg='#f0f2f5')
        bf.pack(fill='x', padx=16, pady=(0, 12))
        tk.Button(bf, text='激活', font=('Microsoft YaHei', 11, 'bold'),
                  bg='#238636', fg='white', padx=24, pady=8, bd=0,
                  command=do_activate).pack(side='left', padx=3)
        tk.Button(bf, text='取消', font=('Microsoft YaHei', 11),
                  bg='#999', fg='white', padx=24, pady=8, bd=0,
                  command=win.destroy).pack(side='left', padx=3)

    def _cmd_install(self):
        """执行安装。"""
        if self.running:
            return
        self.running = True
        self.logbox.delete('1.0', 'end')

        def worker():
            try:
                cfg = load_config()
                install_blocks(cfg, callback=lambda m: self.root.after(0, lambda: self._rlog(m)))
                self.root.after(0, lambda: self.status.config(text='安装完成', fg='#3fb950'))
            except Exception as e:
                self.root.after(0, lambda: self._rlog('[错误] 安装异常: %s' % str(e)))
                self.root.after(0, lambda: self.status.config(text='安装失败', fg='#f85149'))
            finally:
                self.root.after(0, lambda: setattr(self, 'running', False))
                self.root.after(500, self._cmd_update)

        threading.Thread(target=worker, daemon=True).start()

    def _refresh(self):
        """刷新数据卡片。"""
        try:
            fp = DATA_DIR / 'latest.json'
            if fp.exists():
                with open(fp, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('stale'):
                    self.status.config(text='数据过期', fg='#e3b341')
                self.mv['up'].config(text=str(data.get('up', '-')))
                self.mv['down'].config(text=str(data.get('down', '-')))
                self.mv['max'].config(text=str(data.get('maxBoard', '-')) + '板')
                self.mv['zb'].config(text=str(data.get('zhaban', '-')))
                self.mv['lhb'].config(text=str(data.get('lhb', '-')), fg='#3fb950')
                # V5新增卡片
                promo = data.get('promoRate', None)
                self.mv['promo'].config(text='%.0f%%' % promo if promo is not None else '-')
                premium = data.get('premiumRate', None)
                self.mv['premium'].config(text='%.1f%%' % premium if premium is not None else '-')
                zhaban = data.get('zhabanRate', None)
                self.mv['zhaban'].config(text='%.0f%%' % zhaban if zhaban is not None else '-')
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    def _open_settings(self):
        """打开设置面板（V5：增加TG Bot配置）。"""
        win = tk.Toplevel(self.root)
        win.title('设置')
        win.geometry('560x520')
        win.configure(bg='#f0f2f5')
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        cfg = load_config()
        f = tk.Frame(win, bg='white', padx=24, pady=20,
                     highlightbackground='#ddd', highlightthickness=1)
        f.pack(fill='both', expand=True, padx=16, pady=12)

        r = 0
        # 通达信路径
        tk.Label(f, text='通达信路径',
                 font=('Microsoft YaHei', 10), anchor='w').grid(
            row=r, column=0, sticky='w', pady=8)
        tdx_var = tk.StringVar(value=cfg.get('tdxDir', 'C:\\new_tdx'))
        tk.Entry(f, textvariable=tdx_var, width=40,
                 font=('Microsoft YaHei', 10)).grid(row=r, column=1, padx=8)
        r += 1

        # 板块目录
        tk.Label(f, text='板块文件目录',
                 font=('Microsoft YaHei', 10), anchor='w').grid(
            row=r, column=0, sticky='w', pady=8)
        bnd_var = tk.StringVar(value=cfg.get('blocknewDir', ''))
        tk.Entry(f, textvariable=bnd_var, width=40,
                 font=('Microsoft YaHei', 10)).grid(row=r, column=1, padx=8)
        r += 1

        # 分割线
        tk.Frame(f, bg='#eee', height=1).grid(
            row=r, column=0, columnspan=2, sticky='ew', pady=8)
        r += 1

        # 授权状态显示
        lic_info = self.lm.verify()
        tk.Label(f, text='授权状态',
                 font=('Microsoft YaHei', 10), anchor='w').grid(
            row=r, column=0, sticky='w', pady=8)
        status_text = '%s（剩余%d天）' % (lic_info['type'], lic_info['days_left']) if lic_info['valid'] else '未激活'
        status_color = '#3fb950' if lic_info['valid'] else '#f85149'
        tk.Label(f, text=status_text,
                 font=('Microsoft YaHei', 10), fg=status_color).grid(
            row=r, column=1, sticky='w', padx=8)
        r += 1

        tk.Label(f, text='设备指纹',
                 font=('Microsoft YaHei', 10), anchor='w').grid(
            row=r, column=0, sticky='w', pady=8)
        fp = self.lm._make_fingerprint()[:16] + '...'
        tk.Label(f, text=fp,
                 font=('Microsoft YaHei', 9), fg='#666').grid(
            row=r, column=1, sticky='w', padx=8)
        r += 1

        def save():
            c = load_config()
            c['tdxDir'] = tdx_var.get().strip()
            c['blocknewDir'] = os.path.join(tdx_var.get().strip(), 'T0002', 'blocknew')
            save_config(c)
            self.config = c
            messagebox.showinfo('提示', '设置已保存')
            win.destroy()

        bf = tk.Frame(win, bg='#f0f2f5')
        bf.pack(fill='x', padx=16, pady=(0, 12))
        tk.Button(bf, text='保存', font=('Microsoft YaHei', 11, 'bold'),
                  bg='#238636', fg='white', padx=24, pady=8, bd=0,
                  command=save).pack(side='left', padx=3)
        tk.Button(bf, text='取消', font=('Microsoft YaHei', 11),
                  bg='#999', fg='white', padx=24, pady=8, bd=0,
                  command=win.destroy).pack(side='left', padx=3)

    def _on_close(self):
        """关闭窗口。"""
        self.root.withdraw()
        self.root.quit()

    def run(self):
        """启动应用。"""
        self.logbox.insert('end', '连板龙 V4.0 启动\n')
        lic_info = self.lm.verify()
        if lic_info['valid']:
            self.logbox.insert('end', '[授权] %s 剩余 %d 天\n' % (lic_info['type'], lic_info['days_left']))
        self.logbox.insert('end', '点击 [立即更新] 获取最新复盘数据\n')
        self.logbox.insert('end', '点击 [安装板块] 配置通达信自定义板块\n')
        self.logbox.insert('end', '\n')
        tdx_dir = self.config.get('tdxDir', '')
        gridtab = os.path.join(tdx_dir, 'T0002', 'gridtab.dat')
        if not os.path.exists(gridtab):
            self.logbox.insert('end', '[提示] 未检测到板块配置，请先点击 [安装板块]\n')
        self.root.mainloop()


def main():
    """入口函数。"""
    from license import LicenseManager
    lm = LicenseManager()

    clean_old_logs()
    if len(sys.argv) > 1:
        if sys.argv[1] == '--update':
            # 命令行更新：验证授权，过期则跳过快不报错
            lic = lm.verify()
            if not lic.get('valid', False):
                logs = ['[授权] 授权已过期，跳过更新']
                for l in logs:
                    print(l)
                return logs
            logs = run_engine(load_config())
            for l in logs:
                print(l)
            return
        elif sys.argv[1] == '--install':
            install_blocks(load_config())
            print('')
            print('板块安装完成，运行数据更新...')
            run_engine(load_config())
            return
        elif sys.argv[1] == '--activate':
            if len(sys.argv) >= 3:
                code = sys.argv[2]
                try:
                    result = lm.activate(code)
                    print(result['message'])
                except Exception as e:
                    print('激活失败: %s' % e)
            else:
                print('用法: python app.py --activate LICENSE-XXXX-XXXX-XXXX-XXXX')
            return
        elif sys.argv[1] == '--status':
            lic = lm.verify()
            print('授权状态: %s' % ('有效' if lic['valid'] else '无效'))
            print('类型: %s' % lic['type'])
            print('剩余天数: %d' % lic['days_left'])
            print('消息: %s' % lic['message'])
            return
        elif sys.argv[1] == '--trial':
            trial = lm.start_trial(30)
            print(trial['message'])
            return
        elif sys.argv[1] == '--help':
            print('用法:')
            print('  python app.py                   启动桌面APP')
            print('  python app.py --update          命令行更新数据')
            print('  python app.py --install         命令行安装板块')
            print('  python app.py --activate CODE   激活')
            print('  python app.py --status          查看授权状态')
            print('  python app.py --trial           开始30天试用')
            return
    if tk is None:
        print('缺少tkinter，请使用命令行模式: python app.py --update')
        return
    app = FupanApp()
    app.run()


if __name__ == '__main__':
    main()
