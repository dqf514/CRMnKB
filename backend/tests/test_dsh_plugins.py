"""dsh 插件管理服务测试：dump 解析 / 覆盖层读写 / API 鉴权与流程。

dsh CLI 子进程一律 monkeypatch 打桩，不起真实 dsh。
"""
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.config import settings
from app.main import app
from app.services import dsh_plugins


DUMP_SAMPLE = """# == @deepseek-ai/dsh-base
- id: tool-plugin-manager
  name: '@deepseek-ai/dsh-plugin-manager/tools'
  disabled: true
- id: plugin-manager
  name: '@deepseek-ai/dsh-plugin-manager'
  disabled: !!js '!ctx.get(''profileContext'')'
# == @deepseek-ai/dsh-base, patched by @deepseek-ai/dsh-acp-app
- id: hmr
  name: '@deepseek-ai/dsh-hmr'
  disabled: true
  config:
    root: []
- id: llm
  name: '@deepseek-ai/dsh-llm'
"""


# ---------------------------------------------------------------------------
# dump 解析（纯函数）
# ---------------------------------------------------------------------------

def test_parse_dump_basic():
    items = dsh_plugins.parse_dump(DUMP_SAMPLE)
    assert [i["id"] for i in items] == ["tool-plugin-manager", "plugin-manager", "hmr", "llm"]
    # 静态布尔
    assert items[0]["disabled"] is True
    assert items[0]["bundle"] == "@deepseek-ai/dsh-base"
    # !!js 表达式：disabled 为 None，表达式原文保留
    assert items[1]["disabled"] is None
    assert "profileContext" in items[1]["disabled_expr"]
    # 带 config 的条目
    assert items[2]["has_config"] is True
    assert items[2]["bundle"] == "@deepseek-ai/dsh-base, patched by @deepseek-ai/dsh-acp-app"
    # 未声明 disabled 视为启用（dsh 默认行为）
    assert items[3]["disabled"] is False
    assert items[3]["disabled_expr"] is None


def test_parse_dump_empty():
    assert dsh_plugins.parse_dump("") == []
    assert dsh_plugins.parse_dump("# 只有注释\n") == []


# ---------------------------------------------------------------------------
# 覆盖层 plugins.yml 读写（tmp 目录）
# ---------------------------------------------------------------------------

@pytest.fixture
def patches_dir(tmp_path, monkeypatch):
    d = tmp_path / "patches"
    monkeypatch.setattr(settings, "DSH_PATCHES_DIR", str(d))
    return d


def test_overrides_roundtrip(patches_dir):
    assert dsh_plugins.load_overrides() == []
    dsh_plugins.set_plugin_enabled("tool-web", False)
    dsh_plugins.set_plugin_enabled("hmr", True)
    assert dsh_plugins.override_disabled_map() == {"tool-web": True, "hmr": False}
    # 幂等更新同一条目
    dsh_plugins.set_plugin_enabled("tool-web", True)
    assert dsh_plugins.override_disabled_map() == {"tool-web": False, "hmr": False}
    text = (patches_dir / "plugins.yml").read_text(encoding="utf-8")
    assert "tool-web" in text and "hmr" in text


def test_custom_insert_and_remove(patches_dir):
    assert dsh_plugins.custom_insert_entries() == []
    dsh_plugins.save_overrides([{"insert": [{"id": "my-plugin", "name": "@acme/my-plugin"}]}])
    assert dsh_plugins.custom_insert_entries() == [{"id": "my-plugin", "name": "@acme/my-plugin"}]
    assert dsh_plugins.remove_custom_entry("my-plugin") is True
    assert dsh_plugins.custom_insert_entries() == []
    assert dsh_plugins.remove_custom_entry("my-plugin") is False


def test_validate_spec():
    assert dsh_plugins._validate_spec("@acme/dsh-plugin@1.0.0") == "@acme/dsh-plugin@1.0.0"
    assert dsh_plugins._validate_spec("dsh-plugin-foo") == "dsh-plugin-foo"
    with pytest.raises(dsh_plugins.DshPluginError):
        dsh_plugins._validate_spec("")
    with pytest.raises(dsh_plugins.DshPluginError):
        dsh_plugins._validate_spec("--registry=http://evil")  # 选项注入
    with pytest.raises(dsh_plugins.DshPluginError):
        dsh_plugins._validate_spec("not a spec with spaces")


def test_spec_package_name():
    assert dsh_plugins._spec_package_name("@acme/dsh-plugin@1.0.0") == "@acme/dsh-plugin"
    assert dsh_plugins._spec_package_name("dsh-plugin-foo@latest") == "dsh-plugin-foo"
    assert dsh_plugins._spec_package_name("plain") == "plain"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _override_user(role="admin"):
    async def _fake_user():
        return SimpleNamespace(id=1, tenant_id=1, username="u", name="用户", role=role)

    app.dependency_overrides[deps.get_current_user] = _fake_user


def _enable_agent(monkeypatch):
    monkeypatch.setattr(settings, "DSH_AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "DSH_BIN", "/bin/dsh")


async def test_api_503_when_agent_disabled(client, monkeypatch):
    _override_user("admin")
    monkeypatch.setattr(settings, "DSH_AGENT_ENABLED", False)
    resp = await client.get("/api/v1/admin/dsh-plugins")
    assert resp.status_code == 503


async def test_api_403_non_admin(client, monkeypatch):
    _override_user("member")
    _enable_agent(monkeypatch)
    resp = await client.get("/api/v1/admin/dsh-plugins")
    assert resp.status_code == 403


async def test_api_list_merges_overrides(client, monkeypatch, patches_dir):
    _override_user("admin")
    _enable_agent(monkeypatch)

    async def _fake_dump():
        return dsh_plugins.parse_dump(DUMP_SAMPLE)

    monkeypatch.setattr(dsh_plugins, "dump_tree", _fake_dump)
    dsh_plugins.set_plugin_enabled("tool-plugin-manager", True)  # 覆盖：启用原本禁用的
    resp = await client.get("/api/v1/admin/dsh-plugins")
    assert resp.status_code == 200
    items = {i["id"]: i for i in resp.json()["items"]}
    assert items["tool-plugin-manager"]["effective_disabled"] is False
    assert items["tool-plugin-manager"]["overridden"] is True
    # 动态表达式未被覆盖时 effective_disabled 为 None
    assert items["plugin-manager"]["effective_disabled"] is None
    # 未声明 disabled 的条目有效状态为启用
    assert items["llm"]["effective_disabled"] is False


async def test_api_toggle_writes_override_and_restarts(client, monkeypatch, patches_dir):
    _override_user("admin")
    _enable_agent(monkeypatch)

    async def _fake_dump():
        return dsh_plugins.parse_dump(DUMP_SAMPLE)

    monkeypatch.setattr(dsh_plugins, "dump_tree", _fake_dump)
    restarted = []
    import app.services.acp_bridge as bridge_mod

    async def _fake_close():
        restarted.append(1)

    monkeypatch.setattr(bridge_mod.bridge, "close_all", _fake_close)

    resp = await client.put("/api/v1/admin/dsh-plugins/llm", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert dsh_plugins.override_disabled_map() == {"llm": True}
    assert restarted  # 已触发进程重建

    # 未知插件 404
    resp = await client.put("/api/v1/admin/dsh-plugins/no-such-plugin", json={"enabled": True})
    assert resp.status_code == 404


async def test_api_install_invalid_spec(client, monkeypatch, patches_dir):
    _override_user("admin")
    _enable_agent(monkeypatch)
    resp = await client.post("/api/v1/admin/dsh-plugins/install", json={"spec": "--evil"})
    assert resp.status_code == 400
