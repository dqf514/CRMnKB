"""文档库 ↔ 本地文件夹同步服务（OneDrive 式）。

- sha256_file / backfill_sync_fields：内容哈希与回填（同步字段 content_hash/updated_at）
- mark_updated_and_reparse：内容更新后刷新同步字段并把关联 KB 文档重置为 processing
  （process_document 幂等重解析：清旧切片→重新解析→旧内容自动存 DocumentVersion 快照）
- list_changes：增量变更查询（新增/修改用 updated_at 游标，删除用 deleted_at）
"""
import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import KnowledgeDocument
from app.models.library_file import LibraryFile
from app.models.library_folder import LibraryFolder
from app.models.user import User
from app.services.ingestion import is_supported
from app.services.permissions import accessible_ids

logger = logging.getLogger(__name__)

_READ_BLOCK = 1024 * 1024


def utcnow_naive() -> datetime:
    """库中时间列为 naive TIMESTAMP（server_default func.now() 语义一致，UTC）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(_READ_BLOCK):
            h.update(block)
    return h.hexdigest()


async def backfill_sync_fields(session: AsyncSession) -> None:
    """存量回填（幂等）：content_hash/updated_at 为 NULL 的文件按磁盘内容计算。"""
    rows = (
        await session.execute(
            select(LibraryFile).where(
                or_(LibraryFile.content_hash.is_(None), LibraryFile.updated_at.is_(None))
            )
        )
    ).scalars().all()
    for f in rows:
        if f.updated_at is None:
            f.updated_at = f.created_at
        if f.content_hash is None:
            p = Path(f.file_path)
            if p.exists():
                try:
                    f.content_hash = await asyncio.to_thread(sha256_file, p)
                except OSError as exc:
                    logger.warning("回填 hash 失败 file=%s: %s", f.id, exc)
    if rows:
        logger.info("同步字段回填：%s 个文件", len(rows))


async def mark_updated_and_reparse(
    db: AsyncSession, file: LibraryFile, file_size: int, content_hash: str
) -> list[int]:
    """内容更新后：刷新同步字段，并把关联 KB 文档重置为 processing 等待重解析。

    返回需重解析的 KnowledgeDocument id 列表（由 API 层挂后台任务）。
    """
    file.file_size = file_size
    file.content_hash = content_hash
    file.updated_at = utcnow_naive()
    supported = is_supported(file.file_name)
    if supported != file.supported:
        file.supported = supported
    docs = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.file_id == file.id,
                KnowledgeDocument.tenant_id == file.tenant_id,
                KnowledgeDocument.status != "processing",
                KnowledgeDocument.status != "unsupported",
            )
        )
    ).scalars().all()
    parse_doc_ids: list[int] = []
    for doc in docs:
        doc.status = "processing"
        parse_doc_ids.append(doc.id)
    return parse_doc_ids


def build_folder_paths(folders: list[LibraryFolder]) -> dict[int, str]:
    """folder_id → 相对路径（"a/b"）。根目录文件 path 为空串。"""
    by_id = {f.id: f for f in folders}
    cache: dict[int, str] = {}

    def _path(fid: int | None) -> str:
        if fid is None or fid not in by_id:
            return ""
        if fid in cache:
            return cache[fid]
        f = by_id[fid]
        parent = _path(f.parent_id)
        cache[fid] = f"{parent}/{f.name}" if parent else f.name
        return cache[fid]

    return {fid: _path(fid) for fid in by_id}


async def list_changes(
    db: AsyncSession,
    user: User,
    since: datetime,
    folder_id: int | None = None,
    limit: int = 500,
) -> dict:
    """增量变更：since 之后修改（updated_at）或删除（deleted_at）的可见文件。

    返回 {items: [{id, path, file_name, content_hash, file_size, action, updated_at}],
          server_time}；客户端以 server_time 作下次游标（避免本机时钟偏差）。
    """
    accessible = await accessible_ids(db, user, "file")
    if not accessible:
        return {"items": [], "server_time": utcnow_naive()}
    filters = [
        LibraryFile.tenant_id == user.tenant_id,
        LibraryFile.id.in_(accessible),
        or_(
            (LibraryFile.updated_at > since) & LibraryFile.deleted_at.is_(None),
            LibraryFile.deleted_at > since,
        ),
    ]
    if folder_id is not None:
        filters.append(LibraryFile.folder_id == folder_id)
    stmt = (
        select(LibraryFile)
        .where(*filters)
        .order_by(LibraryFile.updated_at.asc().nulls_last())
        .limit(limit)
    )
    files = (await db.execute(stmt)).scalars().all()

    folders = (
        await db.execute(
            select(LibraryFolder).where(LibraryFolder.tenant_id == user.tenant_id)
        )
    ).scalars().all()
    folder_paths = build_folder_paths(folders)

    items = []
    for f in files:
        folder_path = folder_paths.get(f.folder_id, "")
        path = f"{folder_path}/{f.file_name}" if folder_path else f.file_name
        items.append(
            {
                "id": f.id,
                "path": path,
                "file_name": f.file_name,
                "content_hash": f.content_hash,
                "file_size": f.file_size,
                "action": "deleted" if f.deleted_at is not None else "modified",
                "updated_at": f.updated_at,
            }
        )
    return {"items": items, "server_time": utcnow_naive()}
