from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class McpServer(Base):
    """PR-F：MCP server 配置。

    每个租户可连接多个 MCP server（stdio / sse transport）。
    工具发现通过 connect 调用 list_tools() 后缓存到 discovered_tools，
    再由 sync-tools 把每个工具注册为 Skill 行（type=mcp）。
    """

    __tablename__ = "mcp_servers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_mcp_servers_tenant_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str]  # 租户内唯一（与 builtin skill / api skill 命名空间隔离）
    display_name: Mapped[str | None]
    # stdio / sse（Phase 2：streamable_http）
    transport: Mapped[str] = mapped_column(default="stdio")
    # stdio: {"command": str, "args": [str], "env": {...}?}
    # sse:   {"url": str, "headers": {...}?, "auth_token": str?}
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # unknown / connected / error / disconnected
    status: Mapped[str] = mapped_column(default="unknown")
    last_connected_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    # 工具列表缓存：[{name, description, input_schema}, ...]
    discovered_tools: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )