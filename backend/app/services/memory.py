"""个人记忆服务：跨工作区/跨会话的用户偏好与事实沉淀。

口径：
- 严格按 user_id 隔离（隐私边界：不进租户共享，管理员也不经业务接口读他人记忆）
- 每用户上限 _MAX_PER_USER 条，超出按最旧淘汰（FIFO）
- 内容上限 _MAX_CONTENT_LEN 字；完全相同的内容去重（重复保存视为更新原条目时间）
- agent 写入带 chat_session_id 可追溯；手动添加 source=manual
"""
import logging

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_memory import UserMemory

logger = logging.getLogger(__name__)

_MAX_PER_USER = 200
_MAX_CONTENT_LEN = 500
# 注入新会话的记忆条数上限（取最近更新/创建的）
_INJECT_LIMIT = 30


def _clean(content: str) -> str:
    text = (content or "").strip()
    if not text:
        raise ValueError("记忆内容不能为空")
    if len(text) > _MAX_CONTENT_LEN:
        raise ValueError(f"记忆内容过长（上限 {_MAX_CONTENT_LEN} 字）")
    return text


async def list_memories(db: AsyncSession, user_id: int, limit: int = _MAX_PER_USER) -> list[UserMemory]:
    """最近创建/更新的在前（注入 prompt 与管理页共用此序）。"""
    result = await db.execute(
        select(UserMemory)
        .where(UserMemory.user_id == user_id)
        .order_by(UserMemory.updated_at.desc(), UserMemory.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def add_memory(
    db: AsyncSession,
    user_id: int,
    content: str,
    source: str = "agent",
    chat_session_id: int | None = None,
) -> tuple[UserMemory, bool]:
    """新增记忆，返回 (记忆, 是否新建)。完全相同的内容已存在时不重复建行。

    调用方负责 commit。
    """
    text = _clean(content)
    existing = await db.scalar(
        select(UserMemory).where(UserMemory.user_id == user_id, UserMemory.content == text)
    )
    if existing is not None:
        return existing, False
    mem = UserMemory(
        user_id=user_id, content=text, source=source, chat_session_id=chat_session_id
    )
    db.add(mem)
    await db.flush()
    # 超上限淘汰最旧的
    total = await db.scalar(select(func.count()).select_from(UserMemory).where(UserMemory.user_id == user_id))
    if (total or 0) > _MAX_PER_USER:
        stale_ids = await db.scalars(
            select(UserMemory.id)
            .where(UserMemory.user_id == user_id)
            .order_by(UserMemory.updated_at.asc(), UserMemory.id.asc())
            .limit(total - _MAX_PER_USER)
        )
        ids = list(stale_ids.all())
        if ids:
            await db.execute(delete(UserMemory).where(UserMemory.id.in_(ids)))
    return mem, True


async def update_memory(db: AsyncSession, user_id: int, memory_id: int, content: str) -> UserMemory | None:
    """更新记忆内容（仅本人）。返回 None 表示不存在或非本人。"""
    mem = await db.scalar(
        select(UserMemory).where(UserMemory.id == memory_id, UserMemory.user_id == user_id)
    )
    if mem is None:
        return None
    mem.content = _clean(content)
    await db.flush()
    return mem


async def delete_memory(db: AsyncSession, user_id: int, memory_id: int) -> bool:
    """删除单条（仅本人）。"""
    result = await db.execute(
        delete(UserMemory).where(UserMemory.id == memory_id, UserMemory.user_id == user_id)
    )
    return result.rowcount > 0


async def clear_memories(db: AsyncSession, user_id: int) -> int:
    """清空本人全部记忆，返回删除条数。"""
    result = await db.execute(delete(UserMemory).where(UserMemory.user_id == user_id))
    return result.rowcount or 0


async def search_memories(db: AsyncSession, user_id: int, query: str) -> list[UserMemory]:
    """简单关键词匹配（量级小，ILIKE 足够；后续量大再走向量）。"""
    kw = (query or "").strip()
    if not kw:
        return []
    result = await db.execute(
        select(UserMemory)
        .where(UserMemory.user_id == user_id, UserMemory.content.ilike(f"%{kw}%"))
        .order_by(UserMemory.updated_at.desc())
        .limit(20)
    )
    return list(result.scalars().all())


def memory_context_block(memories: list[UserMemory]) -> str:
    """把记忆渲染为注入新 agent 会话的上下文块（不含记忆时返回空串）。"""
    if not memories:
        return ""
    lines = "\n".join(f"- {m.content}" for m in memories)
    return (
        "<user-memory>\n"
        "以下是当前用户的长期个人记忆（跨会话有效，仅供你参考，不要直接复述给用户，除非被问到）：\n"
        f"{lines}\n"
        "当你从对话中得知用户新的长期偏好、身份背景或常用约定时，调用 memory_save 工具保存；"
        "记忆有误时用 memory_delete 删除后重新保存。\n"
        "</user-memory>"
    )
