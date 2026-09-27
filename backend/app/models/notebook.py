from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Notebook(Base):
    """PR-G：NotebookLM 风格"工作区"容器——绑多源（多 KB + 多文件）+ 多内容。"""

    __tablename__ = "notebooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str]  # 同租户内不强制唯一（同名可区分）
    description: Mapped[str | None]
    # 绑定的源（每次打开自动加载，可临时切换）
    source_kb_ids: Mapped[list] = mapped_column(JSONB, default=list)
    source_file_ids: Mapped[list] = mapped_column(JSONB, default=list)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
    # 软删（回收站）：删除仅标记，内容保留；恢复即回
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # 权限：created_by 即 owner；is_private=True 私有，False 团队可见（内容继承）
    is_private: Mapped[bool] = mapped_column(Boolean, default=True)


class NotebookNote(Base):
    """PR-G：工作区下的多内容。支持手动写 / 从 chat 保存 / 从报告保存。"""

    __tablename__ = "notebook_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    notebook_id: Mapped[int] = mapped_column(
        ForeignKey("notebooks.id", ondelete="CASCADE")
    )
    title: Mapped[str]
    content: Mapped[str] = mapped_column(Text, default="")
    # manual / chat / report
    source_type: Mapped[str] = mapped_column(default="manual")
    # {session_id, query_log_id, question, answer, sources}（chat）
    # {report_id, title}（report）
    source_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )