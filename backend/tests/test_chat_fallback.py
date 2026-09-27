"""工作台聊天未命中知识库的兜底策略测试。

覆盖：CHAT_FALLBACK_TO_LLM=true 时降级为普通对话（带专用系统提示词）；
false 时维持固定话术；流式路径同样降级且正常产出 token/done 帧。
全部用 fake session 与 monkeypatch，不触网不触库。
"""
from datetime import datetime
from types import SimpleNamespace

import app.services.chat as chat_module
from app.services.rag import FALLBACK_ANSWER


class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if self._value is not None else []

    def mappings(self):
        return self


class _FakeSession:
    def __init__(self):
        self._execute_queue = []
        self.added = []

    def queue_execute(self, v):
        self._execute_queue.append(v)

    async def execute(self, stmt, params=None):
        return _FakeResult(self._execute_queue.pop(0) if self._execute_queue else None)

    async def scalar(self, stmt):
        return None

    async def get(self, model, ident):
        return None

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for o in self.added:
            if getattr(o, "id", None) is None:
                o.id = 100

    async def commit(self):
        await self.flush()

    async def refresh(self, obj):
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime(2026, 8, 24)


class _RecordingLLM:
    """记录调用与消息的假聊天模型。"""

    def __init__(self, text="你好！很高兴见到你。"):
        self.text = text
        self.chat_called = False
        self.stream_called = False
        self.seen_messages = None

    async def chat(self, messages, **kw):
        self.chat_called = True
        self.seen_messages = messages
        return self.text

    async def chat_stream(self, messages, **kw):
        self.stream_called = True
        self.seen_messages = messages
        yield self.text


class _NeverLLM:
    """任何调用都视为断言失败的假模型。"""

    async def chat(self, messages, **kw):
        raise AssertionError("不应调用对话模型")

    async def chat_stream(self, messages, **kw):
        raise AssertionError("不应调用流式对话模型")
        yield


def _patch_ungrounded(monkeypatch, llm, skills=None):
    """检索全部落空（grounded=False），LLM 与 skills 按给定值替换。"""
    class _FakeEmbed:
        async def embed(self, texts):
            return [[0.1]]

    async def _resolve_embed(caller=None, **kw):
        return _FakeEmbed()

    async def _resolve_chat(caller=None, **kw):
        return llm

    async def _empty(db, tid, *a, **kw):
        return []

    async def _no_rerank(q, cands):
        return None

    async def _skills(db, tenant_id):
        return skills or []

    monkeypatch.setattr(chat_module, "resolve_embed_llm", _resolve_embed)
    monkeypatch.setattr(chat_module, "resolve_chat_llm", _resolve_chat)
    monkeypatch.setattr(chat_module, "search_chunks_vector", _empty)
    monkeypatch.setattr(chat_module, "search_chunks_keyword", _empty)
    monkeypatch.setattr(chat_module, "rerank_chunks", _no_rerank)
    monkeypatch.setattr(chat_module, "get_enabled_skills", _skills)


def _db():
    db = _FakeSession()
    db.queue_execute([])  # load_history：无历史
    db.queue_execute([])  # accessible_ids
    return db


def _user():
    return SimpleNamespace(id=1, tenant_id=1, role="admin")


async def test_ungrounded_falls_back_to_llm(monkeypatch):
    """未命中知识库时降级普通对话：答案来自 LLM，提示词声明未参考知识库。"""
    llm = _RecordingLLM()
    _patch_ungrounded(monkeypatch, llm)

    result = await chat_module.chat_ask(_db(), _user(), "hello")

    assert result["answer"] == "你好！很高兴见到你。"
    assert result["grounded"] is False
    assert llm.chat_called is True
    system = llm.seen_messages[0]["content"]
    assert "未参考企业知识库" in system
    # 历史之后紧跟本次问题
    assert llm.seen_messages[-1] == {"role": "user", "content": "hello"}


async def test_ungrounded_fixed_answer_when_disabled(monkeypatch):
    """关闭降级开关时维持原行为：固定话术，不调用模型。"""
    llm = _NeverLLM()
    _patch_ungrounded(monkeypatch, llm)
    monkeypatch.setattr(chat_module.settings, "CHAT_FALLBACK_TO_LLM", False)

    result = await chat_module.chat_ask(_db(), _user(), "hello")

    assert result["answer"] == FALLBACK_ANSWER
    assert result["grounded"] is False


async def test_stream_ungrounded_falls_back_to_llm(monkeypatch):
    """流式路径：未命中时按配置走普通对话，产出 token 与 done 帧。"""
    llm = _RecordingLLM("流式普通回答")
    _patch_ungrounded(monkeypatch, llm)

    session = SimpleNamespace(id=7, tenant_id=1, user_id=1, updated_at=None)
    events = [e async for e in chat_module.stream_chat_events(_db(), _user(), "hello", session)]

    types = [e["type"] for e in events]
    assert types[0] == "meta"
    assert types[1] == "status"  # 检索阶段状态帧（新协议）
    assert types[2] == "sources"
    tokens = "".join(e["content"] for e in events if e["type"] == "token")
    assert tokens == "流式普通回答"
    assert types[-1] == "done"
    assert llm.stream_called is True
    assert "未参考企业知识库" in llm.seen_messages[0]["content"]


# ---------------------------------------------------------------------------
# 失效会话 id 回退：localStorage 里存的旧会话被删/越权时不应 404，应静默新建
# ---------------------------------------------------------------------------

class _SessionStore:
    def __init__(self, existing=None):
        self._existing = existing
        self.added = []

    async def get(self, model, ident):
        return self._existing

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = 99

    async def flush(self):
        pass


async def test_stale_session_id_creates_new():
    """session_id 已不存在/越权时，回退新建会话而非报 404。"""
    user = SimpleNamespace(id=1, tenant_id=1)
    db = _SessionStore(existing=None)  # 会话 999 不存在
    session = await chat_module.get_or_create_session(db, user, "hello", session_id=999)
    assert session.id == 99  # 新建了一个会话
    assert session.title == "hello"


async def test_valid_session_id_reused():
    """会话存在且属于本人时，复用而不新建。"""
    existing = SimpleNamespace(id=999, tenant_id=1, user_id=1)
    db = _SessionStore(existing=existing)
    user = SimpleNamespace(id=1, tenant_id=1)
    session = await chat_module.get_or_create_session(db, user, "hi", session_id=999)
    assert session is existing


async def test_other_user_session_id_creates_new():
    """session_id 属于别的用户时，不越权复用，静默新建。"""
    existing = SimpleNamespace(id=999, tenant_id=1, user_id=2)  # 属于用户 2
    db = _SessionStore(existing=existing)
    user = SimpleNamespace(id=1, tenant_id=1)
    session = await chat_module.get_or_create_session(db, user, "hi", session_id=999)
    assert session.id == 99  # 新建，不复用别人的会话


class _FakeSkill:
    name = "web_search"
    description = "联网搜索"
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}}

    def __init__(self, result="搜索结果：答案42"):
        self.calls = []
        self._result = result

    async def run(self, args, ctx):
        self.calls.append(args)
        return self._result


class _AgentLLM:
    """第一轮返回 tool_calls，第二轮返回最终答案。"""

    def __init__(self, final="最终答案：42"):
        self.rounds = 0
        self.final = final
        self.called_with_tools = False

    async def chat_with_tools_stream(self, messages, tools, on_token=None, **kw):
        self.rounds += 1
        if self.rounds == 1:
            return {"content": None, "tool_calls": [
                {"id": "call-1", "name": "web_search", "arguments": {"query": "答案"}}
            ]}
        if on_token is not None:
            await on_token(self.final)
        return {"content": self.final, "tool_calls": []}

    async def chat(self, messages, **kw):
        raise AssertionError("不应走纯 chat")


async def test_ungrounded_with_skill_uses_agent(monkeypatch):
    """未命中但有启用技能时走 agent 工具路径，而不是固定话术。"""
    skill = _FakeSkill()
    llm = _AgentLLM()
    _patch_ungrounded(monkeypatch, llm, skills=[skill])

    async def _fake_execute(skill_obj, args, ctx):
        return await skill_obj.run(args, ctx)

    monkeypatch.setattr(chat_module, "execute_skill", _fake_execute)

    result = await chat_module.chat_ask(_db(), _user(), "最新的答案是什么")

    assert result["answer"] == "最终答案：42"
    assert result["tools_used"] == ["web_search"]
    assert skill.calls == [{"query": "答案"}]
