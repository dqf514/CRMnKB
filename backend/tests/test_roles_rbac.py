"""RBAC 角色体系测试（aiosqlite 真实 SQL + API 约束）。

覆盖：
- seed_roles 幂等种子：三系统角色 + 默认团队收纳存量无团队用户 + role='user'→'member'
- has_perm / get_role_perms：admin 恒真、member 有 share、individual 无权限、未知角色无权限、缓存失效
- require_perm 依赖：有权限放行 / 无权限 403
- 角色 CRUD API 约束：admin key 不可占用、重复 key 409、未知权限点 400、admin 角色权限锁定、
  is_system 不可删、使用中删除 409、正常删除
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import deps
from app.database import get_db
from app.main import app
from app.models.base import Base
from app.models.audit_log import AuditLog
from app.models.role import Role
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_group import UserGroup
from app.services import roles as roles_svc

_TABLES = [
    Tenant.__table__,
    UserGroup.__table__,
    User.__table__,
    Role.__table__,
    AuditLog.__table__,
]


@pytest.fixture
async def db():
    """sqlite 内存库：角色相关表子集 + 种子租户（JSONB 临时换成通用 JSON）。"""
    swapped_types: list[tuple] = []
    saved_indexes: dict = {}
    for table in _TABLES:
        for col in table.c:
            if isinstance(col.type, JSONB):
                swapped_types.append((col, col.type))
                col.type = JSON()
        saved_indexes[table] = set(table.indexes)
        table.indexes.clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_TABLES))
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add_all([Tenant(id=1, name="租户一")])
            await session.commit()
            roles_svc.invalidate_role_cache()
            yield session
            roles_svc.invalidate_role_cache()
    finally:
        await engine.dispose()
        for col, col_type in swapped_types:
            col.type = col_type
        for table, indexes in saved_indexes.items():
            table.indexes.update(indexes)


def _user(uid, **kw):
    base = {
        "id": uid, "tenant_id": 1, "username": f"u{uid}", "password_hash": "x",
        "name": f"用户{uid}", "role": "member", "group_id": None, "status": 1,
    }
    base.update(kw)
    return User(**base)


# ---------------------------------------------------------------------------
# seed_roles
# ---------------------------------------------------------------------------

async def test_seed_roles_creates_system_roles_and_default_team(db):
    db.add_all([_user(1, role="admin"), _user(2, role="user")])  # 存量：admin + 旧 role=user
    await db.commit()

    await roles_svc.seed_roles(db)
    await db.commit()

    roles = (await db.execute(select(Role).where(Role.tenant_id == 1))).scalars().all()
    by_key = {r.key: r for r in roles}
    assert set(by_key) == {"admin", "member", "individual"}
    assert by_key["admin"].permissions == ["*"]
    assert by_key["member"].permissions == ["share"]
    assert by_key["individual"].permissions == []
    assert all(r.is_system for r in roles)

    # 默认团队创建并收纳全部存量无团队用户
    team = await db.scalar(select(UserGroup).where(UserGroup.tenant_id == 1))
    assert team is not None and team.name == roles_svc.DEFAULT_TEAM_NAME
    for u in (await db.execute(select(User))).scalars().all():
        assert u.group_id == team.id
    # 旧 role='user' 迁移为 member
    assert (await db.get(User, 2)).role == "member"


async def test_seed_roles_idempotent(db):
    db.add(_user(1, role="admin"))
    await db.commit()
    await roles_svc.seed_roles(db)
    await db.commit()
    # 第二次执行不重复建行、不报错
    await roles_svc.seed_roles(db)
    await db.commit()
    count = len((await db.execute(select(Role).where(Role.tenant_id == 1))).scalars().all())
    assert count == 3
    teams = (await db.execute(select(UserGroup).where(UserGroup.tenant_id == 1))).scalars().all()
    assert len(teams) == 1


# ---------------------------------------------------------------------------
# has_perm / get_role_perms / require_perm
# ---------------------------------------------------------------------------

async def test_has_perm_matrix(db):
    await roles_svc.seed_roles(db)
    await db.commit()
    admin = _user(1, role="admin")
    member = _user(2, role="member")
    individual = _user(3, role="individual")
    unknown = _user(4, role="ghost")

    assert await roles_svc.has_perm(db, admin, "user.admin") is True  # admin 恒真
    assert await roles_svc.has_perm(db, admin, "share") is True
    assert await roles_svc.has_perm(db, member, "share") is True
    assert await roles_svc.has_perm(db, member, "user.admin") is False
    assert await roles_svc.has_perm(db, individual, "share") is False
    assert await roles_svc.has_perm(db, unknown, "share") is False  # 角色不存在按无权限


async def test_role_perms_cache_invalidation(db):
    await roles_svc.seed_roles(db)
    await db.commit()
    assert await roles_svc.get_role_perms(db, 1, "member") == ["share"]
    # 直接改库后缓存仍是旧值；失效后读到新值
    role = await db.scalar(select(Role).where(Role.tenant_id == 1, Role.key == "member"))
    role.permissions = ["share", "agent.approve"]
    await db.commit()
    assert await roles_svc.get_role_perms(db, 1, "member") == ["share"]
    roles_svc.invalidate_role_cache(1)
    assert await roles_svc.get_role_perms(db, 1, "member") == ["share", "agent.approve"]


async def test_require_perm_allows_and_denies(db):
    from fastapi import HTTPException

    await roles_svc.seed_roles(db)
    await db.commit()
    checker = deps.require_perm("share")
    member = _user(2, role="member")
    individual = _user(3, role="individual")
    assert await checker(user=member, db=db) is member
    with pytest.raises(HTTPException) as exc:
        await checker(user=individual, db=db)
    assert exc.value.status_code == 403
    # admin 不经 roles 表直接放行
    assert await deps.require_perm("user.admin")(user=_user(1, role="admin"), db=db)


# ---------------------------------------------------------------------------
# 角色 CRUD API 约束
# ---------------------------------------------------------------------------

@pytest.fixture
async def client(db):
    """admin 身份的 API 客户端：get_db 走真实 sqlite 会话。"""
    async def _fake_get_db():
        yield db

    async def _fake_user():
        return _user(1, role="admin")

    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[deps.get_current_user] = _fake_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def seeded(db):
    await roles_svc.seed_roles(db)
    await db.commit()


async def test_role_crud_flow(client, seeded):
    # 权限点矩阵定义
    resp = await client.get("/api/v1/admin/roles/permission-keys")
    assert resp.status_code == 200
    keys = {i["key"] for g in resp.json()["groups"] for i in g["items"]}
    assert {"share", "user.admin", "agent.approve", "system.admin"} <= keys

    # 列表：三个种子角色
    resp = await client.get("/api/v1/admin/roles")
    assert resp.status_code == 200
    assert {r["key"] for r in resp.json()} == {"admin", "member", "individual"}

    # 新建
    resp = await client.post("/api/v1/admin/roles", json={
        "key": "sales-lead", "name": "销售主管", "permissions": ["share", "agent.approve"],
    })
    assert resp.status_code == 201
    rid = resp.json()["id"]
    assert resp.json()["permissions"] == ["share", "agent.approve"]

    # 重复 key 409 / admin key 不可占用 / 未知权限点 400
    resp = await client.post("/api/v1/admin/roles", json={"key": "sales-lead", "name": "x"})
    assert resp.status_code == 409
    resp = await client.post("/api/v1/admin/roles", json={"key": "admin", "name": "x"})
    assert resp.status_code == 400
    resp = await client.post("/api/v1/admin/roles", json={"key": "bad", "name": "x", "permissions": ["nope"]})
    assert resp.status_code == 400

    # 更新权限生效（含缓存失效：roles 服务立即读到新值）
    resp = await client.put(f"/api/v1/admin/roles/{rid}", json={"permissions": ["share"]})
    assert resp.status_code == 200
    assert resp.json()["permissions"] == ["share"]

    # admin 角色权限锁定；is_system 不可删
    admin_role_id = next(r["id"] for r in (await client.get("/api/v1/admin/roles")).json() if r["key"] == "admin")
    resp = await client.put(f"/api/v1/admin/roles/{admin_role_id}", json={"permissions": ["share"]})
    assert resp.status_code == 400
    member_role_id = next(r["id"] for r in (await client.get("/api/v1/admin/roles")).json() if r["key"] == "member")
    resp = await client.delete(f"/api/v1/admin/roles/{member_role_id}")
    assert resp.status_code == 400

    # 使用中不可删（409）；无引用后删除成功
    resp = await client.delete(f"/api/v1/admin/roles/{rid}")
    assert resp.status_code == 204


async def test_role_delete_in_use_409(client, seeded, db):
    resp = await client.post("/api/v1/admin/roles", json={"key": "temp-role", "name": "临时"})
    rid = resp.json()["id"]
    db.add(_user(9, role="temp-role"))
    await db.commit()
    resp = await client.delete(f"/api/v1/admin/roles/{rid}")
    assert resp.status_code == 409


async def test_role_endpoints_require_user_admin(client, seeded):
    """member（无 user.admin 权限点）访问角色管理 403。"""
    async def _member():
        return _user(2, role="member")

    app.dependency_overrides[deps.get_current_user] = _member
    resp = await client.get("/api/v1/admin/roles")
    assert resp.status_code == 403
