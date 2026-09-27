import json
import logging
import re

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import KnowledgeDocument
from app.models.library_file import LibraryFile
from app.models.user import User
from app.services.llm import resolve_chat_llm, resolve_embed_llm, resolve_rerank_llm
from app.services.permissions import accessible_ids, filter_accessible_ids

logger = logging.getLogger(__name__)

FALLBACK_ANSWER = "未在知识库中找到相关信息，建议转人工处理。"
# 选中文件尚未解析完成时给出的提示（区别于"真没找到"）
FILE_PARSING_ANSWER = "所选文件尚未完成解析（或暂不支持解析该格式），请稍后在知识库中确认解析状态后再试。"


class EmbeddingUnavailable(Exception):
    def __init__(self, detail: str = "嵌入模型不可用，请检查嵌入服务配置（Ollama 是否启动 / API Key 是否配置）后再试。"):
        super().__init__(detail)


class ChatUnavailable(Exception):
    def __init__(self, detail: str = "对话模型不可用，请检查 LLM 服务配置（API Key / Ollama）后再试。"):
        super().__init__(detail)


def decide_grounded(scores: list[float], threshold: float) -> bool:
    """检索结果是否足以支撑回答：非空且最高分达到阈值。"""
    return bool(scores) and max(scores) >= threshold


def reciprocal_rank_fusion(
    *ranked_lists: list[dict], k: int = 60
) -> list[dict]:
    """RRF 融合多个已排序结果列表（按 chunk_id 去重）。纯函数。
    每个条目必须含 chunk_id 与原始 score；融合后保留最高原始 score 并附加 rrf_score。"""
    fused: dict[int, dict] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            cid = item["chunk_id"]
            rrf = 1.0 / (k + rank)
            if cid not in fused:
                merged = dict(item)
                merged["rrf_score"] = rrf
                fused[cid] = merged
            else:
                fused[cid]["rrf_score"] += rrf
                if float(item.get("score", 0.0)) > float(fused[cid].get("score", 0.0)):
                    fused[cid]["score"] = item["score"]
    return sorted(fused.values(), key=lambda x: x["rrf_score"], reverse=True)


def merge_windows(hits: list[dict], window: int) -> dict[int, list[tuple[int, int]]]:
    """Small2Big：把命中切片的 ±window 区间按文档合并（重叠/相邻区间只展开一次）。纯函数。"""
    if window < 0:
        window = 0
    by_doc: dict[int, list[tuple[int, int]]] = {}
    for h in hits:
        idx = h["chunk_index"]
        by_doc.setdefault(h["doc_id"], []).append((max(idx - window, 0), idx + window))
    merged: dict[int, list[tuple[int, int]]] = {}
    for doc_id, ranges in by_doc.items():
        ranges.sort()
        cur: list[tuple[int, int]] = []
        for start, end in ranges:
            if cur and start <= cur[-1][1] + 1:
                cur[-1] = (cur[-1][0], max(cur[-1][1], end))
            else:
                cur.append((start, end))
        merged[doc_id] = cur
    return merged


def build_prompt(question: str, chunks: list[dict]) -> list[dict]:
    """构建带编号上下文的 RAG prompt。"""
    context = "\n\n".join(
        f"[{i + 1}]（来源：{c['doc_title']}）\n{c['content']}" for i, c in enumerate(chunks)
    )
    system = (
        "你是企业内部知识库助手。只能基于给定的上下文回答问题；"
        "如果上下文中没有答案，请明确说明不知道，不要编造。"
        "回答中引用上下文时用 [1]、[2] 这样的编号标注来源。"
    )
    user = f"上下文：\n{context}\n\n问题：{question}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def search_chunks_vector(
    db: AsyncSession,
    tenant_id: int,
    query_vec: list[float],
    limit: int,
    kb_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
) -> list[dict]:
    """pgvector 余弦距离检索，返回带相似度分数的切片；kb_ids/file_ids 限定范围。

    PR-G：file_ids 优先级 > kb_ids（更精确）。
    """
    sql = """
        SELECT c.id AS chunk_id,
               c.document_id AS doc_id,
               c.chunk_index,
               c.content,
               d.title AS doc_title,
               1 - (c.embedding <=> CAST(:vec AS vector)) AS score
        FROM document_chunks c
        JOIN knowledge_documents d ON d.id = c.document_id
        WHERE c.tenant_id = :tid AND c.embedding IS NOT NULL
          AND d.kb_id NOT IN (SELECT id FROM knowledge_bases WHERE deleted_at IS NOT NULL)
          AND (d.file_id IS NULL OR d.file_id NOT IN (SELECT id FROM library_files WHERE deleted_at IS NOT NULL))
    """
    params: dict = {"tid": tenant_id, "k": limit}
    if file_ids:
        sql += " AND d.file_id = ANY(CAST(:file_ids AS bigint[]))"
        params["file_ids"] = [int(x) for x in file_ids]
    elif kb_ids:
        sql += " AND d.kb_id = ANY(CAST(:kb_ids AS bigint[]))"
        params["kb_ids"] = [int(x) for x in kb_ids]
    sql += " ORDER BY c.embedding <=> CAST(:vec AS vector) LIMIT :k"
    vec_str = "[" + ",".join(repr(float(x)) for x in query_vec) + "]"
    params["vec"] = vec_str
    result = await db.execute(text(sql), params)
    return [dict(r) for r in result.mappings().all()]


async def search_chunks_keyword(
    db: AsyncSession,
    tenant_id: int,
    question: str,
    limit: int,
    kb_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
) -> list[dict]:
    """索引化关键词检索：tsvector BM25（GIN tsvector）+ pg_trgm word_similarity（GIN trgm）。

    原先 similarity(c.content, :q) 无阈值无法走索引，是随库增长的全表线性扫描；
    现在用 @@ 与 %（word_similarity 阈值）过滤，命中两个 GIN 索引后仅对候选排序。
    plainto_tsquery 对任意用户输入不抛错（websearch_to_tsquery 对引号会报错）。"""
    tsq = "plainto_tsquery('simple', :q)"
    sql = f"""
        SELECT c.id AS chunk_id,
               c.document_id AS doc_id,
               c.chunk_index,
               c.content,
               d.title AS doc_title,
               (ts_rank_cd(c.search_vector, {tsq})
                + word_similarity(:q, c.content)) AS score
        FROM document_chunks c
        JOIN knowledge_documents d ON d.id = c.document_id
        WHERE c.tenant_id = :tid
          AND c.search_vector IS NOT NULL
          AND (c.search_vector @@ {tsq} OR c.content % :q)
          AND d.kb_id NOT IN (SELECT id FROM knowledge_bases WHERE deleted_at IS NOT NULL)
          AND (d.file_id IS NULL OR d.file_id NOT IN (SELECT id FROM library_files WHERE deleted_at IS NOT NULL))
    """
    params: dict = {"q": question, "tid": tenant_id, "k": limit}
    if file_ids:
        sql += " AND d.file_id = ANY(CAST(:file_ids AS bigint[]))"
        params["file_ids"] = [int(x) for x in file_ids]
    elif kb_ids:
        sql += " AND d.kb_id = ANY(CAST(:kb_ids AS bigint[]))"
        params["kb_ids"] = [int(x) for x in kb_ids]
    sql += (
        f" ORDER BY (ts_rank_cd(c.search_vector, {tsq})"
        f" + word_similarity(:q, c.content)) DESC LIMIT :k"
    )
    result = await db.execute(text(sql), params)
    return [dict(r) for r in result.mappings().all()]


async def search_question_chunks(
    db: AsyncSession,
    tenant_id: int,
    query_vec: list[float],
    limit: int,
    kb_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
) -> list[dict]:
    """向量检索 chunk_questions 表（PR-C，参照 MaxKB Problem 模型）。

    返回 [{chunk_id, similarity, content}]，similarity 为 1 - cosine_distance。
    kb_ids/file_ids 限定文档所属范围；任一阶段失败由调用方捕获。
    """
    sql = """
        SELECT cq.chunk_id,
               cq.content AS question,
               1 - (cq.embedding <=> CAST(:vec AS vector)) AS similarity
        FROM chunk_questions cq
        WHERE cq.tenant_id = :tid AND cq.embedding IS NOT NULL
          AND cq.chunk_id IN (
            SELECT c.id FROM document_chunks c
            JOIN knowledge_documents d ON d.id = c.document_id
            WHERE d.kb_id NOT IN (SELECT id FROM knowledge_bases WHERE deleted_at IS NOT NULL)
              AND (d.file_id IS NULL OR d.file_id NOT IN (SELECT id FROM library_files WHERE deleted_at IS NOT NULL))
    """
    params: dict = {
        "tid": tenant_id,
        "vec": "[" + ",".join(repr(float(x)) for x in query_vec) + "]",
        "limit": limit,
    }
    if file_ids:
        sql += " AND d.file_id = ANY(CAST(:file_ids AS bigint[]))"
        params["file_ids"] = [int(x) for x in file_ids]
    elif kb_ids:
        sql += " AND d.kb_id = ANY(CAST(:kb_ids AS bigint[]))"
        params["kb_ids"] = [int(x) for x in kb_ids]
    sql += """
          )
        ORDER BY cq.embedding <=> CAST(:vec AS vector)
        LIMIT :limit
    """
    result = await db.execute(text(sql), params)
    return [dict(r) for r in result.mappings().all()]


async def search_chunks_blend(
    db: AsyncSession,
    tenant_id: int,
    query_vec: list[float],
    question: str,
    limit: int,
    score_threshold: float,
    kb_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
) -> list[dict]:
    """二阶段混合检索（参照 MaxKB blend_search.sql）。

    阶段 1：pgvector 余弦召回 top_k*10（封顶 500）。
    阶段 2：对候选逐条算 BM25（ts_rank_cd + websearch_to_tsquery）。
    综合分 = (1 - distance) + ts_rank_cd，按 comprehensive_score 降序、阈值过滤后取 top_k。

    返回的 chunk 字典结构与 search_chunks_vector 一致（含 score=comprehensive_score）。
    任一阶段失败由调用方捕获并降级。"""
    # 第一阶段：向量召回 top_k*10（封顶 500）；kb_ids 直接拼到 SQL
    vector_sql = """
        SELECT c.id AS chunk_id,
               c.document_id AS doc_id,
               c.chunk_index,
               c.content,
               d.title AS doc_title,
               d.kb_id,
               (c.embedding <=> CAST(:vec AS vector)) AS distance
        FROM document_chunks c
        JOIN knowledge_documents d ON d.id = c.document_id
        WHERE c.tenant_id = :tid
          AND c.embedding IS NOT NULL
          AND c.search_vector IS NOT NULL
          AND d.kb_id NOT IN (SELECT id FROM knowledge_bases WHERE deleted_at IS NOT NULL)
          AND (d.file_id IS NULL OR d.file_id NOT IN (SELECT id FROM library_files WHERE deleted_at IS NOT NULL))
    """
    vec_params: dict = {
        "tid": tenant_id,
        "vec": "[" + ",".join(repr(float(x)) for x in query_vec) + "]",
        "limit_topn": limit * 10,
    }
    if file_ids:
        vector_sql += " AND d.file_id = ANY(CAST(:file_ids AS bigint[]))"
        vec_params["file_ids"] = [int(x) for x in file_ids]
    elif kb_ids:
        vector_sql += " AND d.kb_id = ANY(CAST(:kb_ids AS bigint[]))"
        vec_params["kb_ids"] = [int(x) for x in kb_ids]
    vector_sql += """
        ORDER BY c.embedding <=> CAST(:vec AS vector)
        LIMIT LEAST(:limit_topn, 500)
    """
    vec_rows = (await db.execute(text(vector_sql), vec_params)).mappings().all()
    if not vec_rows:
        return []

    # 第二阶段：对候选算 BM25，相加融合。
    # 关键：VALUES 子句不能用 ::type 语法（asyncpg 与 :param 占位符冲突），必须用 CAST(:name AS type)。
    placeholders = ", ".join(
        f"(CAST(:id{i} AS bigint), CAST(:doc{i} AS bigint), CAST(:idx{i} AS int), "
        f"CAST(:content{i} AS text), CAST(:title{i} AS text), CAST(:dist{i} AS float8))"
        for i in range(len(vec_rows))
    )
    blend_sql = f"""
        SELECT chunk_id, doc_id, chunk_index, content, doc_title,
               comprehensive_score AS score
        FROM (
            SELECT DISTINCT ON (v.chunk_id) v.chunk_id, v.doc_id, v.chunk_index,
                   v.content, v.doc_title,
                   (1 - v.distance
                    + COALESCE(ts_rank_cd(c.search_vector, websearch_to_tsquery('simple', :q), 32), 0)
                   ) AS comprehensive_score
            FROM (VALUES {placeholders}) AS v(chunk_id, doc_id, chunk_index, content, doc_title, distance)
            JOIN document_chunks c ON c.id = v.chunk_id
            ORDER BY v.chunk_id, comprehensive_score DESC
        ) sub
        WHERE comprehensive_score > :threshold
        ORDER BY comprehensive_score DESC
        LIMIT :topk
    """
    bm_params: dict = {
        "q": question,
        "threshold": score_threshold,
        "topk": limit,
    }
    for i, row in enumerate(vec_rows):
        bm_params[f"id{i}"] = row["chunk_id"]
        bm_params[f"doc{i}"] = row["doc_id"]
        bm_params[f"idx{i}"] = row["chunk_index"]
        bm_params[f"content{i}"] = row["content"]
        bm_params[f"title{i}"] = row["doc_title"]
        bm_params[f"dist{i}"] = float(row["distance"])
    rows = (await db.execute(text(blend_sql), bm_params)).mappings().all()
    return [dict(r) for r in rows]


async def expand_contexts(
    db: AsyncSession, tenant_id: int, hits: list[dict], window: int
) -> list[dict]:
    """Small2Big：按合并后的窗口取相邻切片，返回去重后的上下文块。
    所有文档一次批量查询（原每文档一查的 N+1 已消除）。"""
    windows = merge_windows(hits, window)
    if not windows:
        return []
    all_ranges = [r for ranges in windows.values() for r in ranges]
    lo = min(s for s, _ in all_ranges)
    hi = max(e for _, e in all_ranges)
    stmt = text(
        """
        SELECT document_id, chunk_index, content
        FROM document_chunks
        WHERE tenant_id = :tid AND document_id = ANY(CAST(:dids AS bigint[]))
          AND chunk_index BETWEEN :lo AND :hi
        ORDER BY document_id, chunk_index
        """
    )
    result = await db.execute(stmt, {"tid": tenant_id, "dids": list(windows), "lo": lo, "hi": hi})
    # document_id -> {chunk_index: content}
    chunks_by_doc: dict[int, dict[int, str]] = {}
    for r in result.mappings().all():
        chunks_by_doc.setdefault(r.document_id, {})[r.chunk_index] = r.content
    blocks: list[dict] = []
    for doc_id, ranges in windows.items():
        by_index = chunks_by_doc.get(doc_id, {})
        doc_title = next((h["doc_title"] for h in hits if h["doc_id"] == doc_id), "")
        for start, end in ranges:
            content = "\n".join(by_index[i] for i in range(start, end + 1) if i in by_index)
            if content:
                blocks.append(
                    {
                        "doc_id": doc_id,
                        "doc_title": doc_title,
                        "start": start,
                        "end": end,
                        "content": content,
                    }
                )
    return blocks


def _find_block(blocks: list[dict], hit: dict) -> dict | None:
    for b in blocks:
        if b["doc_id"] == hit["doc_id"] and b["start"] <= hit["chunk_index"] <= b["end"]:
            return b
    return None


async def find_direct_return(
    db: AsyncSession, hits: list[dict]
) -> dict | None:
    """PR-D：检查 hits 中是否有文档设置了 directly_return 短答案。

    仅看 top 3 命中；任一文档设了 directly_return_answer 且相似度达到阈值，
    返回 {doc_id, answer, similarity, doc_title, chunk_id, score}。
    无命中返回 None。"""
    if not hits:
        return None
    candidate_doc_ids = list({h["doc_id"] for h in hits[:3]})
    rows = (
        await db.execute(
            select(
                KnowledgeDocument.id,
                KnowledgeDocument.directly_return_answer,
                KnowledgeDocument.directly_return_similarity,
                KnowledgeDocument.title,
            ).where(
                KnowledgeDocument.id.in_(candidate_doc_ids),
                KnowledgeDocument.directly_return_answer.isnot(None),
                KnowledgeDocument.directly_return_similarity.isnot(None),
            )
        )
    ).all()
    if not rows:
        return None
    doc_map = {r.id: r for r in rows}
    # 按 hits 顺序遍历（已按 score 降序），返回第一个达阈值的
    for h in hits:
        doc = doc_map.get(h["doc_id"])
        if doc is None:
            continue
        threshold = float(doc.directly_return_similarity)
        hit_score = float(h.get("rerank_score", h.get("score", 0)))
        if hit_score >= threshold:
            return {
                "doc_id": doc.id,
                "doc_title": doc.title,
                "answer": doc.directly_return_answer,
                "threshold": threshold,
                "chunk_id": h["chunk_id"],
                "score": hit_score,
            }
    return None


async def attach_file_info(db: AsyncSession, sources: list[dict]) -> None:
    """原地为 sources 回填 library 文件信息（file_id/file_name/file_type/file_size）。

    doc.file_id 为 NULL 或文件已软删时四个字段均为 None，前端凭此判断是否可直达预览。
    批量 in_ 查询，避免 N+1。"""
    doc_ids = {s["doc_id"] for s in sources if s.get("doc_id") is not None}
    if not doc_ids:
        for s in sources:
            s.setdefault("file_id", None)
            s.setdefault("file_name", None)
            s.setdefault("file_type", None)
            s.setdefault("file_size", None)
        return

    doc_rows = (
        await db.execute(
            select(
                KnowledgeDocument.id,
                KnowledgeDocument.file_id,
                KnowledgeDocument.file_name,
                KnowledgeDocument.file_type,
            ).where(KnowledgeDocument.id.in_(doc_ids))
        )
    ).all()
    doc_map = {r.id: r for r in doc_rows}

    file_ids = {r.file_id for r in doc_rows if r.file_id is not None}
    size_map: dict[int, int] = {}
    if file_ids:
        file_rows = (
            await db.execute(
                select(LibraryFile.id, LibraryFile.file_size).where(
                    LibraryFile.id.in_(file_ids),
                    LibraryFile.deleted_at.is_(None),
                )
            )
        ).all()
        size_map = {r.id: r.file_size for r in file_rows}

    for s in sources:
        doc = doc_map.get(s.get("doc_id"))
        file_id = doc.file_id if doc else None
        if file_id is None or file_id not in size_map:
            s["file_id"] = None
            s["file_name"] = None
            s["file_type"] = None
            s["file_size"] = None
        else:
            s["file_id"] = file_id
            s["file_name"] = doc.file_name
            s["file_type"] = doc.file_type
            s["file_size"] = size_map[file_id]


async def count_ready_docs_for_files(
    db: AsyncSession, tenant_id: int, file_ids: list[int]
) -> int:
    """所选文件中已解析完成（status=ready）的文档数。"""
    return (
        await db.scalar(
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.file_id.in_(file_ids),
                KnowledgeDocument.status == "ready",
            )
        )
        or 0
    )


async def rag_query(
    db: AsyncSession,
    tenant_id: int,
    question: str,
    top_k: int | None = None,
    kb_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    user: User | None = None,
) -> dict:
    top_k = top_k or settings.RAG_TOP_K

    # 权限范围：未指定 kb_ids 时检索"用户可读的所有 KB"；指定时过滤掉无权限的。
    # 文件直读/检索同样只允许用户可读的文件。
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
            prompt_chunks = []
            sources = []
            for info in direct["files"]:
                if info.get("chars", 0) <= 0 or info.get("skipped"):
                    continue
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
                "answer": "",  # 由调用方负责 generate
                "sources": sources,
                "context": "\n\n".join(
                    f"[{i + 1}]（来源：{c['doc_title']}）\n{c['content']}"
                    for i, c in enumerate(prompt_chunks)
                ),
            }
        # 直读不可用（超限/不可解析）：若所选文件一个都没解析好，直接提示"解析中"，
        # 避免对空 RAG 兜底成"找不到上下文"误导
        if not direct["context"]:
            ready = await count_ready_docs_for_files(db, tenant_id, file_ids)
            if ready == 0:
                return {"answer": FILE_PARSING_ANSWER, "sources": [], "grounded": False}

    # 1. 问题向量化
    try:
        embed_llm = await resolve_embed_llm(caller="rag", tenant_id=tenant_id)
        vecs = await embed_llm.embed([question])
        query_vec = vecs[0]
    except Exception as exc:
        logger.warning("嵌入模型调用失败: %s", exc)
        raise EmbeddingUnavailable() from exc

    # 2. 检索（按 RAG_SEARCH_MODE 分支：blend / embedding / keywords；默认旧 RAG_HYBRID 路径）
    search_mode = (settings.RAG_SEARCH_MODE or "").lower()
    if search_mode == "blend":
        try:
            blend_rows = await search_chunks_blend(
                db, tenant_id, query_vec, question, top_k,
                settings.RAG_SCORE_THRESHOLD, kb_ids, file_ids,
            )
        except Exception as exc:
            logger.warning("blend 检索失败，回退 embedding+trgm: %s", exc)
            blend_rows = []
        if blend_rows:
            hits = blend_rows
        else:
            vector_rows = await search_chunks_vector(db, tenant_id, query_vec, top_k * 2, kb_ids, file_ids)
            if settings.RAG_HYBRID:
                keyword_rows = await search_chunks_keyword(db, tenant_id, question, top_k * 2, kb_ids, file_ids)
                hits = reciprocal_rank_fusion(vector_rows, keyword_rows)[:top_k]
            else:
                hits = vector_rows[:top_k]
    elif search_mode == "keywords":
        kw_rows = await search_chunks_keyword(db, tenant_id, question, top_k * 2, kb_ids, file_ids)
        hits = kw_rows[:top_k]
    elif search_mode == "embedding":
        vec_rows = await search_chunks_vector(db, tenant_id, query_vec, top_k * 2, kb_ids, file_ids)
        hits = vec_rows[:top_k]
    else:
        vector_rows = await search_chunks_vector(db, tenant_id, query_vec, top_k * 2, kb_ids, file_ids)
        if settings.RAG_HYBRID:
            keyword_rows = await search_chunks_keyword(db, tenant_id, question, top_k * 2, kb_ids, file_ids)
            hits = reciprocal_rank_fusion(vector_rows, keyword_rows)[:top_k]
        else:
            hits = vector_rows[:top_k]

    # PR-C：自动问题命中加分（chunk_questions）
    try:
        q_rows = await search_question_chunks(db, tenant_id, query_vec, top_k * 2, kb_ids)
    except Exception as exc:
        logger.warning("question 检索失败（跳过）: %s", exc)
        q_rows = []
    if q_rows:
        q_score = {r["chunk_id"]: float(r["similarity"]) for r in q_rows}
        for h in hits:
            boost = q_score.get(h["chunk_id"])
            if boost is not None:
                h["score"] = float(h["score"]) + boost * 0.5
        hits.sort(key=lambda x: float(x["score"]), reverse=True)
        hits = hits[:top_k]

    # 3. 阈值兜底（按融合后条目的最高原始相似度判断）
    scores = [float(r["score"]) for r in hits]
    if not decide_grounded(scores, settings.RAG_SCORE_THRESHOLD):
        return {"answer": FALLBACK_ANSWER, "sources": [], "grounded": False}

    # 4. Small2Big 上下文扩展
    blocks = await expand_contexts(db, tenant_id, hits, settings.RAG_NEIGHBOR_WINDOW)
    prompt_chunks = [
        {"doc_title": b["doc_title"], "content": b["content"]} for b in blocks
    ]

    # 5. 生成
    messages = build_prompt(question, prompt_chunks)
    try:
        chat_llm = await resolve_chat_llm(caller="rag", tenant_id=tenant_id)
        answer = await chat_llm.chat(messages)
    except Exception as exc:
        logger.warning("对话模型调用失败: %s", exc)
        raise ChatUnavailable() from exc

    # 6. 返回
    sources = []
    for r in hits:
        block = _find_block(blocks, r)
        excerpt_source = block["content"] if block else r["content"]
        sources.append(
            {
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "doc_title": r["doc_title"],
                "score": round(float(r["score"]), 4),
                "excerpt": excerpt_source[:200],
            }
        )
    await attach_file_info(db, sources)
    return {"answer": answer, "sources": sources, "grounded": True}


# ---------------------------------------------------------------------------
# 三期：多轮对话问答 pipeline 扩展（问题改写 / Rerank 精排 / 长度裁剪 / 追问推荐）
# ---------------------------------------------------------------------------

REWRITE_SYSTEM = (
    "你是检索问题改写助手。根据对话历史，把用户的最新问题改写为完整、独立、"
    "无歧义的检索问题（补全指代与省略的主语）。只输出改写后的问题本身。"
)

RERANK_SYSTEM = (
    "你是检索相关性评估助手。给定一个问题和若干候选片段，为每个片段打 0~1 的"
    "相关性分数（1 表示高度相关）。只输出 JSON 数组，如 [0.9, 0.2, 0.0]，"
    "数组顺序与片段编号一致，不要输出任何其他文字。"
)

def parse_json_array(text: str) -> list | None:
    """从 LLM 输出中容错提取 JSON 数组，失败返回 None。纯函数。"""
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, list) else None


_THINK_RE = re.compile(r"<(?:think|thinking)>.*?</(?:think|thinking)>", re.I | re.S)


def _strip_think(text: str) -> str:
    """剥离推理型模型输出里的 <think>…</think> 思考块（rerank 前处理）。纯函数。"""
    if not text:
        return text
    return _THINK_RE.sub("", text).strip()


def parse_scores(text: str, count: int) -> list[float] | None:
    """解析 rerank 分数数组：长度必须覆盖 count，逐项为 0~1 数值，否则 None。纯函数。"""
    arr = parse_json_array(text)
    if arr is None or len(arr) < count:
        return None
    scores = []
    for item in arr[:count]:
        try:
            score = float(item)
        except (TypeError, ValueError):
            return None
        scores.append(min(max(score, 0.0), 1.0))
    return scores


def parse_string_array(text: str, limit: int = 3) -> list[str]:
    """解析字符串数组（追问建议），失败返回 []。纯函数。"""
    arr = parse_json_array(text)
    if arr is None:
        return []
    return [str(item) for item in arr if isinstance(item, str) and item.strip()][:limit]


def trim_blocks(blocks: list[dict], max_chars: int) -> list[dict]:
    """按顺序累计加入上下文块直到达到字符上限；首个超限块截断保留。纯函数。"""
    if max_chars <= 0 or not blocks:
        return []
    selected: list[dict] = []
    total = 0
    for block in blocks:
        length = len(block["content"])
        if total + length <= max_chars:
            selected.append(block)
            total += length
        elif not selected:
            # 第一个块就超限：截断保留，保证上下文不为空
            selected.append({**block, "content": block["content"][:max_chars]})
            break
        else:
            break
    return selected


async def rewrite_question(question: str, history: list[dict]) -> str:
    """结合对话历史改写问题（指代消解），仅用于检索；任何失败返回原问题。"""
    if not history:
        return question
    messages = (
        [{"role": "system", "content": REWRITE_SYSTEM}]
        + history[-6:]
        + [{"role": "user", "content": f"最新问题：{question}"}]
    )
    try:
        rewritten = (await (await resolve_chat_llm(caller="rewrite")).chat(messages)).strip()
        return rewritten or question
    except Exception as exc:
        logger.warning("问题改写失败，使用原问题: %s", exc)
        return question


async def rerank_chunks(question: str, candidates: list[dict]) -> list[dict] | None:
    """精排候选切片：三级降级——专用 rerank 模型 → chat LLM 打分 → None（调用方回退 RRF 序）。"""
    if not candidates:
        return []

    # 1. 专用 rerank 模型（未配置时 resolve 返回 None）
    try:
        rerank_llm = await resolve_rerank_llm(caller="rerank")
    except Exception as exc:
        logger.warning("rerank 模型解析失败，回退 chat 打分: %s", exc)
        rerank_llm = None
    if rerank_llm is not None:
        try:
            scores = await rerank_llm.rerank(question, [c["content"] for c in candidates])
            reranked = [dict(c, rerank_score=float(s)) for c, s in zip(candidates, scores)]
            reranked.sort(key=lambda x: x["rerank_score"], reverse=True)
            return reranked
        except Exception as exc:
            logger.warning("专用 rerank 调用失败，回退 chat 打分: %s", exc)

    # 2. chat LLM 批量打 0~1 相关分
    numbered = "\n\n".join(
        f"[{i + 1}] {c['content'][:300]}" for i, c in enumerate(candidates)
    )
    messages = [
        {"role": "system", "content": RERANK_SYSTEM},
        {"role": "user", "content": f"问题：{question}\n\n候选片段：\n{numbered}"},
    ]
    try:
        raw = await (await resolve_chat_llm(caller="rerank")).chat(messages)
        # 推理型模型输出常带 <think> 思考块，先剥离再解析 JSON
        scores = parse_scores(_strip_think(raw), len(candidates))
        if scores is None:
            logger.warning("rerank 分数解析失败，跳过精排: %.100s", raw)
            return None
    except Exception as exc:
        logger.warning("rerank 调用失败，跳过精排: %s", exc)
        return None
    reranked = [dict(c, rerank_score=s) for c, s in zip(candidates, scores)]
    reranked.sort(key=lambda x: x["rerank_score"], reverse=True)
    return reranked
