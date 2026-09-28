"""P2：AI 邮件草稿纯函数 + email-draft 端点 + Email Guide 管理端点。"""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.database import get_db
from app.main import app
from app.models.system_setting import SystemSetting
from app.services.email_draft import build_email_draft_prompt, parse_email_draft


def _customer():
    return SimpleNamespace(
        id=7, tenant_id=1, deleted_at=None,
        name="张三", company="某某科技", position="采购总监",
        industries=["制造"], status="intention", ai_brief="## 当前阶段判断\n意向阶段",
    )


def _followups():
    return [
        SimpleNamespace(ai_summary="讨论了报价", content="原始", created_at=datetime(2026, 9, 1)),
    ]


# ---------- build_email_draft_prompt / parse_email_draft 纯函数 ----------


def test_build_email_draft_prompt_inline_data():
    messages = build_email_draft_prompt(
        _customer(), _followups(), brief="阶段简报内容", guide="落款用公司英文名",
        intent="约下周产品演示", language="zh",
    )
    assert [m["role"] for m in messages] == ["system", "user"]
    system = messages[0]["content"]
    assert "落款用公司英文名" in system  # guide 注入
    assert "简体中文" in system  # 语言指令
    assert '"subject"' in system and '"body"' in system  # 严格 JSON 输出约定
    user = messages[1]["content"]
    assert "张三" in user and "某某科技" in user
    assert "阶段简报内容" in user
    assert "讨论了报价" in user
    assert "约下周产品演示" in user


def test_build_email_draft_prompt_without_guide_and_en():
    messages = build_email_draft_prompt(
        _customer(), [], brief=None, guide="", intent="follow up", language="en",
    )
    system = messages[0]["content"]
    assert "Email Guide" not in system  # 空 guide 忽略该段
    assert "English" in system
    user = messages[1]["content"]
    assert "（无）" in user  # 简报与跟进为空时的占位


def test_parse_email_draft_plain_json():
    text = json.dumps({"subject": "合作跟进", "body": "您好，\n\n附件为方案。"}, ensure_ascii=False)
    assert parse_email_draft(text) == {"subject": "合作跟进", "body": "您好，\n\n附件为方案。"}


def test_parse_email_draft_code_fence():
    text = '```json\n{"subject": "报价确认", "body": "正文"}\n```'
    assert parse_email_draft(text) == {"subject": "报价确认", "body": "正文"}


def test_parse_email_draft_surrounding_text():
    text = '好的，以下是草稿：\n{"subject": "S", "body": "B"}\n希望对您有帮助'
    assert parse_email_draft(text) == {"subject": "S", "body": "B"}


def test_parse_email_draft_garbage_returns_empty():
    assert parse_email_draft("") == {}
    assert parse_email_draft(None) == {}
    assert parse_email_draft("完全不是 JSON") == {}
    assert parse_email_draft('{"foo": 1}') == {}  # 缺 subject/body


# ---------- POST /customers/{id}/email-draft ----------


class _FakeScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """假会话：get 按队列返回（先 Customer 后 SystemSetting），execute 返回跟进列表。"""

    def __init__(self, gets=None, followups=()):
        self._gets = list(gets or [])
        self._followups = list(followups)
        self.added = []

    async def get(self, model, ident):
        return self._gets.pop(0) if self._gets else None

    async def execute(self, stmt):
        return _FakeScalarsResult(self._followups)

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


class _FakeLLM:
    def __init__(self, reply):
        self._reply = reply
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        return self._reply


async def test_email_draft_endpoint(client, monkeypatch):
    guide_row = SimpleNamespace(value="语气正式")
    db = _FakeSession(gets=[_customer(), guide_row], followups=_followups())
    _override(db)

    llm = _FakeLLM(json.dumps({"subject": "产品演示邀请", "body": "张总您好：……"}, ensure_ascii=False))

    async def _fake_resolve(caller=None, tenant_id=None):
        return llm

    monkeypatch.setattr("app.api.customers.resolve_chat_llm", _fake_resolve)

    resp = await client.post(
        "/api/v1/customers/7/email-draft",
        json={"intent": "约下周产品演示", "language": "zh"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"subject": "产品演示邀请", "body": "张总您好：……"}
    # 意图进入了发给 LLM 的 user prompt
    assert "约下周产品演示" in llm.messages[1]["content"]
    assert "语气正式" in llm.messages[0]["content"]


async def test_email_draft_llm_garbage_502(client, monkeypatch):
    db = _FakeSession(gets=[_customer(), None], followups=[])
    _override(db)

    async def _fake_resolve(caller=None, tenant_id=None):
        return _FakeLLM("无法完成")

    monkeypatch.setattr("app.api.customers.resolve_chat_llm", _fake_resolve)

    resp = await client.post("/api/v1/customers/7/email-draft", json={"intent": "问候"})
    assert resp.status_code == 502


async def test_email_draft_missing_intent_422(client):
    db = _FakeSession()
    _override(db)
    resp = await client.post("/api/v1/customers/7/email-draft", json={})
    assert resp.status_code == 422


# ---------- GET/PUT /admin/settings/email-guide ----------


async def test_email_guide_get_empty_and_existing(client):
    # 无配置 → 空串
    db = _FakeSession(gets=[None])
    _override(db)
    resp = await client.get("/api/v1/admin/settings/email-guide")
    assert resp.status_code == 200
    assert resp.json() == {"guide": ""}

    # 已有配置 → 原样返回
    db = _FakeSession(gets=[SimpleNamespace(value="统一使用中文商务信函格式")])
    _override(db)
    resp = await client.get("/api/v1/admin/settings/email-guide")
    assert resp.status_code == 200
    assert resp.json() == {"guide": "统一使用中文商务信函格式"}


async def test_email_guide_put_creates_setting(client):
    db = _FakeSession(gets=[None])  # 无既有行 → 新建
    _override(db)
    resp = await client.put("/api/v1/admin/settings/email-guide", json={"guide": "新规范"})
    assert resp.status_code == 200
    assert resp.json() == {"guide": "新规范"}
    created = [a for a in db.added if isinstance(a, SystemSetting) and a.key == "email_guide"]
    assert created and created[0].value == "新规范"


async def test_email_guide_requires_admin(client):
    db = _FakeSession(gets=[None])
    _override(db, role="user")
    resp = await client.get("/api/v1/admin/settings/email-guide")
    assert resp.status_code == 403
