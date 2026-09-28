"""P3 沙箱重置：POST /admin/system/reset-sandbox 的权限/环境门禁与清空流程。"""
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.config import settings
from app.database import get_db
from app.main import app
from app.models.audit_log import AuditLog


class _FakeResult:
    pass


class _FakeSession:
    """假会话：记录 execute 的语句文本与 add 的对象，不触碰真实数据库。"""

    def __init__(self):
        self.statements = []
        self.added = []

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        return _FakeResult()

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


async def test_reset_sandbox_non_admin_403(client):
    db = _FakeSession()
    _override(db, role="user")
    resp = await client.post("/api/v1/admin/system/reset-sandbox")
    assert resp.status_code == 403
    assert not any("TRUNCATE" in s for s in db.statements)


async def test_reset_sandbox_prod_env_403(client, monkeypatch):
    db = _FakeSession()
    _override(db)
    monkeypatch.setattr(settings, "ENV", "prod")

    async def _should_not_run():
        raise AssertionError("prod 环境不应触发备份")

    monkeypatch.setattr("app.api.admin_system.run_backup", _should_not_run)

    resp = await client.post("/api/v1/admin/system/reset-sandbox")
    assert resp.status_code == 403
    assert "沙箱" in resp.json()["detail"]
    assert not any("TRUNCATE" in s for s in db.statements)


async def test_reset_sandbox_dev_ok(client, monkeypatch):
    db = _FakeSession()
    _override(db)
    monkeypatch.setattr(settings, "ENV", "dev")

    backup_calls = []

    async def _fake_backup():
        backup_calls.append(1)
        return "20260928_053000"

    monkeypatch.setattr("app.api.admin_system.run_backup", _fake_backup)

    resp = await client.post("/api/v1/admin/system/reset-sandbox")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "backup": "20260928_053000"}
    assert backup_calls == [1]
    # TRUNCATE 清空业务表（RESTART IDENTITY CASCADE），且保留 users/system_settings
    truncate = [s for s in db.statements if "TRUNCATE" in s]
    assert len(truncate) == 1
    assert "customers" in truncate[0] and "RESTART IDENTITY CASCADE" in truncate[0]
    assert "users" not in truncate[0] and "system_settings" not in truncate[0]
    # 写了一条审计日志
    assert any(isinstance(a, AuditLog) and a.action == "reset" for a in db.added)


async def test_reset_sandbox_backup_failure_aborts(client, monkeypatch):
    db = _FakeSession()
    _override(db)
    monkeypatch.setattr(settings, "ENV", "sandbox")

    async def _failing_backup():
        raise RuntimeError("pg_dump 不可用")

    monkeypatch.setattr("app.api.admin_system.run_backup", _failing_backup)

    resp = await client.post("/api/v1/admin/system/reset-sandbox")
    assert resp.status_code == 500
    assert "备份失败" in resp.json()["detail"]
    # 备份失败不执行清空
    assert not any("TRUNCATE" in s for s in db.statements)
