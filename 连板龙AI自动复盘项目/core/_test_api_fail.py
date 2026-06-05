"""API 失败降级测试 - 模拟网络故障"""
import sys, json, os
from pathlib import Path

# 注入模拟：让 http_get 永远返回 None
import utils
_original_http_get = utils.http_get

def _mock_http_get(url, headers=None, timeout=10):
    print(f'  [MOCK] 拦截请求: {url[:60]}...')
    return (0, '模拟网络故障')

utils.http_get = _mock_http_get

# 保存原 latest.json
DATA_DIR = Path(__file__).parent / 'data'
latest_path = DATA_DIR / 'latest.json'
backup = None
if latest_path.exists():
    with open(latest_path, 'r', encoding='utf-8') as f:
        backup = f.read()

# 运行引擎（模拟失败场景）
import engine

# 注释：由于 mock 了 http_get，fetch 会返回空数据
config = utils.load_config()
logs = engine.run_engine(config)

print('\n=== 降级测试结果 ===')
for line in logs:
    print(f'  {line}')

# 检查 stale 标记
if latest_path.exists():
    with open(latest_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if data.get('stale'):
        print('\n[PASS] API降级成功: stale=True, reason=%s' % data.get('reason'))
    else:
        print('\n[WARN] 未标记 stale: %s' % json.dumps(data, ensure_ascii=False))
else:
    print('\n[WARN] latest.json 未生成')

# 恢复
utils.http_get = _original_http_get
if backup:
    with open(latest_path, 'w', encoding='utf-8') as f:
        f.write(backup)
    print('[OK] 已恢复原 latest.json')
