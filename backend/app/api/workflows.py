from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_run import WorkflowRun
from app.schemas.workflow import (
    WorkflowCreate,
    WorkflowOut,
    WorkflowRunListOut,
    WorkflowRunResult,
    WorkflowUpdate,
)
from app.services.workflow import ACTION_TYPES, TRIGGER_TYPES, run_workflow

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("", response_model=list[WorkflowOut])
async def list_workflows(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(Workflow)
        .where(Workflow.tenant_id == user.tenant_id)
        .order_by(Workflow.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=WorkflowOut, status_code=201)
async def create_workflow(
    body: WorkflowCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if body.trigger_type not in TRIGGER_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的触发器类型: {body.trigger_type}")
    if body.action_type not in ACTION_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的动作类型: {body.action_type}")
    workflow = Workflow(tenant_id=user.tenant_id, **body.model_dump())
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    return workflow


async def _get_workflow_or_404(db: AsyncSession, tenant_id: int, workflow_id: int) -> Workflow:
    workflow = await db.get(Workflow, workflow_id)
    if workflow is None or workflow.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return workflow


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(
    workflow_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await _get_workflow_or_404(db, user.tenant_id, workflow_id)


@router.put("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: int,
    body: WorkflowUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workflow = await _get_workflow_or_404(db, user.tenant_id, workflow_id)
    updates = body.model_dump(exclude_unset=True)
    if "trigger_type" in updates and updates["trigger_type"] not in TRIGGER_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的触发器类型: {updates['trigger_type']}")
    if "action_type" in updates and updates["action_type"] not in ACTION_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的动作类型: {updates['action_type']}")
    for field, value in updates.items():
        setattr(workflow, field, value)
    await db.commit()
    await db.refresh(workflow)
    return workflow


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workflow = await _get_workflow_or_404(db, user.tenant_id, workflow_id)
    await db.delete(workflow)  # 执行历史由外键 ON DELETE CASCADE 清理
    await db.commit()


@router.post("/{workflow_id}/run", response_model=WorkflowRunResult)
async def run_workflow_now(
    workflow_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    workflow = await _get_workflow_or_404(db, user.tenant_id, workflow_id)
    run = await run_workflow(db, workflow)
    await db.commit()
    return WorkflowRunResult(
        run_id=run.id,
        status=run.status,
        matched_count=run.matched_count,
        detail=run.detail,
    )


@router.get("/{workflow_id}/runs", response_model=WorkflowRunListOut)
async def list_runs(
    workflow_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_workflow_or_404(db, user.tenant_id, workflow_id)
    stmt = select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    stmt = stmt.order_by(WorkflowRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return WorkflowRunListOut(items=result.scalars().all(), total=total or 0)
