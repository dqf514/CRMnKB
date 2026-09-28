from datetime import datetime

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    username: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    name: Mapped[str]
    role: Mapped[str] = mapped_column(default="user")
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_groups.id", ondelete="SET NULL")
    )
    # 1 启用 / 0 停用（停用用户登录 403）
    status: Mapped[int] = mapped_column(default=1)
    last_login_at: Mapped[datetime | None]
    # 改密时间（naive UTC）：早于该时间签发的 JWT 一律失效；NULL 跳过校验
    password_changed_at: Mapped[datetime | None]
    email: Mapped[str | None] = mapped_column(String(100))
    # 手机号（租户内唯一，部分唯一索引在 init_db 幂等创建）：为手机号/微信登录做准备
    phone: Mapped[str | None] = mapped_column(String(20))
    # 微信登录预留：开放平台 openid（租户内唯一）/ unionid（跨应用打通用，可空）
    wechat_openid: Mapped[str | None] = mapped_column(String(64))
    wechat_unionid: Mapped[str | None] = mapped_column(String(64))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    preferences: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
