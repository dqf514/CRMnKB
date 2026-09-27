from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str]
    description: Mapped[str | None] = mapped_column(Text)
    # follow_up / meeting / call / email / report
    type: Mapped[str] = mapped_column(default="follow_up")
    # high / medium / low
    priority: Mapped[str] = mapped_column(default="medium")
    due_date: Mapped[datetime | None]
    # pending / in_progress / completed / cancelled
    status: Mapped[str] = mapped_column(default="pending")
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    # manual / rule / ai_analysis
    source: Mapped[str] = mapped_column(default="manual")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    completed_at: Mapped[datetime | None]
