"""数据库维护：VACUUM 回收删除空间；REINDEX 收缩 HNSW/GIN 索引。

Postgres 的 DELETE 只标记 dead tuple，物理空间靠 autovacuum 回收、索引文件不主动
收缩（保留峰值分配）。彻底删除文档后执行 VACUUM、定期 REINDEX 才能真正释放磁盘。
用 asyncpg 直连（autocommit，VACUUM/REINDEX CONCURRENTLY 不能跑在事务内）。
"""
import logging

import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)

_DSN = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

# 需要重建的索引（向量 HNSW 是主要空间大户）
_VECTOR_INDEX = "ix_chunks_embedding_hnsw"
_GIN_INDEXES = ("ix_chunks_search_vector_gin", "ix_chunks_content_trgm")


async def _run(sql: str, label: str, timeout_ms: int = 120000) -> None:
    """执行维护 SQL。statement_timeout 防止被长事务/锁卡死无限挂起。"""
    try:
        conn = await asyncpg.connect(
            _DSN, timeout=15, server_settings={"statement_timeout": str(timeout_ms)}
        )
        try:
            await conn.execute(sql)
            logger.info("维护完成: %s", label)
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("维护失败（%s，忽略）: %s", label, exc)


async def run_vacuum() -> None:
    """VACUUM (ANALYZE)：回收删除产生的 dead tuple，更新统计信息。"""
    await _run("VACUUM (ANALYZE)", "VACUUM(ANALYZE)", timeout_ms=120000)


async def cleanup_orphan_uploads() -> dict:
    """清理上传目录的孤儿文件（磁盘有、library_files 无对应行）。

    来源：上传中断/客户端断连/历史失败批次的残留（DB 已回滚但磁盘文件已写入）。
    返回 {"removed": n, "bytes": n}。"""
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.library_file import LibraryFile

    upload_dir = settings.upload_path
    if not upload_dir.exists():
        return {"removed": 0, "bytes": 0}
    async with AsyncSessionLocal() as session:
        known = set((await session.execute(select(LibraryFile.file_path))).scalars().all())
    removed = 0
    freed = 0
    for p in upload_dir.iterdir():
        if not p.is_file():
            continue
        if str(p) not in known:
            try:
                freed += p.stat().st_size
                p.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("孤儿文件删除失败: %s %s", p, exc)
    logger.info("孤儿文件清理: 删除 %d 个，释放 %.1f MB", removed, freed / 1048576)
    return {"removed": removed, "bytes": freed}


async def reindex_vector_index() -> None:
    """重建向量/GIN 索引（CONCURRENTLY 不锁写）+ VACUUM。超时放宽到 10 分钟。"""
    timeout_ms = 600000
    await _run(
        f"REINDEX INDEX CONCURRENTLY {_VECTOR_INDEX}",
        f"REINDEX {_VECTOR_INDEX}",
        timeout_ms=timeout_ms,
    )
    for idx in _GIN_INDEXES:
        await _run(
            f"REINDEX INDEX CONCURRENTLY {idx}",
            f"REINDEX {idx}",
            timeout_ms=timeout_ms,
        )
    await _run("VACUUM (ANALYZE)", "VACUUM(ANALYZE)", timeout_ms=120000)
