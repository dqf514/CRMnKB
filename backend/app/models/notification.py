from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL")
    )
    title: Mapped[str]
    content: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(default="reminder")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # 分享通知的跳转目标（type='share' 时使用）：kb / file / notebook
    resource_type: Mapped[str | None]
    resource_id: Mapped[int | None]
