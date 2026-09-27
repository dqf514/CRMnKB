from datetime import datetime

from sqlalchemy import ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # customer_analysis / sales_weekly / sales_monthly / custom
    type: Mapped[str]
    title: Mapped[str]
    # generating / revising / ready / failed
    status: Mapped[str] = mapped_column(default="generating")
    content: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    # 生成进度提示（"正在检索资料…/AI 生成中…"），前端轮询展示滚动进度
    progress: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    @property
    def format(self) -> str:
        """输出格式（custom 报告为 html，其余 markdown），存于 params。"""
        return (self.params or {}).get("format", "markdown")

    @property
    def revisions(self) -> list:
        """对话式修改的历史版本（旧版本在前），存于 params。"""
        return (self.params or {}).get("revisions") or []
