"""个人头像上传测试：MIME/魔数/大小校验、落盘、回填 avatar_url、旧头像清理。

HTTP 走 httpx ASGITransport，头像目录 monkeypatch 到临时目录，不触库不触网。
"""
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.config import settings
from app.database import get_db
from app.main import app


def _patch_avatars_path(monkeypatch, path):
    """avatars_path 是只读属性，改类级 property 指向临时目录。"""
    monkeypatch.setattr(type(settings), "avatars_path", property(lambda self: path))


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override(db, user):
    async def _fake_user():
        return user

    async def _fake_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db


def _user():
    return SimpleNamespace(id=1, tenant_id=1, username="u", name="用户", role="admin", avatar_url=None)


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0123456789abcdef"


async def test_upload_avatar_success(client, tmp_path, monkeypatch):
    db = _FakeSession()
    user = _user()
    _override(db, user)
    _patch_avatars_path(monkeypatch, tmp_path)

    resp = await client.post(
        "/api/v1/auth/avatar",
        files={"file": ("a.png", PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["avatar_url"].startswith("/avatars/")
    assert data["avatar_url"].endswith(".png")
    assert (tmp_path / data["avatar_url"].split("/")[-1]).exists()
    assert user.avatar_url == data["avatar_url"]


async def test_upload_avatar_replaces_old(client, tmp_path, monkeypatch):
    db = _FakeSession()
    user = _user()
    old = tmp_path / "old.png"
    old.write_bytes(PNG_BYTES)
    user.avatar_url = f"/avatars/{old.name}"
    _override(db, user)
    _patch_avatars_path(monkeypatch, tmp_path)

    resp = await client.post(
        "/api/v1/auth/avatar",
        files={"file": ("new.png", PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 200
    assert not old.exists()  # 旧头像文件已清理
    assert user.avatar_url == resp.json()["avatar_url"]


async def test_upload_avatar_rejects_fake_image(client, tmp_path, monkeypatch):
    db = _FakeSession()
    _override(db, _user())
    _patch_avatars_path(monkeypatch, tmp_path)

    resp = await client.post(
        "/api/v1/auth/avatar",
        files={"file": ("a.txt", b"hello world", "image/png")},
    )
    assert resp.status_code == 400


async def test_upload_avatar_rejects_wrong_mime(client, tmp_path, monkeypatch):
    db = _FakeSession()
    _override(db, _user())
    _patch_avatars_path(monkeypatch, tmp_path)

    resp = await client.post(
        "/api/v1/auth/avatar",
        files={"file": ("a.png", PNG_BYTES, "application/octet-stream")},
    )
    assert resp.status_code == 400
