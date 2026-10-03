"""内容分享 / 权限管理 API（资源：kb / file / folder / notebook）。

仅资源所有者（或管理员）可管理分享。分享时给目标用户发通知（type=share）。
"""
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.library_file import LibraryFile
from app.models.library_folder import LibraryFolder
from app.models.notebook import Notebook
from app.models.notification import Notification
from app.models.resource_permission import ResourcePermission
from app.models.user import User
from app.services.audit import record_audit
from app.services.permissions import ensure_owner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/permissions", tags=["permissions"])

ResourceType = Literal["kb", "file", "folder", "notebook"]
_PERMISSION = Literal["read", "edit", "owner"]

_MODELS = {"kb": KnowledgeBase, "file": LibraryFile, "folder": LibraryFolder, "notebook": Notebook}
_LABEL = {"kb": "知识库", "file": "文档", "folder": "文件夹", "notebook": "工作区"}
_PERM_LABEL = {"read": "只读", "edit": "编辑", "owner": "所有权"}


class ShareRequest(BaseModel):
    user_id: int
    permission: _PERMISSION = "read"


class PermissionUpdate(BaseModel):
    permission: _PERMISSION


class VisibilityUpdate(BaseModel):
    is_private: bool


class BatchShareRequest(BaseModel):
    file_ids: list[int]
    user_id: int
    permission: _PERMISSION = "read"


def _name_of(rtype: str, obj) -> str:
    return obj.file_name if rtype == "file" else obj.name


async def _get_owned_or_404(db: AsyncSession, user: User, rtype: str, rid: int):
    model = _MODELS[rtype]
    obj = await db.get(model, rid)
    if obj is None or obj.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="资源不存在")
    await ensure_owner(db, user, rtype, rid)
    return obj


async def _target_user_or_404(db: AsyncSession, tenant_id: int, user_id: int) -> User:
    u = await db.get(User, user_id)
    if u is None or u.tenant_id != tenant_id or getattr(u, "status", 1) == 0:
        raise HTTPException(status_code=404, detail="目标用户不存在")
    return u


async def _acl_list(db: AsyncSession, tenant_id: int, rtype: str, rid: int, owner_id: int) -> list[dict]:
    rows = (
        await db.execute(
            select(ResourcePermission, User)
            .join(User, User.id == ResourcePermission.user_id)
            .where(
                ResourcePermission.tenant_id == tenant_id,
                ResourcePermission.resource_type == rtype,
                ResourcePermission.resource_id == rid,
            )
        )
    ).all()
    return [
        {"user_id": p.user_id, "username": u.username, "name": u.name, "permission": p.permission}
        for p, u in rows
    ]


async def _send_share_notice(
    db: AsyncSession, sharer: User, target: User, rtype: str, rid: int, name: str, permission: str
) -> None:
    db.add(
        Notification(
            tenant_id=sharer.tenant_id,
            user_id=target.id,
            task_id=None,
            title="收到分享",
            content=f"{sharer.name or sharer.username} 把{_LABEL[rtype]}「{name}」分享给你（{_PERM_LABEL[permission]}）",
            type="share",
            is_read=False,
            resource_type=rtype,
            resource_id=rid,
        )
    )


@router.post("/files/batch-share")
async def batch_share_files(
    body: BatchShareRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """批量分享多个文件给同一用户（owner 可调）。"""
    target = await _target_user_or_404(db, user.tenant_id, body.user_id)
    shared = 0
    for fid in body.file_ids:
        obj = await _get_owned_or_404(db, user, "file", fid)
        existing = await db.scalar(
            select(ResourcePermission).where(
                ResourcePermission.tenant_id == user.tenant_id,
                ResourcePermission.resource_type == "file",
                ResourcePermission.resource_id == fid,
                ResourcePermission.user_id == body.user_id,
            )
        )
        if existing:
            existing.permission = body.permission
        else:
            db.add(
                ResourcePermission(
                    tenant_id=user.tenant_id,
                    resource_type="file",
                    resource_id=fid,
                    user_id=body.user_id,
                    permission=body.permission,
                )
            )
        await _send_share_notice(db, user, target, "file", fid, _name_of("file", obj), body.permission)
        shared += 1
    record_audit(
        db, user, "share", "file", None,
        {"file_ids": body.file_ids, "target_user_id": body.user_id, "permission": body.permission},
    )
    await db.commit()
    return {"ok": True, "shared": shared}


@router.get("/{rtype}/{rid}/users")
async def list_permissions(
    rtype: ResourceType,
    rid: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    obj = await _get_owned_or_404(db, user, rtype, rid)
    return {
        "resource_type": rtype,
        "resource_id": rid,
        "name": _name_of(rtype, obj),
        "owner_id": obj.owner_id if rtype != "notebook" else obj.created_by,
        "is_private": bool(obj.is_private),
        "items": await _acl_list(db, user.tenant_id, rtype, rid, obj.id),
    }


@router.post("/{rtype}/{rid}/share")
async def share_resource(
    rtype: ResourceType,
    rid: int,
    body: ShareRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    obj = await _get_owned_or_404(db, user, rtype, rid)
    target = await _target_user_or_404(db, user.tenant_id, body.user_id)
    existing = await db.scalar(
        select(ResourcePermission).where(
            ResourcePermission.tenant_id == user.tenant_id,
            ResourcePermission.resource_type == rtype,
            ResourcePermission.resource_id == rid,
            ResourcePermission.user_id == body.user_id,
        )
    )
    if existing:
        existing.permission = body.permission
    else:
        db.add(
            ResourcePermission(
                tenant_id=user.tenant_id,
                resource_type=rtype,
                resource_id=rid,
                user_id=body.user_id,
                permission=body.permission,
            )
        )
    await _send_share_notice(db, user, target, rtype, rid, _name_of(rtype, obj), body.permission)
    record_audit(
        db, user, "share", rtype, rid,
        {"target_user_id": body.user_id, "permission": body.permission, "updated": existing is not None},
    )
    await db.commit()
    return {
        "ok": True,
        "items": await _acl_list(db, user.tenant_id, rtype, rid, obj.id),
    }


@router.put("/{rtype}/{rid}/share/{target_user_id}")
async def update_share(
    rtype: ResourceType,
    rid: int,
    target_user_id: int,
    body: PermissionUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_owned_or_404(db, user, rtype, rid)
    row = await db.scalar(
        select(ResourcePermission).where(
            ResourcePermission.tenant_id == user.tenant_id,
            ResourcePermission.resource_type == rtype,
            ResourcePermission.resource_id == rid,
            ResourcePermission.user_id == target_user_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="尚未分享给该用户")
    old_permission = row.permission
    row.permission = body.permission
    record_audit(
        db, user, "share_update", rtype, rid,
        {"target_user_id": target_user_id, "from": old_permission, "to": body.permission},
    )
    await db.commit()
    return {"ok": True, "items": await _acl_list(db, user.tenant_id, rtype, rid, target_user_id)}


@router.delete("/{rtype}/{rid}/share/{target_user_id}", status_code=204)
async def revoke_share(
    rtype: ResourceType,
    rid: int,
    target_user_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_owned_or_404(db, user, rtype, rid)
    await db.execute(
        ResourcePermission.__table__.delete().where(
            ResourcePermission.tenant_id == user.tenant_id,
            ResourcePermission.resource_type == rtype,
            ResourcePermission.resource_id == rid,
            ResourcePermission.user_id == target_user_id,
        )
    )
    record_audit(db, user, "unshare", rtype, rid, {"target_user_id": target_user_id})
    await db.commit()


@router.put("/{rtype}/{rid}/visibility")
async def set_visibility(
    rtype: ResourceType,
    rid: int,
    body: VisibilityUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    obj = await _get_owned_or_404(db, user, rtype, rid)
    obj.is_private = body.is_private
    record_audit(db, user, "visibility", rtype, rid, {"is_private": body.is_private})
    await db.commit()
    await db.refresh(obj)
    return {"ok": True, "is_private": bool(obj.is_private)}
