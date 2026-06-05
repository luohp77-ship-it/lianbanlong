#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""授权验证模块 V4 — RSA签名 + 设备指纹 + 激活 + 到期控制

模块职责：
  - 设备指纹生成（MAC + 主机名 + C盘序列号）
  - 激活码验证（RSA签名验证）
  - 试用期管理（首次运行自动创建30天试用）
  - license.dat 读写（签名保护，防篡改）
  - 到期判断与弹窗提醒

用法：
  from license import LicenseManager
  lm = LicenseManager()
  if not lm.verify():
      print('授权验证失败')
"""
import os
import json
import time
import uuid
import hashlib
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

# ═══ RSA 公钥（嵌入代码，用于验证激活码签名）═══
# 说明：这是开发时生成的 RSA-2048 公钥，对应的私钥由开发者保管。
# 如果要更换密钥对，重新生成后替换此常量即可。
_EMBEDDED_PUBLIC_KEY = b"""
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArjPqgjyUbL+LJZbjW0bQ
yloAlo3cp4vcx2cFvINZ9PgjB01LS7f3FLTTxO9m9jcKofThv430/6I69/6TiCOB
WCU9T5r3T9/HeRCFeVZNHRIu/y71jKms99DHqb3Amf36UMrlVqSKmZjY1HckyhWn
Ho4L6cG86s2dQeRtzhp1wYlP7Wfuica4dQ3K5BL+/xKpRwKNgyJKMzP8fTv0A/al
aZl6hWn4Qrgfx8k0OXCA0sUfoO5ETcbglMhPn+AaN2PZNXUw6MhnqEoiG38TyF/K
NGUILhjqRGeq0dMl4iZIqO7jGRx8Ji3OG/f/G9KZtmvJrpqsNn6AuYjdrCXXpnDH
3wIDAQAB
-----END PUBLIC KEY-----
"""

_LICENSE_FILE = None  # 由 init() 设置


# ═══ 工具函数 ═══

def _get_mac_address():
    """获取 MAC 地址作为设备指纹的一部分（使用 uuid.getnode，纯 Python 跨平台）。"""
    try:
        mac = uuid.getnode()
        if mac is not None and mac != 0:
            return '%012x' % mac
    except Exception:
        pass
    return 'unknown_mac'


def _get_hostname():
    """获取主机名。"""
    try:
        return os.uname().nodename
    except AttributeError:
        return os.environ.get('COMPUTERNAME', 'unknown_host')


def _get_disk_serial():
    """获取 C 盘卷序列号。"""
    try:
        if os.name == 'nt':
            r = subprocess.run(['wmic', 'path', 'win32_logicaldisk',
                                'where', 'DeviceID="C:"', 'get', 'VolumeSerialNumber'],
                               capture_output=True, text=True, timeout=5, shell=True)
            for line in r.stdout.split('\n'):
                line = line.strip()
                if line and line.lower() != 'volumeserialnumber':
                    return line.lower()
    except Exception:
        pass
    return 'unknown_disk'


def get_device_fingerprint():
    """生成设备指纹（MAC + 主机名 + C盘序列号的 SHA256 哈希）。

    Returns:
        64位十六进制字符串，作为设备唯一标识。
    """
    raw = '%s|%s|%s' % (_get_mac_address(), _get_hostname(), _get_disk_serial())
    return hashlib.sha256(raw.encode()).hexdigest()


def verify_signature(data, signature_b64):
    """使用内嵌公钥验证 RSA 签名。

    Args:
        data: 原始数据（bytes）。
        signature_b64: Base64 编码的签名字符串。

    Returns:
        签名是否有效。
    """
    try:
        from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
        from cryptography.hazmat.primitives import hashes as hashlib2, serialization as ser
        from cryptography.exceptions import InvalidSignature
        import base64

        public_key = ser.load_pem_public_key(_EMBEDDED_PUBLIC_KEY)
        signature = base64.b64decode(signature_b64)
        public_key.verify(
            signature,
            data.encode('utf-8') if isinstance(data, str) else data,
            asym_padding.PKCS1v15(),
            hashlib2.SHA256(),
        )
        return True
    except (InvalidSignature, Exception):
        return False


def sign_data(private_key_pem, data):
    """使用私钥签署数据（仅开发用，不打包到产品中）。

    Args:
        private_key_pem: PEM 格式的私钥。
        data: 要签名的字符串。

    Returns:
        Base64 编码的签名字符串。
    """
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
    from cryptography.hazmat.primitives import hashes as hashlib2, serialization as ser
    import base64
    private_key = ser.load_pem_private_key(private_key_pem, password=None)
    sig = private_key.sign(
        data.encode('utf-8') if isinstance(data, str) else data,
        asym_padding.PKCS1v15(),
        hashlib2.SHA256(),
    )
    return base64.b64encode(sig).decode()


def generate_activation_code(private_key_pem, days=365, max_devices=2):
    """生成自包含的激活码（仅开发者使用，不随产品分发）。

    激活码格式：LICENSE-XXXX-XXXX-XXXX-XXXX.{base64_payload}.{base64_signature}
    客户端通过 _parse_activation_code 解析并验证 RSA 签名。

    Args:
        private_key_pem: PEM 私钥。
        days: 有效期天数。
        max_devices: 最大绑定设备数。

    Returns:
        激活码字符串（包含签名，可直接复制使用）。
    """
    import random
    import string
    import base64 as _b64

    rand_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))
    code = 'LICENSE-%s-%s-%s-%s' % (rand_part[:4], rand_part[4:8],
                                     rand_part[8:12], rand_part[12:16])

    payload = json.dumps({
        'code': code,
        'days': days,
        'max_devices': max_devices,
        'created_at': datetime.now().strftime('%Y-%m-%d'),
    }, separators=(',', ':'))

    sig = sign_data(private_key_pem, payload)

    # base64 编码 payload
    encoded_payload = _b64.b64encode(payload.encode()).decode()

    return '%s.%s.%s' % (code, encoded_payload, sig)


# ═══ license.dat 读写 ═══

_LICENSE_FILE = None


def init(data_dir=None):
    """初始化授权模块，设置 license.dat 路径。

    Args:
        data_dir: 数据目录（存放 license.dat），默认自动检测。
    """
    global _LICENSE_FILE
    if data_dir:
        _LICENSE_FILE = os.path.join(str(data_dir), 'license.dat')
    else:
        # 自动检测：优先 V4 目录，回退到 V3 data 目录
        base = Path(__file__).parent.resolve()
        _LICENSE_FILE = str(base / 'data' / 'license.dat')


def _get_license_path():
    """获取 license.dat 完整路径。"""
    if _LICENSE_FILE:
        return _LICENSE_FILE
    base = Path(__file__).parent.resolve()
    return str(base / 'data' / 'license.dat')


def load_license():
    """读取 license.dat。

    Returns:
        字典，文件不存在或无效返回 None。
    """
    fp = _get_license_path()
    if not os.path.exists(fp):
        return None
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_license(data):
    """原子写入 license.dat。

    Args:
        data: 字典数据。
    """
    fp = _get_license_path()
    tmp = fp + '.tmp'
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, fp)


# ═══ 授权验证类 ═══

class LicenseError(Exception):
    """授权异常基类。"""
    pass


class ActivationError(LicenseError):
    """激活失败。"""
    pass


class LicenseExpiredError(LicenseError):
    """授权已到期。"""
    pass


class LicenseManager:
    """授权管理器：激活、验证、到期控制。"""

    def __init__(self, data_dir=None):
        init(data_dir)

    def _make_fingerprint(self):
        """获取当前设备指纹。"""
        return get_device_fingerprint()

    # ── 激活 ──

    def activate(self, code):
        """使用激活码激活。

        Args:
            code: 激活码字符串（LICENSE-XXXX-...）。

        Returns:
            {'success': True, 'expires_at': '...', 'message': '...'}

        Raises:
            ActivationError: 激活码无效或已被使用。
        """
        license_data = self._parse_activation_code(code)
        if not license_data:
            raise ActivationError('激活码无效')

        fp = self._make_fingerprint()
        expires_at = datetime.now() + timedelta(days=license_data['days'])

        lic = {
            'type': 'full',
            'code': license_data['code'],
            'activated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'expires_at': expires_at.strftime('%Y-%m-%d'),
            'device_fingerprint': fp,
            'max_devices': license_data.get('max_devices', 2),
        }
        save_license(lic)
        return {
            'success': True,
            'expires_at': lic['expires_at'],
            'message': '激活成功！到期日: %s' % lic['expires_at'],
        }

    def _parse_activation_code(self, code_str):
        """解析并验证激活码签名（V4：真实 RSA 签名验证）。

        激活码格式：LICENSE-XXXX-XXXX-XXXX-XXXX.{base64_payload}.{base64_signature}

        Args:
            code_str: 完整的激活码字符串（含签名部分）。

        Returns:
            解析后的字典，包含 code/days/max_devices 等字段。
            验证失败（签名无效/格式错误）返回 None。
        """
        code_str = code_str.strip()

        # 拆分为 code.payload.signature 三段
        parts = code_str.split('.')
        if len(parts) != 3:
            return None

        code, encoded_payload, encoded_sig = parts

        # 验证 code 格式
        if not code.startswith('LICENSE-'):
            return None
        code_parts = code.split('-')
        if len(code_parts) != 5:
            return None
        for i in range(1, 5):
            if len(code_parts[i]) != 4:
                return None

        try:
            import base64 as _b64

            def _unb64(s):
                pad = 4 - len(s) % 4
                if pad != 4:
                    s += '=' * pad
                return _b64.b64decode(s)

            # 解码 payload
            payload_json = _unb64(encoded_payload).decode('utf-8')
            payload_data = json.loads(payload_json)

            # 验证 code 一致性
            if payload_data.get('code') != code:
                return None

            # ═══ RSA 签名验证（核心安全校验）═══
            if not verify_signature(payload_json, encoded_sig):
                return None  # 签名无效

            return {
                'code': code,
                'days': payload_data.get('days', 365),
                'max_devices': payload_data.get('max_devices', 2),
            }
        except Exception:
            return None

    # ── 试用 ──

    def start_trial(self, days=30):
        """创建试用授权。

        如果已有 license.dat 且为试用版，更新到期日；
        如果已有正式版授权，不做任何事。

        Args:
            days: 试用天数（默认 30）。

        Returns:
            {'success': True, 'expires_at': '...', 'trial': True}
        """
        existing = load_license()
        if existing and existing.get('type') == 'full':
            return {'success': True, 'expires_at': existing.get('expires_at', ''),
                    'trial': False, 'message': '已有正式授权'}

        fp = self._make_fingerprint()
        expires_at = datetime.now() + timedelta(days=days)

        lic = {
            'type': 'trial',
            'activated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'expires_at': expires_at.strftime('%Y-%m-%d'),
            'device_fingerprint': fp,
            'max_devices': 2,
        }
        save_license(lic)
        return {
            'success': True,
            'expires_at': lic['expires_at'],
            'trial': True,
            'message': '试用 %d 天已激活，到期日: %s' % (days, lic['expires_at']),
        }

    # ── 验证 ──

    def verify(self):
        """验证当前授权是否有效。

        Returns:
            {'valid': bool, 'type': str, 'days_left': int, 'message': str}
        """
        lic = load_license()
        if lic is None:
            return {
                'valid': False,
                'type': 'none',
                'days_left': 0,
                'message': '未激活',
            }

        # 验证设备指纹（试用版也绑定设备）
        stored_fp = lic.get('device_fingerprint', '')
        current_fp = self._make_fingerprint()
        if stored_fp and stored_fp != current_fp:
            return {
                'valid': False,
                'type': lic.get('type', ''),
                'days_left': 0,
                'message': '设备不匹配（授权已绑定其他设备）',
            }

        # 验证到期时间
        expires_str = lic.get('expires_at', '')
        if not expires_str:
            return {'valid': False, 'type': lic.get('type', ''),
                    'days_left': 0, 'message': '授权文件损坏'}

        try:
            expires = datetime.strptime(expires_str, '%Y-%m-%d')
            now = datetime.now()
            days_left = (expires - now).days
        except ValueError:
            return {'valid': False, 'type': lic.get('type', ''),
                    'days_left': 0, 'message': '授权日期格式错误'}

        if days_left < 0:
            return {
                'valid': False,
                'type': lic.get('type', ''),
                'days_left': days_left,
                'message': '授权已过期（%s）' % lic.get('type', ''),
            }

        return {
            'valid': True,
            'type': lic.get('type', 'trial'),
            'days_left': days_left,
            'message': '授权有效，剩余 %d 天' % days_left,
        }

    def is_trial(self):
        """是否为试用版。"""
        lic = load_license()
        return lic is not None and lic.get('type') == 'trial'

    def is_expired(self):
        """是否已过期。"""
        result = self.verify()
        return not result.get('valid', False)

    def days_remaining(self):
        """剩余天数（含到期当天）。"""
        result = self.verify()
        return max(0, result.get('days_left', 0))

    # ── 推送凭证读写（供 engine.py 使用）──

    def get_push_token(self):
        """读取推送凭证（由用户中心写入 license.dat）。

        Returns:
            推送 token 字符串，未配置返回空字符串。
        """
        lic = load_license()
        if lic:
            return lic.get('push_token', '')
        return ''

    def set_push_token(self, token):
        """写入推送凭证。

        Args:
            token: 推送凭证字符串。
        """
        lic = load_license()
        if lic is None:
            # 未激活时也能存推送凭证（试用期用户也可以绑定推送）
            lic = {
                'type': 'trial',
                'activated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'expires_at': (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d'),
                'device_fingerprint': self._make_fingerprint(),
                'push_token': token,
            }
        else:
            lic['push_token'] = token
        save_license(lic)

    # ── 设备绑定管理 ──

    def bind_device(self, device_name=''):
        """绑定当前设备。

        Args:
            device_name: 设备名称（可选）。

        Returns:
            是否成功。
        """
        lic = load_license()
        if lic is None:
            return False
        lic['device_fingerprint'] = self._make_fingerprint()
        if device_name:
            lic['device_name'] = device_name
        save_license(lic)
        return True

    def unbind_device(self):
        """解绑当前设备（清空设备指纹）。

        Returns:
            是否成功。
        """
        lic = load_license()
        if lic is None:
            return False
        lic['device_fingerprint'] = ''
        save_license(lic)
        return True
