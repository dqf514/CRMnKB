from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, SmallInteger, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    name: Mapped[str]
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    stage: Mapped[str] = mapped_column(default="prospecting")
    expected_close_date: Mapped[date | None]
    probability: Mapped[int] = mapped_column(SmallInteger, default=0)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
