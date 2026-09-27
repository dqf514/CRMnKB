"""Agent 审批单的请求/响应 DTO。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AgentApprovalCreate(BaseModel):
    """POST /agent-approvals（内部端点，MCP 令牌）请求体。"""

    tool_name: str
    arguments: dict = Field(default_factory=dict)
    summary: str
    chat_session_id: int | None = None


class AgentApprovalDecide(BaseModel):
    """POST /agent-approvals/{id}/decide 请求体。"""

    decision: Literal["approve", "reject"]
    reason: str | None = None


class AgentApprovalOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    tenant_id: int
    requester_user_id: int | None = None
    chat_session_id: int | None = None
    tool_name: str
    arguments: dict = Field(default_factory=dict)
    summary: str
    status: str
    decided_by: int | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    result: str | None = None
    created_at: datetime
    updated_at: datetime
