from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.database import get_db
from app.main import app
from app.services.profile import build_profile_prompt


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, get_obj=None):
        self._get_obj = get_obj
        self._execute_queue = []

    def queue_execute(self, rows=None):
        self._execute_queue.append(_FakeResult(rows))

    async def get(self, model, pk):
        return self._get_obj

    async def execute(self, stmt):
        return self._execute_queue.pop(0)

    async def scalar(self, stmt):
        return 0

    def add(self, obj):
        self.added = obj

    async def flush(self):
        if getattr(self.added, "id", None) is None and self.added is not None:
            self.added.id = 1

    async def commit(self):
        pass

    async def refresh(self, obj):
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)


def _fake_user():
    return SimpleNamespace(id=1, tenant_id=1, username="admin", name="管理员", role="admin")


@pytest.fixture
async def client():
    async def _override_user():
        return _fake_user()

    app.dependency_overrides[deps.get_current_user] = _override_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ---------- 客户画像 Prompt 构建 ----------

def test_build_profile_prompt_structure():
    messages = build_profile_prompt(
        {"name": "张三", "industry": "软件", "status": "intention"},
        ["[2026-08-01] 电话沟通了需求"],
        ["大单（金额 100，阶段 negotiating，赢单率 60%）"],
        ["### 合同\n合同内容摘要"],
    )
    assert len(messages) == 2
    system = messages[0]["content"]
    for section in ("基本情况", "需求与痛点", "决策链与关键人", "合作进展", "风险与跟进建议"):
        assert section in system
    user_msg = messages[1]["content"]
    assert "张三" in user_msg
    assert "电话沟通了需求" in user_msg
    assert "大单" in user_msg
    assert "合同内容摘要" in user_msg


def test_build_profile_prompt_empty_data():
    messages = build_profile_prompt({"name": "李四"}, [], [], [])
    user_msg = messages[1]["content"]
    assert user_msg.count("（无）") == 3  # 跟进/商机/文档三个空段


# ---------- 客户专属知识库自动创建（API 冒烟） ----------

async def test_customer_kb_auto_create(client):
    customer = SimpleNamespace(id=2, tenant_id=1, name="张三", deleted_at=None)
    session = _FakeSession(get_obj=customer)
    session.queue_execute([])  # get_or_create_customer_kb: 无既有专属库
    session.queue_execute([])  # _kb_stats: 无文档

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    resp = await client.get("/api/v1/customers/2/kb")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "张三-专属知识库"
    assert data["type"] == "customer"
    assert data["customer_id"] == 2
    assert data["doc_count"] == 0
    assert data["chunk_count"] == 0


async def test_customer_profile_generate_202(client, monkeypatch):
    customer = SimpleNamespace(
        id=2, tenant_id=1, name="张三", deleted_at=None, profile=None, profile_status="idle",
        profile_updated_at=None,
    )
    session = _FakeSession(get_obj=customer)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    # BackgroundTasks 在响应后执行；mock 掉避免触碰真实 DB/LLM
    monkeypatch.setattr("app.api.customers.generate_profile", lambda cid: None)
    resp = await client.post("/api/v1/customers/2/profile/generate")
    assert resp.status_code == 202
    assert resp.json() == {"status": "generating"}
    assert customer.profile_status == "generating"


async def test_customer_profile_get(client):
    customer = SimpleNamespace(
        id=2, tenant_id=1, name="张三", deleted_at=None, profile="# 画像", profile_status="ready",
        profile_updated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    session = _FakeSession(get_obj=customer)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    resp = await client.get("/api/v1/customers/2/profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile"] == "# 画像"
    assert data["status"] == "ready"
    assert data["updated_at"] is not None


async def test_kbs_and_library_require_auth(client):
    app.dependency_overrides.pop(deps.get_current_user, None)
    assert (await client.get("/api/v1/kbs")).status_code == 401
    assert (await client.get("/api/v1/library/tree")).status_code == 401
    assert (await client.get("/api/v1/library/files")).status_code == 401
