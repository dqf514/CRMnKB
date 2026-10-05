from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import hash_password
from app.database import get_db
from app.api import deps
from app.main import app


class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """最小假会话：execute 返回预设用户，不触碰真实数据库。"""

    def __init__(self, user=None):
        self._user = user

    async def execute(self, stmt):
        return _FakeResult(self._user)

    async def scalar(self, stmt):
        return 0  # 登录限流：无失败记录

    def add(self, obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


def _override_db(user=None):
    async def _fake_get_db():
        yield _FakeSession(user)

    app.dependency_overrides[get_db] = _fake_get_db


async def test_login_missing_fields_422(client):
    resp = await client.post("/api/v1/auth/login", json={})
    assert resp.status_code == 422


async def test_login_wrong_credentials_401(client):
    _override_db(user=None)  # 用户不存在
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "nobody", "password": "wrong"}
    )
    assert resp.status_code == 401


async def test_login_success(client):
    fake_user = SimpleNamespace(
        id=1,
        tenant_id=1,
        username="admin",
        name="管理员",
        role="admin",
        password_hash=hash_password("admin123"),
    )
    _override_db(user=fake_user)
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["user"]["username"] == "admin"
    assert data["user"]["role"] == "admin"


async def test_me_without_token_401(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_rag_query_fallback_when_no_data(client, monkeypatch):
    # 免鉴权：替换 get_current_user；db 也用假会话（端点会落 RagQueryLog，勿触真实库）
    async def _fake_user():
        return SimpleNamespace(id=1, tenant_id=1, username="admin", name="管理员", role="admin")

    app.dependency_overrides[deps.get_current_user] = _fake_user
    _override_db()

    # mock 服务层：知识库无数据时返回兜底
    async def _fake_rag_query(db, tenant_id, question, top_k=None, kb_ids=None, user=None):
        return {
            "answer": "未在知识库中找到相关信息，建议转人工处理。",
            "sources": [],
            "grounded": False,
        }

    monkeypatch.setattr("app.api.rag.rag_query", _fake_rag_query)

    resp = await client.post(
        "/api/v1/rag/query",
        json={"question": "退货政策是什么？"},
        headers={"Authorization": "Bearer fake-token"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["grounded"] is False
    assert data["sources"] == []
    assert "转人工" in data["answer"]


async def test_rag_query_requires_auth(client):
    resp = await client.post("/api/v1/rag/query", json={"question": "hello"})
    assert resp.status_code == 401
