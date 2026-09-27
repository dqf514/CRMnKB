from datetime import datetime

from sqlalchemy import ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AiFeedback(Base):
    __tablename__ = "ai_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    query_log_id: Mapped[int | None] = mapped_column(
        ForeignKey("rag_query_logs.id", ondelete="SET NULL")
    )
    # useful / useless
    rating: Mapped[str]
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
