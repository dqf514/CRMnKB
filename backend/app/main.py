import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from app.api import (
    admin_audit,
    admin_errors,
    admin_llm,
    admin_mcp,
    admin_skills,
    admin_system,
    admin_settings,
    admin_users,
    agent_approvals,
    auth,
    brand,
    chat,
    customers,
    dashboard,
    feedback,
    followups,
    industries,
    kbs,
    library,
    notebooks,
    notifications,
    opportunities,
    permissions,
    rag,
    recycle_bin,
    reminders,
    reports,
    search,
    sync_app,
    tasks,
    users,
    workflows,
)
from sqlalchemy import text

from app.config import settings
from app.database import AsyncSessionLocal, init_db
from app.services import mcp_server as kb_mcp_server
from app.services.error_log import log_error
from app.services.monitoring import check_alerts
from app.services.reminder import run_all_rules
from app.services.workflow import run_due_workflows

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# 多 worker 防重复调度：周期任务入口用 PG 会话级咨询锁（固定 key）互斥，
# 抢不到锁的 worker 本轮直接跳过；单 worker 下总能抢到，行为不变。
_ADVISORY_KEY_REMINDER = 727301
_ADVISORY_KEY_WORKFLOW = 727302


async def _with_pg_advisory_lock(key: int, work):
    """抢到 pg_try_advisory_lock 才执行 work()，抢不到返回 None（本轮跳过）。
    锁由本函数持有的连接承载；work 内部自建会话不受影响。"""
    async with AsyncSessionLocal() as session:
        got = await session.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
        if not got:
            return None
        try:
            return await work()
        finally:
            await session.scalar(text("SELECT pg_advisory_unlock(:k)"), {"k": key})


async def _alert_loop() -> None:
    """后台周期任务：定期检查磁盘/内存/LLM 失败，超阈值通知管理员。"""
    while True:
        try:
            fired = await check_alerts()
            if fired:
                logger.info("系统告警触发: %s", fired)
        except Exception as exc:
            logger.warning("告警检查失败（下轮重试）: %s", exc)
        await asyncio.sleep(max(settings.ALERT_INTERVAL_SECONDS, 30))


async def _reminder_loop() -> None:
    """后台周期任务：每 REMINDER_INTERVAL_MINUTES 分钟跑一轮提醒规则 + 到期工作流 + 晨报。"""
    from app.services.dashboard import send_morning_briefs

    interval = max(settings.REMINDER_INTERVAL_MINUTES, 1) * 60
    while True:
        try:
            result = await _with_pg_advisory_lock(_ADVISORY_KEY_REMINDER, run_all_rules)
            if result and (result["tasks_created"] or result["notifications_created"]):
                logger.info("提醒规则本轮生成: %s", result)
        except Exception as exc:
            logger.warning("提醒规则周期任务失败（下轮重试）: %s", exc)
        try:
            wf_result = await _with_pg_advisory_lock(_ADVISORY_KEY_WORKFLOW, run_due_workflows)
            if wf_result and wf_result["executed"]:
                logger.info("工作流本轮执行: %s", wf_result)
        except Exception as exc:
            logger.warning("工作流周期任务失败（下轮重试）: %s", exc)
        try:
            await send_morning_briefs()  # 8:30 后当日未发则发（内部去重）
        except Exception as exc:
            logger.warning("晨报发送失败（下轮重试）: %s", exc)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    warning = settings.validate_jwt_secret()  # 非 dev 环境弱密钥直接抛错拒绝启动
    if warning:
        logger.warning("%s（当前 ENV=%s，仅告警）", warning, settings.ENV)
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    try:
        await init_db()
    except Exception as exc:
        # 数据库暂不可用时服务仍可启动（接口调用时再报错）
        logger.warning("数据库初始化失败，服务仍继续启动: %s", exc)
    # 解析格式启用开关缓存：依赖 init_db 建表，须在其后刷新
    from app.services.ingestion import refresh_enabled_parse_exts

    await refresh_enabled_parse_exts()
    reminder_task = asyncio.create_task(_reminder_loop())
    alert_task = asyncio.create_task(_alert_loop())

    async def _recover_ingestion():
        """启动恢复：进程重启会杀掉在飞的解析任务，把滞留 processing 的文档重新排队；
        支持格式清单升级后回填存量文件 supported 标记并捞回"仅存储"的旧文档。
        逐份串行处理，避免恢复时打爆视觉/嵌入模型接口。"""
        from app.services.ingestion import (
            process_document,
            recover_processing_documents,
            rescan_supported_files,
        )

        fixed = await rescan_supported_files()
        if fixed:
            logger.info("启动恢复：回填 %s 个文件的 supported 标记", fixed)
        ids = await recover_processing_documents()
        if ids:
            logger.info("启动恢复：重新排队 %s 个滞留/新增支持的文档", len(ids))
        for doc_id in ids:
            await process_document(doc_id)

    recovery_task = asyncio.create_task(_recover_ingestion())
    # 知识库 MCP server 的 session manager task group 必须在 FastAPI lifespan 内运行
    async with kb_mcp_server.session_manager_lifespan():
        yield
    reminder_task.cancel()
    alert_task.cancel()
    recovery_task.cancel()
    with suppress(asyncio.CancelledError):
        await reminder_task
    with suppress(asyncio.CancelledError):
        await alert_task
    with suppress(asyncio.CancelledError):
        await recovery_task
    # PR-F：关闭所有 MCP 长连接
    try:
        from app.services.skills.mcp_pool import get_mcp_pool
        await get_mcp_pool().close_all()
    except Exception as exc:
        logger.warning("MCP pool 关闭失败（忽略）: %s", exc)
    # dsh 基座：关闭 dsh ACP 子进程（关闭 stdin，dsh 收到 EOF 有界退出）
    try:
        from app.services.acp_bridge import bridge
        await bridge.close_all()
    except Exception as exc:
        logger.warning("dsh 进程关闭失败（忽略）: %s", exc)


app = FastAPI(title="知识库系统 API", version="0.2.0", lifespan=lifespan)

# 品牌静态资源（自定义 logo）：公开可访问
settings.brand_path.mkdir(parents=True, exist_ok=True)
app.mount("/brand", StaticFiles(directory=settings.brand_path), name="brand")

# 个人头像：公开可访问（由 /avatars 静态服务，前端 <img src> 直接引用）
settings.avatars_path.mkdir(parents=True, exist_ok=True)
app.mount("/avatars", StaticFiles(directory=settings.avatars_path), name="avatars")

# 知识库 MCP server（streamable-http，供 dsh 基座消费）：Bearer 令牌在中间件层校验。
# 用 Route 直挂 ASGI 子应用而非 Mount：Mount 对 POST /api/mcp 会 307 重定向到
# 带尾斜杠路径，MCP 客户端未必跟随；路径不重写，子应用内路由同为 /api/mcp。
app.routes.append(
    Route(
        "/api/mcp",
        endpoint=kb_mcp_server.build_mcp_asgi_app(),
        methods=["GET", "POST", "DELETE"],
        name="kb_mcp",
    )
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(agent_approvals.router, prefix="/api/v1")
app.include_router(brand.router, prefix="/api/v1")
app.include_router(customers.router, prefix="/api/v1")
app.include_router(industries.router, prefix="/api/v1")
app.include_router(followups.router, prefix="/api/v1")
app.include_router(opportunities.router, prefix="/api/v1")
app.include_router(kbs.router, prefix="/api/v1")
app.include_router(library.router, prefix="/api/v1")
app.include_router(rag.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(reminders.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(reports.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(workflows.router, prefix="/api/v1")
app.include_router(admin_users.router, prefix="/api/v1")
app.include_router(admin_llm.router, prefix="/api/v1")
app.include_router(admin_skills.router, prefix="/api/v1")
app.include_router(admin_mcp.router, prefix="/api/v1")
app.include_router(admin_system.router, prefix="/api/v1")
app.include_router(admin_settings.router, prefix="/api/v1")
app.include_router(notebooks.router, prefix="/api/v1")
app.include_router(admin_errors.router, prefix="/api/v1")
app.include_router(admin_audit.router, prefix="/api/v1")
app.include_router(recycle_bin.router, prefix="/api/v1")
app.include_router(permissions.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(sync_app.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """未捕获异常：记录到 error_logs，统一返回 500。HTTPException 走 FastAPI 默认处理器。"""
    logger.exception("未捕获异常: %s %s", request.method, request.url.path)
    await log_error(
        level="error",
        module="http",
        message=f"{request.method} {request.url.path} 未捕获异常",
        detail=repr(exc),
    )
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


@app.get("/health")
async def health():
    """轻量存活检查：含 DB ping（SELECT 1），库不可达返回 503。"""
    from sqlalchemy import text

    from app.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("健康检查 DB ping 失败: %s", exc)
        return JSONResponse(status_code=503, content={"status": "db_unavailable"})
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# 裸机单端口部署：由后端直接托管前端构建产物（无需 nginx）。
# 仅当 .env 配置了 FRONTEND_DIST 且目录存在 index.html 时启用；默认关闭。
# 注意必须放在所有 API 路由与 /health 之后注册，避免通配路由抢占匹配。
# ---------------------------------------------------------------------------
_dist_dir = settings.frontend_dist_path
if _dist_dir is not None and (_dist_dir / "index.html").is_file():
    _dist_resolved = _dist_dir.resolve()
    if (_dist_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_dist_dir / "assets"), name="frontend_assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        """SPA 回退：请求命中 dist 内真实静态文件则直接返回，否则回退 index.html 交给前端路由。"""
        index_html = _dist_resolved / "index.html"
        if not full_path:
            return FileResponse(index_html)
        try:
            candidate = (_dist_dir / full_path).resolve()
            candidate.relative_to(_dist_resolved)  # 防目录穿越
        except (ValueError, OSError):
            return FileResponse(index_html)
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_html)
