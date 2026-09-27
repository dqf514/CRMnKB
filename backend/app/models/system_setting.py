from datetime import datetime

from sqlalchemy import String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SystemSetting(Base):
    """系统设置（键值表）：如"解析文件格式开关"（key=parse_formats_enabled，value=JSON 数组）。

    新增系统级配置项时优先考虑放这里，避免为单个开关建表。
    """

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    # value 存 JSON 字符串，具体结构由各功能约定
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
