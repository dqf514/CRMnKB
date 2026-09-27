from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    # 租户内唯一（init_db 建唯一索引）；builtin skill 用代码注册名
    name: Mapped[str]
    display_name: Mapped[str | None]
    description: Mapped[str | None]
    # builtin / api
    type: Mapped[str]
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # builtin: provider/api_key 等覆盖；api: method/url/headers/body/parameters
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
