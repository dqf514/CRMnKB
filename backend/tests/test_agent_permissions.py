"""agent 工具自动放行白名单测试：解析/匹配纯函数 + 管理 API。

不起真实 DB：get_db 依赖覆盖为内存 fake session；进程缓存每个用例前后清空。
"""
import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.main import app
from app.services import agent_permissions


@pytest.fixture(autouse=True)
def _clear_cache():
    agent_permissions._cache["tools"] = None
    yield
    agent_permissions._cache["tools"] = None


# ---------------------------------------------------------------------------
# 纯函数：parse_tools / is_auto_allowed / validate_tools_payload
# ---------------------------------------------------------------------------

def test_parse_tools_fallback_default():
    """未配置/脏数据回退默认白名单（保证升级后行为不变）。"""
    default = agent_permissions.DEFAULT_AUTO_ALLOW_TOOLS
    assert agent_permissions.parse_tools(None) == default
    assert agent_permissions.parse_tools("") == default
    assert agent_permissions.parse_tools("not json") == default
    assert agent_permissions.parse_tools('{"a": 1}') == default


def test_parse_tools_explicit_empty_list():
    """显式空数组是最严格的合法配置（全部拒绝），不回退默认。"""
    assert agent_permissions.parse_tools("[]") == []
    assert agent_permissions.parse_tools('["bad name!"]') == []  # 全部非法条目被清洗


def test_parse_tools_dedup_and_clean():
    out = agent_permissions.parse_tools(
        json.dumps(["kb_search", "kb_search", " web_fetch ", "bad name!", 123])
    )
    assert out == ["kb_search", "web_fetch", "123"]


def test_is_auto_allowed_exact_and_suffix():
    wl = {"kb_search", "web_fetch"}
    assert agent_permissions.is_auto_allowed("kb_search", wl)
    # MCP 前缀工具按 __ 末段匹配
    assert agent_permissions.is_auto_allowed("mcp__kb__kb_search", wl)
    assert agent_permissions.is_auto_allowed("mcp__kb__web_fetch", wl)
    assert not agent_permissions.is_auto_allowed("mcp__kb__crm_create_customer", wl)
    assert not agent_permissions.is_auto_allowed("bash", wl)
    assert not agent_permissions.is_auto_allowed("", wl)
    # 列表入参同样可用
    assert agent_permissions.is_auto_allowed("kb_search", ["kb_search"])


def test_validate_tools_payload():
    assert agent_permissions.validate_tools_payload(["a", "a", " b "]) == ["a", "b"]
    with pytest.raises(ValueError):
        agent_permissions.validate_tools_payload("not-a-list")
    with pytest.raises(ValueError):
        agent_permissions.validate_tools_payload(["bad name"])
    with pytest.raises(ValueError):
        agent_permissions.validate_tools_payload([""])
    with pytest.raises(ValueError):
        agent_permissions.validate_tools_payload(["x"] * (agent_permissions.MAX_TOOLS + 1))


# ---------------------------------------------------------------------------
# 管理 API
# ---------------------------------------------------------------------------

class _FakeSession:
    """最小内存 session：支撑 SystemSetting 的 get/add/commit。"""

    def __init__(self, store):
        self.store = store

    async def get(self, model, key):
        return self.store.get(key)

    def add(self, row):
        self.store[row.key] = row

    async def commit(self):
        pass


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override_user(role="admin"):
    async def _fake_user():
        return SimpleNamespace(id=1, tenant_id=1, username="u", name="用户", role=role)

    app.dependency_overrides[deps.get_current_user] = _fake_user


def _override_db(store):
    async def _fake_db():
        yield _FakeSession(store)

    app.dependency_overrides[deps.get_db] = _fake_db


async def test_api_403_non_admin(client):
    _override_user("member")
    _override_db({})
    resp = await client.get("/api/v1/admin/agent-tools")
    assert resp.status_code == 403


async def test_api_get_default_whitelist(client):
    """未配置时回退默认白名单（知识库 MCP 只读工具），并附带工具目录。"""
    _override_user("admin")
    _override_db({})
    resp = await client.get("/api/v1/admin/agent-tools")
    assert resp.status_code == 200
    data = resp.json()
    assert data["whitelist"] == agent_permissions.DEFAULT_AUTO_ALLOW_TOOLS
    assert data["defaults"] == agent_permissions.DEFAULT_AUTO_ALLOW_TOOLS
    assert any(t["name"] == "kb_search" for t in data["catalog"])


async def test_api_put_roundtrip(client):
    _override_user("admin")
    store = {}
    _override_db(store)
    resp = await client.put(
        "/api/v1/admin/agent-tools", json={"tools": ["kb_search", "web_fetch", "web_fetch"]}
    )
    assert resp.status_code == 200
    assert resp.json()["whitelist"] == ["kb_search", "web_fetch"]  # 已去重
    # 落库内容可解析
    row = store[agent_permissions.AUTO_ALLOW_KEY]
    assert json.loads(row.value) == ["kb_search", "web_fetch"]
    # 再 GET 读到新值（set 已失效缓存）
    resp = await client.get("/api/v1/admin/agent-tools")
    assert resp.json()["whitelist"] == ["kb_search", "web_fetch"]


async def test_api_put_invalid_name(client):
    _override_user("admin")
    _override_db({})
    resp = await client.put("/api/v1/admin/agent-tools", json={"tools": ["bad name!"]})
    assert resp.status_code == 400
    assert "非法工具名" in resp.json()["detail"]


async def test_api_put_empty_list_allowed(client):
    """空名单合法（全部工具都走拒绝/审批），不回退默认——回退只发生在配置缺失/脏数据时。"""
    _override_user("admin")
    store = {}
    _override_db(store)
    resp = await client.put("/api/v1/admin/agent-tools", json={"tools": []})
    assert resp.status_code == 200
    assert resp.json()["whitelist"] == []
    resp = await client.get("/api/v1/admin/agent-tools")
    assert resp.json()["whitelist"] == []
