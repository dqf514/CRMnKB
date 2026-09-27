from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin
from app.models.error_log import ErrorLog
from app.models.user import User
from app.schemas.admin import ErrorListOut

router = APIRouter(prefix="/admin", tags=["admin-errors"], dependencies=[Depends(require_admin)])


@router.get("/errors", response_model=ErrorListOut)
async def list_errors(
    level: str | None = Query(None),
    resolved: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    filters = [or_(ErrorLog.tenant_id == admin.tenant_id, ErrorLog.tenant_id.is_(None))]
    if level:
        filters.append(ErrorLog.level == level)
    if resolved is not None:
        filters.append(ErrorLog.resolved.is_(resolved))
    total = await db.scalar(select(func.count()).select_from(ErrorLog).where(*filters))
    stmt = (
        select(ErrorLog)
        .where(*filters)
        .order_by(ErrorLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return ErrorListOut(items=result.scalars().all(), total=total or 0)


# 注意：resolve_all 必须先于 /errors/{error_id} 注册，避免被路径参数捕获
@router.post("/errors/resolve_all", response_model=dict)
async def resolve_all_errors(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await db.execute(
        update(ErrorLog)
        .where(
            or_(ErrorLog.tenant_id == admin.tenant_id, ErrorLog.tenant_id.is_(None)),
            ErrorLog.resolved.is_(False),
        )
        .values(resolved=True)
    )
    await db.commit()
    return {"ok": True, "resolved": result.rowcount}


@router.post("/errors/{error_id}/resolve", response_model=dict)
async def resolve_error(
    error_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    error = await db.get(ErrorLog, error_id)
    if error is None or (error.tenant_id is not None and error.tenant_id != admin.tenant_id):
        raise HTTPException(status_code=404, detail="日志不存在")
    error.resolved = True
    await db.commit()
    return {"ok": True}
