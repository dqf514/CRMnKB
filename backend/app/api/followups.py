from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.customer import Customer
from app.models.follow_up import FollowUpRecord
from app.models.user import User
from app.schemas.followup import FollowUpCreate, FollowUpOut
from app.services.followup_hooks import run_after_followup_created
from app.services.permissions import get_access_for, satisfies

router = APIRouter(prefix="/customers", tags=["followups"])


async def _check_customer(
    db: AsyncSession, user: User, customer_id: int, required: str = "read"
) -> Customer:
    """取客户并校验归属/软删/访问权限（read 无权返回 404，edit 无权返回 403）。"""
    customer = await db.get(Customer, customer_id)
    if customer is None or customer.tenant_id != user.tenant_id or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="客户不存在")
    perm = "owner" if user.role == "admin" else await get_access_for(
        db, user.tenant_id, user.id, "customer", customer
    )
    if not satisfies(perm, required):
        if required == "read":
            raise HTTPException(status_code=404, detail="客户不存在")
        raise HTTPException(status_code=403, detail="没有该客户的操作权限")
    return customer


@router.get("/{customer_id}/followups", response_model=list[FollowUpOut])
async def list_followups(
    customer_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _check_customer(db, user, customer_id)
    stmt = (
        select(FollowUpRecord)
        .where(FollowUpRecord.customer_id == customer_id)
        .order_by(FollowUpRecord.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/{customer_id}/followups", response_model=FollowUpOut, status_code=201)
async def create_followup(
    customer_id: int,
    body: FollowUpCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _check_customer(db, user, customer_id, required="edit")
    record = FollowUpRecord(
        customer_id=customer_id,
        user_id=user.id,
        type=body.type,
        content=body.content,
        next_step=body.next_step,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    # 后置钩子（AI 摘要/任务抽取/客户简报刷新）与 Agent 审批路径共用同一函数
    background_tasks.add_task(run_after_followup_created, record.id, customer_id)
    return record
