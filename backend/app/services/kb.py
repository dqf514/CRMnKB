import logging
import os
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import KnowledgeDocument
from app.models.knowledge_base import KnowledgeBase
from app.models.library_file import LibraryFile
from app.models.library_folder import LibraryFolder
from app.models.user import User
from app.services.ingestion import is_supported
from app.services.permissions import ensure_access, filter_accessible_ids

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------

def parse_upload_path(path: str) -> tuple[list[str], str]:
    """解析上传相对路径："a/b/c.txt" → (["a", "b"], "c.txt")；"c.txt" → ([], "c.txt")。"""
    parts = [
        p
        for p in (path or "").replace("\\", "/").split("/")
        if p and p not in (".", "..")
    ]
    if not parts:
        return ([], "")
    return (parts[:-1], parts[-1])


def build_tree(folders: list[dict]) -> list[dict]:
    """把扁平文件夹行 [{id, name, parent_id, ...}] 组装成树 [{id, name, children, ...}]。
    额外字段（perm/owner_id/is_private 等）透传到节点，供前端权限控制。"""
    nodes = {}
    for f in folders:
        node: dict = {"id": f["id"], "name": f["name"], "children": []}
        for extra in ("perm", "owner_id", "is_private"):
            if f.get(extra) is not None:
                node[extra] = f[extra]
        nodes[f["id"]] = node
    roots = []
    for f in folders:
        node = nodes[f["id"]]
        parent_id = f.get("parent_id")
        if parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


def plan_associations(file_ids: list[int], existing_file_ids: set[int]) -> tuple[list[int], int]:
    """关联去重计划：同 kb 内已关联的与重复的 file_id 计入 already。纯函数。"""
    todo: list[int] = []
    already = 0
    seen: set[int] = set()
    for fid in file_ids:
        if fid in existing_file_ids or fid in seen:
            already += 1
        else:
            seen.add(fid)
            todo.append(fid)
    return todo, already


def should_migrate(doc_count: int, kb_count: int) -> bool:
    """旧文档数据迁移条件：有文档且知识库为空（幂等，只执行一次）。纯函数。"""
    return bool(doc_count) and not kb_count


async def get_or_create_auto_kb(db: AsyncSession, tenant_id: int) -> KnowledgeBase:
    """PR-H：上传无 kb_ids 时系统自动建/复用的隐藏 KB（与 reports.workbench-kb 共用）。

    每租户仅一个（is_auto=true，部分唯一索引保证）。前端 KB 列表默认隐藏。
    散文件上传到这里后立即向量化，可被 file_ids 检索使用。
    """
    from sqlalchemy import select

    existing_kb = (
        await db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == tenant_id,
                KnowledgeBase.is_auto.is_(True),
                KnowledgeBase.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing_kb:
        return existing_kb
    kb = KnowledgeBase(
        tenant_id=tenant_id,
        name="AI工作台资料库",
        description="上传文件时未指定 KB，系统自动归档到这里；前端默认隐藏。",
        type="workbench",  # 兼容旧前端 /reports/workbench-kb 探测
        is_auto=True,
        is_private=False,  # 系统资源：团队可见
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    return kb


# ---------------------------------------------------------------------------
# DB 操作
# ---------------------------------------------------------------------------

async def get_or_create_folder(
    session: AsyncSession,
    tenant_id: int,
    parent_id: int | None,
    name: str,
    user: User | None = None,
) -> LibraryFolder:
    """按（tenant, parent, name）查找或创建文件夹（上传路径自动建目录用）。

    权限口径（防文件落进他人私有目录 / 产生 owner=NULL 的孤儿私有目录）：
    - 传入 user 时命中已存在文件夹：要求对该文件夹有 edit 权限（写入操作），否则 403；
    - 新建时 owner_id 落创建者（未传 user 则继承父目录 owner），is_private 继承父目录
      （根目录缺省私有，与 ORM 默认一致）。
    """
    stmt = select(LibraryFolder).where(
        LibraryFolder.tenant_id == tenant_id, LibraryFolder.name == name
    )
    if parent_id is None:
        stmt = stmt.where(LibraryFolder.parent_id.is_(None))
    else:
        stmt = stmt.where(LibraryFolder.parent_id == parent_id)
    folder = (await session.execute(stmt)).scalar_one_or_none()
    if folder is not None:
        if user is not None:
            await ensure_access(session, user, "folder", folder.id, "edit")
        return folder
    parent = await session.get(LibraryFolder, parent_id) if parent_id is not None else None
    owner_id = user.id if user is not None else (parent.owner_id if parent is not None else None)
    is_private = bool(parent.is_private) if parent is not None else True
    folder = LibraryFolder(
        tenant_id=tenant_id,
        parent_id=parent_id,
        name=name,
        owner_id=owner_id,
        is_private=is_private,
    )
    session.add(folder)
    await session.flush()
    return folder


async def associate_files(
    session: AsyncSession,
    tenant_id: int,
    kb_id: int,
    file_ids: list[int],
    user: User | None = None,
) -> dict:
    """把库文件关联到知识库：建 knowledge_documents 行（同 kb 同 file 幂等去重）。
    supported 的文档 status=processing（返回 parse_doc_ids 供后台解析），
    不支持的直接 status=unsupported。
    传入 user 时整批校验其对各文件的 read 权限，任一无权即 403（防借关联绕过文件 ACL）。"""
    if not file_ids:
        return {"associated": 0, "already": 0, "parse_doc_ids": []}
    if user is not None:
        readable = set(await filter_accessible_ids(session, user, "file", file_ids))
        if readable != set(file_ids):
            raise HTTPException(status_code=403, detail="存在无权访问的文件")
    existing = set(
        (
            await session.execute(
                select(KnowledgeDocument.file_id).where(
                    KnowledgeDocument.kb_id == kb_id,
                    KnowledgeDocument.file_id.in_(file_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    todo, already = plan_associations(file_ids, existing)
    # 批量取文件（排除已软删），避免逐条 get 的 N+1
    files = {
        f.id: f
        for f in (
            await session.execute(
                select(LibraryFile).where(
                    LibraryFile.id.in_(todo), LibraryFile.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    } if todo else {}
    associated = 0
    parse_doc_ids: list[int] = []
    for fid in todo:
        file = files.get(fid)
        if file is None or file.tenant_id != tenant_id:
            continue
        # 按当前规则重新判定（格式清单升级后自愈旧文件的 supported 标记）
        supported = is_supported(file.file_name)
        if supported != file.supported:
            file.supported = supported
        doc = KnowledgeDocument(
            tenant_id=tenant_id,
            kb_id=kb_id,
            file_id=fid,
            title=file.file_name,
            file_name=file.file_name,
            file_path=file.file_path,
            file_type=file.file_type,
            status="processing" if supported else "unsupported",
        )
        session.add(doc)
        await session.flush()
        associated += 1
        if supported:
            parse_doc_ids.append(doc.id)
    return {"associated": associated, "already": already, "parse_doc_ids": parse_doc_ids}


async def get_or_create_customer_kb(
    session: AsyncSession, tenant_id: int, customer_id: int, customer_name: str
) -> KnowledgeBase:
    """客户专属知识库：无则自动创建（"{客户名}-专属知识库", type=customer）。"""
    stmt = select(KnowledgeBase).where(
        KnowledgeBase.tenant_id == tenant_id,
        KnowledgeBase.customer_id == customer_id,
        KnowledgeBase.type == "customer",
        KnowledgeBase.deleted_at.is_(None),
    )
    kb = (await session.execute(stmt)).scalar_one_or_none()
    if kb is None:
        kb = KnowledgeBase(
            tenant_id=tenant_id,
            name=f"{customer_name}-专属知识库",
            description="客户资料自动归档",
            type="customer",
            customer_id=customer_id,
            is_private=False,  # 客户专属库对团队可见
        )
        session.add(kb)
        await session.flush()
    return kb


async def delete_kb_documents(session: AsyncSession, kb_id: int) -> None:
    """删除知识库下全部文档（chunks 由外键 ON DELETE CASCADE 清理）；不动库文件与磁盘。"""
    from sqlalchemy import delete

    await session.execute(
        delete(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb_id)
    )


async def delete_library_file(session: AsyncSession, file: LibraryFile) -> None:
    """删除库文件：各知识库中的文档与切片（DB 级联）+ 文件行 + 磁盘文件。"""
    from sqlalchemy import delete

    await session.execute(
        delete(KnowledgeDocument).where(KnowledgeDocument.file_id == file.id)
    )
    await session.delete(file)
    try:
        Path(file.file_path).unlink(missing_ok=True)
    except OSError:
        logger.warning("磁盘文件删除失败（忽略）: %s", file.file_path)


async def migrate_legacy_documents(session: AsyncSession) -> int:
    """一次性数据迁移：旧 knowledge_documents → 默认知识库 + 库文件 + 回填关联。
    幂等：仅当存在文档且知识库为空时执行。返回迁移文档数。"""
    doc_count = await session.scalar(select(func.count()).select_from(KnowledgeDocument))
    kb_count = await session.scalar(select(func.count()).select_from(KnowledgeBase))
    if not should_migrate(doc_count or 0, kb_count or 0):
        return 0
    docs = (
        (await session.execute(select(KnowledgeDocument).where(KnowledgeDocument.kb_id.is_(None))))
        .scalars()
        .all()
    )
    kbs: dict[int, KnowledgeBase] = {}
    migrated = 0
    for doc in docs:
        kb = kbs.get(doc.tenant_id)
        if kb is None:
            kb = KnowledgeBase(
                tenant_id=doc.tenant_id,
                name="默认知识库",
                description="系统迁移创建",
                type="general",
            )
            session.add(kb)
            await session.flush()
            kbs[doc.tenant_id] = kb
        try:
            size = os.path.getsize(doc.file_path)
        except OSError:
            size = 0
        file = LibraryFile(
            tenant_id=doc.tenant_id,
            folder_id=None,
            customer_id=None,
            file_name=doc.file_name,
            file_path=doc.file_path,
            file_type=doc.file_type,
            file_size=size,
            supported=is_supported(doc.file_name),
        )
        session.add(file)
        await session.flush()
        doc.kb_id = kb.id
        doc.file_id = file.id
        migrated += 1
    if migrated:
        logger.info("旧文档数据迁移完成：%s 个文档 → 默认知识库 + 文档库", migrated)
    return migrated
