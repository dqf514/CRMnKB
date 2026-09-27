from datetime import datetime

from sqlalchemy import String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BrandSettings(Base):
    """品牌配置（单行，id=1）：系统名称 + 自定义 logo。

    logo_path 存品牌目录（data/brand）下的文件名，经 /brand 静态服务公开。
    """

    __tablename__ = "brand_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    system_name: Mapped[str] = mapped_column(String(100), default="榜样知识库")
    logo_path: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
