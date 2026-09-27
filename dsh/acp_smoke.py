"""dsh ACP 冒烟脚本（阶段 2：ACP 切换验证）。

验证链路：agent-client-protocol（PyPI）→ `dsh --profile acp`（stdio）→
session/new（mcpServers 动态挂知识库 MCP，headers 带 dsh-mcp 令牌）→
prompt 触发 kb_search → 杀进程 → 重起 → session/resume → 追问验证上下文保留。

运行方式（仓库根目录）：
    backend/.venv/Scripts/python dsh/acp_smoke.py

前置条件：
- 后端 uvicorn 在 8100 运行（/api/mcp 可用）；
- 开发库在 localhost:5433（base.yml 的 DSH_PG_URL 兜底值）；
- acp profile 已注册 PG 插件：dsh plugin --profile acp add link:../dsh/session-persistence-pg
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import acp  # noqa: E402
from acp.schema import (  # noqa: E402
    AllowedOutcome,
    ClientCapabilities,
    DeniedOutcome,
    HttpHeader,
    HttpMcpServer,
    RequestPermissionResponse,
)
from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.security import create_mcp_token  # noqa: E402
from app.database import AsyncSessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.dsh_bridge import DshBridge  # noqa: E402

DSH_BIN = r"D:\AI\CRMnKB\dsh\runtime\node_modules\.bin\dsh.cmd"
DSH_HOME = r"D:\AI\CRMnKB\dsh\home"
WORKSPACE = REPO_ROOT / "backend" / "data" / "dsh" / "workspace"
MCP_URL = "http://127.0.0.1:8100/api/mcp"

PROMPT_1 = "请调用 kb_search 工具，用关键词「客户」搜索知识库，然后用一句话告诉我搜到了几条结果。"
PROMPT_2 = "我刚才让你搜索的关键词是什么？请直接回答关键词本身。"


def make_model_patch(model_name: str, path: Path) -> Path:
    """生成进程级模型 patch：llm-pi-ai kbcrm 路由 + acp 行 provider/model 覆盖。"""
    path.write_text(
        "# ACP 冒烟：进程级静态模型 patch（llm-pi-ai 路由 + acp 默认 provider/model）\n"
        "- id: llm-pi-ai\n"
        "  config:\n"
        "    providers:\n"
        "      kbcrm:\n"
        "        displayName: CRMnKB 模型\n"
        "        api: openai-completions\n"
        '        baseURL: !!js "process.env.DEEPSEEK_BASE_URL ?? \'\'"\n'
        "        apiKeyEnv: DEEPSEEK_API_KEY\n"
        "        models:\n"
        f'          - id: "{model_name}"\n'
        "            contextWindow: 131072\n"
        "- id: acp\n"
        "  config:\n"
        "    provider: kbcrm\n"
        f"    model: {model_name}\n",
        encoding="utf-8",
    )
    return path


class SmokeClient:
    """ACP client 回调：打印 session/update；权限应答——kb 只读工具 allow，其余 reject。"""

    def __init__(self, label: str) -> None:
        self.label = label
        self.allow_count = 0
        self.reject_count = 0
        self.chunks: list[str] = []

    async def session_update(self, session_id: str, update, **kwargs) -> None:
        kind = getattr(update, "session_update", type(update).__name__)
        if kind == "agent_message_chunk":
            text = getattr(update.content, "text", "")
            self.chunks.append(text)
            print(f"[{self.label}] token: {text!r}")
        elif kind in ("tool_call", "tool_call_update"):
            title = getattr(update, "title", None)
            status = getattr(update, "status", None)
            print(f"[{self.label}] {kind}: title={title!r} status={status!r}")
        else:
            print(f"[{self.label}] update: {kind}")

    async def request_permission(self, session_id: str, tool_call, options, **kwargs) -> RequestPermissionResponse:
        title = getattr(tool_call, "title", "") or ""
        raw = getattr(tool_call, "raw_input", None)
        print(f"[{self.label}] request_permission: title={title!r} raw_input={raw!r}")
        allow = title.startswith("mcp__kb__") or "kb_search" in title or "kb_read_doc" in title
        if allow:
            self.allow_count += 1
            option_id = next((o.option_id for o in options if o.option_id == "allow-once"), options[0].option_id)
            print(f"[{self.label}]   -> allow ({option_id})")
            return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=option_id))
        self.reject_count += 1
        print(f"[{self.label}]   -> REJECT (cancelled)")
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


async def run_round(label: str, patches: list[str], env: dict[str, str], token: str, act) -> None:
    """起一个 dsh ACP 进程，执行 act(conn)，退出时 stdin EOF 让进程自尽。"""
    args = ["--profile", "acp"]
    for p in patches:
        args += ["--patch", p]
    client = SmokeClient(label)
    async with acp.spawn_agent_process(client, DSH_BIN, *args, env=env, cwd=str(WORKSPACE)) as (conn, proc):
        init = await conn.initialize(protocol_version=acp.PROTOCOL_VERSION, client_capabilities=ClientCapabilities())
        print(f"[{label}] initialize ok: agent={init.agent_info.name if init.agent_info else '?'} "
              f"capabilities={init.agent_capabilities}")
        mcp = [HttpMcpServer(type="http", name="kb", url=MCP_URL,
                             headers=[HttpHeader(name="Authorization", value=f"Bearer {token}")])]
        await act(conn, client, mcp)
    print(f"[{label}] 进程已退出 rc={proc.returncode}")


async def main() -> None:
    # 1. 模型配置：DB 默认 chat 模型（复用桥接层解析逻辑）
    model_name, base_url, api_key = await DshBridge()._resolve_chat_model(tenant_id=1)
    print(f"模型: {model_name} @ {base_url}")

    # 2. admin 的 dsh-mcp 令牌
    async with AsyncSessionLocal() as s:
        admin = (await s.execute(select(User).where(User.username == "admin"))).scalar_one()
    token = create_mcp_token(admin.id, admin.username, 60)

    # 3. patch：base.yml（PG 持久化）+ 进程级模型 patch
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    model_patch = make_model_patch(model_name, REPO_ROOT / "dsh" / "patches" / "acp-model.yml")
    patches = [str(REPO_ROOT / "backend" / "data" / "dsh" / "patches" / "base.yml"), str(model_patch)]
    env = {
        "DSH_HOME": DSH_HOME,
        "DEEPSEEK_BASE_URL": base_url,
        "DEEPSEEK_API_KEY": api_key,
    }

    # 4. 第一轮：session/new + prompt（要求调 kb_search）
    session_id: str | None = None

    async def round1(conn, client: SmokeClient, mcp):
        nonlocal session_id
        resp = await conn.new_session(cwd=str(WORKSPACE), mcp_servers=mcp)
        session_id = resp.session_id
        print(f"[round1] session/new -> {session_id}")
        pr = await conn.prompt(session_id=session_id, prompt=[acp.text_block(PROMPT_1)])
        print(f"[round1] prompt done: stop_reason={pr.stop_reason}")
        print(f"[round1] 完整回答: {''.join(client.chunks)!r}")

    await run_round("round1", patches, env, token, round1)
    assert session_id, "未拿到 session_id"

    # 5. 第二轮：新进程 resume 同一会话，追问验证上下文保留
    async def round2(conn, client: SmokeClient, mcp):
        resp = await conn.resume_session(session_id=session_id, cwd=str(WORKSPACE), mcp_servers=mcp)
        print(f"[round2] session/resume ok: config_options={resp.config_options}")
        pr = await conn.prompt(session_id=session_id, prompt=[acp.text_block(PROMPT_2)])
        print(f"[round2] prompt done: stop_reason={pr.stop_reason}")
        print(f"[round2] 完整回答: {''.join(client.chunks)!r}")

    await run_round("round2", patches, env, token, round2)
    print("\n冒烟完成：session id =", session_id)


if __name__ == "__main__":
    asyncio.run(main())
