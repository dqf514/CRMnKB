import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.user import User
from app.schemas.chat import (
    ChatAskRequest,
    ChatAskResponse,
    ChatMessageOut,
    ChatSessionCreate,
    ChatSessionOut,
)
from app.services.chat import (
    chat_ask,
    get_or_create_session,
    stream_agent_chat_events,
    stream_chat_events,
)
from app.services.rag import ChatUnavailable, EmbeddingUnavailable

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/sessions", response_model=ChatSessionOut, status_code=201)
async def create_session(
    body: ChatSessionCreate | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = ChatSession(
        tenant_id=user.tenant_id,
        user_id=user.id,
        title=(body.title if body and body.title else "新会话"),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.get("/sessions", response_model=list[ChatSessionOut])
async def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(ChatSession)
        .where(ChatSession.tenant_id == user.tenant_id, ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def _get_session_or_404(
    db: AsyncSession, user: User, session_id: int
) -> ChatSession:
    session = await db.get(ChatSession, session_id)
    if (
        session is None
        or session.tenant_id != user.tenant_id
        or session.user_id != user.id
    ):
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
async def list_messages(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_session_or_404(db, user, session_id)
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _get_session_or_404(db, user, session_id)
    await db.delete(session)  # 消息由外键 ON DELETE CASCADE 清理
    await db.commit()


@router.post("/ask", response_model=ChatAskResponse)
async def ask(
    body: ChatAskRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return await chat_ask(
            db, user, body.question, body.session_id, body.kb_ids, body.file_ids,
            thinking=body.thinking,
        )
    except EmbeddingUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ChatUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/ask/stream")
async def ask_stream(
    body: ChatAskRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # 会话校验/创建在流开始前完成，404 可正常返回
    session = await get_or_create_session(db, user, body.question, body.session_id)
    # 新建会话必须在此立即提交：流式生成器在请求返回后才运行，届时 get_db 已退出、
    # 请求事务被回滚；不提交会导致 chat_messages 外键指向不存在的 session（保存失败）
    await db.commit()

    async def event_source():
        agen = stream_chat_events(
            db, user, body.question, session, body.kb_ids, body.file_ids,
            thinking=body.thinking,
        )
        # 心跳保活：LLM 思考/工具调用可能长时间无帧，期间每 15s 发一条 SSE 注释，
        # 防止中间代理（nginx 默认 60s 读超时）因静默掐断连接
        pending: asyncio.Task | None = None
        while True:
            if pending is None:
                pending = asyncio.create_task(agen.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=15)
            if not done:
                yield ": ping\n\n"
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                break
            finally:
                pending = None
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/ask/agent/stream")
async def ask_agent_stream(
    body: ChatAskRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """dsh agent 流式问答（DSH_AGENT_ENABLED 关闭时 503）。

    与 /ask/stream 同一套 SSE 帧约定（meta/token/tool/done/error），
    由 dsh 基座驱动 agent 循环，知识库检索经 /api/mcp 的 kb_* 工具完成。
    kb_ids/file_ids/thinking 参数在 agent 模式下不适用（由 dsh 自主规划）。
    """
    if not settings.DSH_AGENT_ENABLED:
        raise HTTPException(status_code=503, detail="dsh agent 功能未启用（DSH_AGENT_ENABLED=false）")
    # 会话校验/创建在流开始前完成（同 /ask/stream：必须立即提交，流式生成器
    # 在请求返回后才运行，届时请求事务已回滚）。
    # dsh 侧会话 id 由 ACP session/new 分配，激活与回写在服务层完成
    # （chat_sessions.dsh_session_id 为空或 resume 失败时自动新建）。
    session = await get_or_create_session(db, user, body.question, body.session_id)
    await db.commit()

    async def event_source():
        agen = stream_agent_chat_events(db, user, body.question, session)
        # 心跳保活（同 /ask/stream）：agent 工具调用可能长时间无帧
        pending: asyncio.Task | None = None
        while True:
            if pending is None:
                pending = asyncio.create_task(agen.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=15)
            if not done:
                yield ": ping\n\n"
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                break
            finally:
                pending = None
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
