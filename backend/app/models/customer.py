from datetime import date, datetime

from sqlalchemy import ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str]
    # 旧单列行业：保留不再读写，数据已并入 industries（见 init_db 一次性迁移）
    industry: Mapped[str | None]
    # 多选行业（行业名称数组）与标签数组
    industries: Mapped[list] = mapped_column(JSONB, default=list)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    company: Mapped[str | None]
    position: Mapped[str | None]
    wechat: Mapped[str | None]
    phone: Mapped[str | None]
    email: Mapped[str | None]
    address: Mapped[str | None]
    birthday: Mapped[date | None]
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    # potential / intention / negotiating / closed / lost
    status: Mapped[str] = mapped_column(default="potential")
    source: Mapped[str | None]
    # AI 客户画像
    profile: Mapped[str | None] = mapped_column(Text)
    # idle / generating / ready / failed
    profile_status: Mapped[str] = mapped_column(default="idle")
    profile_updated_at: Mapped[datetime | None]
    # DDQ（尽职调查）状态：none / pending / completed
    ddq_status: Mapped[str] = mapped_column(String(20), default="none")
    # AI 阶段简报（Markdown）与生成时间
    ai_brief: Mapped[str | None] = mapped_column(Text)
    ai_brief_at: Mapped[datetime | None]
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # 软删除时间（回收站）；NULL = 正常
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
