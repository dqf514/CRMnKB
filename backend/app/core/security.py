from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

from app.config import settings

ALGORITHM = "HS256"

# dsh MCP 专用令牌的 aud 声明：仅允许访问 /api/mcp，不能当登录令牌用
MCP_TOKEN_AUDIENCE = "dsh-mcp"

# 文件访问令牌的类型声明：仅允许 ?t= 文件直链，不能当登录令牌用
FILE_TOKEN_TYPE = "file"

_password_hash = PasswordHash((BcryptHasher(),))


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _password_hash.verify(plain, hashed)
    except Exception:
        return False


def create_access_token(user_id: int, username: str, expires_minutes: int | None = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expires_minutes or settings.JWT_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "username": username, "iat": now, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def create_file_token(file_id: int, user_id: int) -> str:
    """短时效（5 分钟）文件访问令牌，仅用于 <img>/<audio>/<video>/<pdf> 直链流式加载。

    带 file 声明并绑定 user_id，避免把长时效登录 JWT 放进 URL（防凭证泄漏/日志窃取）。
    typ=file 用于与登录 JWT 区分：通用 Bearer 鉴权（api/deps.py）拒绝该类型令牌。
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(seconds=300)
    payload = {"sub": str(user_id), "file": file_id, "typ": FILE_TOKEN_TYPE, "iat": now, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def create_mcp_token(user_id: int, username: str, expires_minutes: int) -> str:
    """dsh MCP 专用令牌（aud=dsh-mcp）：注入用户 dsh 进程的 patch yml，
    仅用于 /api/mcp 的知识库工具调用。与登录 JWT 双向隔离：/api/mcp 端点
    强制 aud=dsh-mcp，其余业务接口（api/deps.py）拒绝携带该 aud 的令牌——
    patch 文件对不可信的 dsh 进程可见，令牌泄露时损失限定在知识库只读范围。
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expires_minutes)
    payload = {
        "sub": str(user_id),
        "username": username,
        "aud": MCP_TOKEN_AUDIENCE,
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str, audience: str | None = None) -> dict:
    """校验失败时抛 jwt 异常，由调用方处理。

    audience 不为 None 时强制校验 aud 声明（MCP 端点用）；
    否则跳过 aud 校验（PyJWT 默认对含 aud 的令牌强制 audience 参数，
    登录/文件令牌没有 aud 声明，行为不变；dsh MCP 令牌由 deps.py 显式拒绝）。
    """
    if audience is not None:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM], audience=audience)
    return jwt.decode(
        token, settings.JWT_SECRET, algorithms=[ALGORITHM], options={"verify_aud": False}
    )
