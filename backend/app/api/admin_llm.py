import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Integer, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin
from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.llm_call_log import LlmCallLog
from app.models.llm_model import LlmModel
from app.models.user import User
from app.schemas.admin import (
    LlmModelCreate,
    LlmModelOut,
    LlmModelUpdate,
    LlmRemoteModelsRequest,
    LlmRemoteModelsResult,
    LlmTestConfigRequest,
    LlmTestResult,
)
from app.services.llm.factory import build_llm, invalidate_llm_cache

router = APIRouter(prefix="/admin", tags=["admin-llm"], dependencies=[Depends(require_admin)])

_PROVIDERS = {"api", "ollama"}
_MODEL_TYPES = {"chat", "embed", "vision", "asr", "rerank"}


def mask_api_key(key: str | None) -> str:
    """API Key 脱敏：空→""，短 key 全掩码，否则保留头 3 尾 4。"""
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return f"{key[:3]}****{key[-4:]}"


def _model_out(m: LlmModel) -> LlmModelOut:
    return LlmModelOut(
        id=m.id,
        name=m.name,
        provider=m.provider,
        model_type=m.model_type,
        base_url=m.base_url,
        api_key_masked=mask_api_key(decrypt_secret(m.api_key)),
        model=m.model,
        is_default=m.is_default,
        enabled=m.enabled,
        created_at=m.created_at,
    )


# ---------------------------------------------------------------------------
# 模型注册管理
# ---------------------------------------------------------------------------

@router.get("/llm/models", response_model=list[LlmModelOut])
async def list_models(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    stmt = (
        select(LlmModel)
        .where(LlmModel.tenant_id == admin.tenant_id)
        .order_by(LlmModel.model_type, LlmModel.is_default.desc(), LlmModel.created_at.desc())
    )
    result = await db.execute(stmt)
    return [_model_out(m) for m in result.scalars().all()]


@router.post("/llm/models", response_model=LlmModelOut, status_code=201)
async def create_model(
    body: LlmModelCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if body.provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail="provider 仅支持 api / ollama")
    if body.model_type not in _MODEL_TYPES:
        raise HTTPException(status_code=400, detail="model_type 仅支持 chat / embed / vision / asr / rerank")
    if body.is_default:
        # 先清除同类型其他默认，再插入新模型（避免 autoflush 后把新模型也清掉）
        await db.execute(
            update(LlmModel)
            .where(
                LlmModel.tenant_id == admin.tenant_id,
                LlmModel.model_type == body.model_type,
            )
            .values(is_default=False)
        )
    model = LlmModel(
        tenant_id=admin.tenant_id,
        name=body.name,
        provider=body.provider,
        model_type=body.model_type,
        base_url=body.base_url,
        api_key=encrypt_secret(body.api_key),
        model=body.model,
        is_default=body.is_default,
        enabled=body.enabled,
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)
    invalidate_llm_cache()
    return _model_out(model)


async def _get_model_or_404(db: AsyncSession, tenant_id: int, model_id: int) -> LlmModel:
    model = await db.get(LlmModel, model_id)
    if model is None or model.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="模型不存在")
    return model


@router.put("/llm/models/{model_id}", response_model=LlmModelOut)
async def update_model(
    model_id: int,
    body: LlmModelUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    model = await _get_model_or_404(db, admin.tenant_id, model_id)
    updates = body.model_dump(exclude_unset=True)
    if "provider" in updates and updates["provider"] not in _PROVIDERS:
        raise HTTPException(status_code=400, detail="provider 仅支持 api / ollama")
    if "model_type" in updates and updates["model_type"] not in _MODEL_TYPES:
        raise HTTPException(status_code=400, detail="model_type 仅支持 chat / embed / vision / asr / rerank")
    # api_key 传空/None 表示不修改；非空则加密存储
    if "api_key" in updates and not updates.get("api_key"):
        updates.pop("api_key", None)
    elif "api_key" in updates:
        updates["api_key"] = encrypt_secret(updates["api_key"])
    for field, value in updates.items():
        setattr(model, field, value)
    if updates.get("is_default"):
        await db.execute(
            update(LlmModel)
            .where(
                LlmModel.tenant_id == admin.tenant_id,
                LlmModel.model_type == model.model_type,
                LlmModel.id != model.id,
            )
            .values(is_default=False)
        )
    await db.commit()
    await db.refresh(model)
    invalidate_llm_cache()
    return _model_out(model)


@router.delete("/llm/models/{model_id}", status_code=204)
async def delete_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    model = await _get_model_or_404(db, admin.tenant_id, model_id)
    await db.delete(model)
    await db.commit()
    invalidate_llm_cache()


@router.post("/llm/models/{model_id}/default", response_model=LlmModelOut)
async def set_default_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    model = await _get_model_or_404(db, admin.tenant_id, model_id)
    await db.execute(
        update(LlmModel)
        .where(
            LlmModel.tenant_id == admin.tenant_id,
            LlmModel.model_type == model.model_type,
            LlmModel.id != model.id,
        )
        .values(is_default=False)
    )
    model.is_default = True
    await db.commit()
    await db.refresh(model)
    invalidate_llm_cache()
    return _model_out(model)


@router.post("/llm/models/{model_id}/test", response_model=LlmTestResult)
async def test_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """用该模型配置临时构建实例发一次真实调用，验证连通性。"""
    model = await _get_model_or_404(db, admin.tenant_id, model_id)
    return await _ping_model(
        model.provider, model.base_url, decrypt_secret(model.api_key) or "", model.model_type, model.model
    )


async def _ping_model(
    provider: str, base_url: str, api_key: str, model_type: str, model: str
) -> LlmTestResult:
    """对一组配置发一次真实调用（chat/vision 发 chat ping，embed 发 embed ping，rerank 发 rerank ping）。"""
    if model_type == "asr":
        # ASR 连通性测试需要真实音频文件，暂不在线测试
        return LlmTestResult(ok=True, latency_ms=0, detail="ASR 模型暂不支持连通性测试，请通过上传音频验证")
    start = time.perf_counter()
    try:
        llm = build_llm(provider, base_url, api_key, model, model)
        if model_type in ("chat", "vision"):
            await llm.chat([{"role": "user", "content": "ping"}])
        elif model_type == "rerank":
            await llm.rerank("ping", ["ping doc 1", "ping doc 2"])
        else:
            await llm.embed(["ping"])
        latency_ms = int((time.perf_counter() - start) * 1000)
        return LlmTestResult(ok=True, latency_ms=latency_ms, detail="调用成功")
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return LlmTestResult(ok=False, latency_ms=latency_ms, detail=str(exc)[:500])


@router.post("/llm/models/test-config", response_model=LlmTestResult)
async def test_unsaved_config(
    body: LlmTestConfigRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """不保存直接测试一组模型配置（表单内"测试"按钮用）。
    编辑场景可传 model_id：api_key 为空时回退用库中已保存的 key。"""
    if body.provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail="provider 仅支持 api / ollama")
    if body.model_type not in _MODEL_TYPES:
        raise HTTPException(status_code=400, detail="model_type 仅支持 chat / embed / vision / asr / rerank")
    api_key = body.api_key or ""
    if not api_key and body.model_id is not None:
        saved = await _get_model_or_404(db, admin.tenant_id, body.model_id)
        api_key = decrypt_secret(saved.api_key) or ""
    return await _ping_model(body.provider, body.base_url, api_key, body.model_type, body.model)


@router.post("/llm/models/fetch-remote", response_model=LlmRemoteModelsResult)
async def fetch_remote_models(
    body: LlmRemoteModelsRequest,
    admin: User = Depends(require_admin),
):
    """按 base_url/api_key 拉取远端可用模型列表（OpenAI /models 或 Ollama /api/tags）。"""
    import httpx

    if body.provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail="provider 仅支持 api / ollama")
    base = body.base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if body.provider == "ollama":
                resp = await client.get(f"{base}/api/tags")
                resp.raise_for_status()
                models = [m["name"] for m in resp.json().get("models", []) if m.get("name")]
            else:
                headers = {"Authorization": f"Bearer {body.api_key}"} if body.api_key else {}
                resp = await client.get(f"{base}/models", headers=headers)
                resp.raise_for_status()
                models = [m["id"] for m in resp.json().get("data", []) if m.get("id")]
        return LlmRemoteModelsResult(ok=True, models=sorted(models), detail=f"获取到 {len(models)} 个模型")
    except Exception as exc:
        return LlmRemoteModelsResult(ok=False, models=[], detail=str(exc)[:300])


# ---------------------------------------------------------------------------
# 用量监控
# ---------------------------------------------------------------------------

@router.get("/llm/stats")
async def llm_stats(
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """用量统计：聚合全部 SQL 下推，只把最近 5 条失败明细读入内存。"""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    scope = [
        LlmCallLog.created_at >= since,
        or_(LlmCallLog.tenant_id == admin.tenant_id, LlmCallLog.tenant_id.is_(None)),
    ]
    tokens = func.coalesce(LlmCallLog.prompt_tokens, 0) + func.coalesce(LlmCallLog.completion_tokens, 0)

    total_calls, success_calls, avg_latency, total_tokens = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(func.cast(LlmCallLog.success, Integer)), 0),
                func.coalesce(func.avg(LlmCallLog.latency_ms), 0),
                func.coalesce(func.sum(tokens), 0),
            ).where(*scope)
        )
    ).one()

    by_model = [
        {"model": m, "calls": c, "tokens": t, "success_rate": round(s / c, 4) if c else 0.0}
        for m, c, t, s in (await db.execute(
            select(
                LlmCallLog.model,
                func.count(),
                func.coalesce(func.sum(tokens), 0),
                func.coalesce(func.sum(func.cast(LlmCallLog.success, Integer)), 0),
            ).where(*scope).group_by(LlmCallLog.model).order_by(func.count().desc())
        )).all()
    ]
    day_col = func.to_char(LlmCallLog.created_at, "YYYY-MM-DD")
    by_day = [
        {"date": d, "calls": c, "tokens": t}
        for d, c, t in (await db.execute(
            select(day_col, func.count(), func.coalesce(func.sum(tokens), 0))
            .where(*scope).group_by(day_col).order_by(day_col)
        )).all()
    ]
    by_caller = [
        {"caller": c, "calls": n}
        for c, n in (await db.execute(
            select(LlmCallLog.caller, func.count())
            .where(*scope).group_by(LlmCallLog.caller).order_by(func.count().desc())
        )).all()
    ]
    recent_failures = [
        {"model": r.model, "caller": r.caller, "error": r.error, "created_at": r.created_at.isoformat()}
        for r in (await db.execute(
            select(LlmCallLog)
            .where(*scope, LlmCallLog.success.is_(False))
            .order_by(LlmCallLog.created_at.desc())
            .limit(5)
        )).scalars().all()
    ]
    return {
        "total_calls": total_calls,
        "success_rate": round(success_calls / total_calls, 4) if total_calls else 0.0,
        "avg_latency_ms": round(float(avg_latency)),
        "total_tokens": total_tokens,
        "by_model": by_model,
        "by_day": by_day,
        "by_caller": by_caller,
        "recent_failures": recent_failures,
        "days": days,
    }
