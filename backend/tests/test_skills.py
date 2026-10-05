"""Skill 系统测试：管理端 CRUD/脱敏/禁删/连通性测试、内置工具请求构造、
SSRF 防护、api skill 占位符、对话 agent 循环与降级。

HTTP 全部走 httpx MockTransport / monkeypatch，不触网不触库。
"""
import json
import socket
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

import app.services.chat as chat_module
from app.api import deps
from app.database import get_db
from app.main import app
from app.models.audit_log import AuditLog
from app.services.skills.api_skill import ApiSkill, render_template
from app.services.skills.builtin import WebFetchSkill, WebSearchSkill


@pytest.fixture(autouse=True)
def _stub_skill_call_log(monkeypatch):
    """skill 调用埋点（record_skill_call_log）会另开真实会话写库——本文件一律打桩，
    不触真实库（埋点自身行为由 test_skill_instrumentation.py 覆盖）。"""

    async def _noop(entry):
        return None

    monkeypatch.setattr("app.services.skills.registry.record_skill_call_log", _noop)


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
        self._scalar_queue = []
        self._get_queue = []
        self.added = []
        self.deleted = []

    def queue_execute(self, v):
        self._execute_queue.append(v)

    def queue_scalar(self, v):
        self._scalar_queue.append(v)

    def queue_get(self, v):
        self._get_queue.append(v)

    async def execute(self, stmt, params=None):
        return _FakeResult(self._execute_queue.pop(0) if self._execute_queue else None)

    async def scalar(self, stmt):
        return self._scalar_queue.pop(0) if self._scalar_queue else None

    async def get(self, model, ident):
        return self._get_queue.pop(0) if self._get_queue else None

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
            obj.created_at = datetime(2026, 8, 17)

    async def delete(self, obj):
        self.deleted.append(obj)


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


def _skill_row(**kw):
    base = dict(
        id=7, tenant_id=1, name="web_search", display_name=None,
        description=None, type="builtin", enabled=True,
        config={}, created_at=datetime(2026, 8, 17),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _mock_httpx(monkeypatch, module, handler, captured):
    """把 module 内 httpx.AsyncClient 换成 MockTransport 客户端，handler 记录请求。"""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)
    # check_url_safe 会做真实 DNS 解析：本机代理 fake-ip（198.18.x.x 保留段）会导致
    # example.com 之类被误判为内网。测试里主机名一律解析到固定公网 IP，保持环境无关。
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, *a, **kw: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))],
    )


# ---------------------------------------------------------------------------
# 管理端接口
# ---------------------------------------------------------------------------

async def test_list_skills_shows_builtins(client):
    db = _FakeSession()
    _override(db)
    db.queue_execute([])  # 无 DB 行
    resp = await client.get("/api/v1/admin/skills")
    assert resp.status_code == 200
    items = {i["name"]: i for i in resp.json()}
    assert set(items) == {"web_search", "web_fetch"}
    assert items["web_search"]["type"] == "builtin"
    assert items["web_search"]["enabled"] is False
    assert items["web_search"]["id"] is None  # 未建行


async def test_put_builtin_auto_creates_row(client):
    """未入库的内置技能以 name 作为路径参数保存（前端列表 id 为 NULL 的场景）。"""
    db = _FakeSession()
    _override(db)
    db.queue_scalar(None)  # 按名查找不存在 → 自动建行
    resp = await client.put(
        "/api/v1/admin/skills/web_search",
        json={"enabled": True,
              "config": {"provider": "tavily", "api_key": "tvly-abcdef123456"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["config"]["api_key"] == "tvl****3456"  # 脱敏
    assert data["config"]["provider"] == "tavily"
    from app.models.skill import Skill

    assert any(isinstance(a, Skill) and a.name == "web_search" for a in db.added)
    assert any(isinstance(a, AuditLog) and a.action == "update" and a.resource_type == "skill" for a in db.added)


async def test_put_null_id_returns_404_not_422(client):
    """历史缺陷回归：前端曾把 null 拼进路径导致 422；现在应返回明确的 404。"""
    db = _FakeSession()
    _override(db)
    resp = await client.put("/api/v1/admin/skills/null", json={"enabled": True})
    assert resp.status_code == 404


async def test_put_missing_skill_404(client):
    db = _FakeSession()
    _override(db)
    db.queue_get(None)
    resp = await client.put("/api/v1/admin/skills/7", json={"enabled": True})
    assert resp.status_code == 404


async def test_create_api_skill_and_delete(client):
    db = _FakeSession()
    _override(db)
    db.queue_scalar(None)  # 同名检查
    resp = await client.post(
        "/api/v1/admin/skills",
        json={"name": "stock_query", "description": "查股价",
              "config": {"method": "GET", "url": "https://api.example.com/stock/{{code}}"}},
    )
    assert resp.status_code == 201
    assert resp.json()["type"] == "api"
    assert any(isinstance(a, AuditLog) and a.action == "create" and a.resource_type == "skill" for a in db.added)

    # 删除 api skill
    db2 = _FakeSession()
    _override(db2)
    db2.queue_get(_skill_row(id=8, name="stock_query", type="api"))
    resp = await client.delete("/api/v1/admin/skills/8")
    assert resp.status_code == 204
    assert db2.deleted


async def test_create_api_skill_duplicate_or_builtin_name(client):
    db = _FakeSession()
    _override(db)
    resp = await client.post(
        "/api/v1/admin/skills",
        json={"name": "web_search", "config": {"url": "https://x.com"}},
    )
    assert resp.status_code == 400  # 与内置重名

    db2 = _FakeSession()
    _override(db2)
    db2.queue_scalar(1)  # 已存在同名
    resp = await client.post(
        "/api/v1/admin/skills",
        json={"name": "my_api", "config": {"url": "https://x.com"}},
    )
    assert resp.status_code == 400


async def test_delete_builtin_forbidden(client):
    db = _FakeSession()
    _override(db)
    db.queue_get(_skill_row(type="builtin"))
    resp = await client.delete("/api/v1/admin/skills/7")
    assert resp.status_code == 400


async def test_skill_test_endpoint_fail_branch(client):
    """web_search 配 tavily 但无 key → ok:false（不 500）。"""
    db = _FakeSession()
    _override(db)
    db.queue_get(_skill_row(config={"provider": "tavily"}))
    resp = await client.post("/api/v1/admin/skills/7/test", json={"args": {"query": "hi"}})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "api_key" in data["error"]


async def test_skill_test_endpoint_ok_branch(client, monkeypatch):
    import app.services.skills.builtin as builtin_mod

    def handler(request):
        return httpx.Response(200, json={"results": [{"title": "T", "content": "C", "url": "https://x.com"}]})

    _mock_httpx(monkeypatch, builtin_mod, handler, {})
    db = _FakeSession()
    _override(db)
    db.queue_get(_skill_row(config={"provider": "tavily", "api_key": "k12345678"}))
    resp = await client.post("/api/v1/admin/skills/7/test", json={"args": {"query": "hi"}})
    data = resp.json()
    assert data["ok"] is True
    assert "T" in data["result"]
    assert data["latency_ms"] >= 0


# ---------------------------------------------------------------------------
# 内置工具：请求构造 / SSRF
# ---------------------------------------------------------------------------

async def test_web_search_tavily_request(monkeypatch):
    import app.services.skills.builtin as builtin_mod

    captured = {}

    def handler(request):
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": [{"title": "t", "content": "c", "url": "u"}]})

    _mock_httpx(monkeypatch, builtin_mod, handler, captured)
    skill = WebSearchSkill({"provider": "tavily", "api_key": "secret-key"})
    result = await skill.run({"query": "新能源"}, {})
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["body"]["api_key"] == "secret-key"
    assert captured["body"]["query"] == "新能源"
    assert "1. t" in result


async def test_web_search_bing_request(monkeypatch):
    import app.services.skills.builtin as builtin_mod

    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("Ocp-Apim-Subscription-Key")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"webPages": {"value": [{"name": "n", "snippet": "s", "url": "u"}]}})

    _mock_httpx(monkeypatch, builtin_mod, handler, captured)
    skill = WebSearchSkill({"provider": "bing", "api_key": "bing-key"})
    result = await skill.run({"query": "crm"}, {})
    assert captured["auth"] == "bing-key"
    assert "api.bing.microsoft.com/v7.0/search" in captured["url"]
    assert "1. n" in result


async def test_web_search_duckduckgo_parsing(monkeypatch):
    import app.services.skills.builtin as builtin_mod

    html = (
        '<a class="result__a" href="https://a.com">标题A</a>'
        '<a class="result__snippet">摘要A</a>'
        '<a class="result__a" href="https://b.com">标题B</a>'
        '<a class="result__snippet">摘要B</a>'
    )

    def handler(request):
        return httpx.Response(200, text=html)

    _mock_httpx(monkeypatch, builtin_mod, handler, {})
    skill = WebSearchSkill({"provider": "duckduckgo"})
    result = await skill.run({"query": "x"}, {})
    assert "标题A" in result and "摘要A" in result and "https://b.com" in result


async def test_web_search_bocha_request(monkeypatch):
    """博查（国内通道）：POST Bearer 鉴权，解析 data.webPages.value。"""
    import app.services.skills.builtin as builtin_mod

    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "data": {"webPages": {"value": [
                {"name": "特变电工股价", "snippet": "摘要S", "url": "https://x.com/1"},
                {"name": "第二条", "summary": "摘要T", "url": "https://x.com/2"},
            ]}}
        })

    _mock_httpx(monkeypatch, builtin_mod, handler, captured)
    skill = WebSearchSkill({"provider": "bocha", "api_key": "sk-bocha"})
    result = await skill.run({"query": "特变电工股价"}, {})
    assert captured["url"] == "https://api.bochaai.com/v1/web-search"
    assert captured["auth"] == "Bearer sk-bocha"
    assert captured["body"]["query"] == "特变电工股价"
    assert "特变电工股价" in result and "摘要S" in result and "摘要T" in result


async def test_web_search_bocha_requires_key():
    skill = WebSearchSkill({"provider": "bocha"})
    with pytest.raises(RuntimeError, match="bocha api_key"):
        await skill.run({"query": "x"}, {})


async def test_web_fetch_ssrf_blocked():
    skill = WebFetchSkill()
    for bad in ("http://127.0.0.1/x", "http://localhost/admin", "http://192.168.1.1/",
                "http://10.0.0.5/", "http://172.16.0.1/", "ftp://example.com/f"):
        with pytest.raises(ValueError):
            await skill.run({"url": bad}, {})


async def test_web_fetch_strips_html(monkeypatch):
    import app.services.skills.builtin as builtin_mod

    def handler(request):
        return httpx.Response(200, text="<html><style>.a{}</style><body><h1>标题</h1><p>正文内容</p><script>bad()</script></body></html>")

    _mock_httpx(monkeypatch, builtin_mod, handler, {})
    skill = WebFetchSkill()
    result = await skill.run({"url": "https://example.com/page"}, {})
    assert "标题" in result and "正文内容" in result
    assert "<" not in result and "bad()" not in result


# ---------------------------------------------------------------------------
# SSRF 重定向防护：follow_redirects=False，checked_request 逐跳校验
# ---------------------------------------------------------------------------

async def test_web_fetch_redirect_to_internal_blocked(monkeypatch):
    """历史缺陷回归：302 跳到内网地址，重定向目标必须重新过 SSRF 校验。"""
    import app.services.skills.builtin as builtin_mod

    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})

    _mock_httpx(monkeypatch, builtin_mod, handler, {})
    skill = WebFetchSkill()
    with pytest.raises(ValueError, match="内网"):
        await skill.run({"url": "https://example.com/start"}, {})
    assert calls == ["https://example.com/start"]  # 仅发出第一跳请求


async def test_web_fetch_follows_safe_redirect(monkeypatch):
    """同主机相对路径重定向正常跟随。"""
    import app.services.skills.builtin as builtin_mod

    def handler(request):
        if str(request.url).endswith("/start"):
            return httpx.Response(302, headers={"Location": "/real"})
        return httpx.Response(200, text="<p>最终内容</p>")

    _mock_httpx(monkeypatch, builtin_mod, handler, {})
    skill = WebFetchSkill()
    result = await skill.run({"url": "https://example.com/start"}, {})
    assert "最终内容" in result


async def test_web_fetch_redirect_loop_limited(monkeypatch):
    """重定向环超过最大跳数即报错，不无限跟随。"""
    import app.services.skills.builtin as builtin_mod

    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "/loop"})

    _mock_httpx(monkeypatch, builtin_mod, handler, {})
    skill = WebFetchSkill()
    with pytest.raises(ValueError, match="重定向次数"):
        await skill.run({"url": "https://example.com/loop"}, {})
    assert len(calls) == 6  # 首跳 + 最多 5 次跟随


async def test_api_skill_redirect_to_internal_blocked(monkeypatch):
    """api skill 的重定向目标同样逐跳校验（如云元数据地址 169.254.169.254）。"""
    import app.services.skills.api_skill as api_mod

    def handler(request):
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})

    _mock_httpx(monkeypatch, api_mod, handler, {})
    skill = ApiSkill(
        name="t", description=None, parameters=None,
        config={"method": "GET", "url": "https://api.example.com/x"},
    )
    with pytest.raises(ValueError, match="内网"):
        await skill.run({}, {})


async def test_api_skill_redirect_cross_host_strips_auth(monkeypatch):
    """跨主机重定向剥掉 Authorization 等敏感自定义头，防凭据泄露。"""
    import app.services.skills.api_skill as api_mod

    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("Authorization")))
        if str(request.url).endswith("/start"):
            return httpx.Response(302, headers={"Location": "https://cdn.other-example.com/final"})
        return httpx.Response(200, json={"ok": True})

    _mock_httpx(monkeypatch, api_mod, handler, {})
    skill = ApiSkill(
        name="t", description=None, parameters=None,
        config={"method": "GET", "url": "https://api.example.com/start",
                "headers": {"Authorization": "Bearer secret-token"}},
    )
    result = await skill.run({}, {})
    assert "ok" in result
    assert seen[0] == ("https://api.example.com/start", "Bearer secret-token")
    assert seen[1] == ("https://cdn.other-example.com/final", None)  # 敏感头已剥离


# ---------------------------------------------------------------------------
# 自定义 api skill：占位符替换
# ---------------------------------------------------------------------------

def test_render_template():
    assert render_template("https://x.com/a/{{id}}?q={{q}}", {"id": 5, "q": "你好"}) == "https://x.com/a/5?q=你好"
    assert render_template("{{missing}}", {}) == "{{missing}}"  # 未提供的参数保留原样


async def test_api_skill_placeholder_substitution(monkeypatch):
    import app.services.skills.api_skill as api_mod

    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"price": 42})

    _mock_httpx(monkeypatch, api_mod, handler, captured)
    skill = ApiSkill(
        name="stock", description="查股价", parameters=None,
        config={"method": "POST", "url": "https://api.example.com/stock/{{code}}",
                "body": '{"q": "{{q}}"}'},
    )
    result = await skill.run({"code": "600519", "q": "茅台"}, {})
    assert captured["url"] == "https://api.example.com/stock/600519"
    assert captured["body"] == {"q": "茅台"}
    assert "42" in result


# ---------------------------------------------------------------------------
# 对话 agent 循环
# ---------------------------------------------------------------------------

class _FakeSkill:
    name = "web_search"
    description = "联网搜索"
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}}

    def __init__(self, result="搜索到结果：答案42"):
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
        self.tool_messages_seen = []

    async def chat_with_tools(self, messages, tools, **kw):
        self.rounds += 1
        if self.rounds == 1:
            return {"content": None, "tool_calls": [
                {"id": "call-1", "name": "web_search", "arguments": {"query": "答案"}}
            ]}
        self.tool_messages_seen = [m for m in messages if m.get("role") == "tool"]
        return {"content": self.final, "tool_calls": []}

    async def chat_with_tools_stream(self, messages, tools, on_token=None, **kw):
        self.rounds += 1
        if self.rounds == 1:
            return {"content": None, "tool_calls": [
                {"id": "call-1", "name": "web_search", "arguments": {"query": "答案"}}
            ]}
        self.tool_messages_seen = [m for m in messages if m.get("role") == "tool"]
        if on_token is not None:
            await on_token(self.final)
        return {"content": self.final, "tool_calls": []}

    async def chat(self, messages, **kw):
        raise AssertionError("不应走纯 chat")

    async def chat_stream(self, messages, **kw):
        raise AssertionError("不应走流式")
        yield


class _PlainLLM:
    def __init__(self, text="普通回答"):
        self.text = text
        self.chat_with_tools_called = False

    async def chat(self, messages, **kw):
        return self.text

    async def chat_stream(self, messages, **kw):
        yield self.text

    async def chat_with_tools(self, messages, tools, **kw):
        self.chat_with_tools_called = True
        raise RuntimeError("该模型不支持工具调用")

    async def chat_with_tools_stream(self, messages, tools, on_token=None, **kw):
        self.chat_with_tools_called = True
        raise RuntimeError("该模型不支持工具调用")


def _patch_retrieval(monkeypatch, llm, skills):
    """把 chat_ask 的检索/LLM/skills 全部替换为 fake， grounded=True。"""
    hit = {"chunk_id": 1, "doc_id": 2, "chunk_index": 0,
           "content": "知识库内容", "doc_title": "资料", "score": 0.9}

    class _FakeEmbed:
        async def embed(self, texts):
            return [[0.1]]

    async def _resolve_embed(caller=None, **kw):
        return _FakeEmbed()

    async def _resolve_chat(caller=None, **kw):
        return llm

    async def _vec(db, tid, vec, limit, kb_ids=None, file_ids=None):
        return [hit]

    async def _kw(db, tid, q, limit, kb_ids=None, file_ids=None):
        return [hit]

    async def _no_rerank(q, cands, **_kw):
        return None

    async def _expand(db, tid, hits, window):
        return [{"doc_id": 2, "doc_title": "资料", "start": 0, "end": 0, "content": "知识库内容"}]

    async def _skills(db, tenant_id):
        return skills

    monkeypatch.setattr(chat_module, "resolve_embed_llm", _resolve_embed)
    monkeypatch.setattr(chat_module, "resolve_chat_llm", _resolve_chat)
    monkeypatch.setattr(chat_module, "search_chunks_vector", _vec)
    monkeypatch.setattr(chat_module, "search_chunks_keyword", _kw)
    monkeypatch.setattr(chat_module, "rerank_chunks", _no_rerank)
    monkeypatch.setattr(chat_module, "expand_contexts", _expand)
    monkeypatch.setattr(chat_module, "get_enabled_skills", _skills)


def _chat_db():
    db = _FakeSession()
    db.queue_execute([])  # load_history
    db.queue_execute([SimpleNamespace(id=2, file_id=None, file_name="f", file_type="txt")])  # attach_file_info
    return db


def _user():
    return SimpleNamespace(id=1, tenant_id=1, role="admin")


async def test_agent_loop_executes_tool(monkeypatch):
    llm = _AgentLLM()
    skill = _FakeSkill()
    _patch_retrieval(monkeypatch, llm, [skill])
    db = _chat_db()

    result = await chat_module.chat_ask(db, _user(), "最新答案是多少")

    assert result["answer"] == "最终答案：42"
    assert result["tools_used"] == ["web_search"]
    assert skill.calls == [{"query": "答案"}]
    # 工具结果已回注 messages
    assert llm.tool_messages_seen[0]["name"] == "web_search"
    assert "答案42" in llm.tool_messages_seen[0]["content"]


async def test_agent_falls_back_when_tools_unsupported(monkeypatch):
    llm = _PlainLLM("不支持工具时的普通回答")
    skill = _FakeSkill()
    _patch_retrieval(monkeypatch, llm, [skill])
    db = _chat_db()

    result = await chat_module.chat_ask(db, _user(), "问题")

    assert result["answer"] == "不支持工具时的普通回答"
    assert result["tools_used"] == []
    assert skill.calls == []  # 工具未执行


async def test_no_skills_unchanged(monkeypatch):
    llm = _PlainLLM("无工具普通回答")
    _patch_retrieval(monkeypatch, llm, [])
    db = _chat_db()

    result = await chat_module.chat_ask(db, _user(), "问题")

    assert result["answer"] == "无工具普通回答"
    assert result["tools_used"] == []
    assert llm.chat_with_tools_called is False


async def test_stream_emits_tool_frames(monkeypatch):
    llm = _AgentLLM("流式最终答案")
    skill = _FakeSkill()
    _patch_retrieval(monkeypatch, llm, [skill])
    db = _chat_db()
    session = SimpleNamespace(id=7, tenant_id=1, user_id=1, updated_at=None)

    events = [e async for e in chat_module.stream_chat_events(db, _user(), "问题", session)]

    types = [e["type"] for e in events]
    assert types[0] == "meta"
    assert "sources" in types
    tool_frames = [e for e in events if e["type"] == "tool"]
    assert [(f["name"], f["status"]) for f in tool_frames] == [("web_search", "start"), ("web_search", "done")]
    token_text = "".join(e["content"] for e in events if e["type"] == "token")
    assert token_text == "流式最终答案"
    assert types[-1] == "done"
