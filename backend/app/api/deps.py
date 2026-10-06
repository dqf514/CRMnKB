import jwt
from datetime import timezone

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import FILE_TOKEN_TYPE, MCP_TOKEN_AUDIENCE, decode_token
from app.database import get_db  # noqa: F401  供其他模块/测试统一引用
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


async def _user_from_token(
    token: str | None, db: AsyncSession, allow_file_token: bool = False
) -> User:
    if not token:
        raise HTTPException(status_code=401, detail="未提供认证令牌")
    try:
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    # dsh MCP 专用令牌（aud=dsh-mcp）只允许访问 /api/mcp，不能当登录令牌用
    if payload.get("aud") == MCP_TOKEN_AUDIENCE:
        raise HTTPException(status_code=401, detail="该令牌仅限知识库 MCP 接口使用")
    # 带专用用途声明（typ）的令牌一律不能当登录令牌用：typ=file 仅 ?t= 文件直链
    # （get_current_user_with_query_token 校验 file 声明后以 allow_file_token 放行），
    # typ=ics 仅日历订阅源 /calendar/feed.ics 的 ?token= 参数。
    typ = payload.get("typ")
    if typ == FILE_TOKEN_TYPE:
        if not allow_file_token:
            raise HTTPException(status_code=401, detail="该令牌仅限文件直链访问使用")
    elif typ is not None:
        raise HTTPException(status_code=401, detail="该令牌仅限专用接口使用")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    if getattr(user, "status", 1) == 0:
        raise HTTPException(status_code=401, detail="账号已停用")
    # 改密后签发的旧 token 一律失效（password_changed_at 为空则跳过，兼容旧账号）
    changed_at = getattr(user, "password_changed_at", None)
    iat = payload.get("iat")
    if changed_at is not None and iat is not None:
        changed_ts = changed_at.replace(tzinfo=timezone.utc).timestamp()
        if iat < changed_ts:
            raise HTTPException(status_code=401, detail="令牌已失效，请重新登录")
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    return await _user_from_token(
        credentials.credentials if credentials else None, db
    )


async def get_current_user_with_query_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Bearer 优先；否则仅接受绑定文件 ID 的短时效文件令牌（?t=）。

    仅用于文件内容直链场景（<img>/<audio>/<video> 无法携带 Authorization 头）。
    长时效登录 JWT 不再允许出现在 URL 中（防凭证泄漏/访问日志窃取）：
    必须有 file 声明、且与请求路径的 file_id 一致。
    """
    token = credentials.credentials if credentials else request.query_params.get("t")
    if not token:
        raise HTTPException(status_code=401, detail="未提供认证令牌")
    if credentials is None:
        # URL 令牌：必须带 file 声明（短时效文件令牌），且与请求文件一致
        try:
            payload = decode_token(token)
            file_id = int(payload["file"])
        except (jwt.PyJWTError, KeyError, ValueError):
            raise HTTPException(status_code=401, detail="URL 令牌无效或已过期")
        path_file_id = request.path_params.get("file_id")
        try:
            path_file_id = int(path_file_id)
        except (TypeError, ValueError):
            path_file_id = None
        if path_file_id is None or file_id != path_file_id:
            raise HTTPException(status_code=403, detail="令牌与文件不匹配")
    # URL 分支已校验 file 声明，放行文件令牌；Bearer 分支仍按通用规则拒绝文件令牌
    return await _user_from_token(token, db, allow_file_token=credentials is None)


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """仅 role=admin 可访问，否则 403。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user
