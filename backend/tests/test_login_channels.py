"""登录接入：短信验证码全流程 + 登录与接入配置。

覆盖：配置解析/脱敏/加密保持、验证码签发限流、验证码校验（过期/超次/一次性）、
管理端 login-integrations 读写、短信登录未启用时端点 403。
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.core.crypto import decrypt_secret
from app.database import get_db
from app.main import app
from app.models.login_code import LoginCode
from app.models.system_setting import SystemSetting
from app.services import login_channels as lc


class _FakeSession:
    """支持 get（SystemSetting）/ scalar 队列 / add 收集的最小会话桩。"""

    def __init__(self):
        self._get_queue = []
        self._scalar_queue = []
        self.added = []

    def queue_get(self, v):
        self._get_queue.append(v)

    def queue_scalar(self, v):
        self._scalar_queue.append(v)

    async def get(self, model, ident):
        return self._get_queue.pop(0) if self._get_queue else None

    async def scalar(self, stmt):
        return self._scalar_queue.pop(0) if self._scalar_queue else None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def flush(self):
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


def _enabled_cfg_value():
    return json.dumps({"sms": {"enabled": True, "provider": "log"}})


# ========== 配置解析 / 脱敏 / 加密 ==========


def test_config_defaults_when_empty():
    cfg = lc.get_login_integrations_from_value(None)
    assert cfg["sms"]["enabled"] is False
    assert cfg["sms"]["provider"] == "log"
    assert cfg["wechat"]["enabled"] is False


def test_config_merges_partial_and_tolerates_dirty():
    cfg = lc.get_login_integrations_from_value('{"sms": {"enabled": true}}')
    assert cfg["sms"]["enabled"] is True
    assert cfg["sms"]["provider"] == "log"  # 未给的部分回退默认
    assert lc.get_login_integrations_from_value("not-json")["sms"]["enabled"] is False
    assert lc.get_login_integrations_from_value('["x"]')["wechat"]["app_id"] == ""


def test_mask_hides_secret_and_shows_tail():
    cfg = lc.get_login_integrations_from_value(None)
    cfg["wechat"]["app_secret"] = lc.encrypt_wechat_secret("my-secret-123456", "")
    masked = lc.mask_login_integrations(cfg)
    assert "app_secret" not in masked["wechat"]
    assert masked["wechat"]["has_app_secret"] is True
    assert masked["wechat"]["app_secret_tail"] == "3456"
    assert "my-secret" not in json.dumps(masked)


def test_wechat_secret_update_semantics():
    old = lc.encrypt_wechat_secret("old-secret", "")
    assert decrypt_secret(old) == "old-secret"
    # None=保持原值；""=清除；新明文=覆盖
    assert lc.encrypt_wechat_secret(None, old) == old
    assert lc.encrypt_wechat_secret("", old) == ""
    new = lc.encrypt_wechat_secret("new-secret", old)
    assert decrypt_secret(new) == "new-secret"


# ========== 验证码签发 / 校验 ==========


async def test_issue_login_code_rate_limit_60s():
    db = _FakeSession()
    # 同号 60 秒内已有发送记录 → 拒绝
    db.queue_scalar(datetime.now(timezone.utc).replace(tzinfo=None))
    with pytest.raises(ValueError, match="60 秒"):
        await lc.issue_login_code(db, "13800138000", "1.1.1.1")


async def test_issue_login_code_daily_caps():
    db = _FakeSession()
    db.queue_scalar(None)  # 同号无近期记录
    db.queue_scalar(10)  # 同号今日已 10 条 → 拒绝
    with pytest.raises(ValueError, match="上限"):
        await lc.issue_login_code(db, "13800138000", "1.1.1.1")


async def test_issue_login_code_ok_and_hash_stored():
    db = _FakeSession()
    db.queue_get(None)  # login_integrations 默认（log 通道）
    db.queue_scalar(None)  # 同号无近期记录
    db.queue_scalar(0)  # 同号今日 0 条
    db.queue_scalar(0)  # 同 IP 今日 0 条
    dev_code = await lc.issue_login_code(db, "13800138000", "1.1.1.1")
    row = next(a for a in db.added if isinstance(a, LoginCode))
    assert row.phone == "13800138000"
    assert row.used in (None, False)  # default=False 落库时才生效
    assert row.expires_at > datetime.now(timezone.utc).replace(tzinfo=None)
    # 只存哈希，不明文；dev 环境 log 通道返回 dev_code
    assert dev_code is not None and row.code_hash != dev_code
    assert row.code_hash == lc._code_hash("13800138000", dev_code)


async def test_verify_login_code_one_time_and_attempts():
    db = _FakeSession()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    good = SimpleNamespace(
        code_hash=lc._code_hash("13800138000", "123456"),
        expires_at=now + timedelta(minutes=5),
        attempts=0,
        used=False,
    )
    db.queue_scalar(good)
    assert await lc.verify_login_code(db, "13800138000", "123456") is True
    assert good.used is True

    db2 = _FakeSession()
    wrong = SimpleNamespace(
        code_hash=lc._code_hash("13800138000", "123456"),
        expires_at=now + timedelta(minutes=5),
        attempts=0,
        used=False,
    )
    db2.queue_scalar(wrong)
    assert await lc.verify_login_code(db2, "13800138000", "000000") is False
    assert wrong.attempts == 1 and wrong.used is False

    db3 = _FakeSession()
    expired = SimpleNamespace(
        code_hash=lc._code_hash("13800138000", "123456"),
        expires_at=now - timedelta(minutes=1),
        attempts=0,
        used=False,
    )
    db3.queue_scalar(expired)
    assert await lc.verify_login_code(db3, "13800138000", "123456") is False


# ========== 管理端配置接口 ==========


async def test_get_login_integrations_default(client):
    db = _FakeSession()
    _override(db)
    resp = await client.get("/api/v1/admin/settings/login-integrations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sms"]["enabled"] is False
    assert data["wechat"]["has_app_secret"] is False


async def test_put_login_integrations_keeps_secret_when_null(client):
    db = _FakeSession()
    _override(db)
    old_cipher = lc.encrypt_wechat_secret("keep-me-secret", "")
    db.queue_get(
        SystemSetting(
            key=lc.LOGIN_INTEGRATIONS_KEY,
            value=json.dumps({"sms": {"enabled": False}, "wechat": {"app_secret": old_cipher}}),
        )
    )
    resp = await client.put(
        "/api/v1/admin/settings/login-integrations",
        json={
            "sms": {"enabled": True, "provider": "log",
                    "http": {"url": "", "headers": {}, "body_template": "{}"}},
            "wechat": {"enabled": True, "app_id": "wx123", "app_secret": None, "redirect_uri": ""},
        },
    )
    assert resp.status_code == 200
    # 校验响应：sms 已开、secret 脱敏且保持原值（has_app_secret=True）
    data = resp.json()
    assert data["sms"]["enabled"] is True
    assert data["wechat"]["has_app_secret"] is True
    assert data["wechat"]["app_secret_tail"] == "cret"
    assert "keep-me-secret" not in json.dumps(data)


async def test_sms_code_403_when_disabled(client):
    db = _FakeSession()
    _override(db, role="user")
    resp = await client.post("/api/v1/auth/sms-code", json={"phone": "13800138000"})
    assert resp.status_code == 403


async def test_sms_code_ok_returns_dev_code(client):
    db = _FakeSession()
    _override(db, role="user")
    db.queue_get(SystemSetting(key=lc.LOGIN_INTEGRATIONS_KEY, value=_enabled_cfg_value()))
    db.queue_scalar(None)  # 同号无近期记录
    db.queue_scalar(0)  # 同号今日 0
    db.queue_scalar(0)  # 同 IP 今日 0
    resp = await client.post("/api/v1/auth/sms-code", json={"phone": "13800138000"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data.get("dev_code") and len(data["dev_code"]) == 6  # dev 环境 log 通道


async def test_phone_login_403_when_disabled(client):
    db = _FakeSession()
    _override(db, role="user")
    resp = await client.post(
        "/api/v1/auth/login/phone", json={"phone": "13800138000", "code": "123456"}
    )
    assert resp.status_code == 403
