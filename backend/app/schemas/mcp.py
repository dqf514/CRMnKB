"""PR-F：MCP server 管理的 Pydantic schema。"""
from datetime import datetime

from pydantic import BaseModel, Field


class McpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str | None = None
    transport: str = Field(default="stdio", pattern=r"^(stdio|sse)$")
    config: dict = {}  # stdio: {command, args, env?} / sse: {url, headers?, auth_token?}
    enabled: bool = False


class McpServerUpdate(BaseModel):
    display_name: str | None = None
    transport: str | None = Field(default=None, pattern=r"^(stdio|sse)$")
    config: dict | None = None
    enabled: bool | None = None


class McpServerOut(BaseModel):
    id: int
    name: str
    display_name: str | None
    transport: str
    config: dict = {}  # 敏感字段已脱敏
    enabled: bool
    status: str
    last_connected_at: datetime | None = None
    last_error: str | None = None
    discovered_tools: list = []
    created_at: datetime | None = None
    updated_at: datetime | None = None


class McpToolOut(BaseModel):
    """MCP server 发现的单个工具。"""

    name: str
    description: str
    input_schema: dict = {"type": "object", "properties": {}}


class McpConnectResult(BaseModel):
    """connect 端点返回（PR-F）：连接结果 + 发现的工具。"""

    ok: bool
    error: str | None = None
    tools: list[McpToolOut] = []


class McpSyncResult(BaseModel):
    """sync-tools 端点返回：注册到 skills 表的工具数量。"""

    synced: int