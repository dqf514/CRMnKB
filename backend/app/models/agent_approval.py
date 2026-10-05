"""Agent 审批单（dsh 基座阶段 2）。

架构原则：dsh 侧 MCP 工具只允许只读；一切写操作（新增跟进、发邮件等）
由工具创建审批单，admin 批准后由 FastAPI 执行——LLM 永远无法直接写库。
"""
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentApproval(Base):
    __tablename__ = "agent_approvals"
    __table_args__ = (
        # 热查询复合索引：审批列表按租户 + 状态过滤
        Index("ix_agent_approvals_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    # 发起者（dsh 进程所属用户）；用户删除后置 NULL 保留单据
    requester_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # 关联的问答会话（可空，MCP 工具侧未必携带）
    chat_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL")
    )
    tool_name: Mapped[str]
    arguments: Mapped[dict] = mapped_column(JSONB, default=dict)
    # 给审批人看的人话摘要（含客户名/收件人/内容概要）
    summary: Mapped[str] = mapped_column(Text)
    # pending / approved / rejected / executed / failed / expired
    # （approve 后同步执行，approved 仅作中间态，正常落到 executed/failed）
    status: Mapped[str] = mapped_column(default="pending")
    decided_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None]
    decision_reason: Mapped[str | None] = mapped_column(Text)
    # 执行结果 / 失败原因
    result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
