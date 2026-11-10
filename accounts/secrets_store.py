# -*- coding: utf-8 -*-
"""BYOK 密钥存储：Fernet 加密、末四位提示、缺库时降级。

密钥源自环境变量 API_KEY_ENC_SECRET（任意口令即可，内部用 sha256 派生 32 字节）。
不设置该变量时 BYOK 功能不可用，但应用照常启动。
"""
import base64
import hashlib
import os

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # 未安装 cryptography → 功能降级
    Fernet = None
    InvalidToken = Exception


def _fernet():
    if Fernet is None:
        return None
    raw = (os.getenv("API_KEY_ENC_SECRET") or "").strip()
    if not raw:
        return None
    derived = hashlib.sha256(raw.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def available() -> bool:
    return _fernet() is not None


def unavailable_reason() -> str:
    if Fernet is None:
        return 'server missing cryptography dependency'
    return 'API_KEY_ENC_SECRET is not configured'


def encrypt_secret(plain: str) -> str:
    cipher = _fernet()
    if cipher is None:
        raise RuntimeError(unavailable_reason())
    return cipher.encrypt(plain.encode('utf-8')).decode('ascii')


def decrypt_secret(token: str) -> str:
    cipher = _fernet()
    if cipher is None:
        raise RuntimeError(unavailable_reason())
    return cipher.decrypt(token.encode('ascii')).decode('utf-8')


def key_hint(plain: str) -> str:
    return 'sk-…' + plain[-4:] if len(plain) >= 8 else 'sk-…'


def user_api_key(user) -> str | None:
    """取用户的明文 key；未配置或解密失败一律返回 None（绝不抛给调用方）。"""
    if user is None:
        return None
    token = getattr(user, 'api_key_enc', None)
    if not token:
        return None
    try:
        return decrypt_secret(token)
    except Exception:
        return None
