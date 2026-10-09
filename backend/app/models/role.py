from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Role(Base):
    """可配置角色（RBAC）。

    - `admin` 是硬超管：key 内置不可删，权限恒为 ["*"]，现有 require_admin 不受影响。
    - 其余角色的权限点在管理端「角色管理」矩阵维护（permissions 为权限点 key 数组）。
    - is_system 角色（admin/member/individual）不可删除，可改名称/描述/权限。
    """

    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_roles_tenant_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    # 角色标识（users.role 引用此 key）：字母/数字/下划线/中划线
    key: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    # 权限点 key 数组，如 ["share"]；["*"] 表示超管（仅 admin）
    permissions: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
