#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复盘助手 - 通达信自动复盘桌面应用

用法:
  python app.py           启动桌面APP
  python app.py --update  命令行静默更新
  python app.py --install 命令行安装板块
"""
import sys, os, json, subprocess, threading, webbrowser
from datetime import datetime
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext
except:
    tk = None

BASE_DIR = Path(__file__).parent.resolve()
from utils import load_config, save_config, log, DATA_DIR, LOG_DIR, clean_old_logs
from engine import run_engine, install_blocks


class FupanApp:
    def __init__(self):
        self.config = load_config()
        self.root = tk.Tk()
        self.root.title('复盘助手 - 通达信自动复盘')
        self.root.geometry('720x580')
        self.root.minsize(640, 500)
        self.root.configure(bg='#f0f2f5')
        self.running = False
        self._build_ui()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    def _build_ui(self):
        hdr = tk.Frame(self.root, bg='#1a1a2e', height=50)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)
        tk.Label(hdr, text='复盘助手',
                font=('Microsoft YaHei', 16, 'bold'),
                fg='#e94560', bg='#1a1a2e').pack(side='left', padx=20)
        self.status = tk.Label(hdr, text='就绪',
                               font=('Microsoft YaHei', 10),
                               fg='#8b949e', bg='#1a1a2e')
        self.status.pack(side='right', padx=20)
        main = tk.Frame(self.root, bg='#f0f2f5')
        main.pack(fill='both', expand=True, padx=16, pady=10)
        cards = tk.Frame(main, bg='#f0f2f5')
        cards.pack(fill='x', pady=(0, 10))
        self.mv = {}
        for txt, key, color in [
            ('涨停', 'up', '#f85149'),
            ('跌停', 'down', '#58a6ff'),
            ('最高板', 'max', '#e3b341'),
            ('曾涨停', 'zb', '#da3633'),
            ('龙虎榜', 'lhb', '#3fb950'),
        ]:
            c = tk.Frame(cards, bg='white', highlightbackground='#ddd',
                         highlightthickness=1, padx=8, pady=6)
            c.pack(side='left', expand=True, fill='x', padx=3)
            tk.Label(c, text=txt, font=('Microsoft YaHei', 9), fg='#666').pack()
            v = tk.Label(c, text='-', font=('Microsoft YaHei', 22, 'bold'), fg=color)
            v.pack()
            self.mv[key] = v
        lf = tk.LabelFrame(main, text='运行日志',
                           font=('Microsoft YaHei', 10),
                           bg='#f0f2f5', padx=8, pady=8)
        lf.pack(fill='both', expand=True)
        self.logbox = scrolledtext.ScrolledText(
            lf, height=14, font=('Consolas', 10),
            bg='#1a1a2e', fg='#e0e0e0', insertbackground='white',
            relief='flat', borderwidth=0)
        self.logbox.pack(fill='both', expand=True)
        btns = tk.Frame(main, bg='#f0f2f5')
        btns.pack(fill='x', pady=(10, 0))
        self.btn_update = self._mkbtn(btns, '立即更新', '#238636', self._cmd_update)
        self.btn_update.pack(side='left', padx=3)
        self._mkbtn(btns, '安装板块', '#0f3460', self._cmd_install).pack(side='left', padx=3)
        self._mkbtn(btns, '设置', '#21262d', self._open_settings).pack(side='right', padx=3)
        self._mkbtn(btns, '打开通达信', '#e94560', self._open_tdx).pack(side='right', padx=3)

    def _mkbtn(self, parent, text, color, cmd):
        return tk.Button(parent, text=text,
                        font=('Microsoft YaHei', 11, 'bold'),
                        bg=color, fg='white', padx=14, pady=7,
                        bd=0, cursor='hand2', command=cmd)

    def _rlog(self, msg):
        self.logbox.insert('end', msg + '\n')
        self.logbox.see('end')

    def _open_tdx(self):
        exe = os.path.join(self.config.get('tdxDir', 'C:/new_tdx'), 'TdxW.exe')
        if os.path.exists(exe):
            subprocess.Popen([exe])
        else:
            messagebox.showerror('错误', '未找到通达信: ' + exe)

    def _cmd_update(self):
        if self.running: return
        self.running = True
        self.btn_update.config(state='disabled', text='更新中...')
        self.logbox.delete('1.0', 'end')
        self.status.config(text='更新中...', fg='#e3b341')
        def worker():
            cfg = load_config()
            run_engine(cfg, callback=lambda m: self.root.after(0, lambda: self._rlog(m)))
            self.root.after(0, self._refresh)
            self.root.after(0, lambda: self.status.config(text='完成', fg='#3fb950'))
            self.root.after(0, lambda: self.btn_update.config(state='normal', text='立即更新'))
            self.root.after(0, lambda: setattr(self, 'running', False))
        threading.Thread(target=worker, daemon=True).start()

    def _cmd_install(self):
        if self.running: return
        self.running = True
        self.logbox.delete('1.0', 'end')
        def worker():
            cfg = load_config()
            install_blocks(cfg, callback=lambda m: self.root.after(0, lambda: self._rlog(m)))
            self.root.after(0, lambda: self.status.config(text='安装完成', fg='#3fb950'))
            self.root.after(0, lambda: setattr(self, 'running', False))
            self.root.after(500, self._cmd_update)
        threading.Thread(target=worker, daemon=True).start()

    def _refresh(self):
        try:
            fp = DATA_DIR / 'latest.json'
            if fp.exists():
                data = json.load(open(fp, 'r', encoding='utf-8'))
                if data.get('stale'):
                    self.status.config(text='数据过期', fg='#e3b341')
                self.mv['up'].config(text=data.get('up', '-'))
                self.mv['down'].config(text=data.get('down', '-'))
                self.mv['max'].config(text=str(data.get('maxBoard', '-')) + '板')
                self.mv['zb'].config(text=data.get('zhaban', '-'))
                self.mv['lhb'].config(text=data.get('lhb', '-'))
        except:
            pass

    def _open_settings(self):
        win = tk.Toplevel(self.root)
        win.title('设置')
        win.geometry('520x440')
        win.configure(bg='#f0f2f5')
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)
        cfg = load_config()
        f = tk.Frame(win, bg='white', padx=24, pady=20,
                     highlightbackground='#ddd', highlightthickness=1)
        f.pack(fill='both', expand=True, padx=16, pady=12)
        r = 0
        tk.Label(f, text='通达信路径',
                font=('Microsoft YaHei', 10), anchor='w').grid(row=r, column=0, sticky='w', pady=8)
        tdx_var = tk.StringVar(value=cfg.get('tdxDir', 'C:/new_tdx'))
        tk.Entry(f, textvariable=tdx_var, width=45,
                font=('Microsoft YaHei', 10)).grid(row=r, column=1, padx=8)
        r += 1
        tk.Label(f, text='板块文件目录',
                font=('Microsoft YaHei', 10), anchor='w').grid(row=r, column=0, sticky='w', pady=8)
        bnd_var = tk.StringVar(value=cfg.get('blocknewDir', ''))
        tk.Entry(f, textvariable=bnd_var, width=45,
                font=('Microsoft YaHei', 10)).grid(row=r, column=1, padx=8)
        r += 1
        tk.Frame(f, bg='#eee', height=1).grid(row=r, column=0, columnspan=2, sticky='ew', pady=8)
        r += 1
        wx_var = tk.BooleanVar(value=cfg.get('enableWechat', False))
        tk.Checkbutton(f, text='启用微信推送', variable=wx_var,
                      font=('Microsoft YaHei', 10)).grid(row=r, column=0, columnspan=2, sticky='w', pady=4)
        r += 1
        tk.Label(f, text='ServerChan SendKey',
                font=('Microsoft YaHei', 10), anchor='w').grid(row=r, column=0, sticky='w')
        key_var = tk.StringVar(value=cfg.get('wechatKey', ''))
        tk.Entry(f, textvariable=key_var, width=45,
                font=('Microsoft YaHei', 10), show='*').grid(row=r, column=1, padx=8)
        r += 1
        link = tk.Label(f, text='获取 SendKey: https://sct.ftqq.com',
                       font=('Microsoft YaHei', 9), fg='#58a6ff', cursor='hand2')
        link.grid(row=r, column=1, sticky='w')
        link.bind('<Button-1>', lambda e: webbrowser.open('https://sct.ftqq.com'))
        def save():
            c = load_config()
            c['tdxDir'] = tdx_var.get().strip()
            c['blocknewDir'] = os.path.join(tdx_var.get().strip(), 'T0002', 'blocknew')
            c['enableWechat'] = wx_var.get()
            c['wechatKey'] = key_var.get().strip()
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
        self.root.withdraw()
        self.root.quit()

    def run(self):
        self.logbox.insert('end', '复盘助手 v1.0 启动\n')
        self.logbox.insert('end', '点击 [立即更新] 获取最新复盘数据\n')
        self.logbox.insert('end', '点击 [安装板块] 配置通达信自定义板块\n')
        self.logbox.insert('end', '\n')
        tdx_dir = self.config.get('tdxDir', '')
        gridtab = os.path.join(tdx_dir, 'T0002', 'gridtab.dat')
        if not os.path.exists(gridtab):
            self.logbox.insert('end', '[提示] 未检测到板块配置，请先点击 [安装板块]\n')
        self.root.mainloop()


def main():
    clean_old_logs()
    if len(sys.argv) > 1:
        if sys.argv[1] == '--update':
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
        elif sys.argv[1] == '--help':
            print('用法:')
            print('  python app.py           启动桌面APP')
            print('  python app.py --update  命令行更新数据')
            print('  python app.py --install 命令行安装板块')
            return
    if tk is None:
        print('缺少tkinter，请使用命令行模式: python app.py --update')
        return
    app = FupanApp()
    app.run()

if __name__ == '__main__':
    main()
