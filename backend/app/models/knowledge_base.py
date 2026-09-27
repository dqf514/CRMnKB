from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        # 每租户仅一个自动 KB（is_auto=true）：部分唯一索引（仅对 is_auto=TRUE 生效），
        # 不影响普通知识库（is_auto=false）可建任意多个
        Index(
            "uq_kb_tenant_one_auto",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_auto = TRUE"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str]
    description: Mapped[str | None]
    # general / customer（客户专属）/ auto（上传即自动入库）
    type: Mapped[str] = mapped_column(default="general")
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    # 自动 KB：上传无 kb_ids 时系统创建/复用，仅用于托管散文件。前端默认隐藏。
    is_auto: Mapped[bool] = mapped_column(Boolean, default=False)
    # 软删除时间（回收站）；NULL = 正常
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # 权限：owner（创建者）；is_private=True 私有（仅 owner + 被分享者），False 团队可见
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    is_private: Mapped[bool] = mapped_column(Boolean, default=True)
