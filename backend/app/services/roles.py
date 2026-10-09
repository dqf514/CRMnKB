"""角色权限点（RBAC）服务。

- `admin` 是硬超管：`has_perm` 恒真，现有 79 处 `require_admin` 调用不受影响。
- 非超管角色的权限点存 `roles` 表（JSONB 数组），进程内缓存 60s，角色保存/删除时失效。
- 权限点矩阵定义见 `PERMISSION_GROUPS`（管理端「角色管理」页面的数据源）。
"""
import time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.role import Role
from app.models.user import User
from app.models.user_group import UserGroup

# 可分配权限点（矩阵分组）：key → 说明。`*` 超管仅 admin 角色持有，UI 锁定不可配。
PERMISSION_GROUPS: list[dict] = [
    {
        "group": "协作",
        "items": [
            {"key": "share", "name": "分享资源", "description": "分享内容/设置可见性（私有↔团队共享）"},
        ],
    },
    {
        "group": "管理",
        "items": [
            {"key": "user.admin", "name": "用户与团队管理", "description": "用户、角色、团队的增删改"},
            {"key": "agent.approve", "name": "Agent 审批", "description": "处理 Agent 写操作审批单"},
            # 预留：系统管理（设置/品牌/监控/模型/技能/MCP/审计）暂不拆分，仍属超管
            {"key": "system.admin", "name": "系统管理（预留）", "description": "设置/品牌/监控/模型等，暂不拆分，仍属超管"},
        ],
    },
]

# 全部合法权限点 key（roles 表校验用）
ALL_PERMISSION_KEYS: set[str] = {
    item["key"] for group in PERMISSION_GROUPS for item in group["items"]
}

# 种子角色：key → (名称, 描述, 权限点)
SEED_ROLES: dict[str, tuple[str, str, list[str]]] = {
    "admin": ("管理员", "系统内置超级管理员，拥有全部权限", ["*"]),
    "member": ("团队成员", "可参与团队协作与分享", ["share"]),
    "individual": ("个人用户", "手机注册的默认角色，无共享能力，待管理员授权升级", []),
}

# 默认团队：init_db 为每个租户补一个，存量用户全部划入（保持改造前"全员协作"的感知）
DEFAULT_TEAM_NAME = "默认团队"

_cache: dict[tuple[int, str], tuple[float, list[str]]] = {}
_CACHE_TTL_SECONDS = 60


async def get_role_perms(db: AsyncSession, tenant_id: int, role_key: str) -> list[str]:
    """角色权限点数组（带 60s 进程内缓存）；角色不存在时按无权限处理。"""
    if role_key == "admin":
        return ["*"]
    cache_key = (tenant_id, role_key)
    hit = _cache.get(cache_key)
    if hit and hit[0] > time.time():
        return hit[1]
    perms = await db.scalar(
        select(Role.permissions).where(
            Role.tenant_id == tenant_id, Role.key == role_key
        )
    )
    result = list(perms) if isinstance(perms, list) else []
    _cache[cache_key] = (time.time() + _CACHE_TTL_SECONDS, result)
    return result


def invalidate_role_cache(tenant_id: int | None = None) -> None:
    """角色保存/删除后调用；tenant_id 为 None 时清空全部。"""
    if tenant_id is None:
        _cache.clear()
        return
    for key in [k for k in _cache if k[0] == tenant_id]:
        _cache.pop(key, None)


async def has_perm(db: AsyncSession, user: User, perm_key: str) -> bool:
    """用户是否拥有某权限点。admin 恒真；其余按 roles 表判定。"""
    if user.role == "admin":
        return True
    perms = await get_role_perms(db, user.tenant_id, user.role)
    return "*" in perms or perm_key in perms


async def seed_roles(db: AsyncSession) -> None:
    """幂等种子（init_db 调用）：

    1. 每个租户补齐三个系统角色（admin/member/individual）。
    2. 没有团队的租户补「默认团队」，并把存量 group_id 为空的用户划入
       （保持改造前"团队可见=全租户"的协作感知；新注册的个人用户不进任何团队）。
    3. 存量 users.role='user' 统一迁移为 'member'。
    调用方负责 commit。
    """
    from app.models.tenant import Tenant

    tenant_ids = (await db.execute(select(Tenant.id))).scalars().all()
    for tenant_id in tenant_ids:
        existing = set(
            (await db.execute(select(Role.key).where(Role.tenant_id == tenant_id))).scalars().all()
        )
        for key, (name, description, perms) in SEED_ROLES.items():
            if key not in existing:
                db.add(
                    Role(
                        tenant_id=tenant_id,
                        key=key,
                        name=name,
                        description=description,
                        is_system=True,
                        permissions=perms,
                    )
                )
        # 默认团队：仅当该租户一个团队都没有时创建，并收纳存量无团队用户
        group_count = await db.scalar(
            select(func.count()).select_from(UserGroup).where(UserGroup.tenant_id == tenant_id)
        )
        if not group_count:
            team = UserGroup(
                tenant_id=tenant_id,
                name=DEFAULT_TEAM_NAME,
                description="系统初始化创建的默认团队",
            )
            db.add(team)
            await db.flush()
            users = (
                (
                    await db.execute(
                        select(User).where(
                            User.tenant_id == tenant_id, User.group_id.is_(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for u in users:
                u.group_id = team.id
    # 角色迁移：历史 'user' → 'member'（幂等）
    legacy = (
        (await db.execute(select(User).where(User.role == "user"))).scalars().all()
    )
    for u in legacy:
        u.role = "member"
