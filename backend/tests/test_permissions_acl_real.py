"""ACL 真实实现回归测试：permissions 服务层跑 sqlite 内存库真实 SQL，不再 monkeypatch。

安全边界覆盖：
- owner 可读（owner 权限）
- resource_permissions 授权 read / edit
- 团队共享开关（is_private=False 与存量 NULL 都视为团队共享，仅 owner 同团队成员可见）
- 文件夹级联继承（祖先授权向下覆盖子文件夹与文件，取链上最高权限）
- admin 绕过
- 无权不可见 + 跨租户隔离

实现要点：
- 用 aiosqlite 内存库建真实 AsyncSession，只建权限相关的表子集
  （tenants/users/library_folders/library_files/resource_permissions/knowledge_bases/notebooks），
  避开 document_chunks 等 PG 专有类型（VECTOR/TSVECTOR）的表。
- 子集表中 users.preferences / notebooks.source_*_ids 是 JSONB，sqlite 无法渲染，
  建表前临时替换为通用 JSON，fixture 结束时恢复（不污染其它测试）。
- 表上的 PG 专有索引（如 knowledge_bases 的 postgresql_where 部分唯一索引）在 sqlite 下
  语义会变化，建表前临时摘除、用后恢复——本测试只校验 ACL 查询逻辑，不校验索引。
"""
import pytest
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.knowledge_base import KnowledgeBase
from app.models.library_file import LibraryFile
from app.models.library_folder import LibraryFolder
from app.models.notebook import Notebook
from app.models.resource_permission import ResourcePermission
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_group import UserGroup
from app.services import permissions as perms

_TABLES = [
    Tenant.__table__,
    UserGroup.__table__,
    User.__table__,
    LibraryFolder.__table__,
    LibraryFile.__table__,
    ResourcePermission.__table__,
    KnowledgeBase.__table__,
    Notebook.__table__,
]


@pytest.fixture
async def db():
    """sqlite 内存库真实会话：建权限相关表子集 + 种子租户/用户。"""
    # JSONB → 通用 JSON；摘除表级索引（PG 专有 where 子句在 sqlite 下语义不同）
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
            await _seed_users(session)
            yield session
    finally:
        await engine.dispose()
        for col, col_type in swapped_types:
            col.type = col_type
        for table, indexes in saved_indexes.items():
            table.indexes.update(indexes)


async def _seed_users(session):
    """两个租户 + 团队：1=租户1 admin（团队一），2=alice（团队一），3=bob（团队一），
    4=carol（团队二），5=dave（无团队·个人用户），9=租户2 eve。"""
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


# ---------------------------------------------------------------------------
# 造数辅助
# ---------------------------------------------------------------------------

def _file(fid, owner_id, *, private=True, folder_id=None, tenant_id=1, deleted=False):
    from datetime import datetime, timezone

    return LibraryFile(
        id=fid, tenant_id=tenant_id, folder_id=folder_id,
        file_name=f"f{fid}.txt", file_path=f"/uploads/f{fid}.txt", file_type="txt",
        file_size=10, supported=True, owner_id=owner_id, is_private=private,
        deleted_at=datetime.now(timezone.utc) if deleted else None,
    )


def _folder(fid, owner_id, *, private=True, parent_id=None, tenant_id=1):
    return LibraryFolder(
        id=fid, tenant_id=tenant_id, parent_id=parent_id, name=f"目录{fid}",
        owner_id=owner_id, is_private=private,
    )


def _kb(kid, owner_id, *, private=True, tenant_id=1):
    return KnowledgeBase(
        id=kid, tenant_id=tenant_id, name=f"kb{kid}", type="general",
        is_auto=False, owner_id=owner_id, is_private=private,
    )


def _grant(rtype, rid, user_id, permission, tenant_id=1):
    return ResourcePermission(
        tenant_id=tenant_id, resource_type=rtype, resource_id=rid,
        user_id=user_id, permission=permission,
    )


async def _user(db, uid) -> User:
    return await db.get(User, uid)


# ---------------------------------------------------------------------------
# owner 可读
# ---------------------------------------------------------------------------

async def test_owner_has_owner_access(db):
    db.add(_file(1, owner_id=2))
    await db.commit()
    alice = await _user(db, 2)
    assert await perms.get_access(db, 1, 2, "file", 1) == "owner"
    assert await perms.resolve_permissions(db, alice, "file", [1]) == {1: "owner"}
    assert await perms.accessible_ids(db, alice, "file") == [1]
    await perms.ensure_access(db, alice, "file", 1, "edit")  # owner 满足 edit
    await perms.ensure_owner(db, alice, "file", 1)


# ---------------------------------------------------------------------------
# ACL 授权 read / edit
# ---------------------------------------------------------------------------

async def test_acl_read_grant(db):
    db.add(_file(1, owner_id=2))
    db.add(_grant("file", 1, user_id=3, permission="read"))
    await db.commit()
    bob = await _user(db, 3)
    assert await perms.get_access(db, 1, 3, "file", 1) == "read"
    assert await perms.accessible_ids(db, bob, "file") == [1]
    assert await perms.filter_accessible_ids(db, bob, "file", [1, 2]) == [1]
    await perms.ensure_access(db, bob, "file", 1, "read")
    with pytest.raises(Exception):  # read 不满足 edit
        await perms.ensure_access(db, bob, "file", 1, "edit")


async def test_acl_edit_grant(db):
    db.add(_file(1, owner_id=2))
    db.add(_grant("file", 1, user_id=3, permission="edit"))
    await db.commit()
    assert await perms.get_access(db, 1, 3, "file", 1) == "edit"
    await perms.ensure_access(db, await _user(db, 3), "file", 1, "edit")
    with pytest.raises(Exception):  # edit 不是 owner
        await perms.ensure_owner(db, await _user(db, 3), "file", 1)


# ---------------------------------------------------------------------------
# 团队可见（is_private=False 与存量 NULL）
# ---------------------------------------------------------------------------

async def test_team_visible_when_not_private(db):
    db.add(_file(1, owner_id=2, private=False))
    await db.commit()
    bob = await _user(db, 3)  # 与 owner 同团队
    assert await perms.get_access(db, 1, 3, "file", 1) == "read"
    assert await perms.accessible_ids(db, bob, "file") == [1]


async def test_team_visible_isolated_across_teams(db):
    """团队共享仅同团队可见：跨团队成员与无团队个人用户都看不到（含 accessible_ids）。"""
    db.add(_file(1, owner_id=2, private=False))
    await db.commit()
    carol = await _user(db, 4)   # 团队二
    dave = await _user(db, 5)    # 无团队（个人用户）
    assert await perms.get_access(db, 1, 4, "file", 1) is None
    assert await perms.get_access(db, 1, 5, "file", 1) is None
    assert await perms.accessible_ids(db, carol, "file") == []
    assert await perms.accessible_ids(db, dave, "file") == []
    # 个人用户自己的共享文件，团队成员也看不见
    db.add(_file(2, owner_id=5, private=False))
    await db.commit()
    alice = await _user(db, 2)
    assert await perms.get_access(db, 1, 2, "file", 2) is None
    assert await perms.accessible_ids(db, alice, "file") == [1]  # 只有自己那份，看不到 dave 的
    # 但 ACL 单用户授权不受团队限制：授权给跨团队的 carol 仍生效
    db.add(_grant("file", 1, user_id=4, permission="read"))
    await db.commit()
    assert await perms.get_access(db, 1, 4, "file", 1) == "read"


def test_legacy_null_is_private_counts_as_team_visible():
    """存量 NULL（列新增前的老行）视为团队可见——模型列已 NOT NULL，NULL 只存在于
    PG 老库，sqlite 无法复现，这里锁定纯函数契约。"""
    assert perms.is_team_visible(None) is True
    assert perms.is_team_visible(False) is True
    assert perms.is_team_visible(True) is False


async def test_acl_overrides_team_visible_read(db):
    """团队可见给 read，显式 edit 授权应提升为 edit（取最高）。"""
    db.add(_file(1, owner_id=2, private=False))
    db.add(_grant("file", 1, user_id=3, permission="edit"))
    await db.commit()
    assert await perms.get_access(db, 1, 3, "file", 1) == "edit"


# ---------------------------------------------------------------------------
# 文件夹级联继承
# ---------------------------------------------------------------------------

async def test_folder_grant_cascades_to_children_and_files(db):
    """父文件夹授权 read → 子文件夹与其中文件均可见（级联继承）。"""
    db.add(_folder(10, owner_id=2))                    # 父目录（私有，alice 的）
    db.add(_folder(11, owner_id=2, parent_id=10))      # 子目录
    db.add(_file(1, owner_id=2, folder_id=11))         # 子目录里的文件
    db.add(_grant("folder", 10, user_id=3, permission="read"))
    await db.commit()
    bob = await _user(db, 3)
    assert await perms.get_access(db, 1, 3, "folder", 11) == "read"
    assert await perms.get_access(db, 1, 3, "file", 1) == "read"
    assert await perms.accessible_ids(db, bob, "folder") == [10, 11]
    assert await perms.accessible_ids(db, bob, "file") == [1]
    assert await perms.filter_accessible_ids(db, bob, "file", [1, 99]) == [1]


async def test_direct_grant_beats_inherited(db):
    """文件自身 edit 授权 > 文件夹链继承的 read（取链上最高）。"""
    db.add(_folder(10, owner_id=2))
    db.add(_file(1, owner_id=2, folder_id=10))
    db.add(_grant("folder", 10, user_id=3, permission="read"))
    db.add(_grant("file", 1, user_id=3, permission="edit"))
    await db.commit()
    assert await perms.get_access(db, 1, 3, "file", 1) == "edit"
    bob = await _user(db, 3)
    assert await perms.resolve_permissions(db, bob, "file", [1]) == {1: "edit"}


async def test_folder_edit_grant_inherited_as_edit(db):
    db.add(_folder(10, owner_id=2))
    db.add(_file(1, owner_id=2, folder_id=10))
    db.add(_grant("folder", 10, user_id=3, permission="edit"))
    await db.commit()
    assert await perms.get_access(db, 1, 3, "file", 1) == "edit"


# ---------------------------------------------------------------------------
# admin 绕过
# ---------------------------------------------------------------------------

async def test_admin_bypasses_acl(db):
    db.add(_file(1, owner_id=2))
    db.add(_file(2, owner_id=3))
    db.add(_folder(10, owner_id=2))
    await db.commit()
    admin = await _user(db, 1)
    assert sorted(await perms.accessible_ids(db, admin, "file")) == [1, 2]
    assert await perms.filter_accessible_ids(db, admin, "file", [1, 2, 99]) == [1, 2, 99]
    assert await perms.resolve_permissions(db, admin, "file", [1]) == {1: "owner"}
    await perms.ensure_access(db, admin, "file", 1, "owner")
    await perms.ensure_owner(db, admin, "file", 1)


# ---------------------------------------------------------------------------
# 无权不可见 + 跨租户隔离
# ---------------------------------------------------------------------------

async def test_no_access_invisible(db):
    db.add(_file(1, owner_id=2))
    await db.commit()
    bob = await _user(db, 3)
    assert await perms.get_access(db, 1, 3, "file", 1) is None
    assert await perms.accessible_ids(db, bob, "file") == []
    assert await perms.filter_accessible_ids(db, bob, "file", [1]) == []
    assert await perms.resolve_permissions(db, bob, "file", [1]) == {}
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await perms.ensure_access(db, bob, "file", 1)
    assert exc.value.status_code == 403


async def test_cross_tenant_invisible(db):
    """他租户资源对本租户用户不可见（即使 id 撞上也按 tenant_id 过滤）。"""
    db.add(_file(5, owner_id=9, tenant_id=2))
    db.add(_kb(5, owner_id=9, tenant_id=2))
    await db.commit()
    alice = await _user(db, 2)
    assert await perms.get_access(db, 1, 2, "file", 5) is None
    assert await perms.accessible_ids(db, alice, "file") == []
    assert await perms.accessible_ids(db, alice, "kb") == []
    assert await perms.filter_accessible_ids(db, alice, "kb", [5]) == []


# ---------------------------------------------------------------------------
# kb / notebook 资源类型
# ---------------------------------------------------------------------------

async def test_kb_acl_and_team_visible(db):
    db.add(_kb(1, owner_id=2))                    # 私有
    db.add(_kb(2, owner_id=2, private=False))     # 团队共享
    db.add(_grant("kb", 1, user_id=3, permission="read"))
    await db.commit()
    bob = await _user(db, 3)
    assert await perms.accessible_ids(db, bob, "kb") == [1, 2]
    assert await perms.get_access(db, 1, 3, "kb", 1) == "read"
    assert await perms.get_access(db, 1, 3, "kb", 2) == "read"
    alice = await _user(db, 2)
    assert await perms.get_access(db, 1, 2, "kb", 1) == "owner"
    # 跨团队（carol 团队二）：团队共享的 kb2 不可见
    carol = await _user(db, 4)
    assert await perms.get_access(db, 1, 4, "kb", 2) is None
    assert await perms.accessible_ids(db, carol, "kb") == []


async def test_notebook_owner_and_acl(db):
    db.add(Notebook(
        id=1, tenant_id=1, name="工作区", created_by=2, is_private=True,
        source_kb_ids=[], source_file_ids=[],
    ))
    db.add(_grant("notebook", 1, user_id=3, permission="edit"))
    await db.commit()
    assert await perms.get_access(db, 1, 2, "notebook", 1) == "owner"
    assert await perms.get_access(db, 1, 3, "notebook", 1) == "edit"
    bob = await _user(db, 3)
    assert await perms.accessible_ids(db, bob, "notebook") == [1]
