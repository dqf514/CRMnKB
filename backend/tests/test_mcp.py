"""PR-F：MCP 集成测试。

覆盖：
  - McpSkill 包装：参数透传、结果拼装、close no-op
  - build_skill_from_row 处理 mcp type：拉 server config 注入
  - mcp_discovery 工具发现 + sync_tools 注册到 skills 表
  - mcp_discovery delete_server_skills 删除关联 skill 行
  - admin_mcp API 端点 + 脱敏
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models import mcp_server as mcp_module
from app.services.skills import registry as skills_registry
from app.services.skills import mcp_discovery, mcp_skill
from app.services.skills.mcp_skill import McpSkill


# ---------- McpSkill 纯逻辑 ----------


def test_mcp_skill_init_clamps_timeout():
    skill = McpSkill(
        name="server__tool",
        description="d",
        parameters={"type": "object", "properties": {}},
        config={"server_id": 1, "tool_name": "tool", "transport": "stdio", "timeout": -5},
    )
    assert skill.timeout == 1  # 钳制最小值
    assert skill.server_id == 1
    assert skill.tool_name == "tool"
    assert skill.transport == "stdio"
    assert skill.server_config == {}


def test_mcp_skill_close_no_op():
    skill = McpSkill(
        name="x", description="d", parameters={},
        config={"server_id": 1, "tool_name": "t"},
    )
    # 应可调用且无异常
    import asyncio
    asyncio.run(skill.close())


def test_mcp_skill_run_missing_server_id():
    """无 server_id 应抛清晰错误。"""
    import asyncio
    skill = McpSkill(
        name="x", description="d", parameters={},
        config={"server_id": 0, "tool_name": "t"},
    )
    with pytest.raises(RuntimeError, match="配置不完整"):
        asyncio.run(skill.run({}, {}))


async def test_mcp_skill_run_text_content_assembled(monkeypatch):
    """MCP session.call_tool 返回 [TextContent] 时正确拼装。"""
    # 假 TextContent
    class _TextContent:
        def __init__(self, text):
            self.text = text

    # 假 CallToolResult
    class _CallResult:
        def __init__(self, contents):
            self.content = contents

    # 假 ClientSession
    class _FakeSession:
        async def call_tool(self, name, args):
            return _CallResult([_TextContent("hello"), _TextContent("world")])

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_get_session(server_id, transport, config):
        yield _FakeSession()

    # monkeypatch mcp_skill 引用的 get_mcp_pool（直接 import）
    monkeypatch.setattr(mcp_skill, "get_mcp_pool", lambda: SimpleNamespace(
        get_session=_fake_get_session,
        close=lambda x: None, close_all=lambda: None,
    ))

    skill = McpSkill(
        name="server__tool",
        description="d",
        parameters={"type": "object", "properties": {}},
        config={"server_id": 1, "tool_name": "tool", "transport": "stdio"},
    )
    result = await skill.run({"k": "v"}, {"tenant_id": 1})
    assert "hello" in result
    assert "world" in result


async def test_mcp_skill_run_empty_content_returns_placeholder(monkeypatch):
    """空 content 返回占位文案，不抛错。"""
    class _CallResult:
        content = []

    class _FakeSession:
        async def call_tool(self, name, args):
            return _CallResult()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_get_session(server_id, transport, config):
        yield _FakeSession()

    monkeypatch.setattr(mcp_skill, "get_mcp_pool", lambda: SimpleNamespace(
        get_session=_fake_get_session,
        close=lambda x: None, close_all=lambda: None,
    ))

    skill = McpSkill(
        name="x", description="d", parameters={},
        config={"server_id": 1, "tool_name": "t"},
    )
    result = await skill.run({}, {})
    assert "空结果" in result


# ---------- mcp_discovery sync_tools ----------


class _FakeSession:
    """用于 discovery 测试：scalar / get / execute。"""

    def __init__(self, skill_rows=None, mcp_server=None):
        self._skill_rows = skill_rows or []
        self._mcp_server = mcp_server
        self.executed = []

    def add(self, obj):
        self._skill_rows.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass

    async def get(self, model, ident):
        # 仅支持 McpServer 查询
        if self._mcp_server and model.__name__ == "McpServer" and self._mcp_server.id == ident:
            return self._mcp_server
        return None

    async def scalar(self, stmt):
        # 返回 0 表示 delete 无影响
        return 0

    async def execute(self, stmt, params=None):
        self.executed.append(stmt)
        # 检测 SELECT SkillRow ... 类型=mcp 的查询 → 返回已有行
        sql_str = str(stmt)
        if "SkillRow" in sql_str or "skill" in sql_str.lower() and "SELECT" in sql_str.upper():
            return self._make_select_result(self._skill_rows)
        # 检测 DELETE → 返回带 rowcount 的结果
        if "DELETE" in sql_str.upper():
            return SimpleNamespace(rowcount=0)
        # 默认空结果
        return self._make_select_result([])

    def _make_select_result(self, items):
        """SQLAlchemy .scalars().all() 调用链：execute 返回 result，result.scalars() 返回 Scalars，all() 返回列表。"""
        result = SimpleNamespace()

        class _Scalars:
            def __init__(self, items):
                self._items = items

            def all(self):
                return self._items

        result.scalars = lambda: _Scalars(items)
        return result


class _FakeMcpServer:
    def __init__(self, id, name, tenant_id, transport, config, discovered_tools=None, enabled=True):
        self.id = id
        self.name = name
        self.tenant_id = tenant_id
        self.transport = transport
        self.config = config
        self.discovered_tools = discovered_tools or []
        self.enabled = enabled


async def test_sync_tools_creates_new_skill_rows():
    """discovered_tools 中每个工具 → 一行 Skill(type=mcp)。"""
    server = _FakeMcpServer(
        id=1, name="fs", tenant_id=1, transport="stdio", config={"command": "x"},
        discovered_tools=[
            {"name": "read_file", "description": "读文件", "input_schema": {"type": "object"}},
            {"name": "write_file", "description": "写文件", "input_schema": {"type": "object"}},
        ],
    )
    session = _FakeSession()
    count = await mcp_discovery.sync_tools_to_skills(session, server)
    assert count == 2
    # 检查新行的命名
    names = [r.name for r in session._skill_rows]
    assert "fs__read_file" in names
    assert "fs__write_file" in names
    # config 含 server_id
    for r in session._skill_rows:
        assert r.config["server_id"] == 1
        assert r.type == "mcp"


async def test_sync_tools_updates_existing_rows():
    """同名已存在行 → 原地更新（不增行）。"""
    existing = SimpleNamespace(
        id=10, tenant_id=1, name="fs__read_file", type="mcp", description="",
        config={"server_id": 1}, parameters=None,
    )
    server = _FakeMcpServer(
        id=1, name="fs", tenant_id=1, transport="stdio", config={"command": "x"},
        discovered_tools=[
            {"name": "read_file", "description": "新描述", "input_schema": {"type": "object"}},
        ],
    )
    session = _FakeSession(skill_rows=[existing])
    count = await mcp_discovery.sync_tools_to_skills(session, server)
    assert count == 1
    assert existing.description == "新描述"


async def test_sync_tools_empty_tools_returns_zero():
    server = _FakeMcpServer(
        id=1, name="fs", tenant_id=1, transport="stdio", config={"command": "x"},
        discovered_tools=[],
    )
    session = _FakeSession()
    count = await mcp_discovery.sync_tools_to_skills(session, server)
    assert count == 0
    assert session._skill_rows == []


# ---------- build_skill_from_row 处理 mcp 类型 ----------


async def test_build_skill_from_row_mcp_fetches_server_config():
    """build_skill_from_row 看到 type=mcp 应查 McpServer 行并把 config 注入。"""
    server = _FakeMcpServer(
        id=5, name="git", tenant_id=1, transport="stdio",
        config={"command": "uvx", "args": ["mcp-server-git"]},
    )
    row = SimpleNamespace(
        id=10, tenant_id=1, name="git__status", type="mcp",
        description="git status",
        config={"server_id": 5, "tool_name": "status", "transport": "stdio"},
        parameters={"type": "object", "properties": {}},
        display_name="git__status",
    )
    db = _FakeSession(mcp_server=server)
    skill = await skills_registry.build_skill_from_row(db, row)
    assert isinstance(skill, McpSkill)
    assert skill.name == "git__status"
    assert skill.server_id == 5
    assert skill.tool_name == "status"
    assert skill.transport == "stdio"
    assert skill.server_config == {"command": "uvx", "args": ["mcp-server-git"]}


async def test_build_skill_from_row_builtin_unchanged():
    """builtin 类型走 BUILTIN_SKILLS，不查 DB。"""
    row = SimpleNamespace(
        id=20, tenant_id=1, name="web_search", type="builtin",
        description=None, config={"timeout": 30}, parameters=None,
        display_name=None,
    )
    db = _FakeSession()
    skill = await skills_registry.build_skill_from_row(db, row)
    # 应该是 WebSearchSkill 实例
    assert skill.__class__.__name__ == "WebSearchSkill"
    assert skill.timeout == 30


async def test_build_skill_from_row_api_unchanged():
    """api 类型走 ApiSkill。"""
    row = SimpleNamespace(
        id=30, tenant_id=1, name="my_api", type="api",
        description="自定义 API", config={"url": "https://x"},
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        display_name=None,
    )
    db = _FakeSession()
    skill = await skills_registry.build_skill_from_row(db, row)
    assert skill.__class__.__name__ == "ApiSkill"
    assert skill.name == "my_api"


# ---------- mcp_discovery.refresh_server_status ----------


async def test_refresh_server_status_success_updates_fields():
    server = _FakeMcpServer(id=1, name="fs", tenant_id=1, transport="stdio", config={})
    session = _FakeSession()
    await mcp_discovery.refresh_server_status(
        session, server,
        success=True,
        error=None,
        tools=[{"name": "x", "description": "y", "input_schema": {}}],
    )
    assert server.status == "connected"
    assert server.last_error is None
    assert server.discovered_tools == [{"name": "x", "description": "y", "input_schema": {}}]
    assert server.last_connected_at is not None


async def test_refresh_server_status_failure_keeps_tools():
    server = _FakeMcpServer(
        id=1, name="fs", tenant_id=1, transport="stdio", config={},
        discovered_tools=[{"name": "old", "description": "", "input_schema": {}}],
    )
    session = _FakeSession()
    await mcp_discovery.refresh_server_status(
        session, server,
        success=False,
        error="connect timeout",
        tools=None,
    )
    assert server.status == "error"
    assert server.last_error == "connect timeout"
    assert server.discovered_tools == [{"name": "old", "description": "", "input_schema": {}}]  # 保留旧值


# ---------- McpConnectionPool 行为 ----------


import app.services.skills.mcp_pool as pool_mod


async def test_pool_close_all_clears_connections():
    pool = pool_mod.McpConnectionPool()
    # 模拟添加假连接
    pool._connections[1] = {"session": SimpleNamespace(close=MagicMock()), "transport": "stdio"}
    pool._connections[2] = {"session": SimpleNamespace(close=MagicMock()), "transport": "sse"}
    await pool.close_all()
    assert pool._connections == {}


async def test_pool_close_unknown_id_is_noop():
    pool = pool_mod.McpConnectionPool()
    await pool.close(999)  # 无报错
    assert pool._connections == {}