"""回收站：软删资源的查看 / 恢复 / 彻底删除（仅管理员）。"""
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.api.deps import get_db, require_admin
from app.models.customer import Customer
from app.models.knowledge_base import KnowledgeBase
from app.models.library_file import LibraryFile
from app.models.notebook import Notebook
from app.models.user import User
from app.services.audit import record_audit
from app.services.kb import delete_kb_documents, delete_library_file
from app.services.maintenance import run_vacuum

router = APIRouter(
    prefix="/recycle-bin", tags=["recycle-bin"], dependencies=[Depends(require_admin)]
)

ResourceType = Literal["customer", "file", "kb", "notebook"]

_MODELS = {
    "customer": Customer,
    "file": LibraryFile,
    "kb": KnowledgeBase,
    "notebook": Notebook,
}


def _name_of(resource_type: str, obj) -> str:
    return obj.file_name if resource_type == "file" else obj.name


def _detail_of(resource_type: str, obj) -> dict:
    if resource_type == "customer":
        return {"company": obj.company, "phone": obj.phone}
    if resource_type == "file":
        return {"file_type": obj.file_type, "file_size": obj.file_size}
    if resource_type == "notebook":
        return {"来源数": len(obj.source_kb_ids or []) + len(obj.source_file_ids or [])}
    return {"type": obj.type, "customer_id": obj.customer_id}


@router.get("")
async def list_recycle_bin(
    type: ResourceType | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """分页列出已软删的客户/文件/知识库，type 筛选。"""
    items: list[dict] = []
    total = 0
    for resource_type, model in _MODELS.items():
        if type is not None and resource_type != type:
            continue
        filters = [model.tenant_id == admin.tenant_id, model.deleted_at.isnot(None)]
        total += await db.scalar(select(func.count()).select_from(model).where(*filters)) or 0
        rows = (
            (await db.execute(select(model).where(*filters).order_by(model.deleted_at.desc())))
            .scalars()
            .all()
        )
        items.extend(
            {
                "type": resource_type,
                "id": r.id,
                "name": _name_of(resource_type, r),
                "deleted_at": r.deleted_at,
                "detail": _detail_of(resource_type, r),
            }
            for r in rows
        )
    items.sort(key=lambda x: x["deleted_at"], reverse=True)
    start = (page - 1) * page_size
    return {"items": items[start : start + page_size], "total": total}


async def _get_deleted_or_404(db: AsyncSession, tenant_id: int, resource_type: str, resource_id: int):
    model = _MODELS[resource_type]
    obj = await db.get(model, resource_id)
    if obj is None or obj.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="资源不存在")
    if obj.deleted_at is None:
        raise HTTPException(status_code=400, detail="该资源不在回收站中")
    return obj


async def _check_name_conflict(
    db: AsyncSession, tenant_id: int, resource_type: str, obj
) -> None:
    """恢复前检查同名活跃记录，冲突 409。"""
    if resource_type == "customer":
        count = await db.scalar(
            select(func.count()).select_from(Customer).where(
                Customer.tenant_id == tenant_id,
                Customer.name == obj.name,
                Customer.deleted_at.is_(None),
                Customer.id != obj.id,
            )
        )
    elif resource_type == "file":
        count = await db.scalar(
            select(func.count()).select_from(LibraryFile).where(
                LibraryFile.tenant_id == tenant_id,
                LibraryFile.file_name == obj.file_name,
                LibraryFile.folder_id.is_(obj.folder_id) if obj.folder_id is None
                else LibraryFile.folder_id == obj.folder_id,
                LibraryFile.deleted_at.is_(None),
                LibraryFile.id != obj.id,
            )
        )
    elif resource_type == "notebook":
        # notebook 名称同租户内不强制唯一，恢复不查重
        count = 0
    else:
        count = await db.scalar(
            select(func.count()).select_from(KnowledgeBase).where(
                KnowledgeBase.tenant_id == tenant_id,
                KnowledgeBase.name == obj.name,
                KnowledgeBase.deleted_at.is_(None),
                KnowledgeBase.id != obj.id,
            )
        )
    if count:
        raise HTTPException(status_code=409, detail="存在同名活跃记录，请先改名或处理冲突后再恢复")


@router.post("/{resource_type}/{resource_id}/restore")
async def restore_resource(
    resource_type: ResourceType,
    resource_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    obj = await _get_deleted_or_404(db, admin.tenant_id, resource_type, resource_id)
    await _check_name_conflict(db, admin.tenant_id, resource_type, obj)
    obj.deleted_at = None
    restored_kbs = 0
    if resource_type == "customer":
        # 一并恢复专属知识库与联动软删的附件文件
        kbs = (
            (
                await db.execute(
                    select(KnowledgeBase).where(
                        KnowledgeBase.customer_id == obj.id,
                        KnowledgeBase.type == "customer",
                        KnowledgeBase.tenant_id == admin.tenant_id,
                        KnowledgeBase.deleted_at.isnot(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for kb in kbs:
            kb.deleted_at = None
        restored_kbs = len(kbs)
        from app.models.library_file import LibraryFile

        files = (
            (
                await db.execute(
                    select(LibraryFile).where(
                        LibraryFile.customer_id == obj.id,
                        LibraryFile.tenant_id == admin.tenant_id,
                        LibraryFile.deleted_at.isnot(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for f in files:
            f.deleted_at = None
    record_audit(
        db, admin, "restore", resource_type, obj.id,
        {"name": _name_of(resource_type, obj)},
        request.client.host if request.client else None,
    )
    await db.commit()
    return {"ok": True, "restored_kbs": restored_kbs}


@router.delete("/{resource_type}/{resource_id}", status_code=204)
async def purge_resource(
    resource_type: ResourceType,
    resource_id: int,
    request: Request,
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """彻底删除（物理）：客户级联清理专属知识库 documents/chunks；文件清理磁盘与各库文档。"""
    obj = await _get_deleted_or_404(db, admin.tenant_id, resource_type, resource_id)
    name = _name_of(resource_type, obj)
    await _purge_one(db, resource_type, obj)
    record_audit(
        db, admin, "purge", resource_type, resource_id,
        {"name": name},
        request.client.host if request.client else None,
    )
    await db.commit()
    # 后台 VACUUM 回收删除空间（autovacuum 有滞后；手动触发加速空间复用）
    background_tasks.add_task(run_vacuum)


class PurgeItem(BaseModel):
    type: ResourceType
    id: int


class PurgeBatchRequest(BaseModel):
    items: list[PurgeItem]


@router.post("/purge")
async def purge_batch(
    body: PurgeBatchRequest,
    request: Request,
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """批量彻底删除：单项失败不影响其余（逐项提交），返回成功/失败数。"""
    purged = 0
    failed: list[str] = []
    for item in body.items:
        try:
            obj = await _get_deleted_or_404(db, admin.tenant_id, item.type, item.id)
            name = _name_of(item.type, obj)
            await _purge_one(db, item.type, obj)
            record_audit(db, admin, "purge", item.type, item.id, {"name": name, "batch": True},
                         request.client.host if request.client else None)
            await db.commit()
            purged += 1
        except Exception as exc:
            await db.rollback()
            logger.warning("批量彻底删除失败 %s/%s: %s", item.type, item.id, exc)
            failed.append(f"{item.type}#{item.id}")
    if purged:
        background_tasks.add_task(run_vacuum)
    return {"purged": purged, "failed": failed}


async def _purge_one(db: AsyncSession, resource_type: str, obj) -> None:
    """单项物理删除（不含审计/提交）。"""
    if resource_type == "customer":
        kbs = (
            (
                await db.execute(
                    select(KnowledgeBase).where(
                        KnowledgeBase.customer_id == obj.id,
                        KnowledgeBase.type == "customer",
                        KnowledgeBase.tenant_id == obj.tenant_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for kb in kbs:
            await delete_kb_documents(db, kb.id)
            await db.delete(kb)
        await db.delete(obj)
    elif resource_type == "file":
        await delete_library_file(db, obj)  # 各知识库文档/切片 + 磁盘文件 + 文件行
    elif resource_type == "notebook":
        # 工作区内容由 FK ON DELETE CASCADE 一并清理
        await db.delete(obj)
    else:
        await delete_kb_documents(db, obj.id)
        await db.delete(obj)
