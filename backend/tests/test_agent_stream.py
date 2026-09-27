"""dsh agent 流式端点测试（ACP 阶段）：开关行为 + SSE 帧序列 + ACP 事件映射
+ 会话激活（new/resume 退回）+ 落库。"""
import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.config import settings
from app.database import get_db
from app.main import app
from app.services import acp_bridge
from app.services import chat as chat_service


def _user(**kw):
    base = {"id": 1, "tenant_id": 1, "username": "u1", "role": "member", "status": 1}
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeSession:
    """最小假会话：支持 get_or_create_session / _persist_turn / 显式 UPDATE 的调用面。"""

    def __init__(self):
        self.executed: list = []

    async def get(self, model, ident):
        return None

    def add(self, obj):
        pass

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def execute(self, stmt):
        self.executed.append(stmt)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


def _override_auth(user):
    async def _fake_user():
        return user

    async def _fake_get_db():
        yield _FakeSession()

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_get_db


def _parse_sse(text: str) -> list[dict]:
    frames = []
    for frame in text.split("\n\n"):
        for line in frame.split("\n"):
            if line.startswith("data:"):
                frames.append(json.loads(line[5:].strip()))
    return frames


async def test_agent_stream_503_when_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "DSH_AGENT_ENABLED", False)
    _override_auth(_user())
    resp = await client.post(
        "/api/v1/chat/ask/agent/stream", json={"question": "你好"}
    )
    assert resp.status_code == 503


async def test_agent_stream_sse_sequence(client, monkeypatch):
    """打桩服务层生成器，验证端点的 SSE 帧封装与会话创建。"""
    monkeypatch.setattr(settings, "DSH_AGENT_ENABLED", True)
    _override_auth(_user())

    async def _fake_events(db, user, question, session):
        yield {"type": "meta", "session_id": session.id}
        yield {"type": "token", "content": "块一"}
        yield {"type": "tool", "name": "mcp__kb__kb_search", "status": "start", "call_id": "c1"}
        yield {"type": "token", "content": "块二"}
        yield {"type": "done", "query_log_id": 9, "finish_reason": "end_turn"}

    monkeypatch.setattr("app.api.chat.stream_agent_chat_events", _fake_events)

    resp = await client.post(
        "/api/v1/chat/ask/agent/stream", json={"question": "查一下退货政策"}
    )
    assert resp.status_code == 200
    frames = _parse_sse(resp.text)
    types = [f["type"] for f in frames]
    assert types == ["meta", "token", "tool", "token", "done"]
    assert frames[1]["content"] == "块一"
    assert frames[2]["name"] == "mcp__kb__kb_search"
    assert frames[-1]["query_log_id"] == 9
    assert frames[-1]["finish_reason"] == "end_turn"


# ---------------------------------------------------------------------------
# stream_agent_chat_events：ACP session/update → SSE 帧映射 + 会话激活 + 落库
# ---------------------------------------------------------------------------


class _FakeBridge:
    """ACP 桥假实现：new_session/resume_session/run_turn 三件套。"""

    def __init__(self, items, resume_error: Exception | None = None):
        self._items = items
        self._resume_error = resume_error
        self.new_calls: list[int] = []
        self.resume_calls: list[str] = []
        self.run_calls: list[tuple] = []

    async def new_session(self, user):
        self.new_calls.append(user.id)
        return "acp-new-sid"

    async def resume_session(self, user, session_id):
        self.resume_calls.append(session_id)
        if self._resume_error is not None:
            raise self._resume_error

    def run_turn(self, user, dsh_session_id, question):
        self.run_calls.append((user.id, dsh_session_id, question))

        async def _gen():
            for item in self._items:
                yield item

        return _gen()


def _chunk_event(text: str) -> dict:
    return {
        "session_update": "agent_message_chunk",
        "content": {"type": "text", "text": text},
    }


def _session(**kw):
    base = {"id": 10, "dsh_session_id": "u1_abc", "updated_at": None}
    base.update(kw)
    return SimpleNamespace(**base)


async def test_stream_agent_chat_events_mapping(monkeypatch):
    items = [
        {"kind": "event", "event": _chunk_event("先检索。")},
        {"kind": "event", "event": {"session_update": "tool_call", "tool_call_id": "c1",
                                    "title": "mcp__kb__kb_search", "status": "in_progress"}},
        {"kind": "event", "event": {"session_update": "tool_call_update", "tool_call_id": "c1",
                                    "status": "completed"}},
        {"kind": "event", "event": _chunk_event("最终回答。")},
        {"kind": "event", "event": {"session_update": "usage_update", "used": 100, "size": 131072}},
        {"kind": "done", "final_response": "先检索。最终回答。", "finish_reason": "end_turn"},
    ]
    fake = _FakeBridge(items)
    monkeypatch.setattr(acp_bridge, "bridge", fake)

    frames = [
        f
        async for f in chat_service.stream_agent_chat_events(
            _FakeSession(), _user(), "退货政策？", _session()
        )
    ]
    types = [f["type"] for f in frames]
    assert types == ["meta", "token", "tool", "tool", "token", "done"]
    assert frames[0]["session_id"] == 10
    assert frames[1]["content"] == "先检索。"
    # tool_call → start；tool_call_update(completed) → done 并回填名字；usage_update 不透传
    assert frames[2] == {"type": "tool", "name": "mcp__kb__kb_search", "status": "start", "call_id": "c1"}
    assert frames[3]["status"] == "done" and frames[3]["name"] == "mcp__kb__kb_search"
    assert frames[3]["is_error"] is False
    assert frames[4]["content"] == "最终回答。"
    assert frames[5]["type"] == "done" and frames[5]["finish_reason"] == "end_turn"
    # 已绑定 dsh_session_id：走 resume 而非 new；run_turn 用原会话 id
    assert fake.resume_calls == ["u1_abc"]
    assert fake.new_calls == []
    assert fake.run_calls == [(1, "u1_abc", "退货政策？")]


async def test_stream_agent_chat_events_tool_failed(monkeypatch):
    """tool_call_update status=failed → tool done 帧 is_error=True。"""
    items = [
        {"kind": "event", "event": {"session_update": "tool_call", "tool_call_id": "c1",
                                    "title": "mcp__kb__kb_search", "status": "in_progress"}},
        {"kind": "event", "event": {"session_update": "tool_call_update", "tool_call_id": "c1",
                                    "status": "failed"}},
        {"kind": "event", "event": _chunk_event("检索失败了。")},
        {"kind": "done", "final_response": "检索失败了。", "finish_reason": "end_turn"},
    ]
    monkeypatch.setattr(acp_bridge, "bridge", _FakeBridge(items))
    frames = [
        f
        async for f in chat_service.stream_agent_chat_events(
            _FakeSession(), _user(), "问题", _session()
        )
    ]
    assert frames[2]["status"] == "done" and frames[2]["is_error"] is True


async def test_stream_agent_chat_events_new_session_when_unbound(monkeypatch):
    """chat 会话未绑定 dsh_session_id：session/new 并显式 UPDATE 回写新 id。"""
    db = _FakeSession()
    session = _session(dsh_session_id=None)
    items = [
        {"kind": "event", "event": _chunk_event("回答")},
        {"kind": "done", "final_response": "回答", "finish_reason": "end_turn"},
    ]
    fake = _FakeBridge(items)
    monkeypatch.setattr(acp_bridge, "bridge", fake)

    frames = [
        f async for f in chat_service.stream_agent_chat_events(db, _user(), "问题", session)
    ]
    assert [f["type"] for f in frames] == ["meta", "token", "done"]
    assert fake.new_calls == [1]
    assert fake.resume_calls == []
    assert fake.run_calls == [(1, "acp-new-sid", "问题")]
    assert session.dsh_session_id == "acp-new-sid"
    assert db.executed  # 显式 UPDATE 已执行（生成器内 ORM 已 detach）


async def test_stream_agent_chat_events_resume_fallback_to_new(monkeypatch):
    """resume 失败（会话不存在/cwd 漂移）→ 退回 session/new 并回写新 id。"""
    db = _FakeSession()
    session = _session(dsh_session_id="u1_stale")
    items = [
        {"kind": "event", "event": _chunk_event("回答")},
        {"kind": "done", "final_response": "回答", "finish_reason": "end_turn"},
    ]
    fake = _FakeBridge(items, resume_error=RuntimeError("session cwd does not match"))
    monkeypatch.setattr(acp_bridge, "bridge", fake)

    frames = [
        f async for f in chat_service.stream_agent_chat_events(db, _user(), "问题", session)
    ]
    assert [f["type"] for f in frames] == ["meta", "token", "done"]
    assert fake.resume_calls == ["u1_stale"]
    assert fake.new_calls == [1]
    assert fake.run_calls == [(1, "acp-new-sid", "问题")]
    assert session.dsh_session_id == "acp-new-sid"
    assert db.executed


async def test_stream_agent_chat_events_activation_failure(monkeypatch):
    """会话激活（new/resume）失败 → error 帧，不进入 run_turn。"""

    class _DeadBridge(_FakeBridge):
        async def new_session(self, user):
            raise RuntimeError("dsh 进程不可用")

    monkeypatch.setattr(
        acp_bridge, "bridge", _DeadBridge([], resume_error=RuntimeError("x"))
    )
    frames = [
        f
        async for f in chat_service.stream_agent_chat_events(
            _FakeSession(), _user(), "问题", _session()
        )
    ]
    assert [f["type"] for f in frames] == ["meta", "error"]


async def test_stream_agent_chat_events_error_item(monkeypatch):
    fake = _FakeBridge([{"kind": "error", "detail": "dsh 运行失败: boom"}])
    monkeypatch.setattr(acp_bridge, "bridge", fake)

    frames = [
        f
        async for f in chat_service.stream_agent_chat_events(
            _FakeSession(), _user(), "问题", _session()
        )
    ]
    assert [f["type"] for f in frames] == ["meta", "error"]
    assert "boom" in frames[1]["detail"]


async def test_stream_agent_chat_events_empty_answer(monkeypatch):
    items = [{"kind": "done", "final_response": "", "finish_reason": "error"}]
    monkeypatch.setattr(acp_bridge, "bridge", _FakeBridge(items))

    frames = [
        f
        async for f in chat_service.stream_agent_chat_events(
            _FakeSession(), _user(), "问题", _session()
        )
    ]
    assert [f["type"] for f in frames] == ["meta", "error"]


async def test_stream_agent_chat_events_fallback_final_response(monkeypatch):
    """没有任何 agent_message_chunk 时，用 final_response 补一帧并正常落库。"""
    items = [{"kind": "done", "final_response": "只有最终响应", "finish_reason": "end_turn"}]
    monkeypatch.setattr(acp_bridge, "bridge", _FakeBridge(items))

    frames = [
        f
        async for f in chat_service.stream_agent_chat_events(
            _FakeSession(), _user(), "问题", _session()
        )
    ]
    assert [f["type"] for f in frames] == ["meta", "token", "done"]
    assert frames[1]["content"] == "只有最终响应"
