from datetime import datetime

from sqlalchemy import ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserGroup(Base):
    __tablename__ = "user_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str]
    description: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
