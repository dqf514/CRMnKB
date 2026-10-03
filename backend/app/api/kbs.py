import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.models.chunk import DocumentChunk
from app.models.document import KnowledgeDocument
from app.models.document_version import DocumentVersion
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.schemas.kb import (
    AssociateFilesRequest,
    AssociateResult,
    HitTestRequest,
    KbChunkOut,
    KbCreate,
    KbDocOut,
    KbOut,
    KbUpdate,
)
from app.services.audit import record_audit
from app.services.ingestion import (
    is_supported,
    process_document,
    vision_review_document as run_vision_review,
)
from app.services.kb import associate_files, delete_kb_documents
from app.services.permissions import accessible_ids, ensure_access, resolve_permissions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kbs", tags=["kbs"])


def _kb_out(kb: KnowledgeBase, doc_count: int = 0, chunk_count: int = 0, perm: str | None = None) -> KbOut:
    return KbOut(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        type=kb.type,
        customer_id=kb.customer_id,
        doc_count=doc_count,
        chunk_count=chunk_count,
        is_auto=bool(getattr(kb, "is_auto", False)),
        created_at=kb.created_at,
        owner_id=kb.owner_id,
        is_private=kb.is_private,
        perm=perm,
    )


async def _get_kb_or_404(
    db: AsyncSession, user: User, kb_id: int, required: str = "read"
) -> KnowledgeBase:
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None or kb.tenant_id != user.tenant_id or kb.deleted_at is not None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    await ensure_access(db, user, "kb", kb_id, required)
    return kb


async def _kb_stats(db: AsyncSession, tenant_id: int) -> dict[int, tuple[int, int]]:
    stmt = (
        select(
            KnowledgeDocument.kb_id,
            func.count(KnowledgeDocument.id),
            func.coalesce(func.sum(KnowledgeDocument.chunk_count), 0),
        )
        .where(KnowledgeDocument.tenant_id == tenant_id)
        .group_by(KnowledgeDocument.kb_id)
    )
    rows = (await db.execute(stmt)).all()
    return {kb_id: (doc_count, chunk_count) for kb_id, doc_count, chunk_count in rows}


@router.get("", response_model=list[KbOut])
async def list_kbs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_auto: bool = Query(False, description="PR-H：是否包含系统自动 KB（默认隐藏）"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filters = [
        KnowledgeBase.tenant_id == user.tenant_id,
        KnowledgeBase.deleted_at.is_(None),
    ]
    if not include_auto:
        filters.append(KnowledgeBase.is_auto.is_(False))
    # 仅返回当前用户可读的知识库（owner ∪ 团队可见 ∪ 被分享）
    accessible = await accessible_ids(db, user, "kb")
    filters.append(KnowledgeBase.id.in_(accessible))
    kbs = (
        (
            await db.execute(
                select(KnowledgeBase)
                .where(*filters)
                .order_by(KnowledgeBase.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    stats = await _kb_stats(db, user.tenant_id)
    perm_map = await resolve_permissions(db, user, "kb", [kb.id for kb in kbs])
    return [_kb_out(kb, *stats.get(kb.id, (0, 0)), perm_map.get(kb.id)) for kb in kbs]


@router.post("", response_model=KbOut, status_code=201)
async def create_kb(
    body: KbCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    kb = KnowledgeBase(
        tenant_id=user.tenant_id, name=body.name, description=body.description,
        type="general", owner_id=user.id, is_private=True,
    )
    db.add(kb)
    await db.flush()
    record_audit(db, user, "create", "kb", kb.id, {"name": kb.name})
    await db.commit()
    await db.refresh(kb)
    return _kb_out(kb)


@router.get("/{kb_id}", response_model=KbOut)
async def get_kb(
    kb_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    kb = await _get_kb_or_404(db, user, kb_id)
    stats = await _kb_stats(db, user.tenant_id)
    return _kb_out(kb, *stats.get(kb.id, (0, 0)))


@router.put("/{kb_id}", response_model=KbOut)
async def update_kb(
    kb_id: int,
    body: KbUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    kb = await _get_kb_or_404(db, user, kb_id, "edit")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(kb, field, value)
    await db.commit()
    await db.refresh(kb)
    stats = await _kb_stats(db, user.tenant_id)
    return _kb_out(kb, *stats.get(kb.id, (0, 0)))


@router.delete("/{kb_id}", status_code=204)
async def delete_kb(
    kb_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    kb = await _get_kb_or_404(db, user, kb_id, "owner")
    # PR-H：自动 KB 不允许删除（前端已隐藏，此处兜底）
    if getattr(kb, "is_auto", False):
        raise HTTPException(status_code=400, detail="系统自动 KB 不可删除")
    # 软删除进回收站；彻底删除（连同 documents/chunks）走 /recycle-bin
    kb.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    record_audit(db, user, "delete", "kb", kb.id, {"name": kb.name})
    await db.commit()


@router.get("/{kb_id}/documents", response_model=list[KbDocOut])
async def list_kb_documents(
    kb_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_kb_or_404(db, user, kb_id)
    stmt = (
        select(KnowledgeDocument)
        .where(KnowledgeDocument.kb_id == kb_id)
        .order_by(KnowledgeDocument.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/{kb_id}/documents", response_model=AssociateResult)
async def associate_to_kb(
    kb_id: int,
    body: AssociateFilesRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_kb_or_404(db, user, kb_id, "edit")
    result = await associate_files(db, user.tenant_id, kb_id, body.file_ids, user)
    await db.commit()
    for doc_id in result["parse_doc_ids"]:
        background_tasks.add_task(process_document, doc_id)
    return AssociateResult(associated=result["associated"], already=result["already"])


@router.post("/{kb_id}/documents/{doc_id}/reparse", status_code=202)
async def reparse_kb_document(
    kb_id: int,
    doc_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """重试解析失败/卡死的文档：重置为 processing 并重新排队（process_document 幂等清旧切片）。"""
    await _get_kb_or_404(db, user, kb_id, "edit")
    doc = await db.get(KnowledgeDocument, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.status == "processing":
        raise HTTPException(status_code=409, detail="文档正在处理中")
    if not is_supported(doc.file_name):
        raise HTTPException(status_code=400, detail="该格式暂不支持解析")
    doc.status = "processing"
    record_audit(db, user, "reparse", "document", doc.id, {"title": doc.title})
    await db.commit()
    background_tasks.add_task(process_document, doc_id)
    return {"ok": True}


@router.post("/{kb_id}/documents/{doc_id}/vision-review", status_code=202)
async def vision_review(
    kb_id: int,
    doc_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """视觉复核：对本地 OCR 识别质量差的图片/扫描 PDF，用视觉模型重新转录并更新切片。

    仅允许 status=ready 且视觉可识别（图片/扫描 PDF 结果方法）的文档；
    后台执行，失败保留旧内容，可反复复核。"""
    await _get_kb_or_404(db, user, kb_id, "edit")
    doc = await db.get(KnowledgeDocument, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    cur_method = (doc.doc_metadata or {}).get("processing_method")
    if doc.status != "ready":
        raise HTTPException(status_code=409, detail="仅就绪文档可视觉复核")
    if cur_method not in ("ocr", "image_describe", "vision_ocr"):
        raise HTTPException(status_code=400, detail="仅图片/扫描 PDF（视觉可识别）文档可视觉复核")
    record_audit(db, user, "update", "document", doc.id, {"action": "vision_review", "title": doc.title})
    await db.commit()
    background_tasks.add_task(run_vision_review, doc_id)
    return {"ok": True}


class DirectReturnIn(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)
    similarity: float = Field(ge=0.0, le=1.0)


@router.put("/{kb_id}/documents/{doc_id}/direct-return", response_model=KbDocOut)
async def set_direct_return(
    kb_id: int,
    doc_id: int,
    body: DirectReturnIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """PR-D：设置高置信短答案。检索相似度 ≥ similarity 时直接返回答案、跳过 LLM。"""
    await _get_kb_or_404(db, user, kb_id, "edit")
    doc = await db.get(KnowledgeDocument, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    doc.directly_return_answer = body.answer
    doc.directly_return_similarity = body.similarity
    record_audit(
        db, user, "set_direct_return", "document", doc.id,
        {"similarity": body.similarity, "answer_len": len(body.answer)},
    )
    await db.commit()
    return doc


@router.delete("/{kb_id}/documents/{doc_id}/direct-return", status_code=204)
async def clear_direct_return(
    kb_id: int,
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """PR-D：清除高置信短答案设置。"""
    await _get_kb_or_404(db, user, kb_id, "edit")
    doc = await db.get(KnowledgeDocument, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    doc.directly_return_answer = None
    doc.directly_return_similarity = None
    record_audit(db, user, "clear_direct_return", "document", doc.id, {})
    await db.commit()


@router.delete("/{kb_id}/documents/{doc_id}", status_code=204)
async def remove_kb_document(
    kb_id: int,
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_kb_or_404(db, user, kb_id, "edit")
    doc = await db.get(KnowledgeDocument, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    await db.delete(doc)  # chunks 由外键 ON DELETE CASCADE 清理；不动库文件
    await db.commit()


@router.get("/{kb_id}/documents/{doc_id}/chunks", response_model=list[KbChunkOut])
async def list_kb_document_chunks(
    kb_id: int,
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_kb_or_404(db, user, kb_id)
    doc = await db.get(KnowledgeDocument, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == doc_id)
        .order_by(DocumentChunk.chunk_index)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


# ---------------------------------------------------------------------------
# 文档版本历史
# ---------------------------------------------------------------------------

@router.get("/{kb_id}/documents/{doc_id}/versions")
async def list_doc_versions(
    kb_id: int,
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出文档的解析历史版本（预览内容）。"""
    await _get_kb_or_404(db, user, kb_id)
    doc = await db.get(KnowledgeDocument, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    rows = (
        (
            await db.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == doc_id)
                .order_by(DocumentVersion.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": v.id,
            "created_at": v.created_at,
            "chunk_count": v.chunk_count,
            "note": v.note,
            "preview": (v.content or "")[:200],
        }
        for v in rows
    ]


@router.get("/{kb_id}/documents/{doc_id}/versions/{version_id}")
async def get_doc_version(
    kb_id: int,
    doc_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查看某个历史版本的完整内容。"""
    await _get_kb_or_404(db, user, kb_id)
    version = await db.get(DocumentVersion, version_id)
    if version is None or version.document_id != doc_id:
        raise HTTPException(status_code=404, detail="版本不存在")
    return {"id": version.id, "created_at": version.created_at, "content": version.content}


@router.post("/{kb_id}/documents/{doc_id}/versions/{version_id}/restore", status_code=202)
async def restore_doc_version(
    kb_id: int,
    doc_id: int,
    version_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把文档恢复到某个历史版本（写入该版本内容并重新解析）。"""
    await _get_kb_or_404(db, user, kb_id, "edit")
    doc = await db.get(KnowledgeDocument, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    version = await db.get(DocumentVersion, version_id)
    if version is None or version.document_id != doc_id:
        raise HTTPException(status_code=404, detail="版本不存在")
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    stored = settings.upload_path / f"restore_{doc.id}_{uuid4().hex}.md"
    stored.write_text(version.content or "", encoding="utf-8")
    doc.file_path = str(stored)
    doc.file_name = stored.name
    doc.file_type = "md"
    doc.status = "processing"
    await db.commit()
    background_tasks.add_task(process_document, doc_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 命中测试（只读，不写 rag_query_logs）
# ---------------------------------------------------------------------------

@router.post("/{kb_id}/hit-test")
async def hit_test(
    kb_id: int,
    body: HitTestRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """在指定知识库内试运行检索（向量 + trgm 混合），返回命中切片与命中方式。"""
    await _get_kb_or_404(db, user, kb_id)
    from app.services.rag import (
        reciprocal_rank_fusion,
        search_chunks_keyword,
        search_chunks_vector,
    )

    keyword_hits = await search_chunks_keyword(db, user.tenant_id, body.query, body.top_k, [kb_id])
    degraded = False
    vector_hits: list[dict] = []
    try:
        from app.services.llm import resolve_embed_llm

        embed_llm = await resolve_embed_llm(caller="hit_test", tenant_id=user.tenant_id)
        vec = (await embed_llm.embed([body.query]))[0]
        vector_hits = await search_chunks_vector(db, user.tenant_id, vec, body.top_k, [kb_id])
    except Exception as exc:
        # 嵌入模型不可用：降级为仅关键词检索
        logger.warning("hit-test 向量检索降级（embed 不可用）: %s", exc)
        degraded = True

    # 命中方式标注：vector / trgm / both
    vector_ids = {h["chunk_id"] for h in vector_hits}
    keyword_ids = {h["chunk_id"] for h in keyword_hits}
    if degraded:
        fused = keyword_hits
    else:
        fused = reciprocal_rank_fusion(vector_hits, keyword_hits)
    hits = []
    for h in fused[: body.top_k]:
        if body.threshold is not None and float(h["score"]) < body.threshold:
            continue
        in_vector = h["chunk_id"] in vector_ids
        in_keyword = h["chunk_id"] in keyword_ids
        hits.append(
            {
                "chunk_id": h["chunk_id"],
                "document_id": h["doc_id"],
                "doc_name": h["doc_title"],
                "content": h["content"],
                "score": round(float(h["score"]), 4),
                "hit_method": "both" if in_vector and in_keyword else ("vector" if in_vector else "trgm"),
            }
        )
    result = {"hits": hits, "degraded": degraded}
    if degraded:
        result["message"] = "嵌入模型不可用，已降级为仅关键词检索"
    return result
