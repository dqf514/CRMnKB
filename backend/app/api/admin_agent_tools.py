"""管理端 agent 工具权限白名单（全租户生效）：session/request_permission 自动放行清单。

白名单存 system_settings 表 key=agent_auto_allow_tools（整体替换）；dsh 权限应答
链按此白名单自动 allow-once，白名单外一律拒绝。写操作正常应走后端 agent_approvals
审批链——把写工具加入白名单等于绕开审批，管理端有红色风险提示。
仅硬超管可用（改的是全租户 agent 能力边界，不走 RBAC 权限点）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin
from app.models.user import User
from app.services import agent_permissions

router = APIRouter(prefix="/admin/agent-tools", tags=["admin-agent-tools"])


class ToolsPayload(BaseModel):
    # 整体替换的工具名清单（title 精确匹配，或 MCP 前缀工具按 __ 后缀匹配）
    tools: list[str] = Field(max_length=agent_permissions.MAX_TOOLS)


@router.get("")
async def get_tool_whitelist(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """当前白名单 + 已知工具目录（前端按 source 分组展示，risk 用于风险提示）。"""
    return {
        "whitelist": await agent_permissions.get_auto_allow_tools(db),
        "catalog": agent_permissions.TOOL_CATALOG,
        "defaults": agent_permissions.DEFAULT_AUTO_ALLOW_TOOLS,
    }


@router.put("")
async def set_tool_whitelist(
    body: ToolsPayload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """整体替换白名单（保存即生效——权限应答侧有 60s 缓存，最长一分钟内全量生效）。"""
    try:
        tools = agent_permissions.validate_tools_payload(body.tools)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await agent_permissions.set_auto_allow_tools(db, tools)
    return {"ok": True, "whitelist": tools}
