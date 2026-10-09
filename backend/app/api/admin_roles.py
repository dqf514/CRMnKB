"""角色管理（RBAC）：角色 CRUD + 权限点矩阵定义。

- admin 是硬超管：key 不可占用，其角色行权限恒为 ["*"]，不可改权限、不可删。
- is_system 角色（admin/member/individual）不可删除，可改名称/描述/权限。
- 删除角色前检查 users.role 引用：仍有用户使用时 409。
- 权限点合法性按 services/roles.ALL_PERMISSION_KEYS 校验；保存/删除后失效角色缓存。
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_perm
from app.models.role import Role
from app.models.user import User
from app.schemas.admin import RoleCreate, RoleOut, RoleUpdate
from app.services.audit import record_audit
from app.services.roles import (
    ALL_PERMISSION_KEYS,
    PERMISSION_GROUPS,
    invalidate_role_cache,
)

router = APIRouter(
    prefix="/admin/roles", tags=["admin-roles"],
    dependencies=[Depends(require_perm("user.admin"))],
)


def _validate_permissions(perms: list[str]) -> list[str]:
    """权限点合法性校验（去重保序）；含未知 key 或 '*' 时 400。"""
    bad = [p for p in perms if p not in ALL_PERMISSION_KEYS]
    if bad:
        raise HTTPException(status_code=400, detail=f"未知权限点：{'、'.join(bad)}")
    return list(dict.fromkeys(perms))


async def _get_role_or_404(db: AsyncSession, tenant_id: int, role_id: int) -> Role:
    role = await db.get(Role, role_id)
    if role is None or role.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="角色不存在")
    return role


@router.get("/permission-keys")
async def list_permission_keys():
    """权限点矩阵定义（前端矩阵编辑器数据源，按模块分组）。"""
    return {"groups": PERMISSION_GROUPS}


@router.get("", response_model=list[RoleOut])
async def list_roles(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_perm("user.admin")),
):
    rows = (
        await db.execute(
            select(Role).where(Role.tenant_id == admin.tenant_id).order_by(Role.id)
        )
    ).scalars().all()
    return rows


@router.post("", response_model=RoleOut, status_code=201)
async def create_role(
    body: RoleCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_perm("user.admin")),
):
    if body.key == "admin":
        raise HTTPException(status_code=400, detail="admin 为系统内置超管标识，不可占用")
    exists = await db.scalar(
        select(func.count()).select_from(Role).where(
            Role.tenant_id == admin.tenant_id, Role.key == body.key
        )
    )
    if exists:
        raise HTTPException(status_code=409, detail="角色标识已存在")
    role = Role(
        tenant_id=admin.tenant_id,
        key=body.key,
        name=body.name,
        description=body.description,
        is_system=False,
        permissions=_validate_permissions(body.permissions),
    )
    db.add(role)
    await db.flush()
    record_audit(db, admin, "create", "role", role.id, {"key": role.key},
                 request.client.host if request.client else None)
    await db.commit()
    await db.refresh(role)
    return role


@router.put("/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: int,
    body: RoleUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_perm("user.admin")),
):
    role = await _get_role_or_404(db, admin.tenant_id, role_id)
    updates = body.model_dump(exclude_unset=True)
    if role.key == "admin" and "permissions" in updates:
        raise HTTPException(status_code=400, detail="admin 角色权限固定为全部权限，不可修改")
    if "permissions" in updates and updates["permissions"] is not None:
        updates["permissions"] = _validate_permissions(updates["permissions"])
    for field, value in updates.items():
        setattr(role, field, value)
    record_audit(db, admin, "update", "role", role.id, {"fields": sorted(updates)},
                 request.client.host if request.client else None)
    await db.commit()
    await db.refresh(role)
    invalidate_role_cache(admin.tenant_id)
    return role


@router.delete("/{role_id}", status_code=204)
async def delete_role(
    role_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_perm("user.admin")),
):
    role = await _get_role_or_404(db, admin.tenant_id, role_id)
    if role.is_system:
        raise HTTPException(status_code=400, detail="系统内置角色不可删除")
    in_use = await db.scalar(
        select(func.count()).select_from(User).where(
            User.tenant_id == admin.tenant_id, User.role == role.key
        )
    )
    if in_use:
        raise HTTPException(status_code=409, detail=f"仍有 {in_use} 个用户使用该角色，请先调整其角色")
    await db.delete(role)
    record_audit(db, admin, "delete", "role", role_id, {"key": role.key},
                 request.client.host if request.client else None)
    await db.commit()
    invalidate_role_cache(admin.tenant_id)
