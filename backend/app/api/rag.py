import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.rag_query_log import RagQueryLog
from app.models.user import User
from app.schemas.rag import RagQueryRequest, RagQueryResponse
from app.services.rag import ChatUnavailable, EmbeddingUnavailable, rag_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/query", response_model=RagQueryResponse)
async def query(
    body: RagQueryRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        result = await rag_query(
            db, user.tenant_id, body.question, body.top_k, body.kb_ids, user=user
        )
    except EmbeddingUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ChatUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    # 落一条问答日志（反馈闭环），日志写入失败不影响响应
    try:
        log = RagQueryLog(
            tenant_id=user.tenant_id,
            user_id=user.id,
            question=body.question,
            answer=result["answer"],
            grounded=result["grounded"],
            sources=result["sources"],
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        result["query_log_id"] = log.id
    except Exception as exc:
        logger.warning("RAG 问答日志写入失败（忽略）: %s", exc)
        result["query_log_id"] = None
    return result
