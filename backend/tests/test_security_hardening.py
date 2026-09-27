"""批次 B1 安全/正确性修复的回归测试。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.api.auth import reset_login_rate_limit
from app.core.security import create_access_token, hash_password
from app.database import get_db
from app.main import app
from app.services.reminder import match_due_soon_tasks
from app.services.workflow import should_run


class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if self._value is not None else []


class _FakeSession:
    def __init__(self):
        self._execute_queue = []
        self._scalar_queue = []
        self._get_queue = []
        self.added = []

    def queue_execute(self, value):
        self._execute_queue.append(value)

    def queue_scalar(self, value):
        self._scalar_queue.append(value)

    def queue_get(self, value):
        self._get_queue.append(value)

    async def execute(self, stmt, params=None):
        return _FakeResult(self._execute_queue.pop(0) if self._execute_queue else None)

    async def scalar(self, stmt):
        return self._scalar_queue.pop(0) if self._scalar_queue else None

    async def get(self, model, ident):
        return self._get_queue.pop(0) if self._get_queue else None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def refresh(self, obj):
        pass

    async def delete(self, obj):
        pass


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    reset_login_rate_limit()


def _override_user(user=None, db=None):
    user = user or SimpleNamespace(
        id=1, tenant_id=1, username="admin", name="管理员", role="admin", status=1
    )

    async def _fake_user():
        return user

    async def _fake_db():
        yield db or _FakeSession()

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db
    return user


# ---------------------------------------------------------------------------
# 登录限流：5 次失败锁 15 分钟 → 429
# ---------------------------------------------------------------------------

async def test_login_rate_limit_429_after_5_failures(client):
    reset_login_rate_limit()  # 防止其他测试的同 key 失败计数干扰
    db = _FakeSession()

    async def _fake_db():
        yield db

    app.dependency_overrides[get_db] = _fake_db
    # DB 限流：每次登录先消耗 2 条 scalar（账号+IP / IP 失败数），再 1 条 execute（用户查询）
    for _ in range(5):
        db.queue_scalar(0)
        db.queue_scalar(0)
        db.queue_execute(None)  # 用户不存在 → 401
        resp = await client.post(
            "/api/v1/auth/login", json={"username": "nobody", "password": "wrong"}
        )
        assert resp.status_code == 401
    db.queue_scalar(5)  # 第 6 次：账号+IP 失败数达阈值 → 429
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "nobody", "password": "wrong"}
    )
    assert resp.status_code == 429


async def test_login_success_clears_failures(client):
    reset_login_rate_limit()
    db = _FakeSession()
    user = SimpleNamespace(
        id=1, tenant_id=1, username="admin", name="管理员", role="admin",
        password_hash=hash_password("admin123"), status=1, email=None, avatar_url=None,
    )

    async def _fake_db():
        yield db

    app.dependency_overrides[get_db] = _fake_db
    # 先失败一次
    db.queue_scalar(0)
    db.queue_scalar(0)
    db.queue_execute(None)
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
    )
    assert resp.status_code == 401
    # 再成功
    db.queue_scalar(0)
    db.queue_scalar(0)
    db.queue_execute(user)
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 密码强度：min_length=8
# ---------------------------------------------------------------------------

async def test_change_password_too_short_422(client):
    user = _override_user(
        SimpleNamespace(
            id=1, tenant_id=1, username="u", name="用户", role="user", status=1,
            password_hash=hash_password("oldpass123"), email=None, avatar_url=None,
            preferences={},
        )
    )
    resp = await client.put(
        "/api/v1/auth/password", json={"old_password": "oldpass123", "new_password": "short"}
    )
    assert resp.status_code == 422


async def test_admin_create_user_password_too_short_422(client):
    _override_user(db=_FakeSession())
    resp = await client.post(
        "/api/v1/admin/users",
        json={"username": "newbie", "password": "short", "name": "新人"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 改密后旧 JWT 失效（iat < password_changed_at → 401）
# ---------------------------------------------------------------------------

async def test_old_token_rejected_after_password_change(client):
    token = create_access_token(1, "admin")
    db = _FakeSession()
    # password_changed_at 在 token 签发之后 → 旧 token 失效
    db.queue_get(
        SimpleNamespace(
            id=1, tenant_id=1, username="admin", name="管理员", role="admin", status=1,
            password_changed_at=datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(minutes=1),
        )
    )

    async def _fake_db():
        yield db

    app.dependency_overrides[get_db] = _fake_db
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_token_valid_when_password_changed_before_issue(client):
    token = create_access_token(1, "admin")
    db = _FakeSession()
    db.queue_get(
        SimpleNamespace(
            id=1, tenant_id=1, username="admin", name="管理员", role="admin", status=1,
            email=None, avatar_url=None,
            password_changed_at=datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(minutes=1),
        )
    )

    async def _fake_db():
        yield db

    app.dependency_overrides[get_db] = _fake_db
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 删除用户：有业务数据 409，无数据 204（清理用量日志关联）
# ---------------------------------------------------------------------------

async def test_delete_user_with_business_data_409(client):
    db = _FakeSession()
    _override_user(db=db)
    db.queue_get(SimpleNamespace(id=2, tenant_id=1, username="alice"))
    db.queue_scalar(3)  # 客户 3 条 → 409
    resp = await client.delete("/api/v1/admin/users/2")
    assert resp.status_code == 409
    assert "建议停用" in resp.json()["detail"]


async def test_delete_user_without_data_204(client):
    db = _FakeSession()
    _override_user(db=db)
    db.queue_get(SimpleNamespace(id=2, tenant_id=1, username="alice"))
    # 9 项关联统计全部为 0（队列空 → None）
    db.queue_execute(None)  # 清理 llm_call_logs 残留关联
    resp = await client.delete("/api/v1/admin/users/2")
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# 时区统一（naive UTC）：新记录立即被"今日/到期"逻辑命中
# ---------------------------------------------------------------------------

def test_naive_utc_due_task_matched_today():
    """模拟 DB 读出的 naive UTC due_date，naive now 下到期任务立即命中。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        {"task_id": 1, "title": "刚创建", "user_id": 1,
         "due_date": now + timedelta(hours=1), "status": "pending"},
        {"task_id": 2, "title": "已完成", "user_id": 1,
         "due_date": now + timedelta(hours=1), "status": "completed"},
    ]
    matched = match_due_soon_tasks(rows, threshold_hours=24, now=now)
    assert [m["task_id"] for m in matched] == [1]


def test_should_run_naive_last_run_at_consistent():
    """naive UTC 的 last_run_at（新库统一约定）与 naive now 比较正常。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    last = now - timedelta(minutes=30)
    assert should_run("interval", {"interval_minutes": 60}, last, now) is False
    assert should_run("interval", {"interval_minutes": 20}, last, now) is True
    # daily：同一天的 last_run_at 不再触发，昨天则触发
    assert should_run("daily", {"time": "00:00"}, last, now) is False
    assert should_run("daily", {"time": "00:00"}, last - timedelta(days=1), now) is True
