"""登录接入通道：短信验证码全流程 + 微信登录配置框架。

配置存 system_settings（key=login_integrations），结构见 DEFAULT_CONFIG：
- sms.provider = log（仅写日志，dev 环境把验证码带回响应）/ http（通用 HTTP 网关，
  body_template 支持 {phone} {code} 占位，适配云片/聚合等简单接口）；aliyun/tencent
  预留扩展位（选定平台后在 _send_sms 里加分支）。
- wechat 仅保存 app_id/app_secret（加密存储）/redirect_uri，登录流程后续接入。

安全口径：验证码只存 sha256 哈希（带 JWT_SECRET 盐），10 分钟有效、最多试 5 次、
一次性使用；发送侧限流（同号 60s、同号 10 条/天、同 IP 20 条/天）。
"""
import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.login_code import LoginCode
from app.models.system_setting import SystemSetting

logger = logging.getLogger(__name__)

LOGIN_INTEGRATIONS_KEY = "login_integrations"

DEFAULT_CONFIG: dict = {
    "sms": {
        "enabled": False,
        # log = 仅写日志（开发用）；http = 通用 HTTP 网关；aliyun/tencent 预留
        "provider": "log",
        "http": {
            "url": "",
            "headers": {},
            "body_template": '{"phone": "{phone}", "code": "{code}"}',
        },
    },
    "wechat": {
        "enabled": False,
        "app_id": "",
        # 加密存储（core/crypto）；接口返回时脱敏
        "app_secret": "",
        "redirect_uri": "",
    },
}

_CODE_TTL_MINUTES = 10
_CODE_MAX_ATTEMPTS = 5
_SEND_INTERVAL_SECONDS = 60
_SEND_DAILY_PER_PHONE = 10
_SEND_DAILY_PER_IP = 20


def _now() -> datetime:
    """naive UTC（库表 TIMESTAMP 不带时区，与 auth.py 口径一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _code_hash(phone: str, code: str) -> str:
    return hashlib.sha256(f"{phone}:{code}:{settings.JWT_SECRET}".encode()).hexdigest()


def get_login_integrations_from_value(value: str | None) -> dict:
    """DB value → 配置 dict（与 DEFAULT_CONFIG 深合并，容忍脏数据/缺字段）。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝默认值
    try:
        stored = json.loads(value) if value else {}
    except (TypeError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    for section, defaults in DEFAULT_CONFIG.items():
        part = stored.get(section)
        if not isinstance(part, dict):
            continue
        for key, default in defaults.items():
            if key in part and part[key] is not None:
                if isinstance(default, dict) and isinstance(part[key], dict):
                    cfg[section][key] = {**default, **part[key]}
                else:
                    cfg[section][key] = part[key]
    return cfg


async def get_login_integrations(db: AsyncSession) -> dict:
    """读取登录接入配置（不存在时返回默认值；wechat.app_secret 为密文）。"""
    row = await db.get(SystemSetting, LOGIN_INTEGRATIONS_KEY)
    return get_login_integrations_from_value(row.value if row else None)


async def save_login_integrations(db: AsyncSession, cfg: dict) -> None:
    """保存配置（调用方负责只传允许的字段；app_secret 传入明文会被加密）。"""
    row = await db.get(SystemSetting, LOGIN_INTEGRATIONS_KEY)
    if row is None:
        row = SystemSetting(key=LOGIN_INTEGRATIONS_KEY)
        db.add(row)
    row.value = json.dumps(cfg, ensure_ascii=False)
    await db.commit()


def mask_login_integrations(cfg: dict) -> dict:
    """接口返回用：app_secret 脱敏（has_app_secret + 后 4 位），不下发明文/密文。"""
    masked = json.loads(json.dumps(cfg, ensure_ascii=False))
    secret = decrypt_secret(cfg.get("wechat", {}).get("app_secret") or "") or ""
    w = masked.setdefault("wechat", {})
    w.pop("app_secret", None)
    w["has_app_secret"] = bool(secret)
    w["app_secret_tail"] = secret[-4:] if secret else ""
    return masked


def encrypt_wechat_secret(new_plain: str | None, old_cipher: str) -> str:
    """更新微信 app_secret：空串=清除，None=保持原值，其余按明文加密存储。"""
    if new_plain is None:
        return old_cipher
    if new_plain == "":
        return ""
    return encrypt_secret(new_plain) or ""


async def sms_login_enabled(db: AsyncSession) -> bool:
    """短信登录是否启用（brand 公开接口下发给登录页）。"""
    cfg = await get_login_integrations(db)
    return bool(cfg.get("sms", {}).get("enabled"))


async def _send_sms(cfg: dict, phone: str, code: str) -> str | None:
    """按配置通道发送验证码；返回 dev_code（仅 log 通道 + dev 环境）。"""
    provider = (cfg.get("provider") or "log").lower()
    if provider == "http":
        http_cfg = cfg.get("http") or {}
        url = (http_cfg.get("url") or "").strip()
        if not url:
            raise RuntimeError("短信 HTTP 网关未配置 url（管理端「系统设置 → 登录与接入」）")
        body = (http_cfg.get("body_template") or "").replace("{phone}", phone).replace("{code}", code)
        headers = {str(k): str(v) for k, v in (http_cfg.get("headers") or {}).items()}
        headers.setdefault("Content-Type", "application/json")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, content=body.encode("utf-8"), headers=headers)
            resp.raise_for_status()
        return None
    # log 通道：写日志；dev 环境直接把验证码带回响应（前端自动填充，免查日志）
    logger.warning("[login-code] %s 的登录验证码：%s（log 通道，未真实发送）", phone, code)
    return code if settings.ENV.lower() in ("dev", "development", "test") else None


async def issue_login_code(db: AsyncSession, phone: str, ip: str | None) -> str | None:
    """生成并发送登录验证码（含限流）；返回 dev_code（非 dev 环境为 None）。

    限流失败/发送失败抛 ValueError，由接口层转 400。
    调用方负责 commit。
    """
    now = _now()
    day_ago = now - timedelta(hours=24)
    # 同号 60 秒一条
    last_at = await db.scalar(
        select(func.max(LoginCode.created_at)).where(
            LoginCode.phone == phone, LoginCode.channel == "sms"
        )
    )
    if last_at is not None and (now - last_at).total_seconds() < _SEND_INTERVAL_SECONDS:
        raise ValueError("发送过于频繁，请 60 秒后再试")
    # 同号 10 条/天
    phone_today = await db.scalar(
        select(func.count()).select_from(LoginCode).where(
            LoginCode.phone == phone, LoginCode.created_at >= day_ago
        )
    )
    if (phone_today or 0) >= _SEND_DAILY_PER_PHONE:
        raise ValueError("该手机号今日验证码发送次数已达上限")
    # 同 IP 20 条/天
    if ip:
        ip_today = await db.scalar(
            select(func.count()).select_from(LoginCode).where(
                LoginCode.ip == ip, LoginCode.created_at >= day_ago
            )
        )
        if (ip_today or 0) >= _SEND_DAILY_PER_IP:
            raise ValueError("当前网络环境发送次数已达上限，请稍后再试")

    code = f"{secrets.randbelow(1000000):06d}"
    db.add(
        LoginCode(
            phone=phone,
            code_hash=_code_hash(phone, code),
            channel="sms",
            ip=ip,
            expires_at=now + timedelta(minutes=_CODE_TTL_MINUTES),
        )
    )
    try:
        cfg = await get_login_integrations(db)
        return await _send_sms(cfg.get("sms") or {}, phone, code)
    except Exception:
        raise ValueError("验证码发送失败，请稍后重试或联系管理员") from None


async def verify_login_code(db: AsyncSession, phone: str, code: str) -> bool:
    """校验验证码：最新一条未用记录，过期/超次/不符均失败；通过则标记已用。

    调用方负责 commit。
    """
    row = await db.scalar(
        select(LoginCode)
        .where(
            LoginCode.phone == phone,
            LoginCode.channel == "sms",
            LoginCode.used.is_(False),
        )
        .order_by(LoginCode.id.desc())
        .limit(1)
    )
    if row is None:
        return False
    if row.expires_at < _now() or row.attempts >= _CODE_MAX_ATTEMPTS:
        return False
    row.attempts += 1
    if row.code_hash != _code_hash(phone, code):
        return False
    row.used = True
    return True
