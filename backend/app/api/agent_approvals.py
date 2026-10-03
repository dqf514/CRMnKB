"""Agent 审批链 API（dsh 基座阶段 2）。

- POST /agent-approvals：内部端点，仅接受 dsh MCP 令牌（aud=dsh-mcp），
  普通登录 JWT 拒绝——与 /api/mcp 同一套解析逻辑（decode_mcp_authorization）。
- GET /agent-approvals：登录用户；admin 看全租户，普通用户只看自己发起的。
- POST /agent-approvals/{id}/decide：仅 admin；approve 时同步执行对应动作，
  外部副作用（发邮件、跟进 AI 钩子）在 commit 成功后异步触发。
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_admin
from app.models.agent_approval import AgentApproval
from app.models.user import User
from app.schemas.agent_approval import (
    AgentApprovalCreate,
    AgentApprovalDecide,
    AgentApprovalOut,
)
from app.services import agent_approvals as approval_svc
from app.services.agent_approvals import ApprovalError
from app.services.mcp_server import McpAuthError, decode_mcp_authorization, resolve_mcp_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent-approvals", tags=["agent-approvals"])


async def get_mcp_caller(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    """内部端点鉴权：仅接受 dsh MCP 令牌（复用 MCP server 的令牌解析与账号态校验）。"""
    try:
        payload = decode_mcp_authorization(request.scope.get("headers", []))
        return await resolve_mcp_user(db, payload)
    except McpAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@router.post("", status_code=201)
async def submit_approval(
    body: AgentApprovalCreate,
    db: AsyncSession = Depends(get_db),
    caller: User = Depends(get_mcp_caller),
):
    """创建 pending 审批单 + 通知全部 admin（MCP 写工具的服务端入口）。"""
    try:
        approval = await approval_svc.create_approval(
            db, caller, body.tool_name, body.arguments, body.summary, body.chat_session_id
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return {"approval_id": approval.id}


@router.get("", response_model=list[AgentApprovalOut])
async def list_approvals(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """审批单列表：admin 看全租户，普通用户只看自己发起的；支持 status 过滤。"""
    filters = [AgentApproval.tenant_id == user.tenant_id]
    if user.role != "admin":
        filters.append(AgentApproval.requester_user_id == user.id)
    if status:
        filters.append(AgentApproval.status == status)
    stmt = (
        select(AgentApproval)
        .where(*filters)
        .order_by(AgentApproval.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def _run_post_commit_hook(approval_id: int, hook) -> None:
    """commit 成功后执行外部副作用（发邮件 / 跟进 AI 钩子），失败仅记日志。

    此时审批单已提交为 executed，副作用失败不再翻转状态（与手动路径的
    BackgroundTasks 行为一致）。
    """
    try:
        await hook()
    except Exception as exc:
        logger.warning("审批单 %s 的 commit 后动作失败（忽略）: %s", approval_id, exc)


@router.post("/{approval_id}/decide", response_model=AgentApprovalOut)
async def decide(
    approval_id: int,
    body: AgentApprovalDecide,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """审批（仅 admin）：approve 同步执行对应动作，结果写 result 并通知发起人。"""
    approval = await db.get(AgentApproval, approval_id)
    if approval is None or approval.tenant_id != admin.tenant_id:
        raise HTTPException(status_code=404, detail="审批单不存在")
    post_commit: list = []
    try:
        await approval_svc.decide_approval(
            db, approval, admin, body.decision, body.reason, post_commit=post_commit
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    await db.refresh(approval)
    # 外部副作用（发邮件、跟进 AI 钩子）移到 commit 成功后异步触发：
    # 钩子另开会话读库，提前跑读不到未提交数据；失败也不影响已提交的审批结果
    for hook in post_commit:
        asyncio.create_task(_run_post_commit_hook(approval_id, hook))
    return approval


@router.post("/{approval_id}/retry", response_model=AgentApprovalOut)
async def retry(
    approval_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """重试执行失败的审批单（仅 admin、仅 failed 态）：重新跑执行器，结果写 result。"""
    approval = await db.get(AgentApproval, approval_id)
    if approval is None or approval.tenant_id != admin.tenant_id:
        raise HTTPException(status_code=404, detail="审批单不存在")
    post_commit: list = []
    try:
        await approval_svc.retry_approval(db, approval, admin, post_commit=post_commit)
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    await db.refresh(approval)
    # 与 decide 一致：外部副作用在 commit 成功后异步触发
    for hook in post_commit:
        asyncio.create_task(_run_post_commit_hook(approval_id, hook))
    return approval
