from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.ai_feedback import AiFeedback
from app.models.rag_query_log import RagQueryLog
from app.models.user import User
from app.schemas.feedback import FeedbackCreate, FeedbackOut, FeedbackStatsOut

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackOut, status_code=201)
async def create_feedback(
    body: FeedbackCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if body.query_log_id is not None:
        log = await db.get(RagQueryLog, body.query_log_id)
        if log is None or log.tenant_id != user.tenant_id:
            raise HTTPException(status_code=404, detail="问答日志不存在")
    feedback = AiFeedback(
        tenant_id=user.tenant_id,
        user_id=user.id,
        query_log_id=body.query_log_id,
        rating=body.rating,
        comment=body.comment,
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    return feedback


@router.get("/stats", response_model=FeedbackStatsOut)
async def feedback_stats(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    base = select(func.count()).select_from(AiFeedback).where(AiFeedback.tenant_id == user.tenant_id)
    total = await db.scalar(base)
    useful = await db.scalar(base.where(AiFeedback.rating == "useful"))
    useless = await db.scalar(base.where(AiFeedback.rating == "useless"))
    total = total or 0
    useful = useful or 0
    useless = useless or 0
    return FeedbackStatsOut(
        total=total,
        useful=useful,
        useless=useless,
        useful_rate=round(useful / total, 4) if total else 0.0,
    )
