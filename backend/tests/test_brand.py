"""品牌配置测试：logo 上传格式校验（SVG 拒绝，防存储型 XSS）。

不触库不触真实文件系统：DB 用 fake session，brand_path 指向 tmp_path。
"""
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.config import Settings
from app.database import get_db
from app.main import app


class _FakeSession:
    def __init__(self):
        self._store = {}

    async def get(self, model, ident):
        return self._store.get((model, ident))

    def add(self, obj):
        self._store[(type(obj), getattr(obj, "id", None))] = obj

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override_admin():
    async def _fake_user():
        return SimpleNamespace(id=1, tenant_id=1, username="admin", name="管理员", role="admin", status=1)

    async def _fake_db():
        yield _FakeSession()

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db


async def test_svg_logo_rejected(client):
    """SVG 可内嵌脚本，经 /brand 静态服务同源吐出即 XSS：新上传直接拒绝。"""
    _override_admin()
    files = {"file": ("logo.svg", b"<svg><script>alert(1)</script></svg>", "image/svg+xml")}
    resp = await client.post("/api/v1/brand/logo", files=files)
    assert resp.status_code == 400
    assert "png" in resp.json()["detail"]


async def test_png_logo_accepted(client, monkeypatch, tmp_path):
    """位图 logo 正常上传：写入 brand_path 并在公开配置中返回新 URL。"""
    # brand_path 是 Settings 的 property，打桩到临时目录避免污染真实 data/brand
    monkeypatch.setattr(Settings, "brand_path", property(lambda self: tmp_path))
    _override_admin()
    files = {"file": ("logo.png", b"\x89PNG\r\n\x1a\nfake-bytes", "image/png")}
    resp = await client.post("/api/v1/brand/logo", files=files)
    assert resp.status_code == 200
    assert resp.json()["logo_url"] == "/brand/logo.png"
    assert (tmp_path / "logo.png").read_bytes() == b"\x89PNG\r\n\x1a\nfake-bytes"
