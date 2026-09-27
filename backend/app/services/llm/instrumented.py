import logging
import time

from app.services.llm.base import LLMService
from app.services.llm.usage import record_call_log

logger = logging.getLogger(__name__)


class InstrumentedLLM(LLMService):
    """LLM 调用埋点包装：统一记录用量日志（llm_call_logs），
    失败时同源记录 warning 到 error_logs，然后原样抛出异常（上层降级逻辑不变）。"""

    def __init__(
        self,
        inner: LLMService,
        caller: str,
        tenant_id: int | None = None,
        user_id: int | None = None,
    ):
        self._inner = inner
        self.caller = caller
        self.tenant_id = tenant_id
        self.user_id = user_id

    def _entry(self, model_type: str, latency_ms: int, success: bool, error: str | None) -> dict:
        inner = self._inner
        usage = getattr(inner, "last_usage", None) or {}
        model = getattr(inner, "embed_model" if model_type == "embed" else "chat_model", "")
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "model": model or "",
            "provider": getattr(inner, "provider_name", "unknown"),
            "model_type": model_type,
            "caller": self.caller,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "latency_ms": latency_ms,
            "success": success,
            "error": error,
        }

    async def _record(self, model_type: str, start: float, success: bool, error: str | None = None) -> None:
        latency_ms = int((time.perf_counter() - start) * 1000)
        await record_call_log(self._entry(model_type, latency_ms, success, error))
        if not success:
            from app.services.error_log import log_error

            await log_error(
                level="warning",
                module="llm",
                message=f"LLM 调用失败（{self.caller}）",
                detail=error,
                tenant_id=self.tenant_id,
            )

    async def chat(self, messages: list[dict], **kw) -> str:
        start = time.perf_counter()
        try:
            result = await self._inner.chat(messages, **kw)
        except Exception as exc:
            await self._record("chat", start, False, str(exc)[:500])
            raise
        await self._record("chat", start, True)
        return result

    async def embed(self, texts: list[str]) -> list[list[float]]:
        start = time.perf_counter()
        try:
            result = await self._inner.embed(texts)
        except Exception as exc:
            await self._record("embed", start, False, str(exc)[:500])
            raise
        await self._record("embed", start, True)
        return result

    async def transcribe(self, file_path: str) -> str:
        start = time.perf_counter()
        try:
            result = await self._inner.transcribe(file_path)
        except Exception as exc:
            await self._record("asr", start, False, str(exc)[:500])
            raise
        await self._record("asr", start, True)
        return result

    async def chat_with_tools(self, messages: list[dict], tools: list[dict], **kw) -> dict:
        start = time.perf_counter()
        try:
            result = await self._inner.chat_with_tools(messages, tools, **kw)
        except Exception as exc:
            await self._record("chat", start, False, str(exc)[:500])
            raise
        await self._record("chat", start, True)
        return result

    async def chat_with_tools_stream(self, messages: list[dict], tools: list[dict], on_token=None, **kw) -> dict:
        start = time.perf_counter()
        try:
            result = await self._inner.chat_with_tools_stream(messages, tools, on_token=on_token, **kw)
        except Exception as exc:
            await self._record("chat", start, False, str(exc)[:500])
            raise
        await self._record("chat", start, True)
        return result

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        start = time.perf_counter()
        try:
            result = await self._inner.rerank(query, documents)
        except Exception as exc:
            await self._record("rerank", start, False, str(exc)[:500])
            raise
        await self._record("rerank", start, True)
        return result

    async def chat_stream(self, messages: list[dict], **kw):
        start = time.perf_counter()
        try:
            async for token in self._inner.chat_stream(messages, **kw):
                yield token
        except Exception as exc:
            await self._record("chat", start, False, str(exc)[:500])
            raise
        await self._record("chat", start, True)
