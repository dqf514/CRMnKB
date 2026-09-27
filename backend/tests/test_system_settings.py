"""系统设置：解析文件格式开关。

覆盖：is_supported 随启用集合变化、admin API 读取/保存、非法扩展名被过滤。
"""
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

import app.services.ingestion as ingestion
from app.api import deps
from app.database import get_db
from app.main import app
from app.models.system_setting import SystemSetting


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
        self._get_queue = []
        self.added = []

    def queue_get(self, v):
        self._get_queue.append(v)

    async def get(self, model, ident):
        return self._get_queue.pop(0) if self._get_queue else None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def flush(self):
        pass


@pytest.fixture(autouse=True)
def _reset_cache():
    ingestion._enabled_parse_exts = None
    yield
    ingestion._enabled_parse_exts = None


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override(db):
    async def _fake_user():
        return SimpleNamespace(id=1, tenant_id=1, username="admin", name="管理员", role="admin", status=1)

    async def _fake_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db


def test_set_enabled_affects_is_supported():
    db = _FakeSession()
    assert ingestion.is_supported("a.pdf")
    assert ingestion.is_supported("a.mp3")

    enabled = asyncio.run(ingestion.set_enabled_parse_exts(db, [".pdf", ".txt"]))
    assert enabled == {".pdf", ".txt"}
    assert ingestion.is_supported("a.pdf")
    assert not ingestion.is_supported("a.docx")
    assert not ingestion.is_supported("a.mp3")
    # 配置已落 system_settings 表
    assert any(isinstance(a, SystemSetting) and a.key == ingestion.PARSE_FORMATS_KEY for a in db.added)


def test_set_enabled_ignores_unknown_exts():
    db = _FakeSession()
    enabled = asyncio.run(ingestion.set_enabled_parse_exts(db, [".pdf", ".evil", "noext"]))
    assert enabled == {".pdf"}


async def test_get_formats(client):
    db = _FakeSession()
    _override(db)
    resp = await client.get("/api/v1/admin/settings/formats")
    assert resp.status_code == 200
    items = {i["ext"]: i for i in resp.json()["items"]}
    assert ".pdf" in items
    assert items[".pdf"]["category_label"] == "文本与办公文档"
    assert ".mp4" in items
    assert all(i["enabled"] for i in resp.json()["items"])  # 默认全开


async def test_put_formats(client):
    db = _FakeSession()
    _override(db)
    resp = await client.put("/api/v1/admin/settings/formats", json={"enabled": [".pdf", ".md"]})
    assert resp.status_code == 200
    assert resp.json()["enabled"] == [".md", ".pdf"]  # 排序后返回
    # 进程内缓存已刷新
    assert ingestion.is_supported("a.pdf")
    assert not ingestion.is_supported("a.docx")


async def test_get_upload_formats_public_to_auth_user(client):
    """上传端格式接口对普通登录用户开放（非仅 admin），返回启用扩展名。"""
    db = _FakeSession()
    _override(db)
    # 先设一个已知子集，再验证接口透出
    await ingestion.set_enabled_parse_exts(db, [".pdf", ".docx"])
    resp = await client.get("/api/v1/library/upload-formats")
    assert resp.status_code == 200
    assert resp.json()["enabled"] == [".docx", ".pdf"]
