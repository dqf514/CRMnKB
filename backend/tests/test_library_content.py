from types import SimpleNamespace
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.api.library import guess_content_type
from app.database import get_db
from app.main import app


# ---------------------------------------------------------------------------
# Content-Type 推断（纯函数）
# ---------------------------------------------------------------------------

def test_guess_content_type():
    assert guess_content_type("说明.md") == "text/markdown; charset=utf-8"
    assert guess_content_type("a.markdown") == "text/markdown; charset=utf-8"
    assert guess_content_type("notes.TXT") == "text/plain; charset=utf-8"  # 大小写不敏感
    assert guess_content_type("合同.docx") == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert guess_content_type("表.xlsx") == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert guess_content_type("演示.pptx") == (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert guess_content_type("录音.mp3") == "audio/mpeg"
    assert guess_content_type("录音.wav") == "audio/wav"
    assert guess_content_type("录音.m4a") == "audio/mp4"
    assert guess_content_type("录音.ogg") == "audio/ogg"
    assert guess_content_type("视频.mp4") == "video/mp4"
    assert guess_content_type("视频.webm") == "video/webm"
    assert guess_content_type("图.png") == "image/png"
    assert guess_content_type("图.jpg") == "image/jpeg"
    assert guess_content_type("图.jpeg") == "image/jpeg"
    assert guess_content_type("图.gif") == "image/gif"
    assert guess_content_type("图.webp") == "image/webp"
    assert guess_content_type("图.svg") == "image/svg+xml"
    assert guess_content_type("报告.pdf") == "application/pdf"
    assert guess_content_type("压缩包.unknownext") == "application/octet-stream"


# ---------------------------------------------------------------------------
# content 接口（dependency_overrides + tmp 文件）
# ---------------------------------------------------------------------------

class _FakeSession:
    def __init__(self, file=None):
        self._file = file

    async def get(self, model, ident):
        return self._file


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override(db, tenant_id=1):
    async def _fake_user():
        # role=admin 绕过权限校验（本测试聚焦文件内容服务，非 ACL）
        return SimpleNamespace(id=1, tenant_id=tenant_id, username="u", name="用户", role="admin")

    async def _fake_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[deps.get_current_user_with_query_token] = _fake_user
    app.dependency_overrides[get_db] = _fake_db


def _make_file(tmp_path, name="测试 文档.md", content="# 你好\n这是内容。", tenant_id=1):
    path = tmp_path / "stored.bin"
    path.write_bytes(content.encode("utf-8"))
    return SimpleNamespace(
        id=1, tenant_id=tenant_id, file_name=name, file_path=str(path), deleted_at=None
    )


async def test_content_200_full(client, tmp_path):
    file = _make_file(tmp_path)
    _override(_FakeSession(file))
    resp = await client.get("/api/v1/library/files/1/content")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
    assert resp.headers["accept-ranges"] == "bytes"
    # RFC 5987 UTF-8 编码的中文文件名
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("inline;")
    assert f"filename*=UTF-8''{quote('测试 文档.md')}" in disposition
    assert resp.content == "# 你好\n这是内容。".encode("utf-8")


async def test_content_206_range(client, tmp_path):
    file = _make_file(tmp_path, name="video.mp4", content="0123456789")
    _override(_FakeSession(file))
    resp = await client.get(
        "/api/v1/library/files/1/content", headers={"Range": "bytes=2-5"}
    )
    assert resp.status_code == 206
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.headers["content-range"] == "bytes 2-5/10"
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.content == b"2345"


async def test_content_206_suffix_range(client, tmp_path):
    file = _make_file(tmp_path, name="a.txt", content="0123456789")
    _override(_FakeSession(file))
    resp = await client.get(
        "/api/v1/library/files/1/content", headers={"Range": "bytes=-3"}
    )
    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 7-9/10"
    assert resp.content == b"789"


async def test_content_416_invalid_range(client, tmp_path):
    file = _make_file(tmp_path, name="a.txt", content="0123456789")
    _override(_FakeSession(file))
    resp = await client.get(
        "/api/v1/library/files/1/content", headers={"Range": "bytes=100-"}
    )
    assert resp.status_code == 416  # FileResponse 内置 Range 处理
    # starlette 1.x 起 416 响应的 Content-Range 按 RFC 9110 带 bytes 单位前缀
    assert resp.headers["content-range"] == "bytes */10"


async def test_content_404_record_not_found(client):
    _override(_FakeSession(None))
    resp = await client.get("/api/v1/library/files/999/content")
    assert resp.status_code == 404


async def test_content_404_disk_missing(client, tmp_path):
    file = _make_file(tmp_path)
    file.file_path = str(tmp_path / "不存在.bin")
    _override(_FakeSession(file))
    resp = await client.get("/api/v1/library/files/1/content")
    assert resp.status_code == 404


async def test_content_404_cross_tenant(client, tmp_path):
    file = _make_file(tmp_path, tenant_id=2)  # 别的租户的文件
    _override(_FakeSession(file))
    resp = await client.get("/api/v1/library/files/1/content")
    assert resp.status_code == 404


async def test_content_requires_auth(client):
    resp = await client.get("/api/v1/library/files/1/content")
    assert resp.status_code == 401
