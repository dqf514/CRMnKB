from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.audit import AuditLogListOut

router = APIRouter(prefix="/admin", tags=["admin-audit"], dependencies=[Depends(require_admin)])


@router.get("/audit-logs", response_model=AuditLogListOut)
async def list_audit_logs(
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
    user_id: int | None = Query(None),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    filters = [AuditLog.tenant_id == admin.tenant_id]
    if action:
        filters.append(AuditLog.action == action)
    if resource_type:
        filters.append(AuditLog.resource_type == resource_type)
    if user_id is not None:
        filters.append(AuditLog.user_id == user_id)
    if start is not None:
        filters.append(AuditLog.created_at >= start.replace(tzinfo=None))
    if end is not None:
        filters.append(AuditLog.created_at <= end.replace(tzinfo=None))
    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*filters))
    stmt = (
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return AuditLogListOut(items=result.scalars().all(), total=total or 0)
