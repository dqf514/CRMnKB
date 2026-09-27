from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LoginAttempt(Base):
    """登录尝试记录（DB 持久，跨 worker 生效），用于登录限流。"""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), nullable=True
    )
    username: Mapped[str]
    ip: Mapped[str]
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), index=True
    )
