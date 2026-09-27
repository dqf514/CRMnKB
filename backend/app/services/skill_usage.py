"""PR-E：skill 调用埋点（参照 llm/usage.py 模式）。

独立 session + commit + 失败静默：绝不阻断主流程。"""
import logging

from app.database import AsyncSessionLocal
from app.models.skill_call_log import SkillCallLog

logger = logging.getLogger(__name__)


async def record_skill_call_log(entry: dict) -> None:
    """写一条 skill 调用日志；任何失败静默（绝不影响主流程）。

    entry 字段：tenant_id / user_id / skill_name / caller / latency_ms / success / error
    """
    try:
        async with AsyncSessionLocal() as session:
            session.add(SkillCallLog(**entry))
            await session.commit()
    except Exception as exc:
        logger.debug("skill 调用日志写入失败（忽略）: %s", exc)