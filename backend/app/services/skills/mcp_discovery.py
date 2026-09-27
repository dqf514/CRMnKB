"""PR-F：MCP 工具发现 + 同步到 skills 表。"""
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_server import McpServer
from app.models.skill import Skill as SkillRow
from app.services.skills.mcp_pool import get_mcp_pool

logger = logging.getLogger(__name__)


def _tool_to_dict(tool) -> dict:
    """从 MCP Tool 对象抽出 admin 用字段。"""
    return {
        "name": getattr(tool, "name", ""),
        "description": getattr(tool, "description", "") or "",
        "input_schema": getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}},
    }


async def connect_and_list_tools(server: McpServer) -> list[dict]:
    """连接 MCP server 并列出可用工具。

    返回 [{name, description, input_schema}, ...]；连接失败抛异常。
    """
    pool = get_mcp_pool()
    async with pool.get_session(server.id, server.transport, dict(server.config or {})) as session:
        result = await session.list_tools()
    return [_tool_to_dict(t) for t in (result.tools or [])]


async def test_connection(server: McpServer) -> tuple[bool, str | None, list[dict]]:
    """测试连接：成功返回 (True, None, tools)；失败返回 (False, error_msg, [])。"""
    try:
        tools = await connect_and_list_tools(server)
    except Exception as exc:
        return False, str(exc)[:500], []
    return True, None, tools


async def refresh_server_status(
    db: AsyncSession, server: McpServer, success: bool, error: str | None, tools: list[dict] | None
) -> None:
    """更新 server 状态与 discovered_tools（不入库事务外调用）。"""
    server.status = "connected" if success else "error"
    server.last_error = error
    if success:
        server.last_connected_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if tools is not None:
            server.discovered_tools = tools
    await db.commit()


async def sync_tools_to_skills(db: AsyncSession, server: McpServer) -> int:
    """把 server.discovered_tools 注册为 Skill 行（type=mcp）。

    已存在同名行则更新 description/parameters/config；不存在的增加。
    返回新增/更新数量。
    """
    if not server.discovered_tools:
        return 0
    # 已有 mcp skill 行（按 server_id 过滤）
    existing_rows = (
        (
            await db.execute(
                select(SkillRow).where(
                    SkillRow.tenant_id == server.tenant_id,
                    SkillRow.type == "mcp",
                )
            )
        )
        .scalars()
        .all()
    )
    existing_by_name: dict[str, SkillRow] = {}
    for r in existing_rows:
        if (r.config or {}).get("server_id") == server.id:
            existing_by_name[r.name] = r

    count = 0
    for tool in server.discovered_tools:
        tool_name = tool["name"]
        # Skill 行名：{server.name}__{tool_name}
        full_name = f"{server.name}__{tool_name}"
        cfg = {
            "server_id": server.id,
            "tool_name": tool_name,
            "transport": server.transport,
            "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            "timeout": 30,
        }
        if full_name in existing_by_name:
            row = existing_by_name[full_name]
            row.description = tool.get("description", "")
            row.config = cfg
            row.display_name = full_name
        else:
            row = SkillRow(
                tenant_id=server.tenant_id,
                name=full_name,
                display_name=full_name,
                description=tool.get("description", ""),
                type="mcp",
                enabled=server.enabled,  # 默认继承 server 启停
                config=cfg,
            )
            db.add(row)
        count += 1
    await db.commit()
    return count


async def delete_server_skills(db: AsyncSession, server: McpServer) -> int:
    """删除 server 关联的所有 skill 行。"""
    result = await db.execute(
        delete(SkillRow).where(
            SkillRow.tenant_id == server.tenant_id,
            SkillRow.type == "mcp",
            SkillRow.config["server_id"].as_integer == server.id,
        )
    )
    await db.commit()
    return result.rowcount or 0