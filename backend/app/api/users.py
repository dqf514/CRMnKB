"""轻量用户目录：供分享下拉选择（仅 id/username/name，不含敏感字段）。

团队隔离：admin 返回全租户用户；普通用户只返回同团队成员（分享目标只能选同团队的人）；
个人用户（无团队）返回空列表——其角色无 share 权限，本接口仅作兜底。
"""
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
    filters = [User.tenant_id == user.tenant_id, User.status == 1]
    if user.role != "admin":
        if user.group_id is None:
            return []  # 个人用户不参与团队共享
        filters.append(User.group_id == user.group_id)
    rows = (
        (
            await db.execute(
                select(User).where(*filters).order_by(User.id)
            )
        )
        .scalars()
        .all()
    )
    return [{"id": u.id, "username": u.username, "name": u.name} for u in rows]
