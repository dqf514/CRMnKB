from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.customer import Customer
from app.models.task import Task
from app.models.user import User
from app.schemas.task import TaskCreate, TaskListOut, TaskOut, TaskUpdate
from app.services.permissions import customer_visible_clause, get_access_for, satisfies

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _utcnow() -> datetime:
    """naive UTC（项目约定：连接时区已固定 UTC，列均为 timestamp without time zone）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _ensure_customer_edit(db: AsyncSession, user: User, customer_id: int) -> None:
    """任务挂到客户上时，要求对该客户有编辑权限（客户不存在返回 404，无权 403）。"""
    customer = await db.get(Customer, customer_id)
    if (
        customer is None
        or customer.tenant_id != user.tenant_id
        or customer.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="客户不存在")
    perm = "owner" if user.role == "admin" else await get_access_for(
        db, user.tenant_id, user.id, "customer", customer
    )
    if not satisfies(perm, "edit"):
        raise HTTPException(status_code=403, detail="没有该客户的操作权限")


@router.get("", response_model=TaskListOut)
async def list_tasks(
    status: str | None = Query(None),
    customer_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filters = [Task.tenant_id == user.tenant_id]
    if status:
        filters.append(Task.status == status)
    if customer_id is not None:
        filters.append(Task.customer_id == customer_id)
    # 关联了私有客户的任务，对无权限用户隐藏（无关联客户的任务不受影响）
    visible = customer_visible_clause(user)
    if visible is not None:
        filters.append(or_(Task.customer_id.is_(None), visible))

    total = await db.scalar(select(func.count()).select_from(Task).where(*filters))
    stmt = (
        select(Task, Customer.name)
        .outerjoin(Customer, Customer.id == Task.customer_id)
        .where(*filters)
        .order_by(Task.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).all()
    items = []
    for task, customer_name in rows:
        out = TaskOut.model_validate(task)
        out.customer_name = customer_name
        items.append(out)
    return TaskListOut(items=items, total=total or 0)


@router.post("", response_model=TaskOut, status_code=201)
async def create_task(
    body: TaskCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if body.customer_id is not None:
        await _ensure_customer_edit(db, user, body.customer_id)
    task = Task(
        tenant_id=user.tenant_id,
        user_id=user.id,
        status="pending",
        ai_generated=False,
        source="manual",
        **body.model_dump(),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def _get_task_or_404(db: AsyncSession, tenant_id: int, task_id: int) -> Task:
    task = await db.get(Task, task_id)
    if task is None or task.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


async def _get_task_for_edit(db: AsyncSession, user: User, task_id: int) -> Task:
    """取任务并校验：关联了客户时要求对该客户有编辑权限。"""
    task = await _get_task_or_404(db, user.tenant_id, task_id)
    if task.customer_id is not None:
        await _ensure_customer_edit(db, user, task.customer_id)
    return task


@router.put("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: int,
    body: TaskUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = await _get_task_for_edit(db, user, task_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
    if task.status == "completed" and task.completed_at is None:
        task.completed_at = _utcnow()
    await db.commit()
    await db.refresh(task)
    return task


@router.post("/{task_id}/complete", response_model=TaskOut)
async def complete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = await _get_task_for_edit(db, user, task_id)
    task.status = "completed"
    task.completed_at = _utcnow()
    await db.commit()
    await db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = await _get_task_for_edit(db, user, task_id)
    await db.delete(task)
    await db.commit()
