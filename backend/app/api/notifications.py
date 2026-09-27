from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.notification import Notification
from app.models.user import User
from app.schemas.reminder import NotificationListOut, NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListOut)
async def list_notifications(
    unread_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filters = [Notification.tenant_id == user.tenant_id, Notification.user_id == user.id]
    if unread_only:
        filters.append(Notification.is_read.is_(False))
    stmt = (
        select(Notification)
        .where(*filters)
        .order_by(Notification.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    unread_count = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.tenant_id == user.tenant_id,
            Notification.user_id == user.id,
            Notification.is_read.is_(False),
        )
    )
    return NotificationListOut(items=result.scalars().all(), unread_count=unread_count or 0)


@router.post("/{notification_id}/read", response_model=NotificationOut)
async def mark_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    notification = await db.get(Notification, notification_id)
    if (
        notification is None
        or notification.tenant_id != user.tenant_id
        or notification.user_id != user.id
    ):
        raise HTTPException(status_code=404, detail="通知不存在")
    notification.is_read = True
    await db.commit()
    await db.refresh(notification)
    return notification


@router.post("/read_all")
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        update(Notification)
        .where(
            Notification.tenant_id == user.tenant_id,
            Notification.user_id == user.id,
            Notification.is_read.is_(False),
        )
        .values(is_read=True)
    )
    await db.commit()
    return {"updated": result.rowcount}
