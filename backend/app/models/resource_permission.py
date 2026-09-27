from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ResourcePermission(Base):
    """内容权限 ACL：把某个资源分享给某个用户，权限三档 read/edit/owner。

    resource_type: kb / file / notebook（owner 记录在资源行上，这里只放额外授权的用户）
    """

    __tablename__ = "resource_permissions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "resource_type", "resource_id", "user_id",
            name="uq_resource_perm",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    resource_type: Mapped[str]
    resource_id: Mapped[int]
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # read / edit / owner
    permission: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
