#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复牌助手 Telegram Bot 消息中继
用法: python tg_bot.py       启动Bot（自动回复）
       python tg_bot.py --msg "内容"  发送消息给用户
"""
import sys, os, json, urllib.request, time, threading
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
TOKEN = '8747950702:AAHDSMjzhkjzhVF6d-rpj7d5Y6TqdmLfLBY'
API = 'https://api.telegram.org/bot%s' % TOKEN

# 存允许的用户ID
CONFIG_FILE = BASE_DIR / 'tg_users.json'

def api_call(method, params=None):
    url = '%s/%s' % (API, method)
    if params:
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(url, data=data)
    else:
        req = urllib.request.Request(url)
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except Exception as e:
        return {'ok': False, 'error': str(e)}

def send_message(chat_id, text):
    return api_call('sendMessage', {'chat_id': chat_id, 'text': text})

def get_updates(offset=0):
    return api_call('getUpdates', {'offset': offset, 'timeout': 10})

def main():
    print('=== 复牌助手 Telegram Bot ===')
    print('Bot: @Stoner_AI_bot')
    print()

    # 获取Bot信息
    me = api_call('getMe')
    if me.get('ok'):
        print('Bot 在线: @%s' % me['result']['username'])
    else:
        print('Bot 离线:', me.get('error'))
        return

    print('等待消息...')
    print()

    offset = 0
    while True:
        try:
            updates = get_updates(offset)
            if updates.get('ok'):
                for upd in updates.get('result', []):
                    offset = upd['update_id'] + 1
                    msg = upd.get('message', {})
                    chat_id = msg.get('chat', {}).get('id')
                    text = msg.get('text', '')
                    name = msg.get('from', {}).get('first_name', '?')
                    username = msg.get('from', {}).get('username', '')

                    if text == '/start':
                        send_message(chat_id, '你好! 我是复牌助手的Telegram中继Bot。我会把你的消息存档。')
                        # 记录用户ID
                        users = {}
                        if CONFIG_FILE.exists():
                            users = json.load(open(CONFIG_FILE))
                        users[str(chat_id)] = {'name': name, 'username': username, 'last_msg': time.strftime('%Y-%m-%d %H:%M:%S')}
                        json.dump(users, open(CONFIG_FILE, 'w'), indent=2)
                    else:
                        # 存档消息
                        msg_dir = BASE_DIR / 'tg_messages'
                        os.makedirs(msg_dir, exist_ok=True)
                        ts = time.strftime('%Y%m%d_%H%M%S')
                        with open(msg_dir / ('%s_%s.txt' % (ts, chat_id)), 'w', encoding='utf-8') as f:
                            f.write('From: %s (@%s)\nChatID: %s\nTime: %s\n\n%s\n' % (
                                name, username, chat_id, time.strftime('%Y-%m-%d %H:%M:%S'), text))
                        send_message(chat_id, '收到! (已存档)')
                        print('[消息] %s: %s' % (name, text))
            time.sleep(1)
        except KeyboardInterrupt:
            print('\n停止')
            break
        except Exception as e:
            print('轮询错误:', e)
            time.sleep(5)

if __name__ == '__main__':
    main()
