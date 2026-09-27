from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LibraryFile(Base):
    __tablename__ = "library_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_folders.id", ondelete="SET NULL")
    )
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    file_name: Mapped[str]
    file_path: Mapped[str]
    file_type: Mapped[str]
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    # 是否为可解析格式（pdf/docx/txt/md）；False = 仅存储
    supported: Mapped[bool] = mapped_column(Boolean, default=False)
    # 软删除时间（回收站）；NULL = 正常
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # 权限：owner（上传者）；is_private=True 私有，False 团队可见
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    is_private: Mapped[bool] = mapped_column(Boolean, default=True)
    # 同步（本地 App / OneDrive 式）：内容变更时间与内容哈希（sha256），
    # 客户端用 updated_at 作增量游标、content_hash 做冲突检测
    updated_at: Mapped[datetime | None]
    content_hash: Mapped[str | None] = mapped_column(String(64))
