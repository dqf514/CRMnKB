"""P1 Pipeline：AI 阶段简报 Prompt 纯函数 + brief/refresh 端点。"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.database import get_db
from app.main import app
from app.services.pipeline_brief import build_brief_prompt


def _customer():
    return SimpleNamespace(
        name="张三",
        company="某某科技",
        industries=["制造", "软件"],
        status="intention",
        ddq_status="pending",
    )


# ---------- build_brief_prompt 纯函数 ----------


def test_build_brief_prompt_sections_and_inline_data():
    followups = [
        SimpleNamespace(
            ai_summary="讨论了报价与账期", content="原始内容", next_step="下周发方案",
            type="meeting", created_at=datetime(2026, 9, 1),
        ),
        SimpleNamespace(
            ai_summary=None, content="x" * 150, next_step=None,
            type="call", created_at=datetime(2026, 8, 20),
        ),
    ]
    opportunities = [
        SimpleNamespace(name="年度框架协议", amount=1000000, stage="proposal", probability=60)
    ]
    tasks = [SimpleNamespace(title="发送正式方案", priority="high", due_date=datetime(2026, 9, 30))]

    messages = build_brief_prompt(_customer(), followups, opportunities, tasks)
    assert [m["role"] for m in messages] == ["system", "user"]
    system = messages[0]["content"]
    # 固定三个小节
    for section in ("## 当前阶段判断", "## 近期动态", "## 建议下一步"):
        assert section in system
    user = messages[1]["content"]
    # 客户资料内联
    assert "张三" in user and "某某科技" in user and "制造、软件" in user
    assert "pending" in user  # DDQ 状态
    # 跟进：优先 ai_summary；无摘要取 content 前 100 字
    assert "讨论了报价与账期" in user
    assert "下周发方案" in user  # next_step 内联
    assert "x" * 100 in user and "x" * 101 not in user
    # 商机与未完成任务内联
    assert "年度框架协议" in user and "proposal" in user
    assert "发送正式方案" in user and "high" in user


def test_build_brief_prompt_empty_data():
    messages = build_brief_prompt(_customer(), [], [], [])
    user = messages[1]["content"]
    assert user.count("（无）") == 3


# ---------- POST /customers/{id}/brief/refresh ----------


class _FakeSession:
    """最小假会话：get 返回预设客户，不触碰真实数据库。"""

    def __init__(self, customer):
        self._customer = customer

    async def get(self, model, ident):
        return self._customer

    async def commit(self):
        pass


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override(db, role="user"):
    async def _fake_user():
        return SimpleNamespace(id=1, tenant_id=1, username="u", name="用户", role=role, status=1)

    async def _fake_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db


async def test_brief_refresh_202(client, monkeypatch):
    customer = SimpleNamespace(id=7, tenant_id=1, deleted_at=None, name="张三")
    _override(_FakeSession(customer))

    called = []

    async def _fake_generate_brief(customer_id):
        called.append(customer_id)

    monkeypatch.setattr("app.api.customers.generate_brief", _fake_generate_brief)

    resp = await client.post("/api/v1/customers/7/brief/refresh")
    assert resp.status_code == 202
    assert resp.json() == {"ok": True}
    # BackgroundTasks 在响应发送后由 ASGI 周期执行
    assert called == [7]


async def test_brief_refresh_customer_not_found_404(client):
    other_tenant_customer = SimpleNamespace(id=7, tenant_id=2, deleted_at=None, name="外人")
    _override(_FakeSession(other_tenant_customer))
    resp = await client.post("/api/v1/customers/7/brief/refresh")
    assert resp.status_code == 404


async def test_update_customer_ddq_status_validation(client):
    customer = SimpleNamespace(id=7, tenant_id=1, deleted_at=None, name="张三")

    class _Session(_FakeSession):
        async def refresh(self, obj):
            pass

        def add(self, obj):
            pass

    _override(_Session(customer))
    # 非法取值 → 400
    resp = await client.put("/api/v1/customers/7", json={"ddq_status": "bogus"})
    assert resp.status_code == 400
