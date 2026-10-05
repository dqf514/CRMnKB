import asyncio
import json

import httpx

from app.config import settings
from app.services.llm.base import LLMService

# httpx 客户端池化：按事件循环复用一个 AsyncClient（参照 mcp_pool 的进程内单例模式）。
# 键为事件循环——pytest 每个用例独立 loop、asyncio.run 每次新建 loop，
# 跨 loop 复用同一 client 会触发 anyio "attached to a different loop"，故按 loop 分桶。
# 客户端不设 base_url（各方法用绝对 URL，一个池可服务任意模型端点），
# 超时一律逐请求传入，client 级 timeout 仅兜底。
_clients: dict[asyncio.AbstractEventLoop, httpx.AsyncClient] = {}


def _get_client() -> httpx.AsyncClient:
    """取当前事件循环的共享 AsyncClient（懒创建）。

    构造时引用 httpx.AsyncClient 而非局部 import 的类，
    保证测试里 monkeypatch httpx.AsyncClient（MockTransport）仍然生效。
    """
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=300,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
        _clients[loop] = client
    return client


async def close_http_clients() -> None:
    """关闭全部池化 AsyncClient（应用 lifespan 关闭时调用）。"""
    for loop, client in list(_clients.items()):
        _clients.pop(loop, None)
        try:
            await client.aclose()
        except Exception:  # 跨 loop 关闭可能报错，忽略
            pass


class ApiLLM(LLMService):
    """OpenAI 兼容接口（DeepSeek / OpenAI 等）。"""

    provider_name = "api"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        chat_model: str,
        embed_model: str,
        chat_timeout: int | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.chat_model = chat_model
        self.embed_model = embed_model
        # 非流式 chat 超时：自定义报告等长文 HTML 生成可能超过 60s。
        # 默认读 LLM_CHAT_TIMEOUT_SECONDS，也可显式传入覆盖。
        self.chat_timeout = chat_timeout or settings.LLM_CHAT_TIMEOUT_SECONDS

    def _check_key(self) -> None:
        if not self.api_key:
            raise RuntimeError("LLM API key 未配置")

    @staticmethod
    def _usage(data: dict) -> dict | None:
        usage = data.get("usage") or {}
        return {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
        }

    async def chat(self, messages: list[dict], *, timeout: int | None = None, **kw) -> str:
        self._check_key()
        payload = {"model": self.chat_model, "messages": messages, **kw}
        client = _get_client()
        resp = await client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=timeout or self.chat_timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self.last_usage = self._usage(data)
        return data["choices"][0]["message"]["content"]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self._check_key()
        client = _get_client()
        resp = await client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.embed_model, "input": texts},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        self.last_usage = self._usage(data)
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]

    async def chat_with_tools(self, messages: list[dict], tools: list[dict], *, timeout: int | None = None, **kw) -> dict:
        """OpenAI function calling：透传 tools，解析 tool_calls（arguments JSON 容错）。"""
        self._check_key()
        payload = {
            "model": self.chat_model,
            "messages": messages,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    },
                }
                for t in tools
            ],
            **kw,
        }
        client = _get_client()
        resp = await client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=timeout or self.chat_timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self.last_usage = self._usage(data)
        message = data["choices"][0]["message"]
        tool_calls = []
        for tc in message.get("tool_calls") or []:
            try:
                arguments = json.loads(tc["function"].get("arguments") or "{}")
                if not isinstance(arguments, dict):
                    arguments = {}
            except (json.JSONDecodeError, ValueError, KeyError):
                arguments = {}
            tool_calls.append(
                {
                    "id": tc.get("id") or "",
                    "name": tc["function"]["name"],
                    "arguments": arguments,
                }
            )
        return {"content": message.get("content"), "tool_calls": tool_calls}

    async def chat_with_tools_stream(
        self, messages: list[dict], tools: list[dict], on_token=None, *, timeout: int | None = None, **kw
    ) -> dict:
        """OpenAI 兼容流式工具调用：内容增量实时经 on_token 逐 token 吐出，
        tool_calls 增量按 index 累积后整体返回（arguments 为累积的 JSON 片段，解析为 dict）。"""
        self._check_key()
        payload = {
            "model": self.chat_model,
            "messages": messages,
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    },
                }
                for t in tools
            ],
            **kw,
        }
        content_parts: list[str] = []
        tool_calls_by_index: dict[int, dict] = {}
        client = _get_client()
        # 流式响应在 async with client.stream 内完整消费后才释放；
        # client 是池化共享的，这里只管理本次响应的生命周期，不能关 client
        async with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=timeout or self.chat_timeout,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {}) or {}
                content = delta.get("content")
                if content:
                    content_parts.append(content)
                    if on_token is not None:
                        await on_token(content)
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    entry = tool_calls_by_index.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        entry["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        entry["name"] = fn["name"]
                    if fn.get("arguments"):
                        entry["arguments"] += fn["arguments"]
        self.last_usage = None  # 流式响应通常无 usage
        tool_calls = []
        for idx in sorted(tool_calls_by_index):
            e = tool_calls_by_index[idx]
            try:
                arguments = json.loads(e["arguments"] or "{}")
                if not isinstance(arguments, dict):
                    arguments = {}
            except (json.JSONDecodeError, ValueError):
                arguments = {}
            tool_calls.append({"id": e["id"], "name": e["name"], "arguments": arguments})
        return {"content": "".join(content_parts) or None, "tool_calls": tool_calls}

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """rerank 接口：兼容 Jina/SiliconFlow/Cohere/TEI/Voyage 等常见响应格式。"""
        self._check_key()
        # 用户可能直接粘贴完整端点（…/rerank），避免拼成 /rerank/rerank
        url = self.base_url if self.base_url.endswith("/rerank") else f"{self.base_url}/rerank"
        payload = {"model": self.chat_model, "query": query, "documents": documents}
        client = _get_client()
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=60,
        )
        if resp.status_code >= 400:
            # 透出响应体便于诊断（模型名错误、余额不足、端点路径不对等）
            raise RuntimeError(f"rerank 接口 HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        self.last_usage = None  # rerank 接口通常无 token 计数
        return self._parse_rerank_scores(data, len(documents))

    @staticmethod
    def _parse_rerank_scores(data, expected: int) -> list[float]:
        """解析多种 rerank 响应格式，按 index 重排对齐文档顺序：
        - Jina/SiliconFlow/Cohere：{"results": [{"index": i, "relevance_score": s}]}
        - Voyage 等：{"data": [...]}
        - TEI：[{"index": i, "score": s}] 或 [s, ...]（裸数组）
        无 index 字段时按返回顺序对齐；数量不一致抛 RuntimeError。纯函数。"""
        if isinstance(data, dict):
            items = data.get("results") or data.get("data") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        pairs: list[tuple[int, float]] = []
        for pos, item in enumerate(items):
            if isinstance(item, (int, float)):
                pairs.append((pos, float(item)))
            elif isinstance(item, dict):
                score = item.get("relevance_score", item.get("score"))
                if score is None:
                    continue
                pairs.append((int(item.get("index", pos)), float(score)))
        if len(pairs) != expected:
            raise RuntimeError(f"rerank 返回数量({len(pairs)})与文档数量({expected})不一致")
        pairs.sort(key=lambda p: p[0])
        return [s for _, s in pairs]

    async def transcribe(self, file_path: str) -> str:
        """OpenAI/whisper 兼容语音转写（multipart，超时放宽到 300s 适配大音频）。"""
        self._check_key()
        client = _get_client()
        with open(file_path, "rb") as f:
            resp = await client.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": f},
                data={"model": self.chat_model},
                timeout=300,
            )
        resp.raise_for_status()
        data = resp.json()
        self.last_usage = None  # 转写接口通常无 token 计数
        return data.get("text", "")

    async def chat_stream(self, messages: list[dict], *, timeout: int | None = None, **kw):
        """OpenAI 兼容 SSE 流式输出（流式响应通常无 usage，留空）。"""
        self._check_key()
        self.last_usage = None
        payload = {"model": self.chat_model, "messages": messages, "stream": True, **kw}
        client = _get_client()
        # 流式生成器：响应在 async with client.stream 内逐行消费，退出时才释放；
        # client 是池化共享的，只管理本次响应的生命周期，不能关 client
        async with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=timeout or self.chat_timeout,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                # 部分流式 chunk 的 choices 为空数组（如结尾/仅用量帧），跳过避免 IndexError
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                content = delta.get("content")
                if content:
                    yield content
