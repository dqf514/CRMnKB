"""文件关联 KB / customer_id 触发的 ACL 漏洞回归测试：

- 关联文件到知识库必须校验当前用户对每个文件的 read 权限（有一个无权即 403）；
- batch_associate 必须校验目标 KB 的 edit 权限（此前只查租户归属）；
- update_file 变更 customer_id 会把文件关联进团队可见的客户专属 KB，须 owner 权限。

HTTP 走 httpx ASGITransport；权限/解析用 monkeypatch 打桩，不触真实库。
"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.api import kbs as kbs_api
from app.api import library as library_api
from app.database import get_db
from app.main import app
from app.services import kb as kb_service


class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return self._value if self._value is not None else []


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


def _kb(**kw):
    base = dict(id=5, tenant_id=1, name="库", owner_id=2, is_private=True, deleted_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _file(**kw):
    base = dict(
        id=10, tenant_id=1, folder_id=None, customer_id=None, file_name="a.md",
        file_type="md", file_path="data/uploads/a.md", file_size=3, supported=True,
        owner_id=2, is_private=True, deleted_at=None, content_hash="h",
        created_at=datetime(2026, 8, 26), updated_at=datetime(2026, 8, 26),
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def _allow(*args, **kwargs):
    """ensure_access 打桩：一律放行。"""
    return None


async def _noop_process(doc_id):
    return None


# ---------------------------------------------------------------------------
# ① 无文件 read 权限的关联被拒
# ---------------------------------------------------------------------------

async def test_associate_to_kb_rejects_unreadable_file(client, monkeypatch):
    """associate_to_kb：KB edit 通过，但有一个文件无 read 权限 → 403。"""
    db = _FakeSession()
    _override(db)
    db.queue_get(_kb())
    monkeypatch.setattr(kbs_api, "ensure_access", _allow)

    async def _readable(s, user, rtype, ids):
        return [10]  # 文件 11 无权

    monkeypatch.setattr(kb_service, "filter_accessible_ids", _readable)

    resp = await client.post("/api/v1/kbs/5/documents", json={"file_ids": [10, 11]})
    assert resp.status_code == 403


async def test_batch_associate_rejects_unreadable_file(client, monkeypatch):
    """batch_associate：KB edit 通过，但文件无 read 权限 → 403。"""
    db = _FakeSession()
    _override(db)
    db.queue_get(_kb())
    monkeypatch.setattr(library_api, "ensure_access", _allow)

    async def _readable(s, user, rtype, ids):
        return []  # 全部文件无权

    monkeypatch.setattr(kb_service, "filter_accessible_ids", _readable)

    resp = await client.post("/api/v1/library/associate", json={"file_ids": [10], "kb_ids": [5]})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# ② batch_associate 无 KB edit 权限被拒
# ---------------------------------------------------------------------------

async def test_batch_associate_requires_kb_edit(client, monkeypatch):
    """batch_associate：目标 KB 只有 read（无 edit）→ 403（此前只查租户归属）。"""
    db = _FakeSession()
    _override(db)
    db.queue_get(_kb())

    async def _deny_edit(db_, user, rtype, rid, required="read"):
        if required == "edit":
            raise HTTPException(status_code=403, detail="没有该资源的访问权限")

    monkeypatch.setattr(library_api, "ensure_access", _deny_edit)

    resp = await client.post("/api/v1/library/associate", json={"file_ids": [10], "kb_ids": [5]})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# ③ 非 owner 设置 customer_id 被拒
# ---------------------------------------------------------------------------

async def test_update_file_customer_requires_owner(client, monkeypatch):
    """update_file：仅 edit 权限的用户变更 customer_id → 403（走真实 ensure_owner 判定）。"""
    db = _FakeSession()
    _override(db, uid=3)  # 非 owner
    f = _file(owner_id=2)
    db.queue_get(f)  # _get_file_or_404
    db.queue_get(f)  # ensure_owner → get_access 再取一次
    monkeypatch.setattr(library_api, "ensure_access", _allow)  # 模拟有 edit 权限

    resp = await client.put("/api/v1/library/files/10", json={"customer_id": 7})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# ④ 有权限的正常路径仍通
# ---------------------------------------------------------------------------

async def test_associate_to_kb_ok(client, monkeypatch):
    """associate_to_kb：KB edit + 文件 read 齐全 → 正常关联。"""
    db = _FakeSession()
    _override(db)
    db.queue_get(_kb())
    monkeypatch.setattr(kbs_api, "ensure_access", _allow)
    monkeypatch.setattr(kbs_api, "process_document", _noop_process)

    async def _readable(s, user, rtype, ids):
        return list(ids)

    monkeypatch.setattr(kb_service, "filter_accessible_ids", _readable)
    db.queue_execute([])  # associate_files 查已有关联
    db.queue_execute([_file()])  # associate_files 批量取文件

    resp = await client.post("/api/v1/kbs/5/documents", json={"file_ids": [10]})
    assert resp.status_code == 200
    assert resp.json() == {"associated": 1, "already": 0}


async def test_update_file_customer_owner_ok(client, monkeypatch):
    """update_file：owner 设置 customer_id → 通过并自动关联客户专属 KB。"""
    db = _FakeSession()
    _override(db, uid=2)  # owner
    f = _file()
    db.queue_get(f)  # _get_file_or_404
    db.queue_get(f)  # ensure_owner → get_access（owner 判定）
    monkeypatch.setattr(library_api, "ensure_access", _allow)
    monkeypatch.setattr(library_api, "process_document", _noop_process)
    db.queue_get(SimpleNamespace(id=7, tenant_id=1, name="客户A"))  # 客户

    async def _readable(s, user, rtype, ids):
        return list(ids)

    monkeypatch.setattr(kb_service, "filter_accessible_ids", _readable)
    db.queue_execute([])  # ensure_owner → get_access 的文件夹图查询
    db.queue_execute(None)  # get_or_create_customer_kb 查已有专属库（不存在则新建）
    db.queue_execute([])  # associate_files 查已有关联
    db.queue_execute([f])  # associate_files 批量取文件

    resp = await client.put("/api/v1/library/files/10", json={"customer_id": 7})
    assert resp.status_code == 200
    assert resp.json()["customer_id"] == 7
