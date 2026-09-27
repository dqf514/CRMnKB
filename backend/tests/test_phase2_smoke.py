from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.database import get_db
from app.main import app


class _FakeResult:
    """同时支持 .all() / .scalars().all() / .scalar_one_or_none() 的最小结果集。"""

    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """队列式假会话：execute/scalar 按调用顺序弹出预设结果，不触碰真实数据库。"""

    def __init__(self, get_obj=None):
        self._execute_queue = []
        self._scalar_queue = []
        self._get_obj = get_obj
        self.added = None

    def queue_execute(self, rows=None, rowcount=0):
        self._execute_queue.append(_FakeResult(rows, rowcount))

    def queue_scalar(self, value):
        self._scalar_queue.append(value)

    async def get(self, model, pk):
        return self._get_obj

    async def execute(self, stmt):
        return self._execute_queue.pop(0)

    async def scalar(self, stmt):
        return self._scalar_queue.pop(0)

    def add(self, obj):
        self.added = obj

    async def delete(self, obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 1
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)


def _fake_user():
    return SimpleNamespace(id=1, tenant_id=1, username="admin", name="管理员", role="admin")


@pytest.fixture
async def client():
    async def _override_user():
        return _fake_user()

    app.dependency_overrides[deps.get_current_user] = _override_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override_db(session):
    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db


# ---------- /tasks ----------

async def test_create_task(client):
    session = _FakeSession()
    _override_db(session)
    resp = await client.post(
        "/api/v1/tasks",
        json={"title": "回访客户", "priority": "high"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "回访客户"
    assert data["priority"] == "high"
    assert data["status"] == "pending"
    assert data["source"] == "manual"
    assert data["ai_generated"] is False


async def test_list_tasks_with_customer_name(client):
    task_ns = SimpleNamespace(
        id=5, tenant_id=1, customer_id=2, user_id=1, title="规则任务",
        description=None, type="follow_up", priority="medium", due_date=None,
        status="pending", ai_generated=False, source="rule",
        created_at=datetime.now(timezone.utc), completed_at=None,
    )
    session = _FakeSession()
    session.queue_scalar(1)  # total
    session.queue_execute([(task_ns, "某某科技")])  # (Task, customer_name) 行
    _override_db(session)
    resp = await client.get("/api/v1/tasks", headers={"Authorization": "Bearer fake"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["customer_name"] == "某某科技"


async def test_tasks_require_auth(client):
    app.dependency_overrides.pop(deps.get_current_user, None)
    resp = await client.get("/api/v1/tasks")
    assert resp.status_code == 401


# ---------- /notifications ----------

async def test_list_notifications(client):
    notif_ns = SimpleNamespace(
        id=1, tenant_id=1, user_id=1, task_id=3, title="7天未跟进提醒",
        content="客户 A 已 9 天未跟进", type="reminder", is_read=False,
        created_at=datetime.now(timezone.utc),
    )
    session = _FakeSession()
    session.queue_execute([notif_ns])  # 列表
    session.queue_scalar(1)  # unread_count
    _override_db(session)
    resp = await client.get("/api/v1/notifications", headers={"Authorization": "Bearer fake"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["unread_count"] == 1
    assert data["items"][0]["title"] == "7天未跟进提醒"


# ---------- /feedback ----------

async def test_create_feedback(client):
    session = _FakeSession()
    _override_db(session)
    resp = await client.post(
        "/api/v1/feedback",
        json={"rating": "useful", "comment": "回答准确"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["rating"] == "useful"
    assert data["comment"] == "回答准确"


async def test_create_feedback_invalid_rating_422(client):
    resp = await client.post(
        "/api/v1/feedback",
        json={"rating": "good"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 422


async def test_feedback_stats(client):
    session = _FakeSession()
    session.queue_scalar(10)  # total
    session.queue_scalar(7)  # useful
    session.queue_scalar(3)  # useless
    _override_db(session)
    resp = await client.get("/api/v1/feedback/stats", headers={"Authorization": "Bearer fake"})
    assert resp.status_code == 200
    assert resp.json() == {"total": 10, "useful": 7, "useless": 3, "useful_rate": 0.7}
