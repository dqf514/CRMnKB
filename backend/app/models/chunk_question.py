from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.models.base import Base


class ChunkQuestion(Base):
    """自动生成的问题：每个 chunk 在 ingestion 时用 LLM 生成 3-5 个候选问题。

    检索时同时查 chunk 表与 chunk_questions 表，按 chunk_id 合并命中，
    解决"用户表达和原文用语不一致"的召回难题（参照 MaxKB Problem 模型）。

    删除 chunk 时由外键 ON DELETE CASCADE 自动清理。"""

    __tablename__ = "chunk_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE")
    )
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.EMBEDDING_DIM), nullable=True
    )
    # 命中次数统计（用于分析哪些自动生成的问题真的帮到召回）
    hit_num: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())