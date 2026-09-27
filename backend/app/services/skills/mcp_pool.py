"""PR-F：MCP 连接池。

每个 MCP server 一个长连接（stdio 子进程或 SSE 客户端），
懒连接（首次 call 时才 connect）+ TTL 复用 + 断开自动重连。

不在此处做工具发现与注册，由 mcp_discovery.py 负责。
"""
import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

import mcp.types as mcp_types

logger = logging.getLogger(__name__)

# 协议版本兼容补丁：部分官方 Node MCP 服务器（2026 版）不认识 SDK 的
# LATEST_PROTOCOL_VERSION（2025-11-25），收到 initialize 不回应导致握手卡死。
# 统一协商到各服务器普遍支持的 2025-06-18（调用时按模块属性解析，改这里即生效）。
if mcp_types.LATEST_PROTOCOL_VERSION == "2025-11-25":
    mcp_types.LATEST_PROTOCOL_VERSION = "2025-06-18"


class McpConnectionPool:
    """进程内单例。"""

    def __init__(self) -> None:
        # server_id -> {"session": Any, "transport_ctx": Any, "transport": str, "lock": asyncio.Lock}
        self._connections: dict[int, dict[str, Any]] = {}

    @asynccontextmanager
    async def get_session(self, server_id: int, transport: str, config: dict):
        """懒连接：获取一个可用的 MCP ClientSession。

        用法：
            async with pool.get_session(server_id, transport, config) as session:
                tools = await session.list_tools()
                result = await session.call_tool(name, args)

        长连接在池内保持（transport 上下文不退出），断开/异常时清理缓存，
        下次调用会重建。同一 server 的并发调用用 per-server 锁串行化，
        避免共享 JSON-RPC 流被并发写坏。
        """
        entry = self._connections.get(server_id)
        if entry is None or entry.get("transport") != transport:
            # 新连接或 transport 变了：先丢弃旧连接，再建新 session
            if entry is not None:
                await self.close(server_id)
            entry = await self._create_entry(transport, config)
            self._connections[server_id] = entry
        async with entry["lock"]:
            try:
                yield entry["session"]
            except Exception:
                entry["broken"] = True
                await self.close(server_id)
                raise

    async def _create_entry(self, transport: str, config: dict) -> dict:
        """建立并保持一个连接：transport 上下文（stdio 子进程 / SSE 客户端）随 entry 存活，
        直到 close() 才 __aexit__——避免"用完即退"导致缓存的是已关闭的死连接。"""
        from mcp import ClientSession

        if transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=config.get("command", ""),
                args=config.get("args") or [],
                env=config.get("env"),
            )
            cm = stdio_client(params)
        elif transport == "sse":
            from mcp.client.sse import sse_client

            url = config.get("url", "")
            headers = dict(config.get("headers") or {})
            if config.get("auth_token"):
                headers["Authorization"] = f"Bearer {config['auth_token']}"
            cm = sse_client(url, headers=headers)
        else:
            raise ValueError(f"不支持的 MCP transport: {transport}")

        read, write = await cm.__aenter__()
        try:
            session = ClientSession(read, write)
            # 必须进入 session：__aenter__ 才启动接收循环（receive loop），
            # 否则 initialize() 发出的请求永远等不到响应（之前 connect 卡死即此原因）
            await session.__aenter__()
            await session.initialize()
        except Exception:
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                pass
            try:
                await cm.__aexit__(*sys.exc_info())
            except Exception:
                pass
            raise
        return {
            "session": session,
            "exit_cm": cm,  # 持有 context manager，close() 时才退出（进程/连接保持存活）
            "transport": transport,
            "lock": asyncio.Lock(),
            "broken": False,
        }

    async def close(self, server_id: int) -> None:
        """关闭单个 server 连接：先退出 session（停接收循环），再退出 transport（终止子进程/断 SSE）。"""
        entry = self._connections.pop(server_id, None)
        if entry is None:
            return
        session = entry.get("session")
        if session is not None:
            try:
                await session.__aexit__(None, None, None)
            except Exception as exc:
                logger.debug("MCP server %s session 退出失败（忽略）: %s", server_id, exc)
        exit_cm = entry.get("exit_cm")
        if exit_cm is not None:
            try:
                await exit_cm.__aexit__(None, None, None)
            except Exception as exc:
                logger.debug("MCP server %s transport close 失败（忽略）: %s", server_id, exc)

    async def close_all(self) -> None:
        """关闭所有连接（应用关闭时调用）。"""
        for sid in list(self._connections.keys()):
            await self.close(sid)


_pool: McpConnectionPool | None = None
_pool_lock = asyncio.Lock()


def get_mcp_pool() -> McpConnectionPool:
    """获取进程内单例（懒初始化）。"""
    global _pool
    if _pool is None:
        _pool = McpConnectionPool()
    return _pool