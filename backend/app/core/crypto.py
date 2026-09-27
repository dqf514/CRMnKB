"""敏感字段加密（Fernet）：LLM API Key 落库加密，读取解密。

Key 从 JWT_SECRET 稳定派生（无需新增环境变量）；生产环境建议设置独立 LLM_ENCRYPT_KEY。
旧明文密钥解密失败时原样返回（兼容升级前数据）。
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_fernet = Fernet(
    base64.urlsafe_b64encode(hashlib.sha256(settings.JWT_SECRET.encode()).digest())
)


def encrypt_secret(plain: str | None) -> str | None:
    if not plain:
        return plain
    return _fernet.encrypt(plain.encode()).decode()


def decrypt_secret(token: str | None) -> str | None:
    if not token:
        return token
    try:
        return _fernet.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return token  # 兼容旧明文密钥
