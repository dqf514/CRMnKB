from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_perm
from app.core.security import hash_password
from app.models.ai_feedback import AiFeedback
from app.models.chat_session import ChatSession
from app.models.customer import Customer
from app.models.follow_up import FollowUpRecord
from app.models.llm_call_log import LlmCallLog
from app.models.notification import Notification
from app.models.opportunity import Opportunity
from app.models.rag_query_log import RagQueryLog
from app.models.report import Report
from app.models.task import Task
from app.models.user import User
from app.models.user_group import UserGroup
from app.services.audit import record_audit
from app.schemas.admin import (
    AdminPasswordReset,
    AdminUserCreate,
    AdminUserListOut,
    AdminUserOut,
    AdminUserUpdate,
    GroupCreate,
    GroupOut,
    GroupUpdate,
)

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(require_perm("user.admin"))]
)


# ---------------------------------------------------------------------------
# 用户管理
# ---------------------------------------------------------------------------

async def _check_role_assignable(
    db: AsyncSession, tenant_id: int, role: str, operator: User
) -> None:
    """角色必须存在于 roles 表；admin 角色仅硬超管可授予（防 user.admin 持有者自我提权）。"""
    if role == "admin":
        if operator.role != "admin":
            raise HTTPException(status_code=403, detail="仅超级管理员可授予 admin 角色")
        return
    from app.models.role import Role

    exists = await db.scalar(
        select(func.count()).select_from(Role).where(
            Role.tenant_id == tenant_id, Role.key == role
        )
    )
    if not exists:
        raise HTTPException(status_code=400, detail=f"角色不存在：{role}")

def _admin_user_out(u: User, group_name: str | None = None) -> AdminUserOut:
    return AdminUserOut(
        id=u.id,
        username=u.username,
        name=u.name,
        email=u.email,
        phone=u.phone,
        role=u.role,
        group_id=u.group_id,
        group_name=group_name,
        status=u.status,
        last_login_at=u.last_login_at,
        created_at=u.created_at,
    )


async def _check_phone_unique(
    db: AsyncSession, tenant_id: int, phone: str | None, exclude_user_id: int | None = None
) -> None:
    """手机号租户内唯一（为后续手机号登录做准备；DB 层有部分唯一索引兜底）。"""
    if not phone:
        return
    stmt = select(User.id).where(User.tenant_id == tenant_id, User.phone == phone)
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    if await db.scalar(stmt) is not None:
        raise HTTPException(status_code=409, detail="该手机号已被其他账号使用")


@router.get("/users", response_model=AdminUserListOut)
async def list_users(
    keyword: str | None = Query(None),
    group_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_perm("user.admin")),
):
    filters = [User.tenant_id == admin.tenant_id]
    if keyword:
        like = f"%{keyword}%"
        filters.append(or_(User.username.like(like), User.name.like(like), User.email.like(like)))
    if group_id is not None:
        filters.append(User.group_id == group_id)

    total = await db.scalar(select(func.count()).select_from(User).where(*filters))
    stmt = (
        select(User, UserGroup.name)
        .outerjoin(UserGroup, UserGroup.id == User.group_id)
        .where(*filters)
        .order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).all()
    return AdminUserListOut(
        items=[_admin_user_out(u, gname) for u, gname in rows], total=total or 0
    )


@router.post("/users", response_model=AdminUserOut, status_code=201)
async def create_user(
    body: AdminUserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_perm("user.admin")),
):
    exists = await db.scalar(
        select(func.count()).select_from(User).where(User.username == body.username)
    )
    if exists:
        raise HTTPException(status_code=409, detail="用户名已存在")
    if body.group_id is not None:
        group = await db.get(UserGroup, body.group_id)
        if group is None or group.tenant_id != admin.tenant_id:
            raise HTTPException(status_code=404, detail="分组不存在")
    await _check_role_assignable(db, admin.tenant_id, body.role, admin)
    await _check_phone_unique(db, admin.tenant_id, body.phone)
    user = User(
        tenant_id=admin.tenant_id,
        username=body.username,
        password_hash=hash_password(body.password),
        name=body.name,
        role=body.role,
        group_id=body.group_id,
        email=body.email,
        phone=body.phone,
        status=1,
    )
    db.add(user)
    await db.flush()
    record_audit(db, admin, "create", "user", user.id, {"username": user.username},
                 request.client.host if request.client else None)
    await db.commit()
    await db.refresh(user)
    return _admin_user_out(user)


async def _get_user_or_404(db: AsyncSession, tenant_id: int, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.put("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: int,
    body: AdminUserUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_perm("user.admin")),
):
    target = await _get_user_or_404(db, admin.tenant_id, user_id)
    updates = body.model_dump(exclude_unset=True)
    # 防锁死：不允许把自己停用或降级
    if target.id == admin.id:
        if updates.get("status") == 0:
            raise HTTPException(status_code=400, detail="不能停用当前登录的管理员账号")
        if "role" in updates and updates["role"] != "admin":
            raise HTTPException(status_code=400, detail="不能降级当前登录的管理员账号")
    if updates.get("group_id") is not None:
        group = await db.get(UserGroup, updates["group_id"])
        if group is None or group.tenant_id != admin.tenant_id:
            raise HTTPException(status_code=404, detail="分组不存在")
    if updates.get("role"):
        await _check_role_assignable(db, admin.tenant_id, updates["role"], admin)
    if "phone" in updates:
        await _check_phone_unique(db, admin.tenant_id, updates["phone"], exclude_user_id=target.id)
    for field, value in updates.items():
        setattr(target, field, value)
    record_audit(db, admin, "update", "user", target.id, {"fields": sorted(updates)},
                 request.client.host if request.client else None)
    await db.commit()
    await db.refresh(target)
    group_name = None
    if target.group_id:
        group = await db.get(UserGroup, target.group_id)
        group_name = group.name if group else None
    return _admin_user_out(target, group_name)


@router.put("/users/{user_id}/password", status_code=204)
async def reset_password(
    user_id: int,
    body: AdminPasswordReset,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_perm("user.admin")),
):
    target = await _get_user_or_404(db, admin.tenant_id, user_id)
    target.password_hash = hash_password(body.new_password)
    # 重置密码后旧 JWT 失效（deps 校验 iat 不早于该时间）
    target.password_changed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    record_audit(db, admin, "reset_password", "user", target.id, None,
                 request.client.host if request.client else None)
    await db.commit()


# 删除用户前的业务数据检查：任一存在则 409 建议停用（CRM 惯例：有数据只停用不删除）
_USER_DATA_REFS = [
    ("客户", Customer, Customer.owner_id),
    ("商机", Opportunity, Opportunity.owner_id),
    ("跟进记录", FollowUpRecord, FollowUpRecord.user_id),
    ("任务", Task, Task.user_id),
    ("通知", Notification, Notification.user_id),
    ("会话", ChatSession, ChatSession.user_id),
    ("报告", Report, Report.user_id),
    ("检索日志", RagQueryLog, RagQueryLog.user_id),
    ("AI 反馈", AiFeedback, AiFeedback.user_id),
]


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_perm("user.admin")),
):
    target = await _get_user_or_404(db, admin.tenant_id, user_id)
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除当前登录的管理员账号")
    refs: list[str] = []
    for label, model, column in _USER_DATA_REFS:
        count = await db.scalar(select(func.count()).select_from(model).where(column == user_id))
        if count:
            refs.append(f"{label} {count} 条")
    if refs:
        raise HTTPException(
            status_code=409,
            detail=f"该用户存在业务数据（{'、'.join(refs)}），建议停用而非删除",
        )
    # 无业务数据：清理用量日志残留关联后删除
    await db.execute(
        update(LlmCallLog).where(LlmCallLog.user_id == user_id).values(user_id=None)
    )
    await db.delete(target)
    record_audit(db, admin, "delete", "user", user_id, {"username": target.username},
                 request.client.host if request.client else None)
    await db.commit()


# ---------------------------------------------------------------------------
# 分组管理
# ---------------------------------------------------------------------------

@router.get("/groups", response_model=list[GroupOut])
async def list_groups(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_perm("user.admin")),
):
    stmt = (
        select(UserGroup)
        .where(UserGroup.tenant_id == admin.tenant_id)
        .order_by(UserGroup.created_at)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/groups", response_model=GroupOut, status_code=201)
async def create_group(
    body: GroupCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_perm("user.admin")),
):
    group = UserGroup(tenant_id=admin.tenant_id, name=body.name, description=body.description)
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return group


async def _get_group_or_404(db: AsyncSession, tenant_id: int, group_id: int) -> UserGroup:
    group = await db.get(UserGroup, group_id)
    if group is None or group.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="分组不存在")
    return group


@router.put("/groups/{group_id}", response_model=GroupOut)
async def update_group(
    group_id: int,
    body: GroupUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_perm("user.admin")),
):
    group = await _get_group_or_404(db, admin.tenant_id, group_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    await db.commit()
    await db.refresh(group)
    return group


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_perm("user.admin")),
):
    group = await _get_group_or_404(db, admin.tenant_id, group_id)
    member_count = await db.scalar(
        select(func.count()).select_from(User).where(User.group_id == group_id)
    )
    if member_count:
        raise HTTPException(status_code=400, detail="该分组仍有成员，请先移出成员")
    await db.delete(group)
    await db.commit()
