#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""激活码生成工具（仅开发者使用，不随产品分发）

用法：
  1. 【首次使用】生成密钥对（私钥自己保管，公钥嵌入 license.py）：
     python tools/generate_license.py --gen-keys --out keys/

  2. 【生成激活码】（需要私钥文件）：
     python tools/generate_license.py --gen-code --key keys/private.pem

  3. 【验证激活码】（模拟客户端验证，使用 license.py 中的公钥）：
     python tools/generate_license.py --verify <完整的激活码>

工作流程：
  a) 运行 --gen-keys → 得到 private.pem（自己藏好）和 public.pem
  b) 将 public.pem 的内容复制到 license.py 的 _EMBEDDED_PUBLIC_KEY 常量
  c) 运行 --gen-code --key private.pem → 得到激活码字符串
  d) 把激活码发给用户
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from license import (
    generate_activation_code, verify_signature,
    get_device_fingerprint,
)
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization as ser


def gen_keys(out_dir='keys'):
    """生成 RSA-2048 密钥对并保存到文件。

    Args:
        out_dir: 输出目录。
    """
    os.makedirs(out_dir, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    private_pem = key.private_bytes(
        encoding=ser.Encoding.PEM,
        format=ser.PrivateFormat.PKCS8,
        encryption_algorithm=ser.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=ser.Encoding.PEM,
        format=ser.PublicFormat.SubjectPublicKeyInfo,
    )

    # 写文件
    priv_path = os.path.join(out_dir, 'private.pem')
    pub_path = os.path.join(out_dir, 'public.pem')

    with open(priv_path, 'wb') as f:
        f.write(private_pem)
    with open(pub_path, 'wb') as f:
        f.write(public_pem)

    os.chmod(priv_path, 0o600)  # 私钥仅自己可读

    print('=== 密钥对已生成 ===')
    print('  私钥: %s  【← 妥善保管，不要公开】' % priv_path)
    print('  公钥: %s' % pub_path)
    print()
    print('=== 操作步骤 ===')
    print('1. 打开 license.py，找到 _EMBEDDED_PUBLIC_KEY 常量')
    print('2. 用以下公钥内容替换它：')
    print()
    print(public_pem.decode())
    print('3. 运行 --gen-code --key %s 生成激活码' % priv_path)


def gen_code(private_key_path):
    """从私钥文件生成一个激活码。

    Args:
        private_key_path: PEM 私钥文件路径。
    """
    if not os.path.exists(private_key_path):
        print('错误: 私钥文件不存在: %s' % private_key_path)
        return None

    with open(private_key_path, 'rb') as f:
        private_pem = f.read()

    activation_code = generate_activation_code(private_pem)
    print()
    print('=== 激活码 ===')
    print()
    print(activation_code)
    print()
    print('（将以上整串发给用户，用户粘贴到复盘助手的激活框即可）')
    return activation_code


def verify_code(code_str):
    """验证激活码（模拟客户端验证，使用 license.py 的内嵌公钥）。"""
    from license import LicenseManager
    lm = LicenseManager()
    try:
        result = lm.activate(code_str)
        print('验证成功: %s' % result['message'])
        print('设备指纹: %s...' % get_device_fingerprint()[:16])
        return True
    except Exception as e:
        print('验证失败: %s' % e)
        return False


if __name__ == '__main__':
    if '--gen-keys' in sys.argv:
        idx = sys.argv.index('--gen-keys')
        out_dir = sys.argv[idx + 1] if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith('--') else 'keys'
        gen_keys(out_dir)

    elif '--gen-code' in sys.argv:
        idx = sys.argv.index('--gen-code')
        if idx + 1 < len(sys.argv) and sys.argv[idx + 1] == '--key' and idx + 2 < len(sys.argv):
            gen_code(sys.argv[idx + 2])
        else:
            print('用法: python tools/generate_license.py --gen-code --key <私钥文件>')
            print('提示: 先用 --gen-keys 生成密钥对')

    elif '--verify' in sys.argv:
        idx = sys.argv.index('--verify') + 1
        if idx < len(sys.argv):
            verify_code(sys.argv[idx])
        else:
            print('用法: python tools/generate_license.py --verify <激活码>')

    else:
        print(__doc__)
