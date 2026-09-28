"""dsh ACP 桥接服务（dsh 基座实施方案阶段 2：ACP 切换）。

与阶段 1（Python SDK，stdio JSON-RPC 私有协议）的差异：
- 传输换成标准 ACP v1（PyPI 包 agent-client-protocol，asyncio 原生），
  dsh 以 `dsh --profile acp` 启动，stdin EOF 即退出。
- 进程模型从"每用户一个 dsh 进程 + 每用户 patch yml 注入 MCP/Bearer"改为
  "单租户单 dsh 进程 + 每会话 session/new|resume 动态挂 MCP（headers 携带
  该用户的 dsh-mcp 令牌）"：令牌不落盘；每次会话激活时新签，天然解决过期，
  进程不再因令牌临期而重建。
- 会话可跨进程恢复：session/resume + PG 会话持久化插件（dsh_session_* 两表），
  后端重启/进程崩溃后 agent 上下文不丢（阶段 1 的 SDK 不支持跨进程恢复）。
- 权限应答：dsh 审批链经 session/request_permission 反向请求客户端——
  mcp__kb__ 前缀（知识库只读工具）自动 allow-once，其余一律拒绝
  （写操作本就走后端 agent_approvals 审批链，不经 dsh 执行）。

模型路由：进程级静态 patch（patches/acp-model.yml，进程启动时按 DB 默认 chat
模型重写）注册 llm-pi-ai 的 kbcrm 路由（openai-completions，baseURL/apiKeyEnv
读子进程环境变量 DEEPSEEK_BASE_URL/DEEPSEEK_API_KEY，密钥不落 yml），并覆盖
acp 行的默认 provider/model。注意 llm-pi-ai 拒绝未在 models 列表声明的模型 id，
因此 DB 模型变更要在进程重建后才生效（进程崩溃自愈或后端重启时自然重建）。

会话 cwd：所有会话共用 DSH_WORKSPACE_ROOT（session/resume 要求 cwd 与创建时
realpath 一致，按用户分目录会导致无法恢复）。
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.core.crypto import decrypt_secret
from app.core.security import create_mcp_token
from app.database import AsyncSessionLocal
from app.models.llm_model import LlmModel
from app.models.user import User

logger = logging.getLogger(__name__)

try:
    import acp
    from acp.schema import (
        AllowedOutcome,
        ClientCapabilities,
        DeniedOutcome,
        HttpHeader,
        HttpMcpServer,
        RequestPermissionResponse,
    )
except ImportError:  # agent-client-protocol 未安装时 agent 功能整体不可用
    acp = None
    AllowedOutcome = ClientCapabilities = DeniedOutcome = None
    HttpHeader = HttpMcpServer = RequestPermissionResponse = None


class AcpUnavailable(Exception):
    """dsh ACP 运行环境不可用（agent-client-protocol 未安装、DSH_BIN 未配置等）。"""


class _ClientHandler:
    """ACP client 回调集合。

    - session_update：按 session_id 分发到 run_turn 注册的订阅队列
      （pydantic 模型转 snake_case dict 后投递）。
    - request_permission：dsh 审批链的反向请求。mcp__kb__ 前缀（知识库
      只读工具）自动 allow-once；其余一律拒绝并记日志——写操作走后端
      agent_approvals 审批链，不允许 dsh 直接执行。
    - fs/terminal 等 client 能力未声明（initialize 时 capabilities 全默认关），
      dsh 不会发起对应反向请求。
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(session_id, set()).add(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(session_id)
        if subs is None:
            return
        subs.discard(q)
        if not subs:
            self._subscribers.pop(session_id, None)

    async def session_update(self, session_id: str, update, **kwargs) -> None:
        subs = self._subscribers.get(session_id)
        if not subs:
            return
        event = update.model_dump(exclude_none=True) if hasattr(update, "model_dump") else update
        for q in subs:
            q.put_nowait(event)

    async def request_permission(self, session_id: str, tool_call, options, **kwargs) -> "RequestPermissionResponse":
        title = getattr(tool_call, "title", "") or ""
        if title.startswith("mcp__kb__"):
            option_id = next(
                (o.option_id for o in options if o.option_id == "allow-once"),
                options[0].option_id if options else "allow-once",
            )
            logger.info("dsh 权限应答：自动允许知识库只读工具 %s", title)
            return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=option_id))
        # 其余一律拒绝：优先 reject-once，没有该选项则取消（dsh 侧映射为 rejected/cancelled）
        reject_id = next((o.option_id for o in options if o.option_id == "reject-once"), None)
        logger.warning("dsh 权限应答：拒绝工具 %s（仅放行 mcp__kb__ 只读工具）", title)
        if reject_id is not None:
            return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=reject_id))
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


class AcpBridge:
    """单租户单 dsh ACP 进程（懒启动，崩溃自愈，shutdown 时 close_all）。"""

    def __init__(self) -> None:
        self._handler = _ClientHandler()
        self._cm = None  # spawn_agent_process 的异步上下文管理器（持有以便退出时关闭 stdin）
        self._conn = None  # ClientSideConnection
        self._proc = None  # asyncio.subprocess.Process
        # 当前进程内已激活（session/new 或 resume 成功）的会话；
        # 进程重建时清空（dsh 不允许 resume 内存中已活动的会话）
        self._active_sessions: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return acp is not None

    # ------------------------------------------------------------------
    # patch yml
    # ------------------------------------------------------------------

    def _patches_dir(self) -> Path:
        d = settings.dsh_patches_path
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _ensure_base_patch(self) -> Path:
        """base.yml：PG 会话持久化的静态 patch（开发机已落地；生产由 entrypoint 播种）。"""
        p = self._patches_dir() / "base.yml"
        if not p.exists():
            p.write_text(
                "# dsh 基础 patch（静态，随 ACP 进程一并加载）。\n"
                "# 预留给 PG 会话持久化插件行：禁用 session-persistence-jsonl，\n"
                "# 换 @kbcrm/dsh-session-persistence-pg（databaseUrl 用 DSH_PG_URL 覆盖）。\n"
                "# 生产镜像由 docker-entrypoint.sh 播种母版（docker/dsh-base.yml）。\n",
                encoding="utf-8",
            )
        return p

    @staticmethod
    def _has_content(path: Path) -> bool:
        """判断 patch yml 是否有有效行（忽略空行与注释），纯注释文件不传给 dsh。"""
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    return True
        except OSError:
            return False
        return False

    def _write_process_patch(self, model_name: str) -> Path:
        """生成/刷新进程级模型 patch（acp-model.yml）：llm-pi-ai kbcrm 路由 + acp 默认模型。

        每次进程启动时按 DB 默认 chat 模型重写。llm-pi-ai 拒绝未在 models 列表
        声明的模型 id（自定义 openai-completions 路由必须显式列出），因此 DB 模型
        变更要进程重建后才生效。baseURL/apiKeyEnv 经 !!js 在 dsh 进程内读环境变量，
        密钥不落 yml 文件。
        """
        p = self._patches_dir() / "acp-model.yml"
        p.write_text(
            "# 由 acp_bridge 自动生成的进程级模型 patch（请勿手改，进程重建时覆盖）\n"
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
            f"    model: {model_name}\n"
            # dsh 内置 web_search 走 deepseek 官方 Anthropic 端点，与聊天的 llm-pi-ai
            # 网关 key 不通用（401 WEB_PROVIDER_ERROR）。联网能力由知识库 MCP server 的
            # mcp__kb__web_search / web_fetch 提供（复用管理端 Skill 配置），这里整体禁用。
            "- id: tool-web\n"
            "  disabled: true\n",
            encoding="utf-8",
        )
        return p

    # ------------------------------------------------------------------
    # 模型配置
    # ------------------------------------------------------------------

    async def _resolve_chat_model(self, tenant_id: int) -> tuple[str, str, str]:
        """取当前租户 chat 模型（model, base_url, api_key）：DB 注册模型优先，回退 .env。

        与 llm/factory.py 同一口径（is_default 优先，无默认取最新）；
        Ollama 模型换算成其 OpenAI 兼容端点（/v1），api_key 用占位值。
        """
        try:
            async with AsyncSessionLocal() as session:
                stmt = (
                    select(LlmModel)
                    .where(
                        LlmModel.enabled.is_(True),
                        LlmModel.model_type == "chat",
                        LlmModel.tenant_id == tenant_id,
                    )
                    .order_by(LlmModel.is_default.desc(), LlmModel.created_at.desc())
                    .limit(1)
                )
                model = (await session.execute(stmt)).scalar_one_or_none()
                if model is not None:
                    if model.provider == "ollama":
                        return model.model, model.base_url.rstrip("/") + "/v1", "ollama"
                    return model.model, model.base_url, decrypt_secret(model.api_key) or ""
        except Exception as exc:
            logger.debug("DB 模型读取失败，回退 .env 配置: %s", exc)
        if settings.LLM_CHAT_PROVIDER == "api":
            return settings.LLM_API_CHAT_MODEL, settings.LLM_API_BASE_URL, settings.LLM_API_KEY
        return settings.OLLAMA_CHAT_MODEL, settings.OLLAMA_BASE_URL.rstrip("/") + "/v1", "ollama"

    # ------------------------------------------------------------------
    # 进程生命周期
    # ------------------------------------------------------------------

    def _workspace(self) -> Path:
        """所有会话共用的 cwd（session/resume 要求 cwd 与创建时 realpath 一致）。"""
        p = settings.dsh_workspace_path
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _kb_mcp_server(self, user: User) -> "HttpMcpServer":
        """每会话动态挂载的知识库 MCP：headers 携带该用户新签的 dsh-mcp 令牌。"""
        token = create_mcp_token(
            user.id, user.username, settings.DSH_MCP_TOKEN_EXPIRE_MINUTES
        )
        return HttpMcpServer(
            type="http",
            name="kb",
            url=settings.DSH_MCP_URL,
            headers=[HttpHeader(name="Authorization", value=f"Bearer {token}")],
        )

    async def _get_conn(self, tenant_id: int):
        """取（或懒启动/重建）dsh ACP 进程连接。进程已退出（崩溃/被杀）时自动重建——
        会话历史在 PG，重建后 resume 即可续跑。"""
        if acp is None:
            raise AcpUnavailable("未安装 agent-client-protocol，dsh agent 不可用")
        if not settings.DSH_BIN:
            raise AcpUnavailable("未配置 DSH_BIN（Windows 指 dsh.cmd shim，Linux 指无后缀 shim）")
        async with self._lock:
            self._discard_if_dead()
            if self._conn is not None:
                return self._conn
            model_name, base_url, api_key = await self._resolve_chat_model(tenant_id)
            patches: list[str] = []
            base_patch = self._ensure_base_patch()
            if self._has_content(base_patch):
                patches.append(str(base_patch))
            patches.append(str(self._write_process_patch(model_name)))
            args = ["--profile", "acp"]
            for p in patches:
                args += ["--patch", p]
            env = {
                "DSH_HOME": str(settings.dsh_home_path),
                "DEEPSEEK_BASE_URL": base_url,
                "DEEPSEEK_API_KEY": api_key,
            }
            # spawn_stdio_transport 只按白名单继承少量环境变量，DSH_PG_URL 需显式透传
            if os.environ.get("DSH_PG_URL"):
                env["DSH_PG_URL"] = os.environ["DSH_PG_URL"]
            dsh_home = settings.dsh_home_path
            dsh_home.mkdir(parents=True, exist_ok=True)
            cm = acp.spawn_agent_process(
                self._handler, settings.DSH_BIN, *args, env=env, cwd=str(self._workspace())
            )
            conn, proc = await cm.__aenter__()
            await conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
            )
            self._cm, self._conn, self._proc = cm, conn, proc
            logger.info("dsh ACP 进程已启动（pid=%s，模型 %s）", proc.pid, model_name)
            return conn

    def _discard_if_dead(self) -> None:
        """进程已退出则丢弃连接状态（下次 _get_conn 重建）。"""
        if self._proc is not None and self._proc.returncode is not None:
            logger.warning("dsh ACP 进程已退出（rc=%s），下次请求自动重建", self._proc.returncode)
            self._cm = self._conn = self._proc = None
            self._active_sessions.clear()

    async def _close_locked(self) -> None:
        cm = self._cm
        self._cm = self._conn = self._proc = None
        self._active_sessions.clear()
        if cm is not None:
            try:
                # 退出异步上下文：关闭 stdin（EOF），dsh 收到后有界退出
                await cm.__aexit__(None, None, None)
            except Exception as exc:
                logger.warning("dsh ACP 进程关闭失败（忽略）: %s", exc)

    async def close_all(self) -> None:
        """关闭 dsh ACP 子进程（FastAPI shutdown 时调用）。"""
        async with self._lock:
            await self._close_locked()

    # ------------------------------------------------------------------
    # 会话激活（session/new | session/resume）
    # ------------------------------------------------------------------

    async def new_session(self, user: User) -> str:
        """新建 dsh 会话并挂载该用户的知识库 MCP，返回 dsh 分配的 session_id。"""
        conn = await self._get_conn(user.tenant_id)
        resp = await conn.new_session(
            cwd=str(self._workspace()), mcp_servers=[self._kb_mcp_server(user)]
        )
        self._active_sessions.add(resp.session_id)
        return resp.session_id

    async def resume_session(self, user: User, session_id: str) -> None:
        """恢复既有 dsh 会话（跨进程：事件历史由 PG 持久化插件提供）。

        会话在本进程内已活动时直接返回（dsh 拒绝重复 resume）；
        会话不存在或 cwd 不匹配时由 dsh 抛错，调用方负责退回 new_session。
        注意必须先 _get_conn：进程若已崩溃，_get_conn 重建时会清空活动集，
        先查活动集会错过重建。
        """
        conn = await self._get_conn(user.tenant_id)
        if session_id in self._active_sessions:
            return
        await conn.resume_session(
            session_id=session_id,
            cwd=str(self._workspace()),
            mcp_servers=[self._kb_mcp_server(user)],
        )
        self._active_sessions.add(session_id)

    # ------------------------------------------------------------------
    # 运行一轮 agent 对话
    # ------------------------------------------------------------------

    async def run_turn(self, user: User, dsh_session_id: str, question: str):
        """运行一轮 agent 对话，异步迭代器逐个产出规范化事件：

        - {"kind": "event", "event": <ACP session/update 的 snake_case dict>}
        - {"kind": "done", "final_response": str, "finish_reason": str | None}
        - {"kind": "error", "detail": str}（运行失败统一收口，产出后结束）

        调用前必须已通过 new_session/resume_session 激活会话。
        事件经 _ClientHandler 按 session_id 分发到本轮的订阅队列。
        """
        try:
            conn = await self._get_conn(user.tenant_id)
        except Exception as exc:
            yield {"kind": "error", "detail": f"dsh 进程不可用: {exc}"}
            return
        queue = self._handler.subscribe(dsh_session_id)
        parts: list[str] = []
        prompt_task = asyncio.create_task(
            conn.prompt(session_id=dsh_session_id, prompt=[acp.text_block(question)])
        )
        try:
            while True:
                if prompt_task.done() and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue
                if event.get("session_update") == "agent_message_chunk":
                    content = event.get("content") or {}
                    if content.get("type") == "text":
                        parts.append(content.get("text") or "")
                yield {"kind": "event", "event": event}
            try:
                resp = prompt_task.result()
            except Exception as exc:
                logger.warning("dsh ACP 运行失败（会话 %s）: %s", dsh_session_id, exc)
                self._discard_if_dead()  # 连接错误多半是进程已死，触发下次重建
                yield {"kind": "error", "detail": f"dsh 运行失败: {exc}"}
                return
            yield {
                "kind": "done",
                "final_response": "".join(parts),
                "finish_reason": getattr(resp, "stop_reason", None),
            }
        finally:
            self._handler.unsubscribe(dsh_session_id, queue)
            if not prompt_task.done():
                # 客户端断连停止消费：取消等待并通知 dsh 取消本轮，避免空跑
                prompt_task.cancel()
                try:
                    await conn.cancel(dsh_session_id)
                except Exception:
                    pass


# 全局实例（main.py lifespan 关闭时 close_all）
bridge = AcpBridge()
