"""轻量用户目录：供分享下拉选择（仅 id/username/name，不含敏感字段）。"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.user import User

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
async def list_tenant_users(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = (
        (
            await db.execute(
                select(User).where(
                    User.tenant_id == user.tenant_id,
                    User.status == 1,
                ).order_by(User.id)
            )
        )
        .scalars()
        .all()
    )
    return [{"id": u.id, "username": u.username, "name": u.name} for u in rows]
