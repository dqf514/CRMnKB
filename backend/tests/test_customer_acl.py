"""客户权限（is_private + ACL）真实 SQL 回归测试（sqlite 内存库，同 test_permissions_acl_real.py 风格）。

覆盖：
- 默认团队共享（is_private NULL/FALSE）：**同团队**成员可编辑（协作型资源，沿用历史行为）；
  跨团队/无团队个人用户不可见
- 私有客户：非 owner 无权（read 都不可见）；owner=owner；ACL 授权 read/edit 生效
- customer_visible_clause 列表级 SQL 过滤：私有客户对他人隐藏，admin 不过滤
- 跨租户隔离
"""
import pytest
from sqlalchemy import JSON, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.customer import Customer
from app.models.resource_permission import ResourcePermission
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_group import UserGroup
from app.services import permissions as perms

_TABLES = [
    Tenant.__table__,
    UserGroup.__table__,
    User.__table__,
    Customer.__table__,
    ResourcePermission.__table__,
]


@pytest.fixture
async def db():
    """sqlite 内存库：建表子集 + 种子租户/用户（JSONB 临时换成通用 JSON）。

    用户：1=admin；2=alice（团队一）；3=bob（团队一）；4=carol（团队二）；
    5=dave（无团队·个人用户）；9=eve（租户二）。
    """
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
            session.add_all([
                Tenant(id=1, name="租户一"),
                Tenant(id=2, name="租户二"),
                UserGroup(id=1, tenant_id=1, name="团队一"),
                UserGroup(id=2, tenant_id=1, name="团队二"),
                User(id=1, tenant_id=1, username="admin", password_hash="x", name="管理员", role="admin", group_id=1),
                User(id=2, tenant_id=1, username="alice", password_hash="x", name="甲", role="member", group_id=1),
                User(id=3, tenant_id=1, username="bob", password_hash="x", name="乙", role="member", group_id=1),
                User(id=4, tenant_id=1, username="carol", password_hash="x", name="丙", role="member", group_id=2),
                User(id=5, tenant_id=1, username="dave", password_hash="x", name="丁", role="individual", group_id=None),
                User(id=9, tenant_id=2, username="eve", password_hash="x", name="戊", role="member", group_id=None),
            ])
            await session.commit()
            yield session
    finally:
        await engine.dispose()
        for col, col_type in swapped_types:
            col.type = col_type
        for table, indexes in saved_indexes.items():
            table.indexes.update(indexes)


def _customer(cid, owner_id, *, private=None, tenant_id=1):
    return Customer(
        id=cid, tenant_id=tenant_id, name=f"客户{cid}", owner_id=owner_id,
        is_private=private, industries=[], tags=[], attributes={},
    )


async def _u(db, uid):
    return await db.get(User, uid)


async def test_team_shared_customer_editable_by_all(db):
    """默认团队共享（NULL/FALSE）：同团队成员有 edit（保持协作型历史行为）。"""
    db.add_all([_customer(1, 2, private=None), _customer(2, 2, private=False)])
    await db.commit()
    bob = await _u(db, 3)
    for cid in (1, 2):
        c = await db.get(Customer, cid)
        perm = await perms.get_access_for(db, 1, bob.id, "customer", c)
        assert perm == "edit"
        assert perms.satisfies(perm, "edit")


async def test_team_shared_customer_isolated_across_teams(db):
    """团队共享客户对跨团队成员与无团队个人用户均不可见（NULL ≠ NULL）。"""
    db.add(_customer(1, 2, private=False))  # alice（团队一）的共享客户
    await db.commit()
    c = await db.get(Customer, 1)
    carol = await _u(db, 4)   # 团队二
    dave = await _u(db, 5)    # 无团队（个人用户）
    assert await perms.get_access_for(db, 1, carol.id, "customer", c) is None
    assert await perms.get_access_for(db, 1, dave.id, "customer", c) is None
    # 个人用户自己的共享客户，团队成员也看不见（个人用户不参与团队共享）
    db.add(_customer(2, 5, private=False))
    await db.commit()
    c2 = await db.get(Customer, 2)
    alice = await _u(db, 2)
    assert await perms.get_access_for(db, 1, alice.id, "customer", c2) is None
    assert await perms.get_access_for(db, 1, dave.id, "customer", c2) == "owner"


async def test_private_customer_hidden_from_others(db):
    """私有客户：非 owner 无权限；owner=owner；admin 由调用方短路（这里验证非 admin 语义）。"""
    db.add(_customer(1, 2, private=True))
    await db.commit()
    bob = await _u(db, 3)
    alice = await _u(db, 2)
    c = await db.get(Customer, 1)
    assert await perms.get_access_for(db, 1, bob.id, "customer", c) is None
    assert await perms.get_access_for(db, 1, alice.id, "customer", c) == "owner"


async def test_private_customer_acl_grant(db):
    """私有客户被授权后：read 可看不可改，edit 可改。"""
    db.add_all([
        _customer(1, 2, private=True),
        ResourcePermission(tenant_id=1, resource_type="customer", resource_id=1, user_id=3, permission="read"),
        # 跨租户授权不生效（eve 在租户2，授权行写在租户2 名下，不影响租户1 的判定）
        ResourcePermission(tenant_id=2, resource_type="customer", resource_id=1, user_id=9, permission="owner"),
    ])
    await db.commit()
    c = await db.get(Customer, 1)
    bob = await _u(db, 3)
    perm_bob = await perms.get_access_for(db, 1, bob.id, "customer", c)
    assert perm_bob == "read"
    assert not perms.satisfies(perm_bob, "edit")
    # eve 是租户2 用户，对租户1 客户永远无权（租户隔离）
    eve = await _u(db, 9)
    assert await perms.get_access_for(db, 1, eve.id, "customer", c) is None


async def test_customer_visible_clause_sql(db):
    """列表级过滤：bob（团队一）看不到 alice 的私有客户，看到同团队共享/自己的/被分享的；
    跨团队与无团队用户看不到团队共享客户；admin 不过滤。"""
    db.add_all([
        _customer(1, 2, private=None),    # 团队一共享
        _customer(2, 3, private=False),   # bob 自己的共享客户
        _customer(3, 2, private=True),    # alice 的私有客户
        _customer(4, 2, private=True),    # alice 私有但分享给 bob
        ResourcePermission(tenant_id=1, resource_type="customer", resource_id=4, user_id=3, permission="read"),
    ])
    await db.commit()
    bob = await _u(db, 3)
    clause = perms.customer_visible_clause(bob)
    rows = (await db.execute(select(Customer.id).where(Customer.tenant_id == 1).where(clause))).scalars().all()
    assert sorted(rows) == [1, 2, 4]

    admin = await _u(db, 1)
    assert perms.customer_visible_clause(admin) is None  # admin 不过滤

    # 跨团队（carol 团队二）：团队一的共享客户不可见，也看不到 alice 的私有客户
    carol = await _u(db, 4)
    rows = (await db.execute(
        select(Customer.id).where(Customer.tenant_id == 1).where(perms.customer_visible_clause(carol))
    )).scalars().all()
    assert rows == []

    # 无团队个人用户（dave）：只有自己拥有的（这里他没有客户）
    dave = await _u(db, 5)
    rows = (await db.execute(
        select(Customer.id).where(Customer.tenant_id == 1).where(perms.customer_visible_clause(dave))
    )).scalars().all()
    assert rows == []

    eve = await _u(db, 9)
    clause = perms.customer_visible_clause(eve)
    rows = (await db.execute(select(Customer.id).where(Customer.tenant_id == 2).where(clause))).scalars().all()
    assert rows == []


async def test_resolve_permissions_batch_customer(db):
    """批量权限解析（列表页 my_perm）：owner/edit/授权混合。"""
    db.add_all([
        _customer(1, 3, private=False),   # bob 拥有
        _customer(2, 2, private=False),   # 团队共享 → edit
        _customer(3, 2, private=True),    # 无权
        ResourcePermission(tenant_id=1, resource_type="customer", resource_id=3, user_id=3, permission="read"),
    ])
    await db.commit()
    bob = await _u(db, 3)
    perms_map = await perms.resolve_permissions(db, bob, "customer", [1, 2, 3])
    assert perms_map == {1: "owner", 2: "edit", 3: "read"}
