"""PR-F：admin MCP server 管理 API。

端点：
  GET    /admin/mcp-servers        列表
  POST   /admin/mcp-servers        新建
  PUT    /admin/mcp-servers/{id}   改 config/enabled/display_name
  DELETE /admin/mcp-servers/{id}   删除（连带关联 skill 行）
  POST   /admin/mcp-servers/{id}/connect    试连 + 刷新 discovered_tools
  POST   /admin/mcp-servers/{id}/disconnect 关闭连接
  POST   /admin/mcp-servers/{id}/sync-tools 同步工具到 skills 表
  GET    /admin/mcp-servers/{id}/tools       列出已发现的工具
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_llm import mask_api_key
from app.api.deps import get_db, require_admin
from app.models.mcp_server import McpServer
from app.models.user import User
from app.schemas.mcp import (
    McpConnectResult,
    McpServerCreate,
    McpServerOut,
    McpServerUpdate,
    McpSyncResult,
    McpToolOut,
)
from app.services.audit import record_audit
from app.services.skills.mcp_discovery import (
    delete_server_skills,
    refresh_server_status,
    sync_tools_to_skills,
    test_connection,
)
from app.services.skills.mcp_pool import get_mcp_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-mcp"], dependencies=[Depends(require_admin)])

_SENSITIVE_MARKS = ("key", "secret", "token", "password", "auth")


def _mask_config(config: dict) -> dict:
    """脱敏 config 中 key/secret/token/password/auth 字段。"""
    masked = {}
    for k, v in (config or {}).items():
        if isinstance(v, str) and any(mark in k.lower() for mark in _SENSITIVE_MARKS):
            masked[k] = mask_api_key(v)
        else:
            masked[k] = v
    return masked


async def _get_server_or_404(db: AsyncSession, tenant_id: int, server_id: int) -> McpServer:
    server = await db.get(McpServer, server_id)
    if server is None or server.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="MCP server 不存在")
    return server


def _to_out(server: McpServer) -> McpServerOut:
    return McpServerOut(
        id=server.id,
        name=server.name,
        display_name=server.display_name,
        transport=server.transport,
        config=_mask_config(server.config or {}),
        enabled=server.enabled,
        status=server.status,
        last_connected_at=server.last_connected_at,
        last_error=server.last_error,
        discovered_tools=server.discovered_tools or [],
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


@router.get("/mcp-servers", response_model=list[McpServerOut])
async def list_mcp_servers(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """列出当前租户所有 MCP server（含状态、最后连接时间、发现的工具数）。"""
    rows = (
        (
            await db.execute(
                select(McpServer)
                .where(McpServer.tenant_id == admin.tenant_id)
                .order_by(McpServer.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_to_out(s) for s in rows]


@router.post("/mcp-servers", response_model=McpServerOut, status_code=201)
async def create_mcp_server(
    body: McpServerCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """新建 MCP server（不立即连接，由 /connect 触发试连）。"""
    # 必填字段校验
    if body.transport == "stdio" and not (body.config or {}).get("command"):
        raise HTTPException(status_code=400, detail="stdio 模式需在 config 中配置 command")
    if body.transport == "sse" and not (body.config or {}).get("url"):
        raise HTTPException(status_code=400, detail="sse 模式需在 config 中配置 url")
    server = McpServer(
        tenant_id=admin.tenant_id,
        name=body.name,
        display_name=body.display_name,
        transport=body.transport,
        config=body.config,
        enabled=body.enabled,
    )
    db.add(server)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="同名 MCP server 已存在")
    record_audit(db, admin, "create", "mcp_server", server.id, {"name": server.name})
    await db.commit()
    await db.refresh(server)
    return _to_out(server)


@router.put("/mcp-servers/{server_id}", response_model=McpServerOut)
async def update_mcp_server(
    server_id: int,
    body: McpServerUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """更新 config / enabled / display_name（修改 config 后建议调 /connect 重连）。"""
    server = await _get_server_or_404(db, admin.tenant_id, server_id)
    changed: dict = {}
    if body.display_name is not None:
        server.display_name = body.display_name
    if body.transport is not None:
        if body.transport != server.transport:
            # transport 变了 → 关闭旧连接
            await get_mcp_pool().close(server.id)
            server.status = "unknown"
        server.transport = body.transport
        changed["transport"] = body.transport
    if body.config is not None:
        server.config = body.config
        changed["config_keys"] = list(body.config.keys())
        # config 变了关闭旧连接
        await get_mcp_pool().close(server.id)
        server.status = "unknown"
    if body.enabled is not None and body.enabled != server.enabled:
        server.enabled = body.enabled
        changed["enabled"] = body.enabled
    if changed:
        record_audit(db, admin, "update", "mcp_server", server.id, changed)
    await db.commit()
    await db.refresh(server)
    return _to_out(server)


@router.delete("/mcp-servers/{server_id}", status_code=204)
async def delete_mcp_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """删除 MCP server（先关闭连接，再删关联 skill 行）。"""
    server = await _get_server_or_404(db, admin.tenant_id, server_id)
    await get_mcp_pool().close(server.id)
    deleted = await delete_server_skills(db, server)
    record_audit(
        db, admin, "delete", "mcp_server", server.id,
        {"name": server.name, "skills_deleted": deleted},
    )
    await db.delete(server)
    await db.commit()


@router.post("/mcp-servers/{server_id}/connect", response_model=McpConnectResult)
async def connect_mcp_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """试连 MCP server：成功 → status=connected + discovered_tools 刷新；失败 → status=error + last_error。"""
    server = await _get_server_or_404(db, admin.tenant_id, server_id)
    ok, error, tools = await test_connection(server)
    await refresh_server_status(db, server, success=ok, error=error, tools=tools)
    return McpConnectResult(
        ok=ok,
        error=error,
        tools=[McpToolOut(name=t["name"], description=t["description"], input_schema=t["input_schema"]) for t in tools],
    )


@router.post("/mcp-servers/{server_id}/disconnect", status_code=204)
async def disconnect_mcp_server(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """关闭 MCP server 长连接（不删除 server 配置）。"""
    server = await _get_server_or_404(db, admin.tenant_id, server_id)
    await get_mcp_pool().close(server.id)
    server.status = "disconnected"
    await db.commit()


@router.post("/mcp-servers/{server_id}/sync-tools", response_model=McpSyncResult)
async def sync_tools(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """把 discovered_tools 注册为 Skill 行（type=mcp）。"""
    server = await _get_server_or_404(db, admin.tenant_id, server_id)
    if not server.discovered_tools:
        raise HTTPException(status_code=400, detail="请先调 /connect 发现工具")
    synced = await sync_tools_to_skills(db, server)
    record_audit(
        db, admin, "sync_tools", "mcp_server", server.id,
        {"synced": synced, "tools_count": len(server.discovered_tools)},
    )
    return McpSyncResult(synced=synced)


@router.get("/mcp-servers/{server_id}/tools", response_model=list[McpToolOut])
async def list_tools(
    server_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """返回 server 已发现的工具（不重连）。"""
    server = await _get_server_or_404(db, admin.tenant_id, server_id)
    return [
        McpToolOut(
            name=t.get("name", ""),
            description=t.get("description", ""),
            input_schema=t.get("input_schema") or {"type": "object", "properties": {}},
        )
        for t in (server.discovered_tools or [])
    ]