import logging

from sqlalchemy import select

from app.core.crypto import decrypt_secret

from app.config import settings
from app.models.llm_model import LlmModel
from app.services.llm.api_llm import ApiLLM
from app.services.llm.base import LLMService
from app.services.llm.instrumented import InstrumentedLLM
from app.services.llm.ollama_llm import OllamaLLM

logger = logging.getLogger(__name__)

_chat_llm: LLMService | None = None
_embed_llm: LLMService | None = None

# DB 模型缓存：{(model_type, tenant_id): (version, instance)}
_cache: dict[tuple[str, int | None], tuple[int, LLMService]] = {}
_cache_version = 0


def invalidate_llm_cache() -> None:
    """模型增删改后调用，使 DB 模型缓存整体失效。"""
    global _cache_version
    _cache_version += 1
    _cache.clear()


def build_llm(
    provider: str, base_url: str, api_key: str, chat_model: str, embed_model: str
) -> LLMService:
    if provider == "api":
        return ApiLLM(base_url=base_url, api_key=api_key, chat_model=chat_model, embed_model=embed_model)
    if provider == "ollama":
        return OllamaLLM(base_url=base_url, chat_model=chat_model, embed_model=embed_model)
    raise ValueError(f"未知的 LLM provider: {provider}")


def _build_from_env(model_type: str) -> LLMService:
    provider = settings.LLM_CHAT_PROVIDER if model_type == "chat" else settings.LLM_EMBED_PROVIDER
    return build_llm(
        provider,
        settings.LLM_API_BASE_URL if provider == "api" else settings.OLLAMA_BASE_URL,
        settings.LLM_API_KEY if provider == "api" else "",
        settings.LLM_API_CHAT_MODEL if provider == "api" else settings.OLLAMA_CHAT_MODEL,
        settings.LLM_API_EMBED_MODEL if provider == "api" else settings.OLLAMA_EMBED_MODEL,
    )


async def _load_model_from_db(model_type: str, tenant_id: int | None) -> LlmModel | None:
    """读 DB 中 enabled 的模型：is_default 优先，无默认取最新。DB 不可用返回 None。"""
    try:
        from app.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            stmt = select(LlmModel).where(
                LlmModel.enabled.is_(True), LlmModel.model_type == model_type
            )
            if tenant_id is not None:
                stmt = stmt.where(LlmModel.tenant_id == tenant_id)
            stmt = stmt.order_by(LlmModel.is_default.desc(), LlmModel.created_at.desc()).limit(1)
            return (await session.execute(stmt)).scalar_one_or_none()
    except Exception as exc:
        logger.debug("DB 模型读取失败，回退 .env 配置: %s", exc)
        return None


async def _resolve(
    model_type: str, caller: str, tenant_id: int | None, user_id: int | None
) -> LLMService:
    key = (model_type, tenant_id)
    cached = _cache.get(key)
    if cached is not None and cached[0] == _cache_version:
        inner = cached[1]
    else:
        model = await _load_model_from_db(model_type, tenant_id)
        if model is not None:
            inner = build_llm(model.provider, model.base_url, decrypt_secret(model.api_key) or "", model.model, model.model)
        else:
            # DB 无记录：回退现有 .env 配置（行为与旧版完全一致）
            inner = _build_from_env(model_type)
        _cache[key] = (_cache_version, inner)
    # 缓存只存底层 LLM；每次调用按实际 caller/tenant/user 包一层埋点，
    # 避免共享实例把首个调用方的 caller/user_id 焊死导致用量统计错乱
    return InstrumentedLLM(inner, caller=caller, tenant_id=tenant_id, user_id=user_id)


async def resolve_chat_llm(
    caller: str = "chat", tenant_id: int | None = None, user_id: int | None = None
) -> LLMService:
    """DB 注册模型优先、.env 回退，并带用量埋点。"""
    return await _resolve("chat", caller, tenant_id, user_id)


async def resolve_embed_llm(
    caller: str = "embed", tenant_id: int | None = None, user_id: int | None = None
) -> LLMService:
    return await _resolve("embed", caller, tenant_id, user_id)


async def resolve_vision_llm(
    caller: str = "vision", tenant_id: int | None = None, user_id: int | None = None
) -> LLMService:
    """视觉模型：DB 中 model_type=vision 的启用模型优先；
    未配置时回退 chat 默认模型（多模态 chat 模型如 qwen-vl/gpt-4o 可直接做视觉）。"""
    key = ("vision", tenant_id)
    cached = _cache.get(key)
    if cached is not None and cached[0] == _cache_version:
        inner = cached[1]
    else:
        model = await _load_model_from_db("vision", tenant_id)
        if model is None:
            return await resolve_chat_llm(caller=caller, tenant_id=tenant_id, user_id=user_id)
        inner = build_llm(model.provider, model.base_url, decrypt_secret(model.api_key) or "", model.model, model.model)
        _cache[key] = (_cache_version, inner)
    return InstrumentedLLM(inner, caller=caller, tenant_id=tenant_id, user_id=user_id)


async def resolve_asr_llm(
    caller: str = "asr", tenant_id: int | None = None, user_id: int | None = None
) -> LLMService:
    """语音识别模型：仅取 DB 中 model_type=asr 的启用模型，未配置则抛错（无 .env 回退）。"""
    key = ("asr", tenant_id)
    cached = _cache.get(key)
    if cached is not None and cached[0] == _cache_version:
        inner = cached[1]
    else:
        model = await _load_model_from_db("asr", tenant_id)
        if model is None:
            raise RuntimeError("未配置语音识别模型")
        inner = build_llm(model.provider, model.base_url, decrypt_secret(model.api_key) or "", model.model, model.model)
        _cache[key] = (_cache_version, inner)
    return InstrumentedLLM(inner, caller=caller, tenant_id=tenant_id, user_id=user_id)


async def resolve_rerank_llm(
    caller: str = "rerank", tenant_id: int | None = None, user_id: int | None = None
) -> LLMService | None:
    """专用 rerank 模型：仅取 DB 中 model_type=rerank 的启用模型；
    未配置返回 None（不抛，调用方走 chat 打分降级链）。"""
    key = ("rerank", tenant_id)
    cached = _cache.get(key)
    if cached is not None and cached[0] == _cache_version:
        inner = cached[1]
    else:
        model = await _load_model_from_db("rerank", tenant_id)
        if model is None:
            return None
        inner = build_llm(model.provider, model.base_url, decrypt_secret(model.api_key) or "", model.model, model.model)
        _cache[key] = (_cache_version, inner)
    return InstrumentedLLM(inner, caller=caller, tenant_id=tenant_id, user_id=user_id)


# ---------------------------------------------------------------------------
# 旧同步接口（.env 路径，向后兼容）
# ---------------------------------------------------------------------------

def get_chat_llm() -> LLMService:
    global _chat_llm
    if _chat_llm is None:
        _chat_llm = _build_from_env("chat")
    return _chat_llm


def get_embed_llm() -> LLMService:
    global _embed_llm
    if _embed_llm is None:
        _embed_llm = _build_from_env("embed")
    return _embed_llm
