from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.customer import Customer
from app.models.task import Task
from app.models.user import User
from app.schemas.task import TaskCreate, TaskListOut, TaskOut, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=TaskListOut)
async def list_tasks(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filters = [Task.tenant_id == user.tenant_id]
    if status:
        filters.append(Task.status == status)

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
        customer = await db.get(Customer, body.customer_id)
        if (
            customer is None
            or customer.tenant_id != user.tenant_id
            or customer.deleted_at is not None
        ):
            raise HTTPException(status_code=404, detail="客户不存在")
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


@router.put("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: int,
    body: TaskUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = await _get_task_or_404(db, user.tenant_id, task_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
    if task.status == "completed" and task.completed_at is None:
        task.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(task)
    return task


@router.post("/{task_id}/complete", response_model=TaskOut)
async def complete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = await _get_task_or_404(db, user.tenant_id, task_id)
    task.status = "completed"
    task.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = await _get_task_or_404(db, user.tenant_id, task_id)
    await db.delete(task)
    await db.commit()
