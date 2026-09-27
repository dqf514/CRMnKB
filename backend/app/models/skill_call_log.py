from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SkillCallLog(Base):
    """PR-E：每次 skill 调用的埋点日志（与 llm_call_logs 模式一致）。

    写入由 record_skill_call_log() 在独立 session 完成（失败静默，不影响主流程）。
    用于：
      - 管理端调用统计（总调用 / 平均耗时 / 失败率）
      - 排查 skill 异常
      - 合规审计（用户何时调用了哪个工具）
    """

    __tablename__ = "skill_call_logs"
    __table_args__ = (
        Index("ix_skill_call_logs_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"))
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    skill_name: Mapped[str]  # skill.name（mcp 工具含 server__tool 前缀）
    # 调用来源：chat（对话）/ test（管理端测试）/ agent（agent 循环内置）
    caller: Mapped[str] = mapped_column(default="chat")
    latency_ms: Mapped[int] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())