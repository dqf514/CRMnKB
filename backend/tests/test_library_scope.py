"""文档库列表页签过滤（scope 参数）测试。

验证 GET /api/v1/library/files 的 scope=all|mine|shared|team 在 SQL 查询层生效，
且不传 scope 时行为与现状一致（无额外过滤）。fake session 只记录 count 语句，
不断真实库；权限相关函数 monkeypatch 打桩。
"""
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.api import library as library_api
from app.database import get_db
from app.main import app


class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def all(self):
        return self._value if self._value is not None else []


class _FakeSession:
    """记录 count 查询语句，主查询返回空页。"""

    def __init__(self):
        self.count_stmts = []

    async def scalar(self, stmt):
        self.count_stmts.append(stmt)
        return 0

    async def execute(self, stmt, params=None):
        return _FakeResult([])


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override(db, role="user", uid=2):
    async def _fake_user():
        return SimpleNamespace(id=uid, tenant_id=1, username="u", name="用户", role=role)

    async def _fake_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db


@pytest.fixture
def patched_db(monkeypatch):
    """打桩权限/分类查询，返回 fake session（已记录 SQL）。"""

    def _make(role="user", uid=2):
        db = _FakeSession()
        _override(db, role=role, uid=uid)

        async def _accessible(s, user, rtype):
            return [1, 2, 3]

        async def _perms(s, user, rtype, ids):
            return {i: "read" for i in ids}

        async def _cats(s, ids):
            return {}

        monkeypatch.setattr(library_api, "accessible_ids", _accessible)
        monkeypatch.setattr(library_api, "resolve_permissions", _perms)
        monkeypatch.setattr(library_api, "_file_categories", _cats)
        return db

    return _make


def _count_sql(db) -> str:
    assert db.count_stmts, "未发起 count 查询"
    return str(db.count_stmts[-1].compile(compile_kwargs={"literal_binds": True})).lower()


async def test_no_scope_unchanged(client, patched_db):
    """不传 scope：无 owner/is_private 额外过滤，行为与现状一致；ACL 约束保留。"""
    db = patched_db()
    resp = await client.get("/api/v1/library/files")
    assert resp.status_code == 200
    sql = _count_sql(db)
    assert "library_files.id in" in sql  # 仍可访问集合约束
    assert "owner_id" not in sql
    assert "is_private" not in sql


async def test_scope_all_same_as_default(client, patched_db):
    """scope=all 与不传等价。"""
    db = patched_db()
    resp = await client.get("/api/v1/library/files", params={"scope": "all"})
    assert resp.status_code == 200
    sql = _count_sql(db)
    assert "owner_id" not in sql
    assert "is_private" not in sql


async def test_scope_mine_filters_owner(client, patched_db):
    """scope=mine：追加 owner_id = 当前用户。"""
    db = patched_db(uid=2)
    resp = await client.get("/api/v1/library/files", params={"scope": "mine"})
    assert resp.status_code == 200
    sql = _count_sql(db)
    assert "library_files.owner_id = 2" in sql


async def test_scope_team_filters_team_visible(client, patched_db):
    """scope=team：is_private NULL 或 FALSE（团队可见）。"""
    db = patched_db()
    resp = await client.get("/api/v1/library/files", params={"scope": "team"})
    assert resp.status_code == 200
    sql = _count_sql(db)
    assert "library_files.is_private is null" in sql
    assert "library_files.is_private is false" in sql


async def test_scope_shared_filters(client, patched_db):
    """scope=shared：私有 + 非我拥有 + 排除授予我 owner 的 ACL。"""
    db = patched_db(uid=2)
    resp = await client.get("/api/v1/library/files", params={"scope": "shared"})
    assert resp.status_code == 200
    sql = _count_sql(db)
    assert "library_files.is_private is true" in sql
    assert "library_files.owner_id != 2" in sql
    assert "resource_permissions" in sql  # 排除 owner 级 ACL 授权
    assert "library_files.id in" in sql  # 基础可见集合约束仍在（scope 只收窄）


async def test_scope_invalid_400(client, patched_db):
    patched_db()
    resp = await client.get("/api/v1/library/files", params={"scope": "bogus"})
    assert resp.status_code == 400


async def test_admin_scope_mine_no_extra_filter(client, patched_db):
    """管理员 scope=mine：一切视同 owner，不再收窄（与前端页签表现一致）。"""
    db = patched_db(role="admin")
    resp = await client.get("/api/v1/library/files", params={"scope": "mine"})
    assert resp.status_code == 200
    sql = _count_sql(db)
    assert "owner_id" not in sql


async def test_admin_scope_shared_empty(client, patched_db):
    """管理员 scope=shared：直接返回空，不发起 count 查询。"""
    db = patched_db(role="admin")
    resp = await client.get("/api/v1/library/files", params={"scope": "shared"})
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}
    assert not db.count_stmts
