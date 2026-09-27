from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str]
    description: Mapped[str | None] = mapped_column(Text)
    # interval / daily / weekly / birthday / condition
    trigger_type: Mapped[str]
    trigger_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    # [{"field": ..., "op": ..., "value": ...}]，作用于 customers 表白名单字段
    conditions: Mapped[list] = mapped_column(JSONB, default=list)
    # send_email / create_task / create_notification
    action_type: Mapped[str]
    action_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
