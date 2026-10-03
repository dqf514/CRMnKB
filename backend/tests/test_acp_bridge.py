"""dsh ACP 桥接测试：进程生命周期（monkeypatch spawn_agent_process 假连接）
+ 进程级 patch yml 生成 + 会话激活（new/resume）+ 事件转发 + 权限应答策略。
不起真实 dsh 进程。"""
import asyncio
import time
from types import SimpleNamespace

import pytest

from acp import start_tool_call, update_agent_message_text, update_tool_call

from app.config import settings
from app.services import acp_bridge
from app.services.acp_bridge import AcpBridge, AcpUnavailable


def _user(uid=1, **kw):
    base = {"id": uid, "tenant_id": 1, "username": f"u{uid}", "role": "member", "status": 1}
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeProc:
    def __init__(self):
        self.returncode = None
        self.pid = 12345


class _FakeCM:
    """spawn_agent_process 的假异步上下文管理器。"""

    def __init__(self, conn, proc):
        self._conn = conn
        self._proc = proc
        self.exited = False

    async def __aenter__(self):
        return self._conn, self._proc

    async def __aexit__(self, *args):
        self.exited = True
        self._proc.returncode = 0


class _FakeConn:
    """ClientSideConnection 假实现：记录调用；prompt 时经 client handler 回推事件。"""

    def __init__(self, handler):
        self._handler = handler
        self.initialized = False
        self.new_calls: list[dict] = []
        self.resume_calls: list[str] = []
        self.cancelled: list[str] = []
        self.prompt_error: Exception | None = None
        self.prompt_events: list = []
        self.new_session_id = "acp-sess-1"

    async def initialize(self, **kwargs):
        self.initialized = True
        return SimpleNamespace()

    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        self.new_calls.append({"cwd": cwd, "mcp_servers": mcp_servers})
        return SimpleNamespace(session_id=self.new_session_id)

    async def resume_session(self, session_id, cwd, mcp_servers=None, **kwargs):
        self.resume_calls.append(session_id)
        return SimpleNamespace()

    async def prompt(self, session_id, prompt, **kwargs):
        if self.prompt_error is not None:
            raise self.prompt_error
        for ev in self.prompt_events:
            await self._handler.session_update(session_id, ev)
        return SimpleNamespace(stop_reason="end_turn")

    async def cancel(self, session_id):
        self.cancelled.append(session_id)


class _SpawnRecorder:
    """替换 acp 模块命名空间：记录 spawn 参数，返回假连接。"""

    def __init__(self):
        self.calls: list[dict] = []
        self.conns: list[_FakeConn] = []
        self.cms: list[_FakeCM] = []
        # run_turn 里用到 text_block / PROTOCOL_VERSION，取真实实现
        self.text_block = acp_bridge.acp.text_block
        self.PROTOCOL_VERSION = acp_bridge.acp.PROTOCOL_VERSION

    def spawn_agent_process(self, client, command, *args, env=None, cwd=None):
        conn = _FakeConn(client)
        proc = _FakeProc()
        cm = _FakeCM(conn, proc)
        self.calls.append({"command": command, "args": args, "env": env, "cwd": cwd, "proc": proc})
        self.conns.append(conn)
        self.cms.append(cm)
        return cm


@pytest.fixture
def fake_acp(monkeypatch, tmp_path):
    """打桩 ACP spawn 与 dsh 目录：全部指向 tmp_path，不碰真实进程与文件。"""
    rec = _SpawnRecorder()
    monkeypatch.setattr(acp_bridge, "acp", rec)
    monkeypatch.setattr(settings, "DSH_PATCHES_DIR", str(tmp_path / "patches"))
    monkeypatch.setattr(settings, "DSH_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(settings, "DSH_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setattr(settings, "DSH_BIN", "/fake/dsh")
    monkeypatch.setattr(settings, "DSH_MCP_URL", "http://127.0.0.1:8100/api/mcp")
    return rec


async def _fake_model_cfg(self, tenant_id):
    return ("deepseek-chat", "https://api.deepseek.com/v1", "sk-test")


async def test_process_lazy_start_and_reuse(fake_acp, monkeypatch):
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    user = _user(1)
    await bridge.new_session(user)
    await bridge.new_session(user)
    assert len(fake_acp.calls) == 1  # 单进程复用
    assert fake_acp.conns[0].initialized


async def test_spawn_args_and_env(fake_acp, monkeypatch, tmp_path):
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    await bridge.new_session(_user(1))
    call = fake_acp.calls[0]
    assert call["command"] == "/fake/dsh"
    assert call["args"][0:2] == ("--profile", "acp")
    # base.yml 自动创建的是纯注释占位 → 只传 acp-model.yml
    patches = [call["args"][i + 1] for i, a in enumerate(call["args"]) if a == "--patch"]
    assert len(patches) == 1
    assert patches[0].endswith("acp-model.yml")
    assert call["env"]["DSH_HOME"] == str(tmp_path / "home")
    assert call["env"]["DEEPSEEK_BASE_URL"] == "https://api.deepseek.com/v1"
    assert call["env"]["DEEPSEEK_API_KEY"] == "sk-test"
    assert call["cwd"] == str(tmp_path / "workspace")


async def test_process_patch_content(fake_acp, monkeypatch):
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    await bridge.new_session(_user(1))
    text = (settings.dsh_patches_path / "acp-model.yml").read_text(encoding="utf-8")
    assert "- id: llm-pi-ai" in text
    assert "kbcrm:" in text
    assert "api: openai-completions" in text
    assert "apiKeyEnv: DEEPSEEK_API_KEY" in text
    assert 'id: "deepseek-chat"' in text
    assert "- id: acp" in text
    assert "provider: kbcrm" in text
    assert "model: deepseek-chat" in text
    # 密钥不落 yml
    assert "sk-test" not in text


async def test_base_patch_included_once_it_has_content(fake_acp, monkeypatch):
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    base = bridge._ensure_base_patch()
    assert not bridge._has_content(base)
    base.write_text("- id: session-persistence-jsonl\n  disabled: true\n", encoding="utf-8")
    await bridge.new_session(_user(1))
    patches = [
        fake_acp.calls[0]["args"][i + 1]
        for i, a in enumerate(fake_acp.calls[0]["args"])
        if a == "--patch"
    ]
    assert len(patches) == 2
    assert patches[0].endswith("base.yml")  # base 在前、模型 patch 在后


async def test_new_session_mcp_headers_carry_user_token(fake_acp, monkeypatch):
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    await bridge.new_session(_user(3))
    mcp = fake_acp.conns[0].new_calls[0]["mcp_servers"][0]
    assert mcp.type == "http" and mcp.name == "kb"
    assert mcp.url == "http://127.0.0.1:8100/api/mcp"
    header = mcp.headers[0]
    assert header.name == "Authorization" and header.value.startswith("Bearer ")
    # 令牌是 dsh 专用 JWT（aud=dsh-mcp），每次会话激活时新签
    from app.services.mcp_server import decode_mcp_authorization

    payload = decode_mcp_authorization([(b"authorization", header.value.encode())])
    assert payload["sub"] == "3"


async def test_resume_session_activates_once(fake_acp, monkeypatch):
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    await bridge.resume_session(_user(1), "sess-x")
    await bridge.resume_session(_user(1), "sess-x")  # 已活动：不再重复 resume
    assert fake_acp.conns[0].resume_calls == ["sess-x"]


async def test_crash_self_heal_rebuilds_process(fake_acp, monkeypatch):
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    await bridge.resume_session(_user(1), "sess-x")
    # 进程崩溃：下次请求自动重建，活动会话集清空（resume 需重新执行）
    fake_acp.calls[0]["proc"].returncode = 1
    await bridge.resume_session(_user(1), "sess-x")
    assert len(fake_acp.calls) == 2
    assert fake_acp.conns[1].resume_calls == ["sess-x"]


async def test_resume_rebuilds_process_when_mcp_token_stale(fake_acp, monkeypatch):
    """长会话 MCP 令牌临期：已活动会话 resume 时重建进程并重新挂载（新签令牌）。"""
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    await bridge.resume_session(_user(1), "sess-x")
    assert len(fake_acp.calls) == 1
    # 模拟挂载时间已超过 TTL（默认 7 天）→ 临期
    bridge._mount_times["sess-x"] = time.monotonic() - 8 * 24 * 3600
    await bridge.resume_session(_user(1), "sess-x")
    assert fake_acp.cms[0].exited  # 旧进程被关闭
    assert len(fake_acp.calls) == 2  # 进程重建
    assert fake_acp.conns[1].resume_calls == ["sess-x"]  # 新进程上重新 resume（重新挂 MCP）
    assert "sess-x" in bridge._active_sessions
    # 新挂载时间被刷新，短时间内不再触发重建
    await bridge.resume_session(_user(1), "sess-x")
    assert len(fake_acp.calls) == 2
    assert fake_acp.conns[1].resume_calls == ["sess-x"]


async def test_resume_fresh_mount_no_rebuild(fake_acp, monkeypatch):
    """令牌未临期：已活动会话直接返回，不重建不重复 resume。"""
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    await bridge.resume_session(_user(1), "sess-x")
    await bridge.resume_session(_user(1), "sess-x")
    assert len(fake_acp.calls) == 1
    assert fake_acp.conns[0].resume_calls == ["sess-x"]


async def test_close_all(fake_acp, monkeypatch):
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    await bridge.new_session(_user(1))
    await bridge.close_all()
    assert fake_acp.cms[0].exited
    assert bridge._conn is None


async def test_run_turn_streams_events_then_done(fake_acp, monkeypatch):
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    sid = await bridge.new_session(_user(1))
    conn = fake_acp.conns[0]
    conn.prompt_events = [
        update_agent_message_text("你好"),
        start_tool_call("c1", "mcp__kb__kb_search", status="in_progress"),
        update_tool_call("c1", status="completed"),
    ]
    items = [item async for item in bridge.run_turn(_user(1), sid, "问题")]
    kinds = [i["kind"] for i in items]
    assert kinds == ["event", "event", "event", "done"]
    assert items[0]["event"]["session_update"] == "agent_message_chunk"
    assert items[0]["event"]["content"]["text"] == "你好"
    assert items[1]["event"]["session_update"] == "tool_call"
    assert items[-1]["final_response"] == "你好"
    assert items[-1]["finish_reason"] == "end_turn"


async def test_run_turn_error_becomes_error_item(fake_acp, monkeypatch):
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    sid = await bridge.new_session(_user(1))
    fake_acp.conns[0].prompt_error = RuntimeError("子进程崩溃")
    items = [item async for item in bridge.run_turn(_user(1), sid, "问题")]
    assert len(items) == 1
    assert items[0]["kind"] == "error"
    assert "子进程崩溃" in items[0]["detail"]


async def test_run_turn_unavailable_without_dsh_bin(fake_acp, monkeypatch):
    monkeypatch.setattr(settings, "DSH_BIN", "")
    bridge = AcpBridge()
    items = [item async for item in bridge.run_turn(_user(1), "s", "问题")]
    assert items[0]["kind"] == "error"
    assert "DSH_BIN" in items[0]["detail"]


# ---------------------------------------------------------------------------
# 权限应答策略：mcp__kb__ 前缀自动 allow，其余一律拒绝
# ---------------------------------------------------------------------------


def _options():
    return [
        SimpleNamespace(option_id="allow-once", name="Allow once", kind="allow_once"),
        SimpleNamespace(option_id="reject-once", name="Reject", kind="reject_once"),
    ]


async def test_permission_kb_tools_auto_allowed():
    handler = acp_bridge._ClientHandler()
    resp = await handler.request_permission(
        "s", SimpleNamespace(title="mcp__kb__kb_search"), _options()
    )
    assert resp.outcome.outcome == "selected"
    assert resp.outcome.option_id == "allow-once"


async def test_permission_readonly_whitelist_all_allowed():
    """只读白名单内的工具全部自动放行。"""
    handler = acp_bridge._ClientHandler()
    for tool in (
        "kb_search", "kb_read_doc", "kb_list",
        "crm_list_customers", "crm_search_customers", "crm_get_customer",
        "crm_list_followups", "crm_list_opportunities", "crm_list_tasks",
        "crm_stats", "skill_list",
        "memory_save", "memory_list", "memory_search", "memory_delete",
    ):
        resp = await handler.request_permission(
            "s", SimpleNamespace(title=f"mcp__kb__{tool}"), _options()
        )
        assert resp.outcome.option_id == "allow-once", tool


async def test_permission_side_effect_tools_rejected():
    """写工具 / skill_call / web_search / web_fetch 即使在 mcp__kb__ 前缀下也不放行。"""
    handler = acp_bridge._ClientHandler()
    for title in (
        "mcp__kb__crm_create_customer",
        "mcp__kb__crm_update_customer",
        "mcp__kb__crm_delete_customer",
        "mcp__kb__crm_create_opportunity",
        "mcp__kb__crm_create_task",
        "mcp__kb__crm_add_followup",
        "mcp__kb__mail_draft_create",
        "mcp__kb__skill_create_api",
        "mcp__kb__skill_call",
        "mcp__kb__web_search",
        "mcp__kb__web_fetch",
    ):
        resp = await handler.request_permission("s", SimpleNamespace(title=title), _options())
        assert resp.outcome.option_id == "reject-once", title


async def test_permission_other_tools_rejected():
    handler = acp_bridge._ClientHandler()
    resp = await handler.request_permission("s", SimpleNamespace(title="bash"), _options())
    assert resp.outcome.outcome == "selected"
    assert resp.outcome.option_id == "reject-once"


async def test_permission_reject_fallback_to_cancelled():
    """选项里没有 reject-once 时取消整个请求。"""
    handler = acp_bridge._ClientHandler()
    options = [SimpleNamespace(option_id="allow-once", name="Allow once", kind="allow_once")]
    resp = await handler.request_permission("s", SimpleNamespace(title="bash"), options)
    assert resp.outcome.outcome == "cancelled"


async def test_session_update_dispatch_by_session_id():
    handler = acp_bridge._ClientHandler()
    q1 = handler.subscribe("s1")
    q2 = handler.subscribe("s2")
    await handler.session_update("s1", update_agent_message_text("给s1"))
    assert not q1.empty() and q2.empty()
    assert q1.get_nowait()["content"]["text"] == "给s1"
    handler.unsubscribe("s1", q1)
    await handler.session_update("s1", update_agent_message_text("无人接收"))  # 不报错


# ---------------------------------------------------------------------------
# 模型配置解析
# ---------------------------------------------------------------------------


class _FakeDbCtx:
    """AsyncSessionLocal 假实现：返回预设 chat 模型（或抛错模拟 DB 不可用）。"""

    def __init__(self, model=None, error=None):
        self._model = model
        self._error = error

    async def __aenter__(self):
        if self._error is not None:
            raise self._error
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self._model)


async def test_resolve_chat_model_db_api_provider(monkeypatch):
    bridge = AcpBridge()
    model = SimpleNamespace(provider="api", model="deepseek-chat", base_url="https://x/v1", api_key="enc")
    monkeypatch.setattr(acp_bridge, "AsyncSessionLocal", lambda: _FakeDbCtx(model))
    monkeypatch.setattr(acp_bridge, "decrypt_secret", lambda s: "sk-real")
    assert await bridge._resolve_chat_model(1) == ("deepseek-chat", "https://x/v1", "sk-real")


async def test_resolve_chat_model_db_ollama_provider(monkeypatch):
    bridge = AcpBridge()
    model = SimpleNamespace(provider="ollama", model="qwen2.5:7b", base_url="http://localhost:11434", api_key=None)
    monkeypatch.setattr(acp_bridge, "AsyncSessionLocal", lambda: _FakeDbCtx(model))
    name, base_url, key = await bridge._resolve_chat_model(1)
    assert name == "qwen2.5:7b"
    assert base_url == "http://localhost:11434/v1"  # 换算 OpenAI 兼容端点
    assert key == "ollama"


async def test_resolve_chat_model_fallback_to_env(monkeypatch):
    bridge = AcpBridge()
    monkeypatch.setattr(acp_bridge, "AsyncSessionLocal", lambda: _FakeDbCtx(model=None))
    monkeypatch.setattr(settings, "LLM_CHAT_PROVIDER", "api")
    monkeypatch.setattr(settings, "LLM_API_CHAT_MODEL", "deepseek-chat")
    monkeypatch.setattr(settings, "LLM_API_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-env")
    assert await bridge._resolve_chat_model(1) == (
        "deepseek-chat", "https://api.deepseek.com/v1", "sk-env",
    )


async def test_resolve_chat_model_db_error_fallback(monkeypatch):
    bridge = AcpBridge()
    monkeypatch.setattr(acp_bridge, "AsyncSessionLocal", lambda: _FakeDbCtx(error=RuntimeError("DB down")))
    monkeypatch.setattr(settings, "LLM_CHAT_PROVIDER", "api")
    result = await bridge._resolve_chat_model(1)
    assert result[0] == settings.LLM_API_CHAT_MODEL


async def test_unavailable_when_acp_missing(monkeypatch):
    monkeypatch.setattr(acp_bridge, "acp", None)
    bridge = AcpBridge()
    assert not bridge.available
    with pytest.raises(AcpUnavailable):
        await bridge._get_conn(1)


# ---------------------------------------------------------------------------
# 并发与超时：会话锁串行化 prompt、spawn 超时回收
# ---------------------------------------------------------------------------


async def test_run_turn_serializes_concurrent_prompts(fake_acp, monkeypatch):
    """同一会话并发 run_turn：第二个 prompt 必须等第一个结束才开始（会话锁串行化）。"""
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    sid = await bridge.new_session(_user(1))
    conn = fake_acp.conns[0]
    started: list[str] = []
    gate = asyncio.Event()
    orig_prompt = conn.prompt

    async def _gated_prompt(session_id, prompt, **kw):
        started.append(getattr(prompt[0], "text", str(prompt)))
        if len(started) == 1:
            await gate.wait()  # 第一个 prompt 挂住，模拟长轮
        return await orig_prompt(session_id, prompt, **kw)

    conn.prompt = _gated_prompt

    async def _consume(q):
        return [i async for i in bridge.run_turn(_user(1), sid, q)]

    t1 = asyncio.create_task(_consume("第一问"))
    await asyncio.sleep(0.1)  # 等第一个 prompt 进入并被挂住
    t2 = asyncio.create_task(_consume("第二问"))
    await asyncio.sleep(0.3)
    assert started == ["第一问"]  # 第二个 prompt 被会话锁挡住，未并发进入
    gate.set()
    r1, r2 = await asyncio.gather(t1, t2)
    assert started == ["第一问", "第二问"]
    assert r1[-1]["kind"] == "done" and r2[-1]["kind"] == "done"


async def test_resume_session_concurrent_only_once(fake_acp, monkeypatch):
    """并发 resume 同一会话：会话锁兜底，只真正 resume 一次。"""
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    await asyncio.gather(
        bridge.resume_session(_user(1), "sess-x"),
        bridge.resume_session(_user(1), "sess-x"),
        bridge.resume_session(_user(1), "sess-x"),
    )
    assert fake_acp.conns[0].resume_calls == ["sess-x"]


async def test_spawn_timeout_recovers_half_started_process(fake_acp, monkeypatch):
    """spawn/initialize 挂起：超时后报错并尽力回收半启动进程，连接状态不留半成品。"""
    monkeypatch.setattr(acp_bridge, "_SPAWN_TIMEOUT_SECONDS", 0.05)

    class _HangCM:
        def __init__(self):
            self.exited = False

        async def __aenter__(self):
            await asyncio.sleep(3600)

        async def __aexit__(self, *args):
            self.exited = True

    hang_cm = _HangCM()
    monkeypatch.setattr(fake_acp, "spawn_agent_process", lambda *a, **kw: hang_cm)
    bridge = AcpBridge()
    monkeypatch.setattr(AcpBridge, "_resolve_chat_model", _fake_model_cfg)
    with pytest.raises(asyncio.TimeoutError):
        await bridge.new_session(_user(1))
    assert hang_cm.exited  # 半启动进程被回收
    assert bridge._conn is None
