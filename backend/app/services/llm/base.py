from abc import ABC, abstractmethod


class LLMService(ABC):
    provider_name = "unknown"
    # 最近一次调用的 token 用量（{"prompt_tokens": n, "completion_tokens": n}），取不到为 None
    last_usage: dict | None = None

    @abstractmethod
    async def chat(self, messages: list[dict], **kw) -> str:
        """messages: [{"role": "...", "content": "..."}]，返回助手文本。"""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量文本向量化。"""

    async def chat_stream(self, messages: list[dict], **kw):
        """逐 token 流式生成。默认退化为一次性输出，子类可覆盖为真流式。"""
        yield await self.chat(messages, **kw)

    async def transcribe(self, file_path: str) -> str:
        """语音转写：音频文件 → 文本。默认不支持，由 ApiLLM 覆盖。"""
        raise RuntimeError("该模型不支持语音转写")

    async def chat_with_tools(self, messages: list[dict], tools: list[dict], **kw) -> dict:
        """工具调用对话：tools 为 [{name, description, parameters(JSON Schema)}]，
        返回 {"content": str|None, "tool_calls": [{"id","name","arguments":dict}]}。"""
        raise RuntimeError("该模型不支持工具调用")

    async def chat_with_tools_stream(self, messages: list[dict], tools: list[dict], on_token=None, **kw) -> dict:
        """流式工具调用：实时吐出 content 增量（on_token），返回累计的 tool_calls。

        默认回退非流式 chat_with_tools（内容经 on_token 一次性透出）；
        支持流式的子类覆盖以逐 token 输出。"""
        resp = await self.chat_with_tools(messages, tools, **kw)
        content = resp.get("content")
        if on_token is not None and content:
            await on_token(content)
        return resp

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """重排序：对 documents 按与 query 的相关性打分，返回与 documents 等长的分数列表。"""
        raise RuntimeError("该模型不支持 rerank")
