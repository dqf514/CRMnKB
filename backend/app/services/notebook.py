"""PR-G：Notebook / Note CRUD 辅助函数。"""
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notebook import Notebook, NotebookNote
from app.models.user import User
from app.services.permissions import accessible_ids, ensure_access, resolve_permissions

logger = logging.getLogger(__name__)


async def list_notebooks(db: AsyncSession, user: User) -> list[Notebook]:
    """列工作区（含每区的内容数，排除已软删的；仅返回当前用户可见的）。"""
    accessible = await accessible_ids(db, user, "notebook")
    rows = (
        await db.execute(
            select(Notebook, func.count(NotebookNote.id))
            .outerjoin(NotebookNote, NotebookNote.notebook_id == Notebook.id)
            .where(
                Notebook.tenant_id == user.tenant_id,
                Notebook.deleted_at.is_(None),
                Notebook.id.in_(accessible),
            )
            .group_by(Notebook.id)
            .order_by(Notebook.updated_at.desc())
        )
    ).all()
    perm_map = await resolve_permissions(db, user, "notebook", [nb.id for nb, _ in rows])
    out = []
    for nb, cnt in rows:
        nb.note_count = cnt or 0
        nb.perm = perm_map.get(nb.id)
        out.append(nb)
    return out


async def get_notebook_or_404(
    db: AsyncSession, user: User, notebook_id: int, required: str = "read"
) -> Notebook:
    nb = await db.get(Notebook, notebook_id)
    if nb is None or nb.tenant_id != user.tenant_id or nb.deleted_at is not None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="notebook 不存在")
    await ensure_access(db, user, "notebook", notebook_id, required)
    return nb


async def create_notebook(
    db: AsyncSession, tenant_id: int, user_id: int, **fields
) -> Notebook:
    nb = Notebook(tenant_id=tenant_id, created_by=user_id, is_private=True, **fields)
    db.add(nb)
    await db.commit()
    await db.refresh(nb)
    nb.note_count = 0
    return nb


async def update_notebook(db: AsyncSession, nb: Notebook, **fields) -> Notebook:
    for k, v in fields.items():
        setattr(nb, k, v)
    nb.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(nb)
    return nb


async def delete_notebook(db: AsyncSession, nb: Notebook) -> None:
    """软删 notebook 进回收站：仅标记 deleted_at，内容保留（恢复即回）。

    关联知识库/文档是引用（不随 notebook 删除）；聊天记录由前端按 notebook 的
    session_id 续聊，notebook 恢复后仍可回到原会话。"""
    nb.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()


async def list_notes(
    db: AsyncSession, tenant_id: int, notebook_id: int
) -> list[NotebookNote]:
    rows = (
        await db.execute(
            select(NotebookNote)
            .where(
                NotebookNote.tenant_id == tenant_id,
                NotebookNote.notebook_id == notebook_id,
            )
            .order_by(NotebookNote.sort.asc(), NotebookNote.created_at.asc())
        )
    ).scalars().all()
    return list(rows)


async def get_note_or_404(
    db: AsyncSession, user: User, note_id: int, required: str = "read"
) -> NotebookNote:
    note = await db.get(NotebookNote, note_id)
    if note is None or note.tenant_id != user.tenant_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="note 不存在")
    # 内容继承所属工作区的权限
    await ensure_access(db, user, "notebook", note.notebook_id, required)
    return note


async def create_note(
    db: AsyncSession, tenant_id: int, notebook_id: int, **fields
) -> NotebookNote:
    note = NotebookNote(
        tenant_id=tenant_id, notebook_id=notebook_id, **fields
    )
    db.add(note)
    nb = await db.get(Notebook, notebook_id)
    if nb is not None:
        nb.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(note)
    return note


async def update_note(db: AsyncSession, note: NotebookNote, **fields) -> NotebookNote:
    for k, v in fields.items():
        setattr(note, k, v)
    note.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    # 触达 notebook 的 updated_at
    nb = await db.get(Notebook, note.notebook_id)
    if nb is not None:
        nb.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(note)
    return note


async def delete_note(db: AsyncSession, note: NotebookNote) -> None:
    nb_id = note.notebook_id
    await db.delete(note)
    nb = await db.get(Notebook, nb_id)
    if nb is not None:
        nb.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()


async def create_note_from_chat(
    db: AsyncSession,
    tenant_id: int,
    user_id: int,
    notebook_id: int,
    *,
    session_id: int | None,
    query_log_id: int | None,
    question: str,
    answer: str,
    sources: list,
) -> NotebookNote:
    """从 chat 助手消息生成 note。"""
    title = (question or "").strip()[:30] or "未命名问答"
    # 拼 Markdown：问题 + 回答 + 引用
    sources_md = ""
    if sources:
        lines = [f"- [{s.get('doc_title', '')}](chunk:{s.get('chunk_id', '')}) ({round(float(s.get('score', 0)) * 100, 1)}%)" for s in sources[:10]]
        sources_md = "\n\n## 引用来源\n" + "\n".join(lines)
    content = f"## 问题\n{question}\n\n## 回答\n{answer}{sources_md}".strip()
    return await create_note(
        db,
        tenant_id,
        notebook_id,
        title=title,
        content=content,
        source_type="chat",
        source_ref={
            "session_id": session_id,
            "query_log_id": query_log_id,
            "user_id": user_id,
            "question": question,
            "answer": answer[:500],
            "sources": sources[:5],
        },
    )


async def save_note_as_document(
    db: AsyncSession,
    tenant_id: int,
    user_id: int,
    note: NotebookNote,
    kb_id: int,
) -> dict:
    """把 note 转为 KB 文档：同时建文档库文件（LibraryFile）与知识库文档（KnowledgeDocument），
    后台 ingestion 解析。保存后文档库与知识库都能看到。"""
    import re
    from uuid import uuid4

    from app.config import settings
    from app.models.knowledge_base import KnowledgeBase
    from app.models.library_file import LibraryFile
    from app.models.document import KnowledgeDocument

    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None or kb.tenant_id != tenant_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="目标 KB 不存在")

    # 写 md 文件到上传目录（与上传文件同存储、持久化）；展示名可读，存储名带 uuid 防冲突
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|\r\n]+', "_", (note.title or "note").strip()) or "note"
    display_name = f"{safe[:60]}.md"
    stored_name = f"note_{note.id}_{uuid4().hex}.md"
    stored_path = settings.upload_path / stored_name
    stored_path.write_text(f"# {note.title}\n\n{note.content}", encoding="utf-8")

    file = LibraryFile(
        tenant_id=tenant_id,
        folder_id=None,
        customer_id=None,
        file_name=display_name,
        file_path=str(stored_path),
        file_type="md",
        file_size=stored_path.stat().st_size,
        supported=True,
    )
    db.add(file)
    await db.flush()  # 取 file.id
    # 建 KnowledgeDocument 行（status=processing），后续由 ingestion pipeline 处理
    doc = KnowledgeDocument(
        tenant_id=tenant_id,
        kb_id=kb_id,
        file_id=file.id,
        title=note.title,
        file_name=display_name,
        file_path=str(stored_path),
        file_type="md",
        status="processing",
        doc_metadata={"from_note_id": note.id, "source_type": "notebook_note"},
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    # 触发后台 ingestion（文件已持久化在上传目录，无需临时文件清理）
    import asyncio
    from app.services.ingestion import process_document

    asyncio.create_task(process_document(doc.id))
    # 记录到 note 的 source_ref
    note.source_ref = {
        **(note.source_ref or {}),
        "saved_as_document_id": doc.id,
        "saved_as_kb_id": kb_id,
    }
    await db.commit()
    return {
        "document_id": doc.id,
        "kb_id": kb_id,
        "file_id": file.id,
        "status": "processing",
    }