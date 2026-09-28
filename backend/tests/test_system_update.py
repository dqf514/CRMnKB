"""系统更新（/admin/system/update-info + /admin/system/update）：权限/配置门禁与脚本执行。"""
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.config import settings
from app.database import get_db
from app.main import app
from app.models.audit_log import AuditLog


class _FakeSession:
    """假会话：收集 add 的对象，不触碰真实数据库。"""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override(db, role="admin"):
    async def _fake_user():
        return SimpleNamespace(id=1, tenant_id=1, username="admin", name="管理员", role=role, status=1)

    async def _fake_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db


async def test_update_info_disabled_when_no_script(client, monkeypatch):
    _override(_FakeSession())
    monkeypatch.setattr(settings, "UPDATE_SCRIPT", "")
    resp = await client.get("/api/v1/admin/system/update-info")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False, "commit": None}


async def test_update_info_enabled_reports_commit(client, monkeypatch, tmp_path):
    _override(_FakeSession())
    monkeypatch.setattr(settings, "UPDATE_SCRIPT", str(tmp_path / "update.sh"))

    async def _fake_head():
        return "abc1234"

    monkeypatch.setattr("app.api.admin_system._git_short_head", _fake_head)
    resp = await client.get("/api/v1/admin/system/update-info")
    assert resp.json() == {"enabled": True, "commit": "abc1234"}


async def test_system_update_non_admin_403(client):
    _override(_FakeSession(), role="user")
    resp = await client.post("/api/v1/admin/system/update")
    assert resp.status_code == 403


async def test_system_update_not_configured_400(client, monkeypatch):
    _override(_FakeSession())
    monkeypatch.setattr(settings, "UPDATE_SCRIPT", "")
    resp = await client.post("/api/v1/admin/system/update")
    assert resp.status_code == 400
    assert "UPDATE_SCRIPT" in resp.json()["detail"]


async def test_system_update_script_missing_500(client, monkeypatch, tmp_path):
    _override(_FakeSession())
    monkeypatch.setattr(settings, "UPDATE_SCRIPT", str(tmp_path / "nope.sh"))
    resp = await client.post("/api/v1/admin/system/update")
    assert resp.status_code == 500
    assert "不存在" in resp.json()["detail"]


async def test_system_update_success(client, monkeypatch, tmp_path):
    db = _FakeSession()
    _override(db)
    script = tmp_path / "update.sh"
    script.write_text("echo ok")
    monkeypatch.setattr(settings, "UPDATE_SCRIPT", str(script))

    async def _fake_run(path):
        assert path == str(script)  # 脚本路径来自配置，非请求传参
        return 0, "== git pull ==\n更新完成"

    monkeypatch.setattr("app.api.admin_system._run_update_script", _fake_run)
    resp = await client.post("/api/v1/admin/system/update")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["exit_code"] == 0
    assert "更新完成" in data["output"]
    # 审计在脚本执行前落库（脚本结尾会重启进程）
    assert any(isinstance(a, AuditLog) and a.action == "update" for a in db.added)


async def test_system_update_script_failure_reported(client, monkeypatch, tmp_path):
    db = _FakeSession()
    _override(db)
    script = tmp_path / "update.sh"
    script.write_text("exit 1")
    monkeypatch.setattr(settings, "UPDATE_SCRIPT", str(script))

    async def _fake_run(path):
        return 1, "git pull 冲突"

    monkeypatch.setattr("app.api.admin_system._run_update_script", _fake_run)
    resp = await client.post("/api/v1/admin/system/update")
    assert resp.status_code == 200  # 脚本失败不抛 500，由前端展示输出
    assert resp.json()["ok"] is False
    assert "冲突" in resp.json()["output"]
