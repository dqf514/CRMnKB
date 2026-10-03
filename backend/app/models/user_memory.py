"""个人记忆（跨工作区）：agent 对话中沉淀的用户偏好/事实/常用上下文。

隐私边界：严格按 user_id 隔离，仅本人可见，不进租户共享。
source 标记来源（agent=AI 写入，manual=用户在个人中心手动添加），便于追溯。
"""
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserMemory(Base):
    __tablename__ = "user_memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    # 来源：agent（AI 对话中自动沉淀）/ manual（个人中心手动添加）
    source: Mapped[str] = mapped_column(String(20), default="agent")
    # agent 写入时来源的 chat_sessions.id（可追溯）；手动添加为 NULL
    chat_session_id: Mapped[int | None] = mapped_column(ForeignKey("chat_sessions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
