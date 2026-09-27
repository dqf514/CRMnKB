from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LibraryFolder(Base):
    __tablename__ = "library_folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_folders.id", ondelete="CASCADE")
    )
    name: Mapped[str]
    # 权限：owner（创建者）；is_private=True 私有（仅 owner + 被分享者），False 团队可见
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    is_private: Mapped[bool] = mapped_column(Boolean, default=True)
    # 软删除时间（回收站）；NULL = 正常
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
