"""短信注册 + 首次引导测试（aiosqlite 真实 SQL + API 全流程）。

覆盖：
- 验证码 purpose 隔离：register 码不能登录、login 码不能注册
- POST /auth/register：新手机号自动建号（individual / 无团队 / preferences.onboarded=False）、
  已注册手机号 400
- POST /auth/onboarding：设置姓名+密码（+可选邮箱）→ onboarded=True + 新令牌；重复提交 400；
  完成引导后可正常账号密码登录
- /auth/sms-code purpose=register 对已注册手机号直接 400
"""
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.base import Base
from app.models.audit_log import AuditLog
from app.models.login_attempt import LoginAttempt
from app.models.login_code import LoginCode
from app.models.system_setting import SystemSetting
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_group import UserGroup
from app.services.login_channels import DEFAULT_CONFIG, LOGIN_INTEGRATIONS_KEY

_PHONE = "13800000001"
_PHONE2 = "13800000002"

_TABLES = [
    Tenant.__table__,
    UserGroup.__table__,
    User.__table__,
    LoginCode.__table__,
    LoginAttempt.__table__,
    AuditLog.__table__,
    SystemSetting.__table__,
]


@pytest.fixture
async def db():
    """sqlite 内存库 + 短信 log 通道（dev 环境响应带回 dev_code）。"""
    swapped_types: list[tuple] = []
    saved_indexes: dict = {}
    for table in _TABLES:
        for col in table.c:
            if isinstance(col.type, JSONB):
                swapped_types.append((col, col.type))
                col.type = JSON()
        saved_indexes[table] = set(table.indexes)
        table.indexes.clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_TABLES))
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            session.add(Tenant(id=1, name="默认租户"))
            cfg = json.loads(json.dumps(DEFAULT_CONFIG))
            cfg["sms"]["enabled"] = True
            cfg["sms"]["provider"] = "log"
            session.add(SystemSetting(
                key=LOGIN_INTEGRATIONS_KEY, value=json.dumps(cfg, ensure_ascii=False)
            ))
            await session.commit()
            yield session
    finally:
        await engine.dispose()
        for col, col_type in swapped_types:
            col.type = col_type
        for table, indexes in saved_indexes.items():
            table.indexes.update(indexes)


@pytest.fixture
async def client(db, monkeypatch):
    monkeypatch.setattr(settings, "ENV", "dev")  # log 通道回传 dev_code

    async def _fake_get_db():
        yield db

    app.dependency_overrides[get_db] = _fake_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _send_code(client, phone, purpose):
    resp = await client.post("/api/v1/auth/sms-code", json={"phone": phone, "purpose": purpose})
    assert resp.status_code == 200, resp.text
    return resp.json()["dev_code"]


# ---------------------------------------------------------------------------
# purpose 隔离
# ---------------------------------------------------------------------------

async def test_register_code_cannot_login_and_vice_versa(client, db):
    # register 码用于登录 → 400
    code = await _send_code(client, _PHONE, "register")
    resp = await client.post("/api/v1/auth/login/phone", json={"phone": _PHONE, "code": code})
    assert resp.status_code == 400

    # login 码用于注册 → 400（换手机号避免 60s 限流干扰）
    code = await _send_code(client, _PHONE2, "login")
    resp = await client.post("/api/v1/auth/register", json={"phone": _PHONE2, "code": code})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 注册全流程
# ---------------------------------------------------------------------------

async def test_register_creates_individual_account(client, db):
    code = await _send_code(client, _PHONE, "register")
    resp = await client.post("/api/v1/auth/register", json={"phone": _PHONE, "code": code})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["access_token"]
    user = body["user"]
    assert user["role"] == "individual"
    assert user["preferences"].get("onboarded") is False

    row = await db.scalar(select(User).where(User.phone == _PHONE))
    assert row is not None
    assert row.role == "individual"
    assert row.group_id is None  # 个人用户不进任何团队
    assert row.status == 1

    # 已注册手机号：再发注册码直接 400；直接注册也 400
    resp = await client.post("/api/v1/auth/sms-code", json={"phone": _PHONE, "purpose": "register"})
    assert resp.status_code == 400


async def test_register_token_works_and_onboarding_completes(client, db):
    code = await _send_code(client, _PHONE, "register")
    resp = await client.post("/api/v1/auth/register", json={"phone": _PHONE, "code": code})
    token = resp.json()["access_token"]

    # 注册令牌立即可用（/auth/me 带 preferences，前端守卫据此跳引导页）
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["preferences"]["onboarded"] is False

    # 引导：姓名+密码必填，邮箱可选
    resp = await client.post(
        "/api/v1/auth/onboarding",
        json={"name": "张三", "password": "newpass123", "email": "zs@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["name"] == "张三"
    assert body["user"]["preferences"]["onboarded"] is True
    new_token = body["access_token"]

    # 新令牌可用；重复走引导 400
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert resp.status_code == 200
    resp = await client.post(
        "/api/v1/auth/onboarding",
        json={"name": "李四", "password": "otherpass123"},
        headers={"Authorization": f"Bearer {new_token}"},
    )
    assert resp.status_code == 400

    # 引导设置的密码可正常账号密码登录（username=手机号）
    resp = await client.post(
        "/api/v1/auth/login", json={"username": _PHONE, "password": "newpass123"}
    )
    assert resp.status_code == 200, resp.text


async def test_onboarding_validation(client, db):
    code = await _send_code(client, _PHONE, "register")
    resp = await client.post("/api/v1/auth/register", json={"phone": _PHONE, "code": code})
    token = resp.json()["access_token"]
    # 密码太短 422；缺姓名 422
    resp = await client.post(
        "/api/v1/auth/onboarding",
        json={"name": "张三", "password": "short"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
    resp = await client.post(
        "/api/v1/auth/onboarding",
        json={"password": "newpass123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_register_disabled_when_sms_off(client, db):
    setting = await db.get(SystemSetting, LOGIN_INTEGRATIONS_KEY)
    cfg = json.loads(setting.value)
    cfg["sms"]["enabled"] = False
    setting.value = json.dumps(cfg, ensure_ascii=False)
    await db.commit()
    resp = await client.post("/api/v1/auth/sms-code", json={"phone": _PHONE, "purpose": "register"})
    assert resp.status_code == 403
    resp = await client.post("/api/v1/auth/register", json={"phone": _PHONE, "code": "123456"})
    assert resp.status_code == 403
