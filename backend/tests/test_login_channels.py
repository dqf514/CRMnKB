"""登录接入：短信验证码全流程 + 登录与接入配置。

覆盖：配置解析/脱敏/加密保持、验证码签发限流、验证码校验（过期/超次/一次性）、
管理端 login-integrations 读写、短信登录未启用时端点 403。
"""
import hashlib
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


# ========== 欣欣云短信通道 ==========


class _FakeSmsResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSmsHttpClient:
    """捕获 post 参数的假 httpx.AsyncClient（类属性 payload 控制返回）。"""

    captured: dict = {}
    payload: dict = {"code": 0, "msg": "success", "msg_id": "17"}

    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, data=None, headers=None, content=None):
        _FakeSmsHttpClient.captured = {"url": url, "data": data, "headers": headers}
        return _FakeSmsResp(type(self).payload)


def _xinxinyun_cfg(password_plain="my-api-password"):
    return {
        "sp_id": "352107",
        "password": lc.encrypt_sms_password(password_plain, ""),
        "url": "https://sms.shxinxinyun.com/api/send-sms-batch",
        "sign": "【测试签名】",
        "content_template": "验证码{code}，10 分钟内有效。",
    }


def test_xinxinyun_defaults_and_mask():
    cfg = lc.get_login_integrations_from_value(None)
    x = cfg["sms"]["xinxinyun"]
    assert x["url"] == "https://sms.shxinxinyun.com/api/send-sms-batch"
    assert x["sp_id"] == ""
    # 脱敏：不下发密文/明文，只给 has_password + 尾号
    cfg["sms"]["xinxinyun"] = _xinxinyun_cfg()
    masked = lc.mask_login_integrations(cfg)
    xm = masked["sms"]["xinxinyun"]
    assert "password" not in xm
    assert xm["has_password"] is True
    assert xm["password_tail"] == "word"
    assert "my-api-password" not in json.dumps(masked)


async def test_send_sms_xinxinyun_success(monkeypatch):
    monkeypatch.setattr(lc.httpx, "AsyncClient", _FakeSmsHttpClient)
    _FakeSmsHttpClient.payload = {"code": 0, "msg": "success", "msg_id": "17"}
    result = await lc._send_sms(
        {"provider": "xinxinyun", "xinxinyun": _xinxinyun_cfg()}, "13800138000", "123456"
    )
    assert result is None  # 非 log 通道不回 dev_code
    req = _FakeSmsHttpClient.captured
    assert req["url"] == "https://sms.shxinxinyun.com/api/send-sms-batch"
    assert req["data"]["sp_id"] == "352107"
    assert req["data"]["mobiles"] == "13800138000"
    assert req["data"]["content"] == "【测试签名】验证码123456，10 分钟内有效。"
    # 密码发送时 MD5（文档要求）
    assert req["data"]["password"] == hashlib.md5(b"my-api-password").hexdigest()
    # 供应商文档要求带 User-Agent
    assert "User-Agent" in req["headers"]


async def test_send_sms_xinxinyun_error_code_raises(monkeypatch):
    monkeypatch.setattr(lc.httpx, "AsyncClient", _FakeSmsHttpClient)
    _FakeSmsHttpClient.payload = {"code": 10011, "msg": "余额不足，请尽快充值"}
    with pytest.raises(RuntimeError, match="余额不足"):
        await lc._send_sms(
            {"provider": "xinxinyun", "xinxinyun": _xinxinyun_cfg()}, "13800138000", "123456"
        )


async def test_send_sms_xinxinyun_missing_config_raises():
    bad = _xinxinyun_cfg()
    bad["sp_id"] = ""
    with pytest.raises(RuntimeError, match="sp_id"):
        await lc._send_sms({"provider": "xinxinyun", "xinxinyun": bad}, "13800138000", "123456")


async def test_put_login_integrations_sms_password_semantics(client):
    """短信接口密码：null 保持原值；响应脱敏且不下发明文。"""
    db = _FakeSession()
    _override(db)
    old_cipher = lc.encrypt_sms_password("old-sms-pwd", "")
    db.queue_get(
        SystemSetting(
            key=lc.LOGIN_INTEGRATIONS_KEY,
            value=json.dumps({"sms": {"xinxinyun": {"password": old_cipher}}}),
        )
    )
    resp = await client.put(
        "/api/v1/admin/settings/login-integrations",
        json={
            "sms": {
                "enabled": True,
                "provider": "xinxinyun",
                "http": {"url": "", "headers": {}, "body_template": "{}"},
                "xinxinyun": {
                    "sp_id": "352107",
                    "password": None,  # 不修改
                    "url": "https://sms.shxinxinyun.com/api/send-sms-batch",
                    "sign": "【测试】",
                    "content_template": "验证码{code}",
                },
            },
            "wechat": {"enabled": False, "app_id": "", "app_secret": None, "redirect_uri": ""},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    x = data["sms"]["xinxinyun"]
    assert x["has_password"] is True
    assert x["password_tail"] == "-pwd"
    assert "old-sms-pwd" not in json.dumps(data)
    # 保存落库的值仍是密文且对应原密码
    saved = json.loads(db.added[0].value)
    assert decrypt_secret(saved["sms"]["xinxinyun"]["password"]) == "old-sms-pwd"
