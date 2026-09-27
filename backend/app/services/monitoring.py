"""系统告警检查：磁盘 / 内存 / LLM 失败 超阈值时通知管理员。

只在状态翻转时通知一次（cross → notify，恢复 → 重置），避免刷屏。
"""
import logging
from datetime import datetime, timedelta, timezone

import psutil
from sqlalchemy import func, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.error_log import ErrorLog
from app.models.notification import Notification
from app.models.user import User

logger = logging.getLogger(__name__)

# 已告警状态（key -> 是否处于告警中）
_state: dict[str, bool] = {}


async def _notify_admins(session, title: str, content: str) -> None:
    admins = (
        (
            await session.execute(
                select(User).where(User.role == "admin", User.status == 1)
            )
        )
        .scalars()
        .all()
    )
    for u in admins:
        session.add(
            Notification(
                tenant_id=u.tenant_id,
                user_id=u.id,
                task_id=None,
                title=title,
                content=content,
                type="system_alert",
                is_read=False,
            )
        )
    await session.commit()


async def check_alerts() -> list[str]:
    """跑一轮告警检查，返回本次新触发的告警名。"""
    fired: list[str] = []
    async with AsyncSessionLocal() as session:
        # 磁盘
        try:
            disk_pct = psutil.disk_usage(settings.upload_path.resolve().anchor).percent
        except Exception:
            disk_pct = 0
        if disk_pct >= settings.ALERT_DISK_PERCENT and not _state.get("disk"):
            _state["disk"] = True
            await _notify_admins(session, "磁盘空间告警", f"磁盘使用已达 {disk_pct:.0f}%，请及时清理。")
            fired.append("disk")
        elif disk_pct < settings.ALERT_DISK_PERCENT:
            _state["disk"] = False

        # 内存
        try:
            mem_pct = psutil.virtual_memory().percent
        except Exception:
            mem_pct = 0
        if mem_pct >= settings.ALERT_MEM_PERCENT and not _state.get("mem"):
            _state["mem"] = True
            await _notify_admins(session, "内存使用告警", f"内存使用已达 {mem_pct:.0f}%。")
            fired.append("mem")
        elif mem_pct < settings.ALERT_MEM_PERCENT:
            _state["mem"] = False

        # LLM 连续失败
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            minutes=settings.ALERT_LLM_FAIL_WINDOW_MIN
        )
        llm_fails = (
            await session.scalar(
                select(func.count())
                .select_from(ErrorLog)
                .where(
                    ErrorLog.level == "warning",
                    ErrorLog.module == "llm",
                    ErrorLog.created_at >= since,
                )
            )
            or 0
        )
        if llm_fails >= settings.ALERT_LLM_FAIL_THRESHOLD and not _state.get("llm"):
            _state["llm"] = True
            await _notify_admins(
                session,
                "LLM 服务异常告警",
                f"近 {settings.ALERT_LLM_FAIL_WINDOW_MIN} 分钟 LLM 调用失败 {llm_fails} 次，请检查模型配置。",
            )
            fired.append("llm")
        elif llm_fails < settings.ALERT_LLM_FAIL_THRESHOLD:
            _state["llm"] = False
    return fired
