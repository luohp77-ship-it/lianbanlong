#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载东方财富和大智慧安装包"""
import urllib.request, os

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}

# 大智慧365
print('=== 下载大智慧365 ===')
dzh_urls = [
    ('http://www.gw.com.cn/download/level2/365.exe', '官网'),
    ('http://download.gw.com.cn/pub/365.exe', '备用'),
]
for url, src in dzh_urls:
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=30)
        data = resp.read()
        print('  %s: %d bytes' % (src, len(data)))
        if len(data) > 1000000:
            with open(r'E:\大智慧365_setup.exe', 'wb') as f:
                f.write(data)
            print('  ✅ 下载成功!')
            break
        else:
            print('  ❌ 太小了，不是安装包(HTML页面)')
    except Exception as e:
        print('  ❌ %s: %s' % (src, str(e)[:60]))

# 东方财富
print()
print('=== 下载东方财富 ===')
dfcf_urls = [
    ('https://soft.eastmoney.com/soft/dfcftzrj/dfcf_setup.exe', '官网'),
    ('http://download.18.com.cn/dfcftzrj/dfcf_setup.exe', '备用1'),
    ('https://download.18.com.cn/dfcftzrj/dfcf_setup.exe', '备用2'),
]
for url, src in dfcf_urls:
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=30)
        data = resp.read()
        print('  %s: %d bytes' % (src, len(data)))
        if len(data) > 1000000:
            with open(r'E:\东方财富_setup.exe', 'wb') as f:
                f.write(data)
            print('  ✅ 下载成功!')
            break
        else:
            print('  ❌ 太小了，不是安装包')
    except Exception as e:
        print('  ❌ %s: %s' % (src, str(e)[:60]))
