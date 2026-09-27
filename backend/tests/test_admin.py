from datetime import datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.api.admin_llm import mask_api_key
from app.config import settings
from app.database import get_db
from app.main import app
from app.services.llm import factory
from app.services.llm.api_llm import ApiLLM
from app.services.llm.instrumented import InstrumentedLLM
from app.services.llm.ollama_llm import OllamaLLM
from app.services.llm.usage import aggregate_stats


# ---------------------------------------------------------------------------
# 假会话 / 客户端基础设施（与 test_api_smoke 同款思路，队列式）
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if self._value is not None else []

    @property
    def rowcount(self):
        return self._value or 0


class _FakeSession:
    def __init__(self):
        self._execute_queue = []
        self._scalar_queue = []
        self._get_queue = []
        self.added = []

    def queue_execute(self, value):
        self._execute_queue.append(value)

    def queue_scalar(self, value):
        self._scalar_queue.append(value)

    def queue_get(self, value):
        self._get_queue.append(value)

    async def execute(self, stmt):
        return _FakeResult(self._execute_queue.pop(0) if self._execute_queue else None)

    async def scalar(self, stmt):
        return self._scalar_queue.pop(0) if self._scalar_queue else None

    async def get(self, model, ident):
        return self._get_queue.pop(0) if self._get_queue else None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass

    async def delete(self, obj):
        pass


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override(db, role="admin", **user_fields):
    user = SimpleNamespace(
        id=1,
        tenant_id=1,
        username="admin",
        name="管理员",
        role=role,
        status=1,
        email=None,
        group_id=None,
        last_login_at=None,
        created_at=datetime(2026, 1, 1),
        **user_fields,
    )

    async def _fake_user():
        return user

    async def _fake_db():
        yield db

    app.dependency_overrides[deps.get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _fake_db
    return user


# ---------------------------------------------------------------------------
# require_admin 权限
# ---------------------------------------------------------------------------

async def test_admin_routes_forbid_non_admin(client):
    _override(_FakeSession(), role="user")
    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 403


async def test_admin_routes_require_auth(client):
    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 用户与分组管理
# ---------------------------------------------------------------------------

async def test_list_users(client):
    db = _FakeSession()
    _override(db)
    member = SimpleNamespace(
        id=2, tenant_id=1, username="alice", name="爱丽丝", email=None,
        role="user", group_id=1, status=1, last_login_at=None,
        created_at=datetime(2026, 1, 2),
    )
    db.queue_scalar(1)  # total
    db.queue_execute([(member, "销售组")])
    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["username"] == "alice"
    assert data["items"][0]["group_name"] == "销售组"


async def test_create_user_conflict_409(client):
    db = _FakeSession()
    _override(db)
    db.queue_scalar(1)  # 用户名已存在
    resp = await client.post(
        "/api/v1/admin/users",
        json={"username": "admin", "password": "x1234567", "name": "重复"},
    )
    assert resp.status_code == 409


async def test_update_self_deactivate_400(client):
    db = _FakeSession()
    admin = _override(db)
    db.queue_get(admin)  # _get_user_or_404 返回自己
    resp = await client.put("/api/v1/admin/users/1", json={"status": 0})
    assert resp.status_code == 400


async def test_update_self_demote_400(client):
    db = _FakeSession()
    admin = _override(db)
    db.queue_get(admin)
    resp = await client.put("/api/v1/admin/users/1", json={"role": "user"})
    assert resp.status_code == 400


async def test_delete_self_400(client):
    db = _FakeSession()
    admin = _override(db)
    db.queue_get(admin)
    resp = await client.delete("/api/v1/admin/users/1")
    assert resp.status_code == 400


async def test_delete_group_with_members_400(client):
    db = _FakeSession()
    _override(db)
    db.queue_get(SimpleNamespace(id=5, tenant_id=1, name="销售组"))
    db.queue_scalar(2)  # 仍有 2 名成员
    resp = await client.delete("/api/v1/admin/groups/5")
    assert resp.status_code == 400


async def test_delete_group_empty_204(client):
    db = _FakeSession()
    _override(db)
    db.queue_get(SimpleNamespace(id=5, tenant_id=1, name="空组"))
    db.queue_scalar(0)
    resp = await client.delete("/api/v1/admin/groups/5")
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# API Key 脱敏（纯函数）
# ---------------------------------------------------------------------------

def test_mask_api_key():
    assert mask_api_key(None) == ""
    assert mask_api_key("") == ""
    assert mask_api_key("abcd") == "****"
    assert mask_api_key("sk-1234567890abcd") == "sk-****abcd"


# ---------------------------------------------------------------------------
# 用量聚合（纯函数）
# ---------------------------------------------------------------------------

def test_aggregate_stats():
    rows = [
        {"model": "deepseek-chat", "caller": "chat", "success": True, "latency_ms": 100,
         "prompt_tokens": 10, "completion_tokens": 5, "error": None,
         "created_at": datetime(2026, 8, 12, 10, 0, 0)},
        {"model": "deepseek-chat", "caller": "rag", "success": False, "latency_ms": 300,
         "prompt_tokens": None, "completion_tokens": None, "error": "超时",
         "created_at": datetime(2026, 8, 13, 9, 0, 0)},
        {"model": "bge-m3", "caller": "embed", "success": True, "latency_ms": 200,
         "prompt_tokens": None, "completion_tokens": None, "error": None,
         "created_at": datetime(2026, 8, 13, 10, 0, 0)},
    ]
    stats = aggregate_stats(rows)
    assert stats["total_calls"] == 3
    assert stats["success_rate"] == round(2 / 3, 4)
    assert stats["avg_latency_ms"] == 200
    assert stats["total_tokens"] == 15
    assert stats["by_model"][0]["model"] == "deepseek-chat"
    assert stats["by_model"][0]["calls"] == 2
    assert stats["by_model"][0]["success_rate"] == 0.5
    assert [d["date"] for d in stats["by_day"]] == ["2026-08-12", "2026-08-13"]
    assert stats["by_caller"][0] == {"caller": "chat", "calls": 1}
    assert len(stats["recent_failures"]) == 1
    assert stats["recent_failures"][0]["error"] == "超时"


def test_aggregate_stats_empty():
    stats = aggregate_stats([])
    assert stats["total_calls"] == 0
    assert stats["success_rate"] == 0.0
    assert stats["by_model"] == []


# ---------------------------------------------------------------------------
# 工厂：DB 优先 + .env 回退
# ---------------------------------------------------------------------------

async def test_factory_prefers_db_model(monkeypatch):
    factory.invalidate_llm_cache()

    async def _fake_load(model_type, tenant_id):
        return SimpleNamespace(
            provider="ollama", base_url="http://localhost:11434",
            api_key=None, model="qwen-test",
        )

    monkeypatch.setattr(factory, "_load_model_from_db", _fake_load)
    llm = await factory.resolve_chat_llm(caller="test", tenant_id=1)
    assert isinstance(llm, InstrumentedLLM)
    assert isinstance(llm._inner, OllamaLLM)
    assert llm._inner.chat_model == "qwen-test"
    factory.invalidate_llm_cache()


async def test_factory_falls_back_to_env(monkeypatch):
    factory.invalidate_llm_cache()
    monkeypatch.setattr(settings, "LLM_CHAT_PROVIDER", "api")

    async def _fake_load(model_type, tenant_id):
        return None  # DB 无记录

    monkeypatch.setattr(factory, "_load_model_from_db", _fake_load)
    llm = await factory.resolve_chat_llm(caller="test", tenant_id=1)
    assert isinstance(llm._inner, ApiLLM)
    assert llm._inner.chat_model == settings.LLM_API_CHAT_MODEL
    factory.invalidate_llm_cache()


# ---------------------------------------------------------------------------
# 用量埋点
# ---------------------------------------------------------------------------

class _OkInner:
    provider_name = "api"
    chat_model = "m-chat"
    embed_model = "m-embed"
    last_usage = {"prompt_tokens": 5, "completion_tokens": 3}

    async def chat(self, messages, **kw):
        return "ok"

    async def embed(self, texts):
        return [[0.1]]


class _FailInner(_OkInner):
    async def chat(self, messages, **kw):
        raise RuntimeError("连接超时")


async def test_instrumented_records_success(monkeypatch):
    entries = []

    async def _record(entry):
        entries.append(entry)

    monkeypatch.setattr("app.services.llm.instrumented.record_call_log", _record)

    llm = InstrumentedLLM(_OkInner(), caller="chat", tenant_id=1, user_id=7)
    assert await llm.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert len(entries) == 1
    entry = entries[0]
    assert entry["model"] == "m-chat"
    assert entry["provider"] == "api"
    assert entry["model_type"] == "chat"
    assert entry["caller"] == "chat"
    assert entry["tenant_id"] == 1
    assert entry["user_id"] == 7
    assert entry["prompt_tokens"] == 5
    assert entry["completion_tokens"] == 3
    assert entry["success"] is True
    assert entry["latency_ms"] >= 0


async def test_instrumented_records_failure_and_reraises(monkeypatch):
    entries = []
    errors = []

    async def _record(entry):
        entries.append(entry)

    async def _log_error(level, module, message, detail=None, tenant_id=None):
        errors.append({"level": level, "module": module, "message": message})

    monkeypatch.setattr("app.services.llm.instrumented.record_call_log", _record)
    monkeypatch.setattr("app.services.error_log.log_error", _log_error)

    llm = InstrumentedLLM(_FailInner(), caller="rag", tenant_id=1)
    with pytest.raises(RuntimeError, match="连接超时"):
        await llm.chat([{"role": "user", "content": "hi"}])
    assert entries[0]["success"] is False
    assert "连接超时" in entries[0]["error"]
    assert errors[0]["level"] == "warning"
    assert errors[0]["module"] == "llm"


# ---------------------------------------------------------------------------
# 异常日志中心
# ---------------------------------------------------------------------------

def _error_ns(**kw):
    base = dict(
        id=1, tenant_id=1, level="error", module="http",
        message="出错了", detail=None, resolved=False,
        created_at=datetime(2026, 8, 13, 8, 0, 0),
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def test_list_errors(client):
    db = _FakeSession()
    _override(db)
    db.queue_scalar(1)  # total
    db.queue_execute([_error_ns()])
    resp = await client.get("/api/v1/admin/errors?level=error&resolved=false")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["message"] == "出错了"
    assert data["items"][0]["resolved"] is False


async def test_resolve_error(client):
    db = _FakeSession()
    _override(db)
    error = _error_ns()
    db.queue_get(error)
    resp = await client.post("/api/v1/admin/errors/1/resolve")
    assert resp.status_code == 200
    assert error.resolved is True


async def test_resolve_error_404(client):
    db = _FakeSession()
    _override(db)
    db.queue_get(None)
    resp = await client.post("/api/v1/admin/errors/999/resolve")
    assert resp.status_code == 404


async def test_resolve_all_errors(client):
    db = _FakeSession()
    _override(db)
    db.queue_execute(3)  # rowcount
    resp = await client.post("/api/v1/admin/errors/resolve_all")
    assert resp.status_code == 200
    assert resp.json()["resolved"] == 3


# ---------------------------------------------------------------------------
# 模型配置：远程模型列表拉取与未保存配置测试
# ---------------------------------------------------------------------------

def _patch_httpx(monkeypatch, handler):
    """把 httpx.AsyncClient 换成 MockTransport 版本（保留原类以便构造）。"""
    import httpx as _httpx

    real_client = _httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_client(transport=_httpx.MockTransport(handler))

    monkeypatch.setattr(_httpx, "AsyncClient", _factory)


async def test_fetch_remote_models_api(client, monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/models"
        assert request.headers.get("authorization") == "Bearer sk-x"
        import httpx as _httpx

        return _httpx.Response(200, json={"data": [{"id": "deepseek-reasoner"}, {"id": "deepseek-chat"}]})

    _patch_httpx(monkeypatch, handler)
    _override(_FakeSession())
    resp = await client.post(
        "/api/v1/admin/llm/models/fetch-remote",
        json={"provider": "api", "base_url": "https://api.deepseek.com/v1", "api_key": "sk-x"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["models"] == ["deepseek-chat", "deepseek-reasoner"]


async def test_fetch_remote_models_ollama(client, monkeypatch):
    def handler(request):
        assert request.url.path == "/api/tags"
        import httpx as _httpx

        return _httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})

    _patch_httpx(monkeypatch, handler)
    _override(_FakeSession())
    resp = await client.post(
        "/api/v1/admin/llm/models/fetch-remote",
        json={"provider": "ollama", "base_url": "http://127.0.0.1:11434"},
    )
    assert resp.status_code == 200
    assert resp.json()["models"] == ["qwen3:8b"]


async def test_fetch_remote_models_failure(client, monkeypatch):
    def handler(request):
        import httpx as _httpx

        raise _httpx.ConnectError("connection refused")

    _patch_httpx(monkeypatch, handler)
    _override(_FakeSession())
    resp = await client.post(
        "/api/v1/admin/llm/models/fetch-remote",
        json={"provider": "api", "base_url": "http://x", "api_key": "k"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "connection refused" in data["detail"]


async def test_fetch_remote_models_bad_provider(client):
    _override(_FakeSession())
    resp = await client.post(
        "/api/v1/admin/llm/models/fetch-remote",
        json={"provider": "xxx", "base_url": "http://x"},
    )
    assert resp.status_code == 400


def _fake_llm_factory(monkeypatch, chat_ret="pong", embed_ret=None, exc=None):
    import app.api.admin_llm as admin_llm

    class _Fake:
        async def chat(self, messages, **kw):
            if exc:
                raise exc
            return chat_ret

        async def embed(self, texts, **kw):
            if exc:
                raise exc
            return embed_ret or [[0.1]]

    monkeypatch.setattr(admin_llm, "build_llm", lambda *a, **k: _Fake())


async def test_test_config_chat_ok(client, monkeypatch):
    _fake_llm_factory(monkeypatch)
    _override(_FakeSession())
    resp = await client.post(
        "/api/v1/admin/llm/models/test-config",
        json={"provider": "api", "model_type": "chat", "base_url": "http://x", "api_key": "k", "model": "m"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_test_config_embed_failure(client, monkeypatch):
    _fake_llm_factory(monkeypatch, exc=RuntimeError("LLM API key 未配置"))
    _override(_FakeSession())
    resp = await client.post(
        "/api/v1/admin/llm/models/test-config",
        json={"provider": "api", "model_type": "embed", "base_url": "http://x", "model": "m"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "未配置" in data["detail"]


async def test_test_config_asr_not_supported(client):
    _override(_FakeSession())
    resp = await client.post(
        "/api/v1/admin/llm/models/test-config",
        json={"provider": "api", "model_type": "asr", "base_url": "http://x", "model": "whisper"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert "暂不支持" in resp.json()["detail"]


async def test_test_config_bad_type(client):
    _override(_FakeSession())
    resp = await client.post(
        "/api/v1/admin/llm/models/test-config",
        json={"provider": "api", "model_type": "xxx", "base_url": "http://x", "model": "m"},
    )
    assert resp.status_code == 400


async def test_test_config_model_id_key_fallback(client, monkeypatch):
    """编辑场景：api_key 为空 + model_id → 用库中已保存的 key 测试。"""
    import app.api.admin_llm as admin_llm

    captured = {}

    class _Fake:
        async def chat(self, messages, **kw):
            return "pong"

    def _build(provider, base_url, api_key, chat_model, embed_model):
        captured["api_key"] = api_key
        return _Fake()

    monkeypatch.setattr(admin_llm, "build_llm", _build)
    db = _FakeSession()
    db.queue_get(SimpleNamespace(id=7, tenant_id=1, api_key="sk-saved"))
    _override(db)
    resp = await client.post(
        "/api/v1/admin/llm/models/test-config",
        json={"provider": "api", "model_type": "chat", "base_url": "http://x", "model": "m", "model_id": 7},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert captured["api_key"] == "sk-saved"


async def test_test_config_model_id_not_found(client):
    db = _FakeSession()
    db.queue_get(None)
    _override(db)
    resp = await client.post(
        "/api/v1/admin/llm/models/test-config",
        json={"provider": "api", "model_type": "chat", "base_url": "http://x", "model": "m", "model_id": 999},
    )
    assert resp.status_code == 404
