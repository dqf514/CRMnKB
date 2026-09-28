import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.database import AsyncSessionLocal
from app.models.customer import Customer
from app.models.follow_up import FollowUpRecord
from app.models.user import User
from app.schemas.followup import FollowUpCreate, FollowUpOut
from app.services.ai_tasks import extract_and_create_tasks
from app.services.llm import resolve_chat_llm
from app.services.pipeline_brief import generate_brief

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/customers", tags=["followups"])


async def _check_customer(db: AsyncSession, tenant_id: int, customer_id: int) -> Customer:
    customer = await db.get(Customer, customer_id)
    if customer is None or customer.tenant_id != tenant_id or customer.deleted_at is not None:
        raise HTTPException(status_code=404, detail="客户不存在")
    return customer


async def generate_ai_summary(record_id: int) -> None:
    """后台任务：调用 chat LLM 生成一句话总结，失败则留空不报错。"""
    async with AsyncSessionLocal() as session:
        record = await session.get(FollowUpRecord, record_id)
        if record is None:
            return
        try:
            customer = await session.get(Customer, record.customer_id)
            tenant_id = customer.tenant_id if customer else None
            chat_llm = await resolve_chat_llm(caller="summary", tenant_id=tenant_id)
            summary = await chat_llm.chat(
                [
                    {
                        "role": "user",
                        "content": f"请用一句话总结以下客户跟进记录（不超过50字）：\n{record.content}",
                    }
                ]
            )
            record.ai_summary = summary.strip()
            await session.commit()
        except Exception as exc:
            logger.warning("跟进记录 %s AI 总结生成失败（忽略）: %s", record_id, exc)


@router.get("/{customer_id}/followups", response_model=list[FollowUpOut])
async def list_followups(
    customer_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _check_customer(db, user.tenant_id, customer_id)
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
    await _check_customer(db, user.tenant_id, customer_id)
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
    background_tasks.add_task(generate_ai_summary, record.id)
    background_tasks.add_task(extract_and_create_tasks, record.id)
    # 跟进变化后异步刷新客户 AI 阶段简报
    background_tasks.add_task(generate_brief, customer_id)
    return record
