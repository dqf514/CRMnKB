"""文件夹级权限/分享测试：新文件夹默认私有、树按 ACL 过滤并补祖先链、越权浏览 403。

HTTP 走 httpx ASGITransport；权限查询用 monkeypatch 打桩，不触真实库。
"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.api import library as library_api
from app.database import get_db
from app.main import app
from app.models.library_folder import LibraryFolder
from app.services import permissions as perms


class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalars(self):
        return self

    def all(self):
        return self._value if self._value is not None else []

    def mappings(self):
        return self


class _FakeSession:
    def __init__(self):
        self._execute_queue = []
        self._get_queue = []
        self.added = []

    def queue_execute(self, v):
        self._execute_queue.append(v)

    def queue_get(self, v):
        self._get_queue.append(v)

    async def execute(self, stmt, params=None):
        return _FakeResult(self._execute_queue.pop(0) if self._execute_queue else [])

    async def scalar(self, stmt):
        return None

    async def get(self, model, ident):
        return self._get_queue.pop(0) if self._get_queue else None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        await self.flush()

    async def refresh(self, obj):
        pass

    async def flush(self):
        for o in self.added:
            if getattr(o, "id", None) is None:
                o.id = 100


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


def _folder(**kw):
    base = dict(
        id=1, tenant_id=1, parent_id=None, name="目录", owner_id=2,
        is_private=True, deleted_at=None, created_at=datetime(2026, 8, 26),
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def test_new_folder_private_owned(client):
    db = _FakeSession()
    _override(db)
    resp = await client.post("/api/v1/library/folders", json={"name": "新建目录"})
    assert resp.status_code == 201
    folder = next((a for a in db.added if isinstance(a, LibraryFolder)), None)
    assert folder is not None
    assert folder.owner_id == 2
    assert folder.is_private is True


async def test_folder_tree_filters_and_adds_ancestors(client, monkeypatch):
    db = _FakeSession()
    _override(db)

    async def _accessible(s, user, rtype):
        return [2]

    async def _perms(s, user, rtype, ids):
        return {2: "owner"}

    monkeypatch.setattr(library_api, "accessible_ids", _accessible)
    monkeypatch.setattr(library_api, "resolve_permissions", _perms)
    db.queue_execute([
        _folder(id=1, parent_id=None, name="私有父"),
        _folder(id=2, parent_id=1, name="共享子"),
    ])

    resp = await client.get("/api/v1/library/tree")
    assert resp.status_code == 200
    tree = resp.json()
    # 只显示可见子目录及其祖先，隐藏无关文件夹
    assert [n["name"] for n in tree] == ["私有父"]
    assert tree[0]["children"][0]["name"] == "共享子"
    assert tree[0]["children"][0]["perm"] == "owner"
    assert tree[0]["perm"] is None  # 父目录仅路径可见


async def test_browse_private_folder_403(client):
    db = _FakeSession()
    _override(db, uid=3)  # 非 owner
    db.queue_get(_folder(id=1, owner_id=2, is_private=True))
    resp = await client.get("/api/v1/library/files", params={"folder_id": 1})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 级联继承（共享父文件夹 → 子文件夹及其中文件）
# ---------------------------------------------------------------------------

def test_ancestors_chain():
    parent = {1: None, 2: 1, 3: 2, 4: 99}
    assert perms._ancestors(parent, 3) == [2, 1]
    assert perms._ancestors(parent, 1) == []
    assert perms._ancestors(parent, 4) == []  # 99 不在图中，忽略


def test_descendants_closure():
    children = {1: [2, 3], 2: [4], 5: []}
    assert perms._descendants(children, {1}) == {1, 2, 3, 4}
    assert perms._descendants(children, {2}) == {2, 4}


async def test_resolve_file_inherits_folder_perm(monkeypatch):
    """文件自身无权限，但父文件夹被分享 → 文件继承 read。"""
    db = _FakeSession()
    db.queue_execute([SimpleNamespace(id=100, folder_id=2)])  # 文件 100 在文件夹 2 下

    async def _fg(db, tid):
        return {2: 1, 1: None}, {None: [1], 1: [2]}

    async def _dp(db, user, rtype, ids):
        if rtype == "file":
            return {100: None}  # 文件直接无权限
        return {1: "read"}  # 父文件夹 read

    monkeypatch.setattr(perms, "_folder_graph", _fg)
    monkeypatch.setattr(perms, "_direct_perms", _dp)
    user = SimpleNamespace(id=9, tenant_id=1, role="user")
    res = await perms.resolve_permissions(db, user, "file", [100])
    assert res[100] == "read"


async def test_resolve_folder_inherits_highest(monkeypatch):
    """子文件夹权限取链上最高：父 read + 子自身 edit → 子为 edit。"""

    async def _fg(db, tid):
        return {2: 1, 1: None}, {None: [1], 1: [2]}

    async def _dp(db, user, rtype, ids):
        if rtype == "folder":
            return {1: "read", 2: "edit"}
        return {}

    monkeypatch.setattr(perms, "_folder_graph", _fg)
    monkeypatch.setattr(perms, "_direct_perms", _dp)
    user = SimpleNamespace(id=9, tenant_id=1, role="user")
    res = await perms.resolve_permissions(_FakeSession(), user, "folder", [2])
    assert res[2] == "edit"


async def test_get_access_inherits_ancestor_folder(monkeypatch):
    """单资源访问：子文件夹直接无权限，但祖先文件夹 read → 可读（可浏览）。"""
    db = _FakeSession()
    db.queue_get(_folder(id=2, parent_id=1, owner_id=2, is_private=True))  # 子
    db.queue_get(_folder(id=1, parent_id=None, owner_id=2, is_private=True))  # 祖先

    async def _fg(db, tid):
        return {2: 1, 1: None}, {None: [1], 1: [2]}

    monkeypatch.setattr(perms, "_folder_graph", _fg)

    async def _gda(db, tid, uid, rtype, obj):
        return "read" if obj.id == 1 else None

    monkeypatch.setattr(perms, "_get_direct_access", _gda)

    perm = await perms.get_access(db, 1, 9, "folder", 2)
    assert perm == "read"
