from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Industry(Base):
    """行业字典（租户级可配置，init_db 预置全行业清单）。"""

    __tablename__ = "industries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_industries_tenant_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str]
    sort: Mapped[int] = mapped_column(default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
