import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.rag_query_log import RagQueryLog
from app.models.user import User
from app.services.llm import resolve_chat_llm, resolve_embed_llm
from app.services.skills.base import tool_spec
from app.services.skills.registry import execute_skill, get_enabled_skills
from app.services.permissions import accessible_ids, filter_accessible_ids
from app.services.rag import (
    FALLBACK_ANSWER,
    FILE_PARSING_ANSWER,
    ChatUnavailable,
    EmbeddingUnavailable,
    _find_block,
    count_ready_docs_for_files,
    attach_file_info,
    build_prompt,
    decide_grounded,
    expand_contexts,
    find_direct_return,
    reciprocal_rank_fusion,
    rerank_chunks,
    rewrite_question,
    search_chunks_blend,
    search_chunks_keyword,
    search_chunks_vector,
    search_question_chunks,
    trim_blocks,
)

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 10
# 工具调用循环上限轮数
MAX_TOOL_ROUNDS = 3
# 有启用 skills 时追加的系统提示词引导
TOOL_GUIDE = "你可以使用提供的工具联网检索最新信息；当知识库内容不足以回答时，请主动使用工具。"

# 未命中知识库、按配置降级为普通对话时的系统提示词：
# 保留"如实说明知识库没有"的诚实性，同时允许模型用通用知识正常应答
FALLBACK_CHAT_SYSTEM = (
    "你是企业内部智能助手。用户的问题没有命中企业知识库中的资料，"
    "请不要生硬地回复\"未找到相关信息\"，而是基于你的通用知识正常、友好地回答。"
    "回答开头先用一句简短说明：以下回答未参考企业知识库。"
    "若问题明显涉及企业内部制度、客户资料、内部文档等内部信息，"
    "请如实告知知识库中暂无相关资料，并建议用户补充上传或换个问法。"
)


def _fallback_messages(history: list[dict], question: str) -> list[dict]:
    """未命中知识库时的普通对话消息序列（带历史上下文，保持多轮连贯）。"""
    return (
        [{"role": "system", "content": FALLBACK_CHAT_SYSTEM}]
        + history[-HISTORY_LIMIT:]
        + [{"role": "user", "content": question}]
    )


# 推理型模型（如 MiniMax reasoner）输出的思考块标签
_THINK_RE = re.compile(r"<(?:think|thinking)>.*?</(?:think|thinking)>", re.I | re.S)
_TAG_OPEN = re.compile(r"<(?:think|thinking)>", re.I)
_TAG_CLOSE = re.compile(r"</(?:think|thinking)>", re.I)


def _strip_think_text(text: str) -> str:
    """一次性剥离完整 <think>…</think> / <thinking>…</thinking> 块。纯函数。"""
    if not text:
        return text
    return _THINK_RE.sub("", text).strip()


def _tag_prefix_len(text: str, tags: tuple[str, ...]) -> int:
    """返回 text 尾部最长的是哪个标签前缀的长度（大小写不敏感）；无则 0。"""
    t = text.lower()
    best = 0
    for tag in tags:
        prefix = tag.lower()
        for n in range(1, len(prefix) + 1):
            if n > best and t.endswith(prefix[:n]):
                best = n
    return best


class _ThinkStripper:
    """增量剥离思考块的状态机：跨 token 断句安全，逐段 feed 返回干净文本。

    未闭合标签残留超过 _MAX_PENDING 时按普通内容整体放行，避免模型输出
    异常导致内容被长期吞掉。"""
    _MAX_PENDING = 4096

    def __init__(self):
        self._pending = ""
        self._inside = False

    def feed(self, text: str) -> str:
        data = self._pending + text
        self._pending = ""
        out: list[str] = []
        pos = 0
        while pos < len(data):
            pattern = _TAG_CLOSE if self._inside else _TAG_OPEN
            m = pattern.search(data, pos)
            if m is None:
                rest = data[pos:]
                if self._inside:
                    # 块内内容整体扣住等待闭合（防跨 token 的闭标签被误发）
                    self._pending = rest
                else:
                    hold = _tag_prefix_len(rest, ("<think>", "<thinking>"))
                    if hold:
                        out.append(rest[:-hold])
                        self._pending = rest[-hold:]
                    else:
                        out.append(rest)
                break
            if not self._inside:
                out.append(data[pos:m.start()])
            self._inside = not self._inside
            pos = m.end()
        if len(self._pending) > self._MAX_PENDING:
            out.append(self._pending)
            self._pending = ""
            self._inside = False
        return "".join(out)


async def _clean_stream(stream):
    """把聊天流式生成器包一层：逐 token 剥离思考块后产出干净内容。"""
    stripper = _ThinkStripper()
    async for token in stream:
        if not token:
            continue
        clean = stripper.feed(token)
        if clean:
            yield clean


def _thinking_extra(use_thinking: bool) -> dict:
    """思考开关对应的请求体附加参数：关闭思考时向模型发送 LLM_CHAT_THINKING_PARAM=false。"""
    if use_thinking:
        return {}
    if settings.LLM_CHAT_THINKING_PARAM:
        return {settings.LLM_CHAT_THINKING_PARAM: False}
    return {}


def _resolve_thinking(thinking: bool | None) -> bool:
    """未显式指定时按 CHAT_DEFAULT_THINKING 默认。"""
    if thinking is None:
        return settings.CHAT_DEFAULT_THINKING
    return bool(thinking)


async def _load_skills(db: AsyncSession, tenant_id: int) -> list:
    """加载租户启用的 skills；任何失败按无工具降级（对话行为与无 skill 一致）。"""
    try:
        return await get_enabled_skills(db, tenant_id)
    except Exception:
        logger.warning("skills 加载失败（按无工具降级）", exc_info=True)
        return []


async def _run_agent(
    llm,
    messages: list[dict],
    skills: list,
    ctx: dict,
    on_tool_event=None,
    on_phase=None,
    on_token=None,
    extra: dict | None = None,
) -> tuple[str, list[str]]:
    """工具调用循环：chat_with_tools_stream → 执行 tool_calls → 结果回注，最多 MAX_TOOL_ROUNDS 轮。
    单个工具失败写"调用失败: 原因"继续；模型不支持工具调用抛 RuntimeError 由上层回退。
    on_phase("thinking"/"done") 标记 LLM 思考阶段；on_token 逐 token 吐出最终答案（真正流式）。
    extra 为透传给模型的附加请求参数（如 enable_thinking=false）。
    返回 (最终答案, tools_used)。"""
    tools = [tool_spec(s) for s in skills]
    by_name = {s.name: s for s in skills}
    tools_used: list[str] = []
    for _ in range(MAX_TOOL_ROUNDS):
        if on_phase is not None:
            await on_phase("thinking")
        resp = await llm.chat_with_tools_stream(
            messages, tools, on_token=on_token, **(extra or {})
        )
        calls = resp.get("tool_calls") or []
        if on_phase is not None:
            await on_phase("done")
        if not calls:
            return resp.get("content") or "", tools_used
        messages.append(
            {
                "role": "assistant",
                "content": resp.get("content") or "",
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c["arguments"], ensure_ascii=False),
                        },
                    }
                    for c in calls
                ],
            }
        )
        for c in calls:
            if on_tool_event is not None:
                await on_tool_event(c["name"], "start")
            skill = by_name.get(c["name"])
            if skill is None:
                result = f"调用失败: 未知工具 {c['name']}"
            else:
                try:
                    result = await execute_skill(skill, c["arguments"], ctx)
                except Exception as exc:
                    logger.warning("工具 %s 调用失败: %s", c["name"], exc)
                    result = f"调用失败: {exc}"
            if on_tool_event is not None:
                await on_tool_event(c["name"], "done")
            tools_used.append(c["name"])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": c["id"],
                    "name": c["name"],
                    "content": result,
                }
            )
    # 轮数用尽：不带工具再要一次最终回答（仍流式）
    if on_phase is not None:
        await on_phase("thinking")
    resp = await llm.chat_with_tools_stream(
        messages, [], on_token=on_token, **(extra or {})
    )
    if on_phase is not None:
        await on_phase("done")
    return resp.get("content") or "", tools_used


async def get_or_create_session(
    db: AsyncSession, user: User, question: str, session_id: int | None
) -> ChatSession:
    if session_id is not None:
        session = await db.get(ChatSession, session_id)
        if (
            session is not None
            and session.tenant_id == user.tenant_id
            and session.user_id == user.id
        ):
            return session
        # 前端 localStorage 里存的会话 id 已失效（被删除/越权/换账号）：静默开新会话，
        # 不报错——meta 帧会把新 session_id 回写前端，自动自愈
        logger.info("会话 %s 不存在或无权（用户 %s），改开新会话", session_id, user.id)
    session = ChatSession(
        tenant_id=user.tenant_id,
        user_id=user.id,
        title=question[:30] or "新会话",
    )
    db.add(session)
    await db.flush()  # 取 session.id，随后续消息一起提交
    return session


async def load_history(
    db: AsyncSession, session_id: int, limit: int = HISTORY_LIMIT
) -> list[dict]:
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


async def _prepare(
    db: AsyncSession,
    tenant_id: int,
    question: str,
    history: list[dict],
    kb_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    user: User | None = None,
) -> dict:
    """改写 → 混合检索（可按 kb_ids / file_ids 限定范围） → rerank → Small2Big → 长度裁剪。
    返回 {grounded, sources, prompt_chunks, direct_files?, direct_files_truncated?}。

    PR-H：当 file_ids 提供且总字符数 < RAG_DIRECT_FILE_MAX_CHARS，直接读全文塞进
    prompt（跳过 RAG 检索和嵌入）。超阈值或文件不可解析时回退 RAG。
    """
    # 权限范围：未指定 kb_ids 时检索"用户可读的所有 KB"；指定时过滤无权限的；
    # 文件直读/检索仅允许用户可读的文件。
    if user is not None:
        if kb_ids is None:
            kb_ids = await accessible_ids(db, user, "kb")
        else:
            kb_ids = await filter_accessible_ids(db, user, "kb", kb_ids)
        if file_ids:
            file_ids = await filter_accessible_ids(db, user, "file", file_ids)

    # PR-H：file_ids 直读快速通道（小文档即时问答）
    if file_ids:
        from app.services.file_context import build_direct_file_context

        direct = await build_direct_file_context(db, tenant_id, file_ids)
        if direct["context"] and not direct["too_large"]:
            logger.info(
                "direct file context: %d files, %d chars, skip RAG",
                len(direct["files"]), direct["total_chars"],
            )
            # 把每个文件作为独立 prompt chunk（doc_title=文件名）便于 build_prompt 标注来源
            prompt_chunks = []
            sources = []
            for info in direct["files"]:
                if info.get("chars", 0) <= 0 or info.get("skipped"):
                    continue
                # 从原始 context 里切出该文件段落
                marker = f"## {info['name']}\n"
                idx = direct["context"].find(marker)
                if idx == -1:
                    continue
                end = direct["context"].find("\n## ", idx + len(marker))
                body = direct["context"][idx + len(marker): end if end != -1 else None]
                prompt_chunks.append({"doc_title": info["name"], "content": body})
                sources.append({
                    "chunk_id": 0, "doc_id": 0,
                    "doc_title": info["name"],
                    "score": 1.0,
                    "excerpt": body[:200],
                    "direct": True,
                })
            return {
                "grounded": True,
                "sources": sources,
                "prompt_chunks": prompt_chunks,
                "direct_files": direct["files"],
                "direct_files_truncated": False,
            }
        # too_large 或 files 不可解析 → 继续走 RAG（用 file_ids 过滤 chunks）
        if not direct["context"]:
            logger.info("direct file context: 0 chars readable, fall back to RAG")
            # 直读不可用且所选文件一个都没解析好：提示"解析中"，避免空 RAG 兜底误导
            ready = await count_ready_docs_for_files(db, tenant_id, file_ids)
            if ready == 0:
                return {"grounded": False, "sources": [], "prompt_chunks": [], "files_unready": True}

    search_question = question
    if settings.RAG_QUERY_REWRITE and history:
        search_question = await rewrite_question(question, history)

    try:
        embed_llm = await resolve_embed_llm(caller="chat", tenant_id=tenant_id)
        vecs = await embed_llm.embed([search_question])
        query_vec = vecs[0]
    except Exception as exc:
        logger.warning("嵌入模型调用失败: %s", exc)
        raise EmbeddingUnavailable() from exc

    top_k = settings.RAG_TOP_K
    search_mode = (settings.RAG_SEARCH_MODE or "").lower()
    candidates: list[dict]
    if search_mode == "blend":
        # 二阶段：向量召回 top_k*10 → BM25 精排 → 相加融合 → top_k
        try:
            blend_rows = await search_chunks_blend(
                db, tenant_id, query_vec, search_question, top_k,
                settings.RAG_SCORE_THRESHOLD, kb_ids, file_ids,
            )
        except Exception as exc:
            logger.warning("blend 检索失败，回退 embedding+trgm: %s", exc)
            blend_rows = []
        if blend_rows:
            candidates = blend_rows
        else:
            # 回退到旧路径
            vector_rows = await search_chunks_vector(db, tenant_id, query_vec, top_k * 2, kb_ids, file_ids)
            if settings.RAG_HYBRID:
                keyword_rows = await search_chunks_keyword(db, tenant_id, search_question, top_k * 2, kb_ids, file_ids)
                candidates = reciprocal_rank_fusion(vector_rows, keyword_rows)[: top_k * 2]
            else:
                candidates = vector_rows[: top_k * 2]
    elif search_mode == "keywords":
        candidates = await search_chunks_keyword(db, tenant_id, search_question, top_k * 2, kb_ids, file_ids)
    elif search_mode == "embedding":
        candidates = await search_chunks_vector(db, tenant_id, query_vec, top_k * 2, kb_ids, file_ids)
    else:
        # 未知模式：走旧 RAG_HYBRID 路径
        vector_rows = await search_chunks_vector(db, tenant_id, query_vec, top_k * 2, kb_ids, file_ids)
        if settings.RAG_HYBRID:
            keyword_rows = await search_chunks_keyword(db, tenant_id, search_question, top_k * 2, kb_ids, file_ids)
            candidates = reciprocal_rank_fusion(vector_rows, keyword_rows)[: top_k * 2]
        else:
            candidates = vector_rows[: top_k * 2]

    # PR-C：自动问题命中加分。chunk_questions 中命中的 chunk 给原分加 question 相似度 × 0.5
    try:
        q_rows = await search_question_chunks(db, tenant_id, query_vec, top_k * 2, kb_ids, file_ids)
    except Exception as exc:
        logger.warning("question 检索失败（跳过）: %s", exc)
        q_rows = []
    if q_rows:
        q_score = {r["chunk_id"]: float(r["similarity"]) for r in q_rows}
        for c in candidates:
            boost = q_score.get(c["chunk_id"])
            if boost is not None:
                c["score"] = float(c["score"]) + boost * 0.5
                c["matched_via"] = "question"
        candidates.sort(key=lambda x: float(x["score"]), reverse=True)
        candidates = candidates[: top_k * 2]

    reranked = await rerank_chunks(search_question, candidates) if settings.RAG_RERANK else None
    if reranked is not None:
        # rerank 只用于排序：取 top_k 交给 LLM 判断相关性，不做硬阈值过滤
        # （本地嵌入/chat 模型的分数分布不校准，硬阈值会误杀真实命中）
        hits = reranked[:top_k]
    else:
        # 回退：原相似度阈值逻辑（融合后 top_k，最高分过阈值则整体保留）
        top = candidates[:top_k]
        scores = [float(h["score"]) for h in top]
        hits = top if decide_grounded(scores, settings.RAG_SCORE_THRESHOLD) else []

    if not hits:
        return {"grounded": False, "sources": [], "prompt_chunks": []}

    blocks = await expand_contexts(db, tenant_id, hits, settings.RAG_NEIGHBOR_WINDOW)
    blocks = trim_blocks(blocks, settings.RAG_MAX_CONTEXT_CHARS)
    prompt_chunks = [{"doc_title": b["doc_title"], "content": b["content"]} for b in blocks]

    sources = []
    for h in hits:
        block = _find_block(blocks, h)
        if block is None:
            continue
        sources.append(
            {
                "chunk_id": h["chunk_id"],
                "doc_id": h["doc_id"],
                "doc_title": h["doc_title"],
                "score": round(float(h.get("rerank_score", h["score"])), 4),
                "excerpt": block["content"][:200],
            }
        )
    await attach_file_info(db, sources)
    return {
        "grounded": True,
        "sources": sources,
        "prompt_chunks": prompt_chunks,
        "hits": hits,  # PR-D：find_direct_return 用
    }


async def _persist_turn(
    db: AsyncSession,
    session: ChatSession,
    user: User,
    question: str,
    answer: str,
    sources: list,
    grounded: bool,
) -> int:
    """落库：rag_query_logs（反馈闭环）+ 用户/助手两条消息 + 会话更新时间。"""
    log = RagQueryLog(
        tenant_id=user.tenant_id,
        user_id=user.id,
        question=question,
        answer=answer,
        grounded=grounded,
        sources=sources,
    )
    db.add(log)
    await db.flush()
    db.add(ChatMessage(session_id=session.id, role="user", content=question))
    db.add(
        ChatMessage(
            session_id=session.id,
            role="assistant",
            content=answer,
            sources=sources,
            grounded=grounded,
            query_log_id=log.id,
        )
    )
    # updated_at 为 TIMESTAMP（不带时区），写入 naive UTC
    session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    return log.id


async def chat_ask(
    db: AsyncSession,
    user: User,
    question: str,
    session_id: int | None = None,
    kb_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    thinking: bool | None = None,
) -> dict:
    """非流式问答。嵌入/对话模型不可用分别抛 EmbeddingUnavailable / ChatUnavailable。
    thinking=False 时向模型发送 LLM_CHAT_THINKING_PARAM=false 并剥离 <think> 输出。"""
    session = await get_or_create_session(db, user, question, session_id)
    history = await load_history(db, session.id)
    prepared = await _prepare(db, user.tenant_id, question, history, kb_ids, file_ids, user=user)
    skills = await _load_skills(db, user.tenant_id)
    use_thinking = _resolve_thinking(thinking)
    extra_kw = _thinking_extra(use_thinking)
    tools_used: list[str] = []

    # PR-D：高置信 directly_return 短答案（跳过 LLM）
    direct = None
    if prepared["grounded"]:
        direct = await find_direct_return(db, prepared.get("hits", []))

    if direct is not None:
        answer = direct["answer"]
        query_log_id = await _persist_turn(
            db, session, user, question, answer, prepared["sources"], True
        )
        return {
            "answer": answer,
            "sources": prepared["sources"],
            "grounded": True,
            "query_log_id": query_log_id,
            "session_id": session.id,
            "tools_used": [],
            "direct_return": True,
        }

    if not prepared["grounded"] and prepared.get("files_unready"):
        # 所选文件均未解析完成：明确提示等待解析（区别于普通未命中）
        answer = FILE_PARSING_ANSWER
    elif not prepared["grounded"] and not skills and settings.CHAT_FALLBACK_TO_LLM:
        # 未命中知识库且无可用工具：降级为普通对话，由模型用通用知识回答；
        # 模型不可用或输出为空时回退固定话术，保证总有应答
        try:
            chat_llm = await resolve_chat_llm(caller="chat", tenant_id=user.tenant_id, user_id=user.id)
            answer = await chat_llm.chat(_fallback_messages(history, question), **extra_kw)
        except Exception as exc:
            logger.warning("未命中知识库的普通对话生成失败，回退固定话术: %s", exc)
            answer = FALLBACK_ANSWER
        if not answer:
            answer = FALLBACK_ANSWER
    elif not prepared["grounded"] and not skills:
        # 未命中知识库、无工具、且关闭了普通对话兜底：固定话术
        answer = FALLBACK_ANSWER
    else:
        # grounded 或 未命中但有技能可用（可联网/工具回答）：正常 LLM / agent 生成
        messages = build_prompt(question, prepared["prompt_chunks"])
        try:
            chat_llm = await resolve_chat_llm(caller="chat", tenant_id=user.tenant_id, user_id=user.id)
            if skills:
                messages[0]["content"] += TOOL_GUIDE
                try:
                    answer, tools_used = await _run_agent(
                        chat_llm, messages, skills,
                        {"tenant_id": user.tenant_id, "user_id": user.id, "caller": "chat"},
                        extra=extra_kw,
                    )
                except RuntimeError:
                    # 模型不支持工具调用：回退原 chat 路径
                    answer = await chat_llm.chat(messages, **extra_kw)
                    tools_used = []
            else:
                answer = await chat_llm.chat(messages, **extra_kw)
        except Exception as exc:
            logger.warning("对话模型调用失败: %s", exc)
            raise ChatUnavailable() from exc
        if not answer:
            answer = FALLBACK_ANSWER

    answer = _strip_think_text(answer)

    query_log_id = await _persist_turn(
        db, session, user, question, answer, prepared["sources"], prepared["grounded"]
    )
    return {
        "answer": answer,
        "sources": prepared["sources"],
        "grounded": prepared["grounded"],
        "query_log_id": query_log_id,
        "session_id": session.id,
        "tools_used": tools_used,
    }


async def stream_chat_events(
    db: AsyncSession,
    user: User,
    question: str,
    session: ChatSession,
    kb_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    thinking: bool | None = None,
):
    """SSE 事件流生成器：meta → sources → token* → done；任何失败发 error 帧。
    thinking=False 时向模型发送 LLM_CHAT_THINKING_PARAM=false 并剥离 <think> 输出。"""
    yield {"type": "meta", "session_id": session.id}
    # 检索阶段（改写/向量检索/重排）可能耗时数秒，先发状态帧让前端即时反馈
    yield {"type": "status", "text": "正在检索知识库…"}
    try:
        history = await load_history(db, session.id)
        prepared = await _prepare(db, user.tenant_id, question, history, kb_ids, file_ids, user=user)
    except EmbeddingUnavailable as exc:
        yield {"type": "error", "detail": str(exc)}
        return
    except Exception:
        logger.exception("流式问答检索阶段失败")
        yield {"type": "error", "detail": "检索失败，请稍后重试"}
        return

    use_thinking = _resolve_thinking(thinking)
    extra_kw = _thinking_extra(use_thinking)

    yield {
        "type": "sources",
        "sources": prepared["sources"],
        "grounded": prepared["grounded"],
    }
    skills = await _load_skills(db, user.tenant_id)

    # PR-D：高置信 directly_return 短答案（跳过 LLM + 流式帧）
    if prepared["grounded"]:
        direct = await find_direct_return(db, prepared.get("hits", []))
        if direct is not None:
            yield {
                "type": "direct_return",
                "doc_id": direct["doc_id"],
                "score": round(direct["score"], 4),
            }
            answer = direct["answer"]
            yield {"type": "token", "content": answer}
            try:
                query_log_id = await _persist_turn(
                    db, session, user, question, answer, prepared["sources"], True
                )
            except Exception:
                logger.exception("直接返回消息落库失败")
                yield {"type": "error", "detail": "会话保存失败，请稍后重试"}
                return
            yield {"type": "done", "query_log_id": query_log_id}
            return

    if not prepared["grounded"] and prepared.get("files_unready"):
        # 所选文件均未解析完成：明确提示等待解析（区别于普通未命中）
        answer = FILE_PARSING_ANSWER
        yield {"type": "token", "content": answer}
    elif not prepared["grounded"] and not skills and settings.CHAT_FALLBACK_TO_LLM:
        # 未命中知识库且无可用工具：降级为普通对话，流式输出；
        # 模型不可用或输出为空时回退固定话术
        try:
            chat_llm = await resolve_chat_llm(caller="chat", tenant_id=user.tenant_id, user_id=user.id)
            parts: list[str] = []
            async for token in _clean_stream(
                chat_llm.chat_stream(_fallback_messages(history, question), **extra_kw)
            ):
                parts.append(token)
                yield {"type": "token", "content": token}
            answer = "".join(parts)
            if not answer:
                answer = FALLBACK_ANSWER
                yield {"type": "token", "content": answer}
        except Exception as exc:
            logger.warning("未命中知识库的普通对话生成失败，回退固定话术: %s", exc)
            answer = FALLBACK_ANSWER
            yield {"type": "token", "content": answer}
    elif not prepared["grounded"] and not skills:
        answer = FALLBACK_ANSWER
        yield {"type": "token", "content": answer}
    else:
        # grounded 或 未命中但有技能可用：正常 LLM / agent 生成
        messages = build_prompt(question, prepared["prompt_chunks"])
        parts: list[str] = []
        try:
            chat_llm = await resolve_chat_llm(caller="chat", tenant_id=user.tenant_id, user_id=user.id)
            need_stream = True
            if skills:
                messages[0]["content"] += TOOL_GUIDE
                # 事件实时逐帧吐出：思考/工具事件不再等 agent 跑完才一次性返回
                events_q: asyncio.Queue = asyncio.Queue()
                stripper = _ThinkStripper()

                async def _on_tool(name, status):
                    await events_q.put({"type": "tool", "name": name, "status": status})

                async def _on_phase(status):
                    await events_q.put({"type": "thinking", "status": status})

                async def _on_token(content):
                    # 剥离推理块后仅把干净内容入队
                    clean = stripper.feed(content)
                    if clean:
                        await events_q.put({"type": "token", "content": clean})

                agent_task = asyncio.create_task(
                    _run_agent(
                        chat_llm, messages, skills,
                        {"tenant_id": user.tenant_id, "user_id": user.id, "caller": "chat"},
                        _on_tool, _on_phase, _on_token,
                        extra=extra_kw,
                    )
                )
                # 边跑边排空事件；agent 结束后清空剩余
                while True:
                    if agent_task.done():
                        while not events_q.empty():
                            try:
                                yield events_q.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                        break
                    try:
                        ev = await asyncio.wait_for(events_q.get(), timeout=0.3)
                        yield ev
                    except asyncio.TimeoutError:
                        continue
                try:
                    agent_result = agent_task.result()
                except RuntimeError:
                    agent_result = None  # 模型不支持工具调用：回退流式生成
                if agent_result is not None:
                    need_stream = False  # 最终答案已由 on_token 逐 token 流出
                    agent_answer, _tools_used = agent_result
                    answer = _strip_think_text(agent_answer) or FALLBACK_ANSWER
                    if not answer or answer == FALLBACK_ANSWER:
                        yield {"type": "token", "content": answer}
            if need_stream:
                async for token in _clean_stream(chat_llm.chat_stream(messages, **extra_kw)):
                    parts.append(token)
                    yield {"type": "token", "content": token}
                answer = "".join(parts)
        except Exception:
            logger.warning("流式生成失败", exc_info=True)
            yield {"type": "error", "detail": "生成失败，请稍后重试"}
            return

    answer = _strip_think_text(answer)

    try:
        query_log_id = await _persist_turn(
            db, session, user, question, answer, prepared["sources"], prepared["grounded"]
        )
    except Exception:
        logger.exception("流式问答消息落库失败")
        yield {"type": "error", "detail": "会话保存失败，请稍后重试"}
        return
    yield {"type": "done", "query_log_id": query_log_id}


# ---------------------------------------------------------------------------
# dsh agent 模式（dsh 基座实施方案阶段 2：ACP）
# ---------------------------------------------------------------------------


async def stream_agent_chat_events(
    db: AsyncSession,
    user: User,
    question: str,
    session: ChatSession,
):
    """dsh agent 模式的 SSE 事件流：meta → token*/tool* → done；失败发 error 帧。

    会话激活：chat_sessions.dsh_session_id 已绑定 → session/resume（PG 持久化
    跨进程恢复，模型上下文由 dsh 侧还原；历史展示靠我们自己的 chat_messages，
    resume 不回放历史 update）；未绑定或 resume 失败（会话不存在/cwd 漂移）
    → session/new 并把新 id 显式 UPDATE 回写。

    ACP session/update → SSE 帧映射：
    - agent_message_chunk（text 块）→ token 帧
    - tool_call → tool 帧（status=start）
    - tool_call_update（status=completed/failed）→ tool 帧（status=done，is_error）
    - 其余（usage_update / plan / thought 等）不透传

    落库用 _persist_turn 等价逻辑：answer 取 token 帧拼接（空则回退 final_response），
    sources 为空、grounded=False（agent 自行调用 MCP 工具，不走本地 RAG 管线）。
    """
    from app.services.acp_bridge import bridge

    yield {"type": "meta", "session_id": session.id}
    try:
        dsh_sid = session.dsh_session_id
        if dsh_sid:
            try:
                await bridge.resume_session(user, dsh_sid)
            except Exception as exc:
                logger.info("dsh 会话 %s 恢复失败（%s），退回新会话", dsh_sid, exc)
                dsh_sid = None
        if not dsh_sid:
            dsh_sid = await bridge.new_session(user)
            # 注意：流式生成器运行时请求事务已结束，session ORM 对象已被 detach，
            # 直接改属性再 commit 不会落库，必须显式 UPDATE
            await db.execute(
                update(ChatSession)
                .where(ChatSession.id == session.id)
                .values(dsh_session_id=dsh_sid)
            )
            await db.commit()
            session.dsh_session_id = dsh_sid
    except Exception:
        logger.exception("dsh 会话激活失败")
        yield {"type": "error", "detail": "agent 会话创建失败，请稍后重试"}
        return

    parts: list[str] = []
    call_names: dict[str, str] = {}
    final_response = ""
    finish_reason: str | None = None
    try:
        async for item in bridge.run_turn(user, dsh_sid, question):
            if item["kind"] == "error":
                yield {"type": "error", "detail": item["detail"]}
                return
            if item["kind"] == "done":
                final_response = item["final_response"] or ""
                finish_reason = item["finish_reason"]
                break
            event = item["event"]
            etype = event.get("session_update")
            if etype == "agent_message_chunk":
                content = event.get("content") or {}
                text = (content.get("text") or "") if content.get("type") == "text" else ""
                if text:
                    parts.append(text)
                    yield {"type": "token", "content": text}
            elif etype == "tool_call":
                name = str(event.get("title") or "")
                call_id = str(event.get("tool_call_id") or "")
                if call_id:
                    call_names[call_id] = name
                yield {"type": "tool", "name": name, "status": "start", "call_id": call_id}
            elif etype == "tool_call_update":
                status = event.get("status")
                if status in ("completed", "failed"):
                    call_id = str(event.get("tool_call_id") or "")
                    yield {
                        "type": "tool",
                        "name": call_names.get(call_id, ""),
                        "status": "done",
                        "call_id": call_id,
                        "is_error": status == "failed",
                    }
    except Exception:
        logger.exception("dsh agent 流式运行失败")
        yield {"type": "error", "detail": "agent 运行失败，请稍后重试"}
        return

    answer = "".join(parts).strip() or final_response.strip()
    if not parts and answer:
        # agent 没有产出任何 agent_message_chunk（异常路径），把最终响应补成一帧
        yield {"type": "token", "content": answer}
    if not answer:
        yield {"type": "error", "detail": "agent 未返回有效回答"}
        return
    try:
        query_log_id = await _persist_turn(db, session, user, question, answer, [], False)
    except Exception:
        logger.exception("agent 会话消息落库失败")
        yield {"type": "error", "detail": "会话保存失败，请稍后重试"}
        return
    done: dict = {"type": "done", "query_log_id": query_log_id}
    if finish_reason:
        done["finish_reason"] = finish_reason
    yield done
