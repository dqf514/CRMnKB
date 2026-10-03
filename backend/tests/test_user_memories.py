"""个人记忆（跨工作区）：service 纯逻辑 + REST 端点 + MCP 工具 + agent 新会话注入。

DB 访问层全部打桩（fake session / monkeypatch 模块级 import），不连真实库。
"""
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.database import get_db
from app.main import app
from app.services import acp_bridge
from app.services import chat as chat_service
from app.services import memory as mem_svc
from app.services import mcp_server


def _user(**kw):
    base = {"id": 1, "tenant_id": 1, "username": "u1", "role": "member", "status": 1}
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeSession:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1

    async def flush(self):
        pass

    def add(self, obj):
        pass

    async def execute(self, stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    async def get(self, model, ident):
        return None


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override(db, user=None):
    u = user or _user()

    async def _fake_user():
        return u

    async def _fake_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db


def _memory_ns(**kw):
    base = {
        "id": 1,
        "content": "报告默认用中文",
        "source": "agent",
        "chat_session_id": None,
        "created_at": None,
        "updated_at": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


# ========== service 纯逻辑 ==========


def test_memory_context_block_empty():
    assert mem_svc.memory_context_block([]) == ""


def test_memory_context_block_renders_contents():
    block = mem_svc.memory_context_block([_memory_ns(content="偏好A"), _memory_ns(id=2, content="偏好B")])
    assert block.startswith("<user-memory>")
    assert "偏好A" in block and "偏好B" in block
    assert "memory_save" in block  # 附带工具使用指引


async def test_add_memory_validates_content():
    with pytest.raises(ValueError, match="不能为空"):
        await mem_svc.add_memory(None, 1, "   ")  # 校验先于 DB 访问
    with pytest.raises(ValueError, match="过长"):
        await mem_svc.add_memory(None, 1, "x" * 501)


# ========== REST 端点 ==========


async def test_rest_get_memories(client, monkeypatch):
    _override(_FakeSession())

    async def _fake_list(db, user_id, limit=200):
        assert user_id == 1
        return [_memory_ns()]

    monkeypatch.setattr("app.api.auth.list_memories", _fake_list)
    resp = await client.get("/api/v1/auth/memories")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items[0]["content"] == "报告默认用中文"
    assert items[0]["source"] == "agent"


async def test_rest_create_memory(client, monkeypatch):
    _override(_FakeSession())

    async def _fake_add(db, user_id, content, source="agent", chat_session_id=None):
        assert source == "manual"  # 个人中心添加标记为 manual
        return _memory_ns(id=9, content=content, source="manual"), True

    monkeypatch.setattr("app.api.auth.add_memory", _fake_add)
    resp = await client.post("/api/v1/auth/memories", json={"content": "我是做外贸的"})
    assert resp.status_code == 201
    assert resp.json()["created"] is True


async def test_rest_create_memory_invalid_400(client, monkeypatch):
    _override(_FakeSession())

    async def _fake_add(*a, **k):
        raise ValueError("记忆内容不能为空")

    monkeypatch.setattr("app.api.auth.add_memory", _fake_add)
    resp = await client.post("/api/v1/auth/memories", json={"content": ""})
    assert resp.status_code == 400


async def test_rest_update_memory_404_and_ok(client, monkeypatch):
    _override(_FakeSession())

    async def _fake_update(db, user_id, memory_id, content):
        return None

    monkeypatch.setattr("app.api.auth.update_memory", _fake_update)
    resp = await client.put("/api/v1/auth/memories/5", json={"content": "x"})
    assert resp.status_code == 404

    async def _fake_update_ok(db, user_id, memory_id, content):
        return _memory_ns(id=memory_id, content=content)

    monkeypatch.setattr("app.api.auth.update_memory", _fake_update_ok)
    resp = await client.put("/api/v1/auth/memories/5", json={"content": "改后"})
    assert resp.status_code == 200
    assert resp.json()["item"]["content"] == "改后"


async def test_rest_delete_memory(client, monkeypatch):
    _override(_FakeSession())

    async def _fake_delete(db, user_id, memory_id):
        return memory_id == 1

    monkeypatch.setattr("app.api.auth.delete_memory", _fake_delete)
    assert (await client.delete("/api/v1/auth/memories/1")).status_code == 204
    assert (await client.delete("/api/v1/auth/memories/2")).status_code == 404


async def test_rest_clear_memories(client, monkeypatch):
    _override(_FakeSession())

    async def _fake_clear(db, user_id):
        return 7

    monkeypatch.setattr("app.api.auth.clear_memories", _fake_clear)
    resp = await client.delete("/api/v1/auth/memories")
    assert resp.json() == {"ok": True, "deleted": 7}


# ========== MCP 工具 ==========


class _SessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *args):
        return False


def _patch_mcp_ctx(monkeypatch, db):
    """打桩 MCP 工具三件套：DB session 工厂 / 令牌解析 / 用户解析。"""
    monkeypatch.setattr(mcp_server, "AsyncSessionLocal", lambda: _SessionCtx(db))
    monkeypatch.setattr(mcp_server, "_payload_from_ctx", lambda ctx: {"sub": "1"})

    async def _fake_resolve(db_, payload):
        return _user()

    monkeypatch.setattr(mcp_server, "resolve_mcp_user", _fake_resolve)


async def test_mcp_memory_save_ok(monkeypatch):
    db = _FakeSession()
    _patch_mcp_ctx(monkeypatch, db)

    async def _fake_add(db_, user_id, content, source="agent", chat_session_id=None):
        assert user_id == 1 and source == "agent"  # agent 写入可追溯
        return _memory_ns(id=7, content=content), True

    monkeypatch.setattr(mcp_server, "add_memory", _fake_add)
    result = await mcp_server._memory_save_tool(content="我是做外贸的", ctx=None)
    assert result["ok"] is True and result["created"] is True and result["memory_id"] == 7
    assert db.commits == 1


async def test_mcp_memory_save_dedup(monkeypatch):
    db = _FakeSession()
    _patch_mcp_ctx(monkeypatch, db)

    async def _fake_add(db_, user_id, content, **kw):
        return _memory_ns(id=7, content=content), False

    monkeypatch.setattr(mcp_server, "add_memory", _fake_add)
    result = await mcp_server._memory_save_tool(content="已有内容", ctx=None)
    assert result["created"] is False


async def test_mcp_memory_save_invalid_raises_tool_error(monkeypatch):
    db = _FakeSession()
    _patch_mcp_ctx(monkeypatch, db)

    async def _fake_add(*a, **k):
        raise ValueError("记忆内容不能为空")

    monkeypatch.setattr(mcp_server, "add_memory", _fake_add)
    with pytest.raises(mcp_server.McpToolError, match="不能为空"):
        await mcp_server._memory_save_tool(content="", ctx=None)


async def test_mcp_memory_list_and_search(monkeypatch):
    db = _FakeSession()
    _patch_mcp_ctx(monkeypatch, db)

    async def _fake_list(db_, user_id, limit=200):
        return [_memory_ns()]

    async def _fake_search(db_, user_id, query):
        assert query == "报告"
        return [_memory_ns()]

    monkeypatch.setattr(mcp_server, "list_memories", _fake_list)
    monkeypatch.setattr(mcp_server, "search_memories", _fake_search)
    listed = await mcp_server._memory_list_tool(ctx=None)
    assert listed[0]["content"] == "报告默认用中文"
    searched = await mcp_server._memory_search_tool(query="报告", ctx=None)
    assert len(searched) == 1


async def test_mcp_memory_delete_not_found(monkeypatch):
    db = _FakeSession()
    _patch_mcp_ctx(monkeypatch, db)

    async def _fake_delete(db_, user_id, memory_id):
        return False

    monkeypatch.setattr(mcp_server, "delete_memory", _fake_delete)
    with pytest.raises(mcp_server.McpToolError, match="不存在"):
        await mcp_server._memory_delete_tool(memory_id=99, ctx=None)


# ========== agent 新会话注入 ==========


class _FakeBridge:
    def __init__(self, items):
        self._items = items
        self.run_calls: list[tuple] = []

    async def new_session(self, user):
        return "acp-new-sid"

    async def resume_session(self, user, session_id):
        pass

    def run_turn(self, user, dsh_session_id, question):
        self.run_calls.append((user.id, dsh_session_id, question))

        async def _gen():
            for item in self._items:
                yield item

        return _gen()


def _chat_session(**kw):
    base = {"id": 10, "dsh_session_id": None, "updated_at": None}
    base.update(kw)
    return SimpleNamespace(**base)


_DONE_ITEMS = [
    {"kind": "event", "event": {"session_update": "agent_message_chunk",
                                "content": {"type": "text", "text": "好的"}}},
    {"kind": "done", "final_response": "好的", "finish_reason": "end_turn"},
]


async def test_agent_new_session_injects_memories(monkeypatch):
    """新 dsh 会话：发给 dsh 的 prompt 带记忆块；落库仍是用户原始问题。"""
    fake = _FakeBridge(_DONE_ITEMS)
    monkeypatch.setattr(acp_bridge, "bridge", fake)

    async def _fake_list(db, user_id, limit=200):
        return [_memory_ns(content="报告默认用中文")]

    monkeypatch.setattr(chat_service, "list_memories", _fake_list)
    frames = [
        f
        async for f in chat_service.stream_agent_chat_events(
            _FakeSession(), _user(), "帮我写报告", _chat_session()
        )
    ]
    assert frames[-1]["type"] == "done"
    prompt = fake.run_calls[0][2]
    assert prompt.startswith("<user-memory>")
    assert "报告默认用中文" in prompt
    assert prompt.endswith("帮我写报告")


async def test_agent_resume_session_skips_injection(monkeypatch):
    """已绑定 dsh 会话（resume）：上下文 dsh 侧已有，prompt 原样透传。"""
    fake = _FakeBridge(_DONE_ITEMS)
    monkeypatch.setattr(acp_bridge, "bridge", fake)

    async def _boom(*a, **k):
        raise AssertionError("resume 会话不应再加载记忆")

    monkeypatch.setattr(chat_service, "list_memories", _boom)
    frames = [
        f
        async for f in chat_service.stream_agent_chat_events(
            _FakeSession(), _user(), "问题", _chat_session(dsh_session_id="u1_abc")
        )
    ]
    assert frames[-1]["type"] == "done"
    assert fake.run_calls[0][2] == "问题"


async def test_agent_injection_failure_degrades(monkeypatch):
    """记忆加载异常不应阻断对话：按无记忆继续。"""
    fake = _FakeBridge(_DONE_ITEMS)
    monkeypatch.setattr(acp_bridge, "bridge", fake)

    async def _fail(db, user_id, limit=200):
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_service, "list_memories", _fail)
    frames = [
        f
        async for f in chat_service.stream_agent_chat_events(
            _FakeSession(), _user(), "问题", _chat_session()
        )
    ]
    assert frames[-1]["type"] == "done"
    assert fake.run_calls[0][2] == "问题"
