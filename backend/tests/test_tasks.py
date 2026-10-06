"""任务列表：customer_id 过滤参数。"""
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.database import get_db
from app.main import app


class _FakeRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """假会话：记录 execute 的语句，scalar 恒 0，execute 恒空列表。"""

    def __init__(self):
        self.stmts = []

    async def scalar(self, stmt):
        return 0

    async def execute(self, stmt):
        self.stmts.append(str(stmt))
        return _FakeRowsResult([])


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override(db):
    async def _fake_user():
        return SimpleNamespace(id=1, tenant_id=1, username="u", name="用户", role="user", status=1)

    async def _fake_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db


async def test_list_tasks_with_customer_id_filter(client):
    db = _FakeSession()
    _override(db)
    resp = await client.get("/api/v1/tasks", params={"customer_id": 5})
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}
    assert db.stmts, "应执行了列表查询"
    # 精确匹配 tasks.customer_id = :param 谓词（可见性子查询里的 customer_id 不算）
    assert "tasks.customer_id = " in db.stmts[-1]


async def test_list_tasks_without_customer_id_filter(client):
    db = _FakeSession()
    _override(db)
    resp = await client.get("/api/v1/tasks")
    assert resp.status_code == 200
    assert "tasks.customer_id = " not in db.stmts[-1]
