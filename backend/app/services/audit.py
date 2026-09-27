from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.user import User


def record_audit(
    db: AsyncSession,
    user: User | None,
    action: str,
    resource_type: str | None = None,
    resource_id: int | None = None,
    detail: dict | None = None,
    ip: str | None = None,
) -> None:
    """写一条操作审计日志（随主事务一起 commit，失败会随主事务回滚）。"""
    db.add(
        AuditLog(
            tenant_id=user.tenant_id if user else None,
            user_id=user.id if user else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
            ip=ip,
        )
    )
