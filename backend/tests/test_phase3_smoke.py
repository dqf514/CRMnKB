from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.core.security import hash_password, verify_password
from app.database import get_db
from app.main import app


class _FakeSession:
    def __init__(self, get_obj=None):
        self._get_obj = get_obj

    async def get(self, model, pk):
        return self._get_obj

    def add(self, obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 1
        now = datetime.now(timezone.utc)
        for field in ("created_at", "updated_at"):
            if getattr(obj, field, None) is None:
                setattr(obj, field, now)


def _make_user():
    return SimpleNamespace(
        id=1,
        tenant_id=1,
        username="admin",
        name="管理员",
        role="admin",
        email=None,
        avatar_url=None,
        preferences={},
        password_hash=hash_password("old123"),
    )


@pytest.fixture
def user():
    return _make_user()


@pytest.fixture
async def client(user):
    async def _override_user():
        return user

    async def _fake_get_db():
        yield _FakeSession()

    app.dependency_overrides[deps.get_current_user] = _override_user
    app.dependency_overrides[get_db] = _fake_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ---------- 个人中心 ----------

async def test_update_profile(client, user):
    resp = await client.put(
        "/api/v1/auth/profile",
        json={"name": "新名字", "email": "a@b.com"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "新名字"
    assert data["email"] == "a@b.com"
    assert user.name == "新名字"


async def test_update_profile_ignores_avatar_url(client, user):
    """avatar_url 只能走 /auth/avatar 上传端点：profile 更新静默忽略该字段（防绕过 MIME 校验）。"""
    user.avatar_url = "/avatars/old.png"
    resp = await client.put(
        "/api/v1/auth/profile",
        json={"name": "新名字", "avatar_url": "https://evil.example/x.png"},
    )
    assert resp.status_code == 200
    assert resp.json()["avatar_url"] == "/avatars/old.png"
    assert user.avatar_url == "/avatars/old.png"


async def test_change_password_success(client, user):
    resp = await client.put(
        "/api/v1/auth/password",
        json={"old_password": "old123", "new_password": "newpass456"},
    )
    assert resp.status_code == 204
    assert verify_password("newpass456", user.password_hash)


async def test_change_password_wrong_old_400(client, user):
    resp = await client.put(
        "/api/v1/auth/password",
        json={"old_password": "wrong", "new_password": "newpass456"},
    )
    assert resp.status_code == 400


async def test_preferences_roundtrip(client, user):
    resp = await client.put("/api/v1/auth/preferences", json={"theme": "dark", "lang": "zh"})
    assert resp.status_code == 200
    assert resp.json() == {"theme": "dark", "lang": "zh"}
    assert user.preferences == {"theme": "dark", "lang": "zh"}

    resp = await client.get("/api/v1/auth/preferences")
    assert resp.status_code == 200
    assert resp.json()["theme"] == "dark"


async def test_preferences_too_large_422(client, user):
    """preferences 序列化后超过 16KB → 422（schema 层校验，不落库）。"""
    resp = await client.put("/api/v1/auth/preferences", json={"data": "x" * (17 * 1024)})
    assert resp.status_code == 422


async def test_change_password_clears_must_change_flag(client, user):
    """首登强制改密：改密成功后 must_change_password 置 False。"""
    user.must_change_password = True
    resp = await client.put(
        "/api/v1/auth/password", json={"old_password": "old123", "new_password": "newpass456"}
    )
    assert resp.status_code == 204
    assert user.must_change_password is False


# ---------- 多轮对话问答（mock pipeline） ----------

async def test_chat_ask_mocked(client, monkeypatch):
    async def _fake_chat_ask(db, u, question, session_id=None, kb_ids=None, file_ids=None, **kw):
        return {
            "answer": "测试回答",
            "sources": [],
            "grounded": False,
            "query_log_id": 7,
            "session_id": 3,
        }

    monkeypatch.setattr("app.api.chat.chat_ask", _fake_chat_ask)
    resp = await client.post("/api/v1/chat/ask", json={"question": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "测试回答"
    assert data["session_id"] == 3
    assert data["query_log_id"] == 7


async def test_chat_endpoints_require_auth(client):
    app.dependency_overrides.pop(deps.get_current_user, None)
    assert (await client.post("/api/v1/chat/ask", json={"question": "hi"})).status_code == 401
    assert (await client.post("/api/v1/chat/ask/stream", json={"question": "hi"})).status_code == 401
    assert (await client.get("/api/v1/chat/sessions")).status_code == 401


async def test_workflows_require_auth(client):
    app.dependency_overrides.pop(deps.get_current_user, None)
    assert (await client.get("/api/v1/workflows")).status_code == 401
