from datetime import datetime

from sqlalchemy import BigInteger, Float, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    kb_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("knowledge_bases.id", ondelete="CASCADE")
    )
    file_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("library_files.id", ondelete="SET NULL")
    )
    title: Mapped[str]
    file_name: Mapped[str]
    file_path: Mapped[str]
    file_type: Mapped[str]
    content: Mapped[str | None] = mapped_column(Text)
    # SQLAlchemy 保留字 metadata，属性名用 doc_metadata，映射到 metadata 列
    doc_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    # processing / ready / failed / unsupported（格式不支持，仅存储）
    status: Mapped[str] = mapped_column(default="processing")
    chunk_count: Mapped[int] = mapped_column(default=0)
    # PR-D：directly_return 高置信短答案。检索相似度 ≥ directly_return_similarity 时直接返回预设答案，跳过 LLM。
    directly_return_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    directly_return_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    @property
    def processing_method(self) -> str | None:
        """摄入方式（text / vision_ocr / image_describe / asr / video_asr），存于 metadata。"""
        return (self.doc_metadata or {}).get("processing_method")

    @property
    def error(self) -> str | None:
        """失败原因：metadata.error 优先，兼容旧数据从 content 的 [处理失败] 前缀解析。"""
        if self.status != "failed":
            return None
        msg = (self.doc_metadata or {}).get("error")
        if msg:
            return msg
        c = self.content or ""
        if c.startswith("[处理失败]"):
            return c[len("[处理失败]"):].strip() or "未知错误"
        return c or None

    @property
    def vision_reviewed_at(self) -> str | None:
        """视觉复核时间（ISO），存于 metadata；无则 None。"""
        return (self.doc_metadata or {}).get("vision_reviewed_at")

    @property
    def vision_review_error(self) -> str | None:
        """视觉复核失败原因，存于 metadata；无则 None。"""
        return (self.doc_metadata or {}).get("vision_review_error")
