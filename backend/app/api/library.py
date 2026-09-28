import asyncio
import hashlib
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_user_with_query_token, get_db
from app.config import settings
from app.core.security import create_file_token
from app.models.customer import Customer
from app.models.document import KnowledgeDocument
from app.models.knowledge_base import KnowledgeBase
from app.models.library_file import LibraryFile
from app.models.library_folder import LibraryFolder
from app.models.user import User
from app.schemas.library import (
    BatchAssociateRequest,
    ContentUpdateResult,
    FolderCreate,
    FolderNode,
    FolderUpdate,
    LibraryChangesOut,
    LibraryFileCategoryUpdate,
    LibraryFileListOut,
    LibraryFileOut,
    LibraryFileUpdate,
    UploadedFileInfo,
    UploadResult,
)
from app.schemas.kb import AssociateResult
from app.services.ingestion import enabled_parse_exts, is_supported, process_document
from app.services.audit import record_audit
from app.services.doc_categories import category_values, get_doc_categories
from app.services.kb import (
    associate_files,
    build_tree,
    delete_library_file,
    get_or_create_customer_kb,
    get_or_create_folder,
    parse_upload_path,
)
from app.services.library_sync import list_changes, mark_updated_and_reparse, utcnow_naive
from app.services.permissions import accessible_ids, ensure_access, get_access, resolve_permissions

router = APIRouter(prefix="/library", tags=["library"])


# ---------------------------------------------------------------------------
# 客户文档资料类型（category）：存 KnowledgeDocument.metadata["category"]，不改表
# ---------------------------------------------------------------------------

async def _file_categories(db: AsyncSession, file_ids: list[int]) -> dict[int, str]:
    """批量读取文件的资料类型：取该文件任一关联知识库文档 metadata.category（先到先得）。"""
    if not file_ids:
        return {}
    rows = (
        await db.execute(
            select(KnowledgeDocument.file_id, KnowledgeDocument.doc_metadata).where(
                KnowledgeDocument.file_id.in_(file_ids)
            )
        )
    ).all()
    categories: dict[int, str] = {}
    for file_id, metadata in rows:
        if file_id in categories:
            continue
        category = (metadata or {}).get("category")
        if category:
            categories[file_id] = category
    return categories


async def _set_file_category(
    db: AsyncSession, file_ids: list[int], category: str, kb_ids: list[int] | None = None
) -> None:
    """把资料类型写入文件关联的知识库文档 metadata（JSONB 整体重赋值以触发变更检测）。"""
    if not file_ids:
        return
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.file_id.in_(file_ids))
    if kb_ids:
        stmt = stmt.where(KnowledgeDocument.kb_id.in_(kb_ids))
    docs = (await db.execute(stmt)).scalars().all()
    for doc in docs:
        doc.doc_metadata = {**(doc.doc_metadata or {}), "category": category}


# ---------------------------------------------------------------------------
# 文件夹
# ---------------------------------------------------------------------------

@router.get("/tree", response_model=list[FolderNode])
async def folder_tree(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """文件夹树：仅返回当前用户可见的文件夹（owner ∪ 团队可见 ∪ 被分享），
    并补齐可见子文件夹的祖先链（能看到子文件夹就能看到路径）。"""
    rows = (
        (
            await db.execute(
                select(LibraryFolder)
                .where(
                    LibraryFolder.tenant_id == user.tenant_id,
                    LibraryFolder.deleted_at.is_(None),
                )
                .order_by(LibraryFolder.created_at)
            )
        )
        .scalars()
        .all()
    )
    if user.role == "admin":
        folder_ids = {f.id for f in rows}
    else:
        folder_ids = set(await accessible_ids(db, user, "folder"))
        # 祖先链：把可见文件夹的所有祖先也纳入（否则看不到通向子文件夹的路径）
        by_id = {f.id: f for f in rows}
        for fid in list(folder_ids):
            cur = by_id.get(fid)
            while cur and cur.parent_id and cur.parent_id in by_id and cur.parent_id not in folder_ids:
                folder_ids.add(cur.parent_id)
                cur = by_id[cur.parent_id]
    perm_map = await resolve_permissions(db, user, "folder", list(folder_ids))
    node_rows = [
        {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id,
            "perm": perm_map.get(f.id),
            "owner_id": f.owner_id,
            "is_private": f.is_private,
        }
        for f in rows
        if f.id in folder_ids
    ]
    return build_tree(node_rows)


async def _get_folder_or_404(
    db: AsyncSession, user: User, folder_id: int, access: str = "read"
) -> LibraryFolder:
    folder = await db.get(LibraryFolder, folder_id)
    if (
        folder is None
        or folder.tenant_id != user.tenant_id
        or folder.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="文件夹不存在")
    await ensure_access(db, user, "folder", folder_id, access)
    return folder


@router.post("/folders", response_model=FolderNode, status_code=201)
async def create_folder(
    body: FolderCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if body.parent_id is not None:
        await _get_folder_or_404(db, user, body.parent_id)
    folder = LibraryFolder(
        tenant_id=user.tenant_id,
        parent_id=body.parent_id,
        name=body.name,
        owner_id=user.id,
        is_private=True,
    )
    db.add(folder)
    await db.commit()
    await db.refresh(folder)
    return FolderNode(
        id=folder.id, name=folder.name, children=[],
        perm="owner", owner_id=folder.owner_id, is_private=folder.is_private,
    )


@router.put("/folders/{folder_id}", response_model=FolderNode)
async def update_folder(
    folder_id: int,
    body: FolderUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    folder = await _get_folder_or_404(db, user, folder_id, "edit")
    updates = body.model_dump(exclude_unset=True)
    if updates.get("parent_id") is not None:
        if updates["parent_id"] == folder_id:
            raise HTTPException(status_code=400, detail="不能移动到自身")
        await _get_folder_or_404(db, user, updates["parent_id"])
    for field, value in updates.items():
        setattr(folder, field, value)
    await db.commit()
    return FolderNode(id=folder.id, name=folder.name, children=[])


async def _collect_folder_ids(db: AsyncSession, tenant_id: int, root_id: int) -> list[int]:
    ids = [root_id]
    frontier = [root_id]
    while frontier:
        children = (
            (
                await db.execute(
                    select(LibraryFolder.id).where(
                        LibraryFolder.tenant_id == tenant_id,
                        LibraryFolder.parent_id.in_(frontier),
                    )
                )
            )
            .scalars()
            .all()
        )
        ids.extend(children)
        frontier = list(children)
    return ids


@router.delete("/folders/{folder_id}", status_code=204)
async def delete_folder(
    folder_id: int,
    recursive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    folder = await _get_folder_or_404(db, user, folder_id, "owner")
    folder_ids = await _collect_folder_ids(db, user.tenant_id, folder.id)
    file_count = await db.scalar(
        select(func.count())
        .select_from(LibraryFile)
        .where(LibraryFile.folder_id.in_(folder_ids))
    )
    if not recursive and (len(folder_ids) > 1 or file_count):
        raise HTTPException(status_code=400, detail="文件夹非空，需 recursive=true 递归删除")
    if recursive:
        files = (
            (
                await db.execute(
                    select(LibraryFile).where(LibraryFile.folder_id.in_(folder_ids))
                )
            )
            .scalars()
            .all()
        )
        for file in files:
            await delete_library_file(db, file)  # 含磁盘文件与各知识库文档/切片
        await db.execute(
            delete(LibraryFolder).where(LibraryFolder.id.in_(folder_ids))
        )
    else:
        await db.delete(folder)
    await db.commit()


# ---------------------------------------------------------------------------
# 文件上传（支持文件夹上传：paths 传各文件相对路径，自动创建中间文件夹）
# ---------------------------------------------------------------------------

@router.get("/upload-formats")
async def get_upload_formats(user: User = Depends(get_current_user)):
    """当前启用解析的扩展名清单（供上传端过滤选择；登录用户即可读，非仅管理员）。

    关闭的格式由管理员在 系统设置 → 解析文件格式 里控制。"""
    return {"enabled": sorted(enabled_parse_exts())}


@router.post("/upload", response_model=UploadResult, status_code=201)
async def upload_files(
    background_tasks: BackgroundTasks,
    request: Request,
    files: list[UploadFile] = File(...),
    paths: list[str] = Form([]),
    folder_id: int | None = Form(None),
    customer_id: int | None = Form(None),
    kb_ids: list[str] = Form([]),
    category: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if category is not None:
        # 资料类型取值动态读取配置（管理端可增删改，见 services/doc_categories.py）
        valid = await category_values(db)
        if category not in valid:
            raise HTTPException(
                status_code=400, detail=f"category 仅支持 {'/'.join(sorted(valid))}"
            )
    if folder_id is not None:
        await _get_folder_or_404(db, user, folder_id)
    customer = None
    if customer_id is not None:
        customer = await db.get(Customer, customer_id)
        if customer is None or customer.tenant_id != user.tenant_id:
            raise HTTPException(status_code=404, detail="客户不存在")

    settings.upload_path.mkdir(parents=True, exist_ok=True)
    uploaded: list[UploadedFileInfo] = []
    skipped_files: list[str] = []
    file_ids: list[int] = []
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    for i, upload in enumerate(files):
        rel = paths[i] if i < len(paths) else (upload.filename or "")
        folder_parts, file_name = parse_upload_path(rel or upload.filename or "")
        if not file_name:
            continue
        # 单文件超限直接跳过该文件继续，不再中断整批上传（前端也会预过滤并提示）
        declared = getattr(upload, "size", None)
        if declared is not None and declared > max_bytes:
            skipped_files.append(f"{file_name}（超过 {settings.MAX_UPLOAD_MB}MB 上限）")
            continue
        target_folder_id = folder_id
        for part in folder_parts:
            folder = await get_or_create_folder(db, user.tenant_id, target_folder_id, part)
            target_folder_id = folder.id

        suffix = Path(file_name).suffix.lower()
        stored_path = settings.upload_path / f"{uuid4().hex}{suffix}"
        # 打开目标文件：PermissionError（杀毒/网盘等瞬时占用）重试一次
        try:
            fh = await asyncio.to_thread(open, stored_path, "wb")
        except PermissionError:
            await asyncio.sleep(0.3)
            try:
                fh = await asyncio.to_thread(open, stored_path, "wb")
            except Exception:
                skipped_files.append(f"{file_name}（文件被占用或权限不足，已跳过）")
                continue
        file_size = 0
        too_large = False
        hasher = hashlib.sha256()
        try:
            with fh:
                # 分块流式写入，避免整文件入内存；同步计算内容哈希（同步/冲突检测用）
                while chunk := await upload.read(1024 * 1024):
                    file_size += len(chunk)
                    if file_size > max_bytes:
                        too_large = True
                        break
                    hasher.update(chunk)
                    await asyncio.to_thread(fh.write, chunk)
        except Exception:
            await asyncio.to_thread(stored_path.unlink, True)
            skipped_files.append(f"{file_name}（写入失败，已跳过）")
            continue
        if too_large:
            await asyncio.to_thread(stored_path.unlink, True)
            skipped_files.append(f"{file_name}（超过 {settings.MAX_UPLOAD_MB}MB 上限）")
            continue

        file = LibraryFile(
            tenant_id=user.tenant_id,
            folder_id=target_folder_id,
            customer_id=customer_id,
            file_name=file_name,
            file_path=str(stored_path),
            file_type=suffix.lstrip("."),
            file_size=file_size,
            supported=is_supported(file_name),
            owner_id=user.id,
            is_private=True,
            updated_at=utcnow_naive(),
            content_hash=hasher.hexdigest(),
        )
        db.add(file)
        await db.flush()
        file_ids.append(file.id)
        uploaded.append(UploadedFileInfo(id=file.id, file_name=file_name, supported=file.supported))

    # 自动关联：显式 kb_ids + 客户专属库
    target_kb_ids: list[int] = []
    for raw in kb_ids:
        for x in str(raw).split(","):
            if not x.strip():
                continue
            try:
                target_kb_ids.append(int(x))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"kb_ids 含非法值: {x}")
    if customer is not None:
        kb = await get_or_create_customer_kb(db, user.tenant_id, customer.id, customer.name)
        target_kb_ids.append(kb.id)
    # PR-H：上传未指定 KB 且无客户 → 自动归档到租户的隐藏 KB（散文件资料库）
    auto_kb_id: int | None = None
    if not target_kb_ids:
        from app.services.kb import get_or_create_auto_kb
        auto_kb = await get_or_create_auto_kb(db, user.tenant_id)
        target_kb_ids.append(auto_kb.id)
        auto_kb_id = auto_kb.id

    # 校验全部目标 KB 归属当前租户且未删除（防跨租户注入，与 batch_associate 对齐）
    if target_kb_ids:
        from app.models.knowledge_base import KnowledgeBase

        found = set(
            (
                await db.execute(
                    select(KnowledgeBase.id).where(
                        KnowledgeBase.id.in_(target_kb_ids),
                        KnowledgeBase.tenant_id == user.tenant_id,
                        KnowledgeBase.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if found != set(target_kb_ids):
            raise HTTPException(status_code=400, detail="存在无权访问或已删除的知识库")

    parse_doc_ids: list[int] = []
    for kb_id in dict.fromkeys(target_kb_ids):
        result = await associate_files(db, user.tenant_id, kb_id, file_ids)
        parse_doc_ids.extend(result["parse_doc_ids"])

    # 客户文档资料类型：写入本次关联产生的知识库文档 metadata
    if category is not None:
        await _set_file_category(db, file_ids, category, target_kb_ids)

    record_audit(
        db, user, "upload", "file", None,
        {"uploaded": len(uploaded), "names": [f.file_name for f in uploaded][:20]},
        request.client.host if request.client else None,
    )
    await db.commit()
    for doc_id in parse_doc_ids:
        background_tasks.add_task(process_document, doc_id)

    supported_count = sum(1 for f in uploaded if f.supported)
    return UploadResult(
        uploaded=len(uploaded),
        supported=supported_count,
        unsupported=len(uploaded) - supported_count,
        files=uploaded,
        folder_id=folder_id,
        auto_kb_id=auto_kb_id,
        skipped=len(skipped_files),
        skipped_files=skipped_files,
    )


# ---------------------------------------------------------------------------
# 文件列表 / 操作 / 批量关联
# ---------------------------------------------------------------------------

@router.get("/files", response_model=LibraryFileListOut)
async def list_files(
    folder_id: str | None = Query(None),  # 缺省=根目录；"all"=全部
    customer_id: int | None = Query(None),
    keyword: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filters = [LibraryFile.tenant_id == user.tenant_id, LibraryFile.deleted_at.is_(None)]
    # 仅返回当前用户可读的文件（owner ∪ 团队可见 ∪ 被分享）
    accessible = await accessible_ids(db, user, "file")
    filters.append(LibraryFile.id.in_(accessible))
    if folder_id is None:
        filters.append(LibraryFile.folder_id.is_(None))
    elif folder_id != "all":
        try:
            fid = int(folder_id)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"folder_id 非法: {folder_id}")
        # 浏览文件夹需先有该文件夹的访问权限（配合文件夹级分享/权限）
        await _get_folder_or_404(db, user, fid)
        filters.append(LibraryFile.folder_id == fid)
    if customer_id is not None:
        filters.append(LibraryFile.customer_id == customer_id)
    if keyword:
        filters.append(LibraryFile.file_name.like(f"%{keyword}%"))

    total = await db.scalar(select(func.count()).select_from(LibraryFile).where(*filters))
    # 关联知识库数：只统计"可见"的知识库——排除系统自动库（is_auto）与已软删知识库，
    # 否则每个上传文件都带一个隐藏的"AI工作台资料库"关联，导致数字虚高
    kb_count_sq = (
        select(func.count())
        .select_from(KnowledgeDocument)
        .join(KnowledgeBase, KnowledgeBase.id == KnowledgeDocument.kb_id)
        .where(
            KnowledgeDocument.file_id == LibraryFile.id,
            KnowledgeBase.is_auto.is_(False),
            KnowledgeBase.deleted_at.is_(None),
        )
        .correlate(LibraryFile)
        .scalar_subquery()
    )
    stmt = (
        select(LibraryFile, kb_count_sq.label("kb_count"))
        .where(*filters)
        .order_by(LibraryFile.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).all()
    perm_map = await resolve_permissions(db, user, "file", [f.id for f, _ in rows])
    categories = await _file_categories(db, [f.id for f, _ in rows])
    items = [
        LibraryFileOut(
            id=f.id,
            folder_id=f.folder_id,
            customer_id=f.customer_id,
            file_name=f.file_name,
            file_type=f.file_type,
            file_size=f.file_size,
            supported=f.supported,
            kb_count=kb_count,
            created_at=f.created_at,
            owner_id=f.owner_id,
            is_private=f.is_private,
            perm=perm_map.get(f.id),
            content_hash=f.content_hash,
            updated_at=f.updated_at,
            category=categories.get(f.id),
        )
        for f, kb_count in rows
    ]
    return LibraryFileListOut(items=items, total=total or 0)


async def _get_file_or_404(
    db: AsyncSession, user: User, file_id: int, required: str = "read"
) -> LibraryFile:
    file = await db.get(LibraryFile, file_id)
    if file is None or file.tenant_id != user.tenant_id or file.deleted_at is not None:
        raise HTTPException(status_code=404, detail="文件不存在")
    await ensure_access(db, user, "file", file_id, required)
    return file


async def _kb_assoc_count(db: AsyncSession, file_id: int) -> int:
    """文件关联的"可见"知识库数：排除系统自动库（is_auto）与已软删知识库。"""
    return (
        await db.scalar(
            select(func.count())
            .select_from(KnowledgeDocument)
            .join(KnowledgeBase, KnowledgeBase.id == KnowledgeDocument.kb_id)
            .where(
                KnowledgeDocument.file_id == file_id,
                KnowledgeBase.is_auto.is_(False),
                KnowledgeBase.deleted_at.is_(None),
            )
        )
        or 0
    )


@router.get("/files/{file_id}/pst-progress")
async def pst_progress(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """PST 拆解进度（上传面板轮询）：{state, done, total, error}。"""
    await _get_file_or_404(db, user, file_id, "read")
    from app.services.pst import get_pst_progress

    return await get_pst_progress(db, user.tenant_id, file_id)


@router.get("/files/{file_id}", response_model=LibraryFileOut)
async def get_file(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """单文件元数据（同步客户端冲突预检：读 content_hash/updated_at）。"""
    file = await _get_file_or_404(db, user, file_id, "read")
    kb_count = await _kb_assoc_count(db, file.id)
    perm = await get_access(db, user.tenant_id, user.id, "file", file.id)
    category = (await _file_categories(db, [file.id])).get(file.id)
    return LibraryFileOut(
        id=file.id,
        folder_id=file.folder_id,
        customer_id=file.customer_id,
        file_name=file.file_name,
        file_type=file.file_type,
        file_size=file.file_size,
        supported=file.supported,
        kb_count=kb_count or 0,
        created_at=file.created_at,
        owner_id=file.owner_id,
        is_private=file.is_private,
        perm=perm,
        content_hash=file.content_hash,
        updated_at=file.updated_at,
        category=category,
    )


@router.put("/files/{file_id}", response_model=LibraryFileOut)
async def update_file(
    file_id: int,
    body: LibraryFileUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    file = await _get_file_or_404(db, user, file_id, "edit")
    updates = body.model_dump(exclude_unset=True)
    if "folder_id" in updates and updates["folder_id"] is not None:
        await _get_folder_or_404(db, user, updates["folder_id"])
    new_customer_id = updates.get("customer_id")
    customer = None
    if new_customer_id is not None:
        customer = await db.get(Customer, new_customer_id)
        if customer is None or customer.tenant_id != user.tenant_id:
            raise HTTPException(status_code=404, detail="客户不存在")
    for field, value in updates.items():
        setattr(file, field, value)
    # 路径可能变化（重命名/移动）：刷新同步游标，客户端按 changes 拉取新路径
    if "file_name" in updates or "folder_id" in updates:
        file.updated_at = utcnow_naive()

    # 设置 customer_id 时自动关联客户专属库
    if customer is not None:
        kb = await get_or_create_customer_kb(db, user.tenant_id, customer.id, customer.name)
        result = await associate_files(db, user.tenant_id, kb.id, [file.id])
        for doc_id in result["parse_doc_ids"]:
            background_tasks.add_task(process_document, doc_id)

    await db.commit()
    await db.refresh(file)
    kb_count = await _kb_assoc_count(db, file.id)
    perm = await get_access(db, user.tenant_id, user.id, "file", file.id)
    category = (await _file_categories(db, [file.id])).get(file.id)
    return LibraryFileOut(
        id=file.id,
        folder_id=file.folder_id,
        customer_id=file.customer_id,
        file_name=file.file_name,
        file_type=file.file_type,
        file_size=file.file_size,
        supported=file.supported,
        kb_count=kb_count or 0,
        created_at=file.created_at,
        owner_id=file.owner_id,
        is_private=file.is_private,
        perm=perm,
        content_hash=file.content_hash,
        updated_at=file.updated_at,
        category=category,
    )


@router.get("/categories")
async def list_doc_categories(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """资料类型列表（登录用户可读，上传/展示用）；维护入口在管理端系统设置。"""
    return {"items": await get_doc_categories(db)}


@router.put("/files/{file_id}/category")
async def update_file_category(
    file_id: int,
    body: LibraryFileCategoryUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """修改文件（客户文档）的资料类型：写入其关联知识库文档的 metadata.category。"""
    file = await _get_file_or_404(db, user, file_id, "edit")
    valid = await category_values(db)
    if body.category not in valid:
        raise HTTPException(status_code=400, detail=f"category 仅支持 {'/'.join(sorted(valid))}")
    await _set_file_category(db, [file.id], body.category)
    await db.commit()
    return {"ok": True, "id": file.id, "category": body.category}


@router.delete("/files/{file_id}", status_code=204)
async def delete_file(
    file_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    file = await _get_file_or_404(db, user, file_id, "owner")
    # 软删除进回收站；彻底删除（磁盘 + 知识库文档/切片）走 /recycle-bin
    file.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    record_audit(db, user, "delete", "file", file.id, {"file_name": file.file_name},
                 request.client.host if request.client else None)
    await db.commit()


# ---------------------------------------------------------------------------
# 同步（本地 App / OneDrive 式）
# ---------------------------------------------------------------------------


@router.put("/files/{file_id}/content", response_model=ContentUpdateResult)
async def update_file_content(
    file_id: int,
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """更新文件内容（同步客户端上行）：覆盖磁盘文件 → 关联 KB 文档自动重解析。

    重解析走 process_document（幂等清旧切片），旧内容自动存 DocumentVersion 快照，
    之后问答/报告立即使用新内容。"""
    target = await _get_file_or_404(db, user, file_id, "edit")
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    declared = getattr(file, "size", None)
    if declared is not None and declared > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件超过大小上限 {settings.MAX_UPLOAD_MB}MB")

    stored_path = Path(target.file_path)
    # 打开目标文件：PermissionError（杀毒/网盘等瞬时占用）重试一次
    try:
        fh = await asyncio.to_thread(open, stored_path, "wb")
    except PermissionError:
        await asyncio.sleep(0.3)
        fh = await asyncio.to_thread(open, stored_path, "wb")
    file_size = 0
    hasher = hashlib.sha256()
    try:
        with fh:
            while chunk := await file.read(1024 * 1024):
                file_size += len(chunk)
                if file_size > max_bytes:
                    raise HTTPException(
                        status_code=413, detail=f"文件超过大小上限 {settings.MAX_UPLOAD_MB}MB"
                    )
                hasher.update(chunk)
                await asyncio.to_thread(fh.write, chunk)
    except HTTPException:
        # 超限：恢复原文件不可行（已截断），如实报错并标记，前端提示重新上传
        raise

    parse_doc_ids = await mark_updated_and_reparse(
        db, target, file_size, hasher.hexdigest()
    )
    record_audit(
        db, user, "update", "file", target.id,
        {"action": "content", "file_name": target.file_name, "reparse_docs": len(parse_doc_ids)},
        request.client.host if request.client else None,
    )
    await db.commit()
    for doc_id in parse_doc_ids:
        background_tasks.add_task(process_document, doc_id)
    return ContentUpdateResult(
        id=target.id,
        file_name=target.file_name,
        file_size=target.file_size,
        content_hash=target.content_hash,
        updated_at=target.updated_at,
        reparse_docs=len(parse_doc_ids),
    )


@router.get("/changes", response_model=LibraryChangesOut)
async def library_changes(
    since: datetime = Query(..., description="上次同步游标（ISO 时间，取上次响应的 server_time）"),
    folder_id: int | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """增量变更列表（同步客户端下行轮询）：since 之后修改或删除的可见文件。"""
    if since.tzinfo is not None:
        since = since.astimezone(timezone.utc).replace(tzinfo=None)  # 库列均为 naive UTC
    result = await list_changes(db, user, since, folder_id=folder_id, limit=limit)
    return LibraryChangesOut(**result)


@router.post("/associate", response_model=AssociateResult)
async def batch_associate(
    body: BatchAssociateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.models.knowledge_base import KnowledgeBase

    associated = 0
    already = 0
    parse_doc_ids: list[int] = []
    for kb_id in body.kb_ids:
        kb = await db.get(KnowledgeBase, kb_id)
        if kb is None or kb.tenant_id != user.tenant_id:
            raise HTTPException(status_code=404, detail=f"知识库不存在: {kb_id}")
        result = await associate_files(db, user.tenant_id, kb_id, body.file_ids)
        associated += result["associated"]
        already += result["already"]
        parse_doc_ids.extend(result["parse_doc_ids"])
    await db.commit()
    for doc_id in parse_doc_ids:
        background_tasks.add_task(process_document, doc_id)
    return AssociateResult(associated=associated, already=already)


# ---------------------------------------------------------------------------
# 文件内容在线查看（支持 Range，音视频拖动播放）
# ---------------------------------------------------------------------------

# mimetypes 对以下扩展名推断不准或缺失，显式补全
_CONTENT_TYPE_OVERRIDES = {
    ".md": "text/markdown; charset=utf-8",
    ".markdown": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".eml": "message/rfc822",
    ".msg": "application/vnd.ms-outlook",
}


def guess_content_type(file_name: str) -> str:
    """按扩展名推断 Content-Type，未知扩展名返回 application/octet-stream。"""
    ext = Path(file_name).suffix.lower()
    if ext in _CONTENT_TYPE_OVERRIDES:
        return _CONTENT_TYPE_OVERRIDES[ext]
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"


@router.get("/files/{file_id}/preview-text")
async def preview_text(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Office 文档（doc/ppt/pptx/xls/xlsx）的文本化预览：实时提取正文返回。"""
    file = await _get_file_or_404(db, user, file_id, "read")
    ext = "." + (file.file_type or "").lower().lstrip(".")
    if ext not in (".doc", ".ppt", ".pptx", ".xls", ".xlsx", ".docx"):
        raise HTTPException(status_code=400, detail="该格式请直接在线预览或下载")
    from app.services.ingestion import parse_file

    try:
        text = await asyncio.to_thread(parse_file, Path(file.file_path), file.file_type)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"内容提取失败: {str(exc)[:200]}")
    return {"text": text[:20000], "truncated": len(text) > 20000}


@router.get("/files/{file_id}/preview.png")
async def preview_png(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """tif/tiff 浏览器不认，转 PNG 供预览。"""
    file = await _get_file_or_404(db, user, file_id, "read")
    ext = "." + (file.file_type or "").lower().lstrip(".")
    if ext not in (".tif", ".tiff"):
        raise HTTPException(status_code=400, detail="仅 tif/tiff 需要转换预览")
    import io

    from fastapi.responses import Response
    from PIL import Image

    def _convert() -> bytes:
        img = Image.open(file.file_path)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    try:
        data = await asyncio.to_thread(_convert)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"图片转换失败: {str(exc)[:200]}")
    return Response(content=data, media_type="image/png")


@router.get("/files/{file_id}/token")
async def file_token(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """签发短时效（5 分钟）文件访问令牌，供 <img>/<audio>/<video>/<pdf> 直链流式加载。

    代替把登录 JWT 塞进 URL（登录 JWT 被日志/Referer 泄露即等同凭证泄露）。
    文件软删/不存在时 404，不签发。
    """
    await _get_file_or_404(db, user, file_id)
    return {"token": create_file_token(file_id, user.id)}


# 可执行文档类型：内联展示时加 CSP sandbox，脚本不可执行，防存储型 XSS
_EXEC_CONTENT_EXTS = {".html", ".htm", ".svg", ".xml", ".xhtml"}
# 防御性 CSP：禁脚本/禁外联，仅允许内联样式与同文件资源
_FILE_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; media-src blob:"


@router.get("/files/{file_id}/content")
async def file_content(
    file_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user_with_query_token),
):
    """在线查看文件内容：inline 展示；FileResponse 自带 Range 分段与流式传输。

    鉴权 Bearer 优先，兼容短时效文件令牌 ?t=（供 <img>/<audio>/<video> 直链流式播放）。
    html/svg/xml 等可执行内容附加 CSP sandbox + nosniff 头，脚本无法执行。
    """
    file = await _get_file_or_404(db, user, file_id)
    path = Path(file.file_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件已不存在于磁盘")
    headers = {
        "Accept-Ranges": "bytes",
        "X-Content-Type-Options": "nosniff",
        # RFC 5987 filename* UTF-8 编码，兼容中文文件名
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(file.file_name)}",
    }
    if Path(file.file_name).suffix.lower() in _EXEC_CONTENT_EXTS:
        headers["Content-Security-Policy"] = _FILE_CSP
    return FileResponse(
        path,
        media_type=guess_content_type(file.file_name),
        headers=headers,
    )
