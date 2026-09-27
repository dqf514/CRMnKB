from datetime import datetime

from sqlalchemy import ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    """操作审计日志（登录/增删改/恢复/导入导出等关键动作）。"""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # login / create / update / delete / restore / purge / import / export / reset_password ...
    action: Mapped[str]
    # customer / kb / file / user ...
    resource_type: Mapped[str | None]
    resource_id: Mapped[int | None]
    detail: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
