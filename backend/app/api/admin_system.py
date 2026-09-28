import logging
import platform
import time

import psutil
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin
from app.config import settings
from app.models.user import User
from app.services.audit import record_audit
from app.services.backup import list_backups, restore_backup, run_backup
from app.services.maintenance import cleanup_orphan_uploads, reindex_vector_index, run_vacuum

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-system"], dependencies=[Depends(require_admin)])

START_TIME = time.time()

# 需要统计行数的核心表
_COUNT_TABLES = [
    "customers",
    "users",
    "library_files",
    "knowledge_documents",
    "document_chunks",
    "tasks",
    "chat_messages",
    "llm_call_logs",
    "knowledge_bases",
    "workflows",
    "reports",
    "ai_feedback",
]


@router.get("/system/overview")
async def system_overview(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """系统资源 + 数据库概览。psutil 不可用时对应字段返回 None（优雅降级）。"""
    try:
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(settings.upload_path.resolve().anchor)
        system = {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_total_mb": round(mem.total / 1024 / 1024),
            "memory_used_mb": round(mem.used / 1024 / 1024),
            "memory_percent": mem.percent,
            "disk_total_gb": round(disk.total / 1024**3, 1),
            "disk_used_gb": round(disk.used / 1024**3, 1),
            "disk_percent": disk.percent,
        }
    except Exception as exc:
        logger.warning("psutil 读取系统资源失败: %s", exc)
        system = None

    db_size = None
    table_counts: dict[str, int] = {}
    try:
        db_size = (
            await db.scalar(text("SELECT pg_size_pretty(pg_database_size(current_database()))"))
        )
        for table in _COUNT_TABLES:
            table_counts[table] = await db.scalar(text(f"SELECT count(*) FROM {table}")) or 0
    except Exception as exc:
        logger.warning("数据库统计读取失败: %s", exc)

    return {
        "uptime_seconds": int(time.time() - START_TIME),
        "python_version": platform.python_version(),
        "system": system,
        "database": {"size": db_size, "table_counts": table_counts},
    }


@router.post("/system/maintenance")
async def system_maintenance(
    action: str = Query("vacuum", pattern="^(vacuum|reindex|cleanup)$"),
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """数据库维护（后台执行）：
    - vacuum：VACUUM (ANALYZE) 回收删除空间
    - reindex：重建 HNSW/GIN 索引（CONCURRENTLY 不锁写）+ VACUUM
    - cleanup：清理上传目录孤儿文件（磁盘有、库里无记录的上传残留），前台返回数量
    """
    if action == "cleanup":
        result = await cleanup_orphan_uploads()
        return {"ok": True, "action": action, **result}
    if action == "reindex":
        background_tasks.add_task(reindex_vector_index)
    else:
        background_tasks.add_task(run_vacuum)
    return {"ok": True, "started": True, "action": action}


@router.post("/system/backups")
async def create_backup(
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """立即执行一次完整备份（后台）。"""
    background_tasks.add_task(run_backup)
    return {"ok": True, "started": True}


@router.get("/system/backups")
async def get_backups(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """列出已有备份。"""
    return await list_backups()


@router.post("/system/backups/{name}/restore")
async def do_restore(
    name: str,
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """从指定备份恢复（破坏性：覆盖当前数据库与上传目录，建议维护窗口执行）。"""
    existing = await list_backups()
    if not any(b["name"] == name for b in existing):
        raise HTTPException(status_code=404, detail="备份不存在")
    background_tasks.add_task(restore_backup, name)
    return {"ok": True, "started": True, "name": name}


# ---------------------------------------------------------------------------
# 沙箱重置（仅 dev/sandbox/test 环境）
# ---------------------------------------------------------------------------

# 重置时清空的业务表（表名已核对 models/ 下真实 __tablename__）。
# 保留：users / tenants / llm_models / system_settings / brand_settings /
#       industries / skills / mcp_servers（以及 audit_logs，保留操作审计轨迹）。
_SANDBOX_RESET_TABLES = [
    "customers",
    "follow_up_records",
    "opportunities",
    "tasks",
    "notifications",
    "reports",
    "knowledge_documents",
    "document_chunks",
    "chunk_questions",
    "knowledge_bases",
    "library_files",
    "library_folders",
    "chat_sessions",
    "chat_messages",
    "notebooks",
    "notebook_notes",
    "login_codes",
    "login_attempts",
    "workflow_runs",
    "rag_query_logs",
    "llm_call_logs",
    "error_logs",
    "ai_feedback",
    "agent_approvals",
]

# 允许重置的环境（sandbox 与 dev/test 同等待遇；prod 一律拒绝）
_SANDBOX_RESET_ENVS = ("dev", "sandbox", "test")


@router.post("/system/reset-sandbox")
async def reset_sandbox(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """沙箱/开发环境一键清空业务数据：先自动备份（失败则中止），再 TRUNCATE 业务表。

    仅 ENV ∈ dev/sandbox/test 可用，prod 返回 403。
    """
    if settings.ENV.lower() not in _SANDBOX_RESET_ENVS:
        raise HTTPException(status_code=403, detail="仅沙箱/开发环境可重置")
    # 先备份：备份失败不执行清空，避免无可挽回的数据丢失
    try:
        backup_name = await run_backup()
    except Exception as exc:
        logger.error("沙箱重置前自动备份失败，已中止: %s", exc)
        raise HTTPException(status_code=500, detail=f"自动备份失败，已中止重置: {exc}")
    await db.execute(
        text(f"TRUNCATE {', '.join(_SANDBOX_RESET_TABLES)} RESTART IDENTITY CASCADE")
    )
    record_audit(
        db, admin, "reset", "system", None,
        {"action": "reset_sandbox", "backup": backup_name},
    )
    await db.commit()
    return {"ok": True, "backup": backup_name}
