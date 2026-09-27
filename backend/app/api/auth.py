import logging
import secrets
import time
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.services.audit import record_audit
from app.schemas.auth import (
    LoginRequest,
    PasswordChange,
    ProfileUpdate,
    SsoExchangeRequest,
    TokenResponse,
    UserOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# 登录限流（DB 持久，跨 worker 生效）：账号+IP 15 分钟失败 5 次锁定；IP 全局 20 次熔断
_LOGIN_MAX_FAILURES = 5
_LOGIN_LOCK_SECONDS = 15 * 60
_IP_MAX_FAILURES = 20
_WINDOW = timedelta(seconds=_LOGIN_LOCK_SECONDS)


async def _count_failures(db: AsyncSession, *, username: str | None = None, ip: str | None = None) -> int:
    stmt = select(func.count()).select_from(LoginAttempt).where(LoginAttempt.success.is_(False))
    if username is not None:
        stmt = stmt.where(LoginAttempt.username == username)
    if ip is not None:
        stmt = stmt.where(LoginAttempt.ip == ip)
    since = datetime.now(timezone.utc).replace(tzinfo=None) - _WINDOW
    stmt = stmt.where(LoginAttempt.created_at >= since)
    return await db.scalar(stmt) or 0


async def _check_login_rate_limit(db: AsyncSession, username: str, ip: str) -> None:
    if await _count_failures(db, username=username, ip=ip) >= _LOGIN_MAX_FAILURES:
        raise HTTPException(status_code=429, detail="失败次数过多，账号已临时锁定，请 15 分钟后再试")
    if await _count_failures(db, ip=ip) >= _IP_MAX_FAILURES:
        raise HTTPException(status_code=429, detail="失败次数过多，请稍后再试")


async def _record_login_failure(db: AsyncSession, username: str, ip: str) -> None:
    db.add(LoginAttempt(username=username, ip=ip, success=False))


async def _clear_login_failures(db: AsyncSession, username: str, ip: str) -> None:
    await db.execute(
        delete(LoginAttempt).where(
            LoginAttempt.username == username, LoginAttempt.ip == ip
        )
    )


def reset_login_rate_limit() -> None:
    """清空限流计数（兼容旧测试接口）；DB 方案下失败记录随窗口过期，无需清理。"""
    pass


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    await _check_login_rate_limit(db, body.username, ip)
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        await _record_login_failure(db, body.username, ip)
        record_audit(db, None, "login", "user", None, {"username": body.username, "ok": False}, ip)
        await db.commit()
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if getattr(user, "status", 1) == 0:
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    await _clear_login_failures(db, body.username, ip)
    # 记录最近登录时间（TIMESTAMP 不带时区，写 naive UTC）
    user.last_login_at = datetime.now(timezone.utc).replace(tzinfo=None)
    record_audit(db, user, "login", "user", user.id, {"ok": True}, ip)
    await db.commit()
    token = create_access_token(
        user.id, user.username,
        expires_minutes=30 * 24 * 60 if body.long_lived else None,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


# ---- SSO 免登（同步 App「打开网页版」用）：长效 token 换 60 秒一次性 code ----
_SSO_CODES: dict[str, tuple[int, float]] = {}  # code -> (user_id, 过期时间戳)


@router.post("/sso-code")
async def issue_sso_code(user: User = Depends(get_current_user)):
    """签发一次性 SSO code（60 秒有效、单次使用）。"""
    # 顺带清理过期 code，避免字典无限增长
    now = time.time()
    for c, (_, exp) in list(_SSO_CODES.items()):
        if exp < now:
            _SSO_CODES.pop(c, None)
    code = secrets.token_urlsafe(24)
    _SSO_CODES[code] = (user.id, now + 60)
    return {"code": code}


@router.post("/sso-exchange", response_model=TokenResponse)
async def sso_exchange(body: SsoExchangeRequest, db: AsyncSession = Depends(get_db)):
    """一次性 code 换正式 JWT（消费即失效）。"""
    entry = _SSO_CODES.pop(body.code, None)
    if entry is None or entry[1] < time.time():
        raise HTTPException(status_code=401, detail="登录码无效或已过期")
    user = await db.get(User, entry[0])
    if user is None or getattr(user, "status", 1) == 0:
        raise HTTPException(status_code=403, detail="账号不可用")
    token = create_access_token(user.id, user.username)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserOut.model_validate(user),
    )


@router.put("/profile", response_model=UserOut)
async def update_profile(
    body: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


# 头像：前端已做正方形裁剪，这里仅校验/落盘/回填 avatar_url（相对路径 /avatars/xxx）
_AVATAR_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_AVATAR_MAX_BYTES = 5 * 1024 * 1024


@router.post("/avatar", response_model=UserOut)
async def upload_avatar(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """上传头像：前端裁剪为正方形后提交，保存到 data/avatars 并经 /avatars 静态服务。"""
    mime = (file.content_type or "").lower()
    ext = _AVATAR_MIME_EXT.get(mime)
    if ext is None:
        raise HTTPException(status_code=400, detail="仅支持 png/jpg/jpeg/webp/gif 图片")
    data = await file.read()
    if len(data) > _AVATAR_MAX_BYTES:
        raise HTTPException(status_code=413, detail="头像图片不能超过 5MB")
    if not data:
        raise HTTPException(status_code=400, detail="图片内容为空")
    # 简单魔数校验（与声明 MIME 一致），防任意文件伪装图片
    _magic_ok = {
        ".png": data[:8] == b"\x89PNG\r\n\x1a\n",
        ".jpg": data[:3] in (b"\xff\xd8\xff",),
        ".webp": data[:4] == b"RIFF" and data[8:12] == b"WEBP",
        ".gif": data[:6] in (b"GIF87a", b"GIF89a"),
    }.get(ext, False)
    if not _magic_ok:
        raise HTTPException(status_code=400, detail="文件不是有效图片")

    avatars = settings.avatars_path
    avatars.mkdir(parents=True, exist_ok=True)
    # 替换旧头像：先删旧文件（best-effort）
    old = user.avatar_url or ""
    if old.startswith("/avatars/"):
        (avatars / Path(old).name).unlink(missing_ok=True)
    filename = f"{uuid4().hex}{ext}"
    await file.close()
    try:
        (avatars / filename).write_bytes(data)
    except Exception as exc:
        logger.warning("头像写入失败: %s", exc)
        raise HTTPException(status_code=500, detail="头像保存失败")
    user.avatar_url = f"/avatars/{filename}"
    record_audit(db, user, "update", "user", user.id, {"avatar": "updated"})
    await db.commit()
    await db.refresh(user)
    return user


@router.put("/password", status_code=204)
async def change_password(
    body: PasswordChange,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="旧密码错误")
    user.password_hash = hash_password(body.new_password)
    # 改密后旧 JWT 失效（deps 校验 iat 不早于该时间）
    user.password_changed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()


@router.get("/preferences")
async def get_preferences(user: User = Depends(get_current_user)):
    return user.preferences or {}


@router.put("/preferences")
async def put_preferences(
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """整体覆盖保存 preferences（前端存主题等设置）。JSONB 需重新赋值触发变更检测。"""
    user.preferences = body
    await db.commit()
    return user.preferences
