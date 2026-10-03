"""rerank 模型类型测试：ApiLLM.rerank 请求/解析、rerank_chunks 三级降级、管理端校验。"""
import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

import app.services.rag as rag_module
from app.api import deps
from app.database import get_db
from app.main import app
from app.services.llm.api_llm import ApiLLM


# ---------------------------------------------------------------------------
# ApiLLM.rerank：请求构造与响应解析
# ---------------------------------------------------------------------------

def _mock_httpx(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def test_api_rerank_request_and_parse(monkeypatch):
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        # index 乱序返回，验证按 index 重排
        return httpx.Response(200, json={"results": [
            {"index": 1, "relevance_score": 0.2},
            {"index": 0, "relevance_score": 0.9},
        ]})

    _mock_httpx(monkeypatch, handler)
    llm = ApiLLM(base_url="https://api.example.com/v1", api_key="k", chat_model="bge-reranker", embed_model="")
    scores = await llm.rerank("退货政策", ["文档A", "文档B"])
    assert captured["url"] == "https://api.example.com/v1/rerank"
    assert captured["auth"] == "Bearer k"
    assert captured["body"] == {"model": "bge-reranker", "query": "退货政策", "documents": ["文档A", "文档B"]}
    assert scores == [0.9, 0.2]


async def test_api_rerank_count_mismatch(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.5}]})

    _mock_httpx(monkeypatch, handler)
    llm = ApiLLM(base_url="https://api.example.com", api_key="k", chat_model="m", embed_model="")
    with pytest.raises(RuntimeError, match="数量"):
        await llm.rerank("q", ["a", "b"])


async def test_api_rerank_tei_bare_list(monkeypatch):
    """TEI 风格：裸数组 + score 字段。"""

    def handler(request):
        return httpx.Response(200, json=[
            {"index": 1, "score": 0.3},
            {"index": 0, "score": 0.8},
        ])

    _mock_httpx(monkeypatch, handler)
    llm = ApiLLM(base_url="https://tei.example.com", api_key="k", chat_model="m", embed_model="")
    assert await llm.rerank("q", ["a", "b"]) == [0.8, 0.3]


async def test_api_rerank_data_field(monkeypatch):
    """Voyage 风格：data 字段 + 无 index 时按返回顺序对齐。"""

    def handler(request):
        return httpx.Response(200, json={"data": [
            {"relevance_score": 0.6},
            {"relevance_score": 0.1},
        ]})

    _mock_httpx(monkeypatch, handler)
    llm = ApiLLM(base_url="https://api.example.com/v1", api_key="k", chat_model="m", embed_model="")
    assert await llm.rerank("q", ["a", "b"]) == [0.6, 0.1]


async def test_api_rerank_float_list(monkeypatch):
    """极简风格：直接返回浮点数组。"""

    def handler(request):
        return httpx.Response(200, json=[0.7, 0.2])

    _mock_httpx(monkeypatch, handler)
    llm = ApiLLM(base_url="https://api.example.com", api_key="k", chat_model="m", embed_model="")
    assert await llm.rerank("q", ["a", "b"]) == [0.7, 0.2]


async def test_api_rerank_url_suffix_dedup(monkeypatch):
    """base_url 直接粘贴完整端点（…/rerank）时不重复拼接。"""
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.5}]})

    _mock_httpx(monkeypatch, handler)
    llm = ApiLLM(base_url="https://api.jina.ai/v1/rerank", api_key="k", chat_model="m", embed_model="")
    await llm.rerank("q", ["a"])
    assert captured["url"] == "https://api.jina.ai/v1/rerank"


async def test_api_rerank_http_error_detail(monkeypatch):
    """HTTP 错误透出响应体片段，便于诊断模型名/权限/路径问题。"""

    def handler(request):
        return httpx.Response(404, json={"error": {"message": "model not found"}})

    _mock_httpx(monkeypatch, handler)
    llm = ApiLLM(base_url="https://api.example.com/v1", api_key="k", chat_model="bad-model", embed_model="")
    with pytest.raises(RuntimeError, match="HTTP 404.*model not found"):
        await llm.rerank("q", ["a"])


# ---------------------------------------------------------------------------
# rerank_chunks：三级降级
# ---------------------------------------------------------------------------

def _candidates():
    return [
        {"chunk_id": 1, "doc_id": 1, "chunk_index": 0, "content": "甲", "score": 0.8},
        {"chunk_id": 2, "doc_id": 1, "chunk_index": 1, "content": "乙", "score": 0.7},
    ]


async def test_rerank_dedicated_model_path(monkeypatch):
    """有专用 rerank 模型：直接打分排序，不走 chat LLM。"""
    class _FakeRerank:
        async def rerank(self, query, documents):
            assert documents == ["甲", "乙"]
            return [0.1, 0.95]  # 乙更相关

    async def _resolve_rerank(caller=None, **kw):
        return _FakeRerank()

    async def _chat_must_not_call(caller=None, **kw):
        raise AssertionError("不应走 chat 打分")

    monkeypatch.setattr(rag_module, "resolve_rerank_llm", _resolve_rerank)
    monkeypatch.setattr(rag_module, "resolve_chat_llm", _chat_must_not_call)

    result = await rag_module.rerank_chunks("问题", _candidates())
    assert [r["chunk_id"] for r in result] == [2, 1]  # 按 rerank_score 排序
    assert result[0]["rerank_score"] == 0.95


async def test_rerank_dedicated_failure_falls_back_to_chat(monkeypatch):
    """专用模型调用失败 → 回退 chat LLM 打分路径。"""
    class _BrokenRerank:
        async def rerank(self, query, documents):
            raise RuntimeError("rerank 服务超时")

    class _FakeChat:
        async def chat(self, messages, **kw):
            return "[0.9, 0.1]"

    async def _resolve_rerank(caller=None, **kw):
        return _BrokenRerank()

    async def _resolve_chat(caller=None, **kw):
        return _FakeChat()

    monkeypatch.setattr(rag_module, "resolve_rerank_llm", _resolve_rerank)
    monkeypatch.setattr(rag_module, "resolve_chat_llm", _resolve_chat)

    result = await rag_module.rerank_chunks("问题", _candidates())
    assert [r["chunk_id"] for r in result] == [1, 2]  # chat 分 0.9 > 0.1


async def test_rerank_unconfigured_uses_chat(monkeypatch):
    """未配置专用模型（None）→ 直接走 chat 打分。"""
    class _FakeChat:
        async def chat(self, messages, **kw):
            return "[0.2, 0.8]"

    async def _resolve_rerank(caller=None, **kw):
        return None

    async def _resolve_chat(caller=None, **kw):
        return _FakeChat()

    monkeypatch.setattr(rag_module, "resolve_rerank_llm", _resolve_rerank)
    monkeypatch.setattr(rag_module, "resolve_chat_llm", _resolve_chat)

    result = await rag_module.rerank_chunks("问题", _candidates())
    assert [r["chunk_id"] for r in result] == [2, 1]


async def test_rerank_both_fail_returns_none(monkeypatch):
    """两级都失败 → None（调用方回退 RRF 序，现状行为）。"""
    async def _resolve_rerank(caller=None, **kw):
        return None

    async def _resolve_chat(caller=None, **kw):
        class _Broken:
            async def chat(self, messages, **kw):
                raise RuntimeError("chat 不可用")

        return _Broken()

    monkeypatch.setattr(rag_module, "resolve_rerank_llm", _resolve_rerank)
    monkeypatch.setattr(rag_module, "resolve_chat_llm", _resolve_chat)

    assert await rag_module.rerank_chunks("问题", _candidates()) is None


async def test_rerank_passes_tenant_and_user(monkeypatch):
    """tenant_id/user_id 透传到 rerank/chat 模型解析（按租户选模型、用量记正确租户）。"""
    seen: dict = {}

    async def _resolve_rerank(caller=None, tenant_id=None, user_id=None, **kw):
        seen.update(rerank_tenant=tenant_id, rerank_user=user_id)
        return None

    async def _resolve_chat(caller=None, tenant_id=None, user_id=None, **kw):
        seen.update(chat_tenant=tenant_id, chat_user=user_id)

        class _FakeChat:
            async def chat(self, messages, **kw):
                return "[0.5, 0.6]"

        return _FakeChat()

    monkeypatch.setattr(rag_module, "resolve_rerank_llm", _resolve_rerank)
    monkeypatch.setattr(rag_module, "resolve_chat_llm", _resolve_chat)

    result = await rag_module.rerank_chunks("问题", _candidates(), tenant_id=3, user_id=8)
    assert result is not None
    assert seen == {
        "rerank_tenant": 3, "rerank_user": 8,
        "chat_tenant": 3, "chat_user": 8,
    }


async def test_rewrite_passes_tenant_and_user(monkeypatch):
    """问题改写同样透传 tenant_id/user_id。"""
    seen: dict = {}

    async def _resolve_chat(caller=None, tenant_id=None, user_id=None, **kw):
        seen.update(caller=caller, tenant_id=tenant_id, user_id=user_id)

        class _FakeChat:
            async def chat(self, messages, **kw):
                return "改写后的问题"

        return _FakeChat()

    monkeypatch.setattr(rag_module, "resolve_chat_llm", _resolve_chat)
    out = await rag_module.rewrite_question(
        "他呢", [{"role": "user", "content": "甲公司怎么样"}], tenant_id=3, user_id=8
    )
    assert out == "改写后的问题"
    assert seen == {"caller": "rewrite", "tenant_id": 3, "user_id": 8}


# ---------------------------------------------------------------------------
# 管理端：model_type=rerank 校验与连通性测试
# ---------------------------------------------------------------------------

class _FakeSession:
    def __init__(self):
        self.added = []
        self._scalar_queue = []
        self._get_queue = []

    def queue_scalar(self, v):
        self._scalar_queue.append(v)

    def queue_get(self, v):
        self._get_queue.append(v)

    async def scalar(self, stmt):
        return self._scalar_queue.pop(0) if self._scalar_queue else None

    async def get(self, model, ident):
        return self._get_queue.pop(0) if self._get_queue else None

    def add(self, obj):
        self.added.append(obj)

    async def execute(self, stmt, params=None):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    async def commit(self):
        for o in self.added:
            if getattr(o, "id", None) is None:
                o.id = 100

    async def refresh(self, obj):
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime(2026, 8, 17)


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override(db):
    async def _fake_user():
        return SimpleNamespace(id=1, tenant_id=1, username="admin", name="管理员", role="admin", status=1)

    async def _fake_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db


async def test_admin_create_rerank_model(client):
    db = _FakeSession()
    _override(db)
    resp = await client.post(
        "/api/v1/admin/llm/models",
        json={"name": "bge重排", "provider": "api", "model_type": "rerank",
              "base_url": "https://api.example.com", "api_key": "k", "model": "bge-reranker-v2"},
    )
    assert resp.status_code == 201
    assert resp.json()["model_type"] == "rerank"


def _fake_model(**over):
    base = dict(
        id=7, tenant_id=1, name="旧配置", provider="api", model_type="chat",
        base_url="https://api.example.com", api_key="k", model="m",
        is_default=False, enabled=True, created_at=datetime(2026, 8, 17),
    )
    base.update(over)
    return SimpleNamespace(**base)


async def test_admin_update_model_type_to_rerank(client):
    """回归：编辑已有模型改类型为重排序——LlmModelUpdate 曾缺 model_type 字段导致被静默丢弃。"""
    model = _fake_model()
    db = _FakeSession()
    db.queue_get(model)
    _override(db)
    resp = await client.put("/api/v1/admin/llm/models/7", json={"model_type": "rerank"})
    assert resp.status_code == 200
    assert resp.json()["model_type"] == "rerank"
    assert model.model_type == "rerank"


async def test_admin_update_model_type_invalid(client):
    model = _fake_model()
    db = _FakeSession()
    db.queue_get(model)
    _override(db)
    resp = await client.put("/api/v1/admin/llm/models/7", json={"model_type": "bad"})
    assert resp.status_code == 400
    assert model.model_type == "chat"


async def test_admin_ping_rerank_branch(client, monkeypatch):
    """_ping_model rerank 分支：调 rerank('ping', ['ping doc 1', 'ping doc 2']) 验证连通。"""
    called = {}

    class _FakeLLM:
        async def rerank(self, query, documents):
            called["args"] = (query, documents)
            return [1.0]

    monkeypatch.setattr("app.api.admin_llm.build_llm", lambda *a, **k: _FakeLLM())
    db = _FakeSession()
    _override(db)
    resp = await client.post(
        "/api/v1/admin/llm/models/test-config",
        json={"provider": "api", "model_type": "rerank", "base_url": "http://x",
              "api_key": "k", "model": "m"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert called["args"] == ("ping", ["ping doc 1", "ping doc 2"])
