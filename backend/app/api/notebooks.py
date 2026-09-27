"""PR-G：notebook + note API。"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.library_file import LibraryFile
from app.models.notebook import Notebook, NotebookNote
from app.models.user import User
from app.schemas.notebook import (
    NotebookCreate,
    NotebookNoteCreate,
    NotebookNoteFromChat,
    NotebookNoteOut,
    NotebookNoteUpdate,
    NotebookOut,
    NotebookUpdate,
    SaveAsDocumentRequest,
)
from app.services.permissions import get_access
from app.services.notebook import (
    create_note,
    create_note_from_chat,
    create_notebook,
    delete_note,
    delete_notebook,
    get_note_or_404,
    get_notebook_or_404,
    list_notebooks,
    list_notes,
    save_note_as_document,
    update_note,
    update_notebook,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notebooks", tags=["notebooks"])


def _to_notebook_out(nb: Notebook) -> NotebookOut:
    return NotebookOut(
        id=nb.id,
        name=nb.name,
        description=nb.description,
        source_kb_ids=nb.source_kb_ids or [],
        source_file_ids=nb.source_file_ids or [],
        created_by=nb.created_by,
        created_at=nb.created_at,
        updated_at=nb.updated_at,
        note_count=getattr(nb, "note_count", 0),
        is_private=nb.is_private,
        perm=getattr(nb, "perm", None),
    )


async def _sanitize_sources(
    db: AsyncSession, tenant_id: int, kb_ids: list[int], file_ids: list[int]
) -> tuple[list[int], list[int]]:
    """过滤工作区来源里已失效的引用（KB/文件被删除或不存在），避免前端计数与检索失真。"""
    valid_kbs: set[int] = set()
    if kb_ids:
        rows = (
            await db.execute(
                select(KnowledgeBase.id).where(
                    KnowledgeBase.id.in_(kb_ids),
                    KnowledgeBase.tenant_id == tenant_id,
                    KnowledgeBase.deleted_at.is_(None),
                )
            )
        ).all()
        valid_kbs = {r[0] for r in rows}
    valid_files: set[int] = set()
    if file_ids:
        rows = (
            await db.execute(
                select(LibraryFile.id).where(
                    LibraryFile.id.in_(file_ids),
                    LibraryFile.tenant_id == tenant_id,
                    LibraryFile.deleted_at.is_(None),
                )
            )
        ).all()
        valid_files = {r[0] for r in rows}
    return (
        [k for k in (kb_ids or []) if k in valid_kbs],
        [f for f in (file_ids or []) if f in valid_files],
    )


async def _to_notebook_out_sanitized(
    db: AsyncSession, tenant_id: int, nb: Notebook
) -> NotebookOut:
    """序列化并净化来源：只保留仍然存在且未删除的 KB / 文件。"""
    kb_ids, file_ids = await _sanitize_sources(
        db, tenant_id, nb.source_kb_ids or [], nb.source_file_ids or []
    )
    return _to_notebook_out(nb).model_copy(
        update={"source_kb_ids": kb_ids, "source_file_ids": file_ids}
    )


@router.get("", response_model=list[NotebookOut])
async def list_(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列工作区（含内容数，仅当前用户可见）。"""
    rows = await list_notebooks(db, user)
    return [await _to_notebook_out_sanitized(db, user.tenant_id, nb) for nb in rows]


@router.post("", response_model=NotebookOut, status_code=201)
async def create(
    body: NotebookCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """新建 notebook。"""
    nb = await create_notebook(
        db,
        user.tenant_id,
        user.id,
        name=body.name,
        description=body.description,
        source_kb_ids=body.source_kb_ids,
        source_file_ids=body.source_file_ids,
    )
    return await _to_notebook_out_sanitized(db, user.tenant_id, nb)


@router.get("/{notebook_id}", response_model=NotebookOut)
async def get(
    notebook_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    nb = await get_notebook_or_404(db, user, notebook_id)
    nb.perm = await get_access(db, user.tenant_id, user.id, "notebook", notebook_id)
    return await _to_notebook_out_sanitized(db, user.tenant_id, nb)


@router.put("/{notebook_id}", response_model=NotebookOut)
async def update(
    notebook_id: int,
    body: NotebookUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    nb = await get_notebook_or_404(db, user, notebook_id, "edit")
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if fields:
        nb = await update_notebook(db, nb, **fields)
    return await _to_notebook_out_sanitized(db, user.tenant_id, nb)


@router.delete("/{notebook_id}", status_code=204)
async def delete(
    notebook_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    nb = await get_notebook_or_404(db, user, notebook_id, "owner")
    await delete_notebook(db, nb)


# ============ notes ============


@router.get("/{notebook_id}/notes", response_model=list[NotebookNoteOut])
async def list_notes_endpoint(
    notebook_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await get_notebook_or_404(db, user, notebook_id)
    rows = await list_notes(db, user.tenant_id, notebook_id)
    return rows


@router.post(
    "/{notebook_id}/notes",
    response_model=NotebookNoteOut,
    status_code=201,
)
async def create_note_endpoint(
    notebook_id: int,
    body: NotebookNoteCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await get_notebook_or_404(db, user, notebook_id, "edit")
    return await create_note(
        db,
        user.tenant_id,
        notebook_id,
        title=body.title,
        content=body.content,
        source_type=body.source_type,
        source_ref=body.source_ref,
    )


# ============ 独立 note 端点（必须放在 /{notebook_id}/notes 之前以避免路径冲突） ============


@router.post("/notes/from-chat-message", response_model=NotebookNoteOut, status_code=201)
async def create_from_chat(
    body: NotebookNoteFromChat,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """从 chat 助手消息生成 note。"""
    await get_notebook_or_404(db, user, body.notebook_id, "edit")
    return await create_note_from_chat(
        db,
        user.tenant_id,
        user.id,
        body.notebook_id,
        session_id=body.session_id,
        query_log_id=body.query_log_id,
        question=body.question,
        answer=body.answer,
        sources=body.sources,
    )


@router.get("/notes/{note_id}", response_model=NotebookNoteOut)
async def get_note(
    note_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await get_note_or_404(db, user, note_id)


@router.put("/notes/{note_id}", response_model=NotebookNoteOut)
async def update_note_endpoint(
    note_id: int,
    body: NotebookNoteUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    note = await get_note_or_404(db, user, note_id, "edit")
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if fields:
        note = await update_note(db, note, **fields)
    return note


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note_endpoint(
    note_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    note = await get_note_or_404(db, user, note_id, "edit")
    await delete_note(db, note)


@router.post("/notes/{note_id}/save-as-document")
async def save_as_document(
    note_id: int,
    body: SaveAsDocumentRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把 note 转 KB 文档（异步 ingestion，UI 轮询状态）。"""
    note = await get_note_or_404(db, user, note_id, "edit")
    return await save_note_as_document(db, user.tenant_id, user.id, note, body.kb_id)