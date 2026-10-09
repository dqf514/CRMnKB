"""dsh agent 模式的关联范围上下文注入。

工作台对话可关联知识库/文档（普通模式作为 kb_ids/file_ids 走 RAG 管线）；
agent 模式由 dsh 自主规划，无法走本地 RAG，改为把关联范围作为 <attached-context>
块注入发给 dsh 的 prompt（不落库，chat_messages 仍存用户原始问题）：

- 关联知识库：列 id 与名称，引导 agent 用 kb_search（kb_ids 参数）限定范围检索
- 关联文档：小文件直接附全文（解析未完成也立即可用，与 PR-H 直读通道同源）；
  大文件/超阈值时列出 doc_id，引导 agent 用 kb_read_doc 按需读取

ACL：kb_ids/file_ids 先经 filter_accessible_ids 过滤，防越权注入他人私有资源。
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import KnowledgeDocument
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.services.file_context import build_direct_file_context
from app.services.permissions import filter_accessible_ids

logger = logging.getLogger(__name__)

# agent prompt 注入上限（字符）：比 PR-H 直读通道（50k）收紧，避免多轮对话重复注入撑爆上下文
MAX_ATTACHED_CHARS = 20000


async def build_attached_context(
    db: AsyncSession,
    user: User,
    kb_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
) -> str:
    """构建 <attached-context> 块；无有效关联时返回空串。"""
    sections: list[str] = []

    if kb_ids:
        allowed = await filter_accessible_ids(db, user, "kb", kb_ids)
        if allowed:
            rows = (
                await db.execute(
                    select(KnowledgeBase.id, KnowledgeBase.name).where(
                        KnowledgeBase.id.in_(allowed),
                        KnowledgeBase.tenant_id == user.tenant_id,
                        KnowledgeBase.deleted_at.is_(None),
                    )
                )
            ).all()
            if rows:
                lines = "\n".join(f"- kb_id={r.id}《{r.name}》" for r in rows)
                sections.append(
                    "【关联知识库】（用 kb_search 检索时传 kb_ids 参数限定在这些库内）\n" + lines
                )

    if file_ids:
        allowed_files = await filter_accessible_ids(db, user, "file", file_ids)
        if allowed_files:
            # 小文件直读全文注入（解析中/未解析也立即可用）
            direct = await build_direct_file_context(
                db, user.tenant_id, allowed_files, user
            )
            context = direct["context"]
            truncated_note = ""
            if context and len(context) > MAX_ATTACHED_CHARS:
                context = context[:MAX_ATTACHED_CHARS]
                truncated_note = "\n（全文过长已截断，需要完整内容请用 kb_read_doc 按 doc_id 读取）"
            if context:
                sections.append("【关联文档全文】\n" + context + truncated_note)
            if not context or direct["too_large"] or truncated_note:
                # 直读不可用/被截断：列出可 kb_read_doc 的解析完成文档
                doc_rows = (
                    await db.execute(
                        select(
                            KnowledgeDocument.id,
                            KnowledgeDocument.title,
                        ).where(
                            KnowledgeDocument.file_id.in_(allowed_files),
                            KnowledgeDocument.tenant_id == user.tenant_id,
                            KnowledgeDocument.status == "ready",
                        )
                    )
                ).all()
                if doc_rows:
                    seen: set[int] = set()
                    lines = []
                    for r in doc_rows:
                        if r.id in seen:
                            continue
                        seen.add(r.id)
                        lines.append(f"- doc_id={r.id}《{r.title}》")
                    sections.append(
                        "【关联文档】（用 kb_read_doc 按 doc_id 读取全文）\n" + "\n".join(lines)
                    )

    if not sections:
        return ""
    return (
        "<attached-context>\n"
        "用户为本次对话关联了以下资料，回答时请优先基于这些内容；"
        "需要更多细节可用 kb_search / kb_read_doc 进一步检索读取。\n\n"
        + "\n\n".join(sections)
        + "\n</attached-context>"
    )
