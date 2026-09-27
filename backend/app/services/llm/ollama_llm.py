import json

import httpx

from app.services.llm.base import LLMService


class OllamaLLM(LLMService):
    """本地 Ollama 服务。"""

    provider_name = "ollama"

    def __init__(self, base_url: str, chat_model: str, embed_model: str):
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embed_model = embed_model

    @staticmethod
    def _usage(data: dict) -> dict:
        return {
            "prompt_tokens": data.get("prompt_eval_count"),
            "completion_tokens": data.get("eval_count"),
        }

    async def chat(self, messages: list[dict], **kw) -> str:
        payload = {
            "model": self.chat_model,
            "messages": messages,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        self.last_usage = self._usage(data)
        return data["message"]["content"]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/api/embed",
                json={"model": self.embed_model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
        self.last_usage = None  # Ollama embed 无 token 计数
        return data["embeddings"]

    async def transcribe(self, file_path: str) -> str:
        raise RuntimeError("Ollama 不支持语音转写")

    async def chat_with_tools(self, messages: list[dict], tools: list[dict], **kw) -> dict:
        raise RuntimeError("Ollama 暂不支持工具调用")

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        raise RuntimeError("Ollama 不支持 rerank，请注册 OpenAI 兼容的 rerank 接口模型")

    async def chat_stream(self, messages: list[dict], **kw):
        """Ollama NDJSON 流式输出（done 帧带 token 计数）。"""
        payload = {"model": self.chat_model, "messages": messages, "stream": True}
        self.last_usage = None
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST", f"{self.base_url}/api/chat", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    content = chunk.get("message", {}).get("content")
                    if content:
                        yield content
                    if chunk.get("done"):
                        self.last_usage = self._usage(chunk)
                        break
