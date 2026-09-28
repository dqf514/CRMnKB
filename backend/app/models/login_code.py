from datetime import datetime

from sqlalchemy import Boolean, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LoginCode(Base):
    """登录验证码（短信通道）：只存哈希，10 分钟有效，最多试 5 次，一次性使用。

    限流规则在 services/login_channels.py：同手机号 60 秒一条、每手机号每天 10 条、
    每 IP 每天 20 条。channel 预留（未来微信扫码态等可复用本表）。
    """

    __tablename__ = "login_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(20), default="sms")
    ip: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime]
    attempts: Mapped[int] = mapped_column(default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
