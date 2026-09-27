from datetime import datetime

from sqlalchemy import ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DocumentVersion(Base):
    """知识文档的解析版本历史：每次重解析前保存上一版内容。

    便于回看/恢复旧版本（内容为解析后的文本快照）。
    """

    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE")
    )
    content: Mapped[str] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
