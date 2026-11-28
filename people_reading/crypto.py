"""本地加密：口令 → scrypt 派生密钥 → AES-256-GCM。原话、对话、草稿、观察、备份、导出一律加密后落盘。

说明：标准库 sqlite3 不带 SQLCipher，所以 MVP 采用“敏感字段逐列加密 + 备份/导出整体加密”。
结构信息（表名、行数、时间戳）仍是明文；换成 SQLCipher 后只需替换 Store 的连接层。
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"PRS1"
CHECK_PLAIN = "people-reading-key-check"


class WrongPassphrase(Exception):
    pass


def derive_key(passphrase: str, salt: bytes) -> bytes:
    if not passphrase:
        raise WrongPassphrase("口令不能为空")
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def new_salt() -> bytes:
    return os.urandom(16)


def encrypt_bytes(key: bytes, data: bytes, aad: bytes = b"") -> bytes:
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, aad)


def decrypt_bytes(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    if not blob.startswith(MAGIC):
        raise WrongPassphrase("不是本系统的加密数据")
    try:
        return AESGCM(key).decrypt(blob[4:16], blob[16:], aad)
    except InvalidTag as e:
        raise WrongPassphrase("口令错误或数据被改动") from e


def enc_text(key: bytes, text: str, aad: str = "") -> str:
    return base64.b64encode(encrypt_bytes(key, text.encode(), aad.encode())).decode()


def dec_text(key: bytes, token: str, aad: str = "") -> str:
    return decrypt_bytes(key, base64.b64decode(token), aad.encode()).decode()
