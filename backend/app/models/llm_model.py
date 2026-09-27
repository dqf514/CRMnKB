from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LlmModel(Base):
    __tablename__ = "llm_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str]
    # api / ollama
    provider: Mapped[str]
    # chat / embed / vision / asr / rerank
    model_type: Mapped[str]
    base_url: Mapped[str]
    api_key: Mapped[str | None]  # 存明文，响应中脱敏
    model: Mapped[str]
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
