from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReminderRule(Base):
    __tablename__ = "reminder_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str]
    # days_since_last_followup / opportunity_stagnant / task_due_soon
    trigger_type: Mapped[str]
    # {"threshold_days": n} 或 {"threshold_hours": n}
    trigger_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    # {"template": "支持 {{customer_name}} / {{days}} 等占位"}
    action_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
