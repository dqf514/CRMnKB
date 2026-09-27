import logging

from app.models.error_log import ErrorLog

logger = logging.getLogger(__name__)


async def log_error(
    level: str,
    module: str,
    message: str,
    detail: str | None = None,
    tenant_id: int | None = None,
) -> None:
    """写一条异常日志；任何失败静默（绝不影响主流程）。"""
    try:
        from app.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            session.add(
                ErrorLog(
                    tenant_id=tenant_id,
                    level=level,
                    module=module,
                    message=message[:500],
                    detail=(detail or "")[:2000] or None,
                )
            )
            await session.commit()
    except Exception as exc:
        logger.debug("异常日志写入失败（忽略）: %s", exc)
