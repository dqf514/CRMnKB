import logging

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

logger = logging.getLogger(__name__)

# server_settings.timezone=UTC：保证 func.now() 与手工写入的 naive UTC 同一基准
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    connect_args={"server_settings": {"timezone": "UTC"}},
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """创建 pgvector 扩展与全部表，并写入种子数据。"""
    from app.models import Base  # noqa: F401  确保所有模型已注册
    from app.models.reminder_rule import ReminderRule
    from app.models.tenant import Tenant
    from app.models.user import User
    from app.core.security import hash_password

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
        # 幂等迁移：老库补列（新库 create_all 已包含，IF NOT EXISTS 无副作用）
        await conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS birthday DATE"))
        await conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS profile TEXT"))
        await conn.execute(
            text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS profile_status VARCHAR(20) DEFAULT 'idle'")
        )
        await conn.execute(
            text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS profile_updated_at TIMESTAMP")
        )
        # P1 Pipeline：DDQ 状态 + AI 阶段简报
        await conn.execute(
            text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS ddq_status VARCHAR(20) DEFAULT 'none'")
        )
        await conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS ai_brief TEXT"))
        await conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS ai_brief_at TIMESTAMP"))
        # 客户私有开关（TRUE=仅 owner+被分享者+admin 可见；NULL/FALSE=团队共享，默认沿用历史行为）
        await conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS is_private BOOLEAN"))
        # P1 跟进记录：下一步行动
        await conn.execute(text("ALTER TABLE follow_up_records ADD COLUMN IF NOT EXISTS next_step TEXT"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(100)"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500)"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(20)"))
        # 验证码用途隔离（login/register）
        await conn.execute(
            text("ALTER TABLE login_codes ADD COLUMN IF NOT EXISTS purpose VARCHAR(20) DEFAULT 'login'")
        )        # 手机号租户内唯一（部分唯一索引，NULL 不参与），为后续手机号/微信登录做准备
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tenant_phone "
                "ON users (tenant_id, phone) WHERE phone IS NOT NULL"
            )
        )
        # 微信登录预留列（登录流程后续接入，先存绑定关系）
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS wechat_openid VARCHAR(64)"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS wechat_unionid VARCHAR(64)"))
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tenant_wechat_openid "
                "ON users (tenant_id, wechat_openid) WHERE wechat_openid IS NOT NULL"
            )
        )
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS preferences JSONB DEFAULT '{}'")
        )
        # 首登强制改密标记（种子 admin 置 True，改密成功后由 /auth/password 置 False）
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN DEFAULT FALSE")
        )
        await conn.execute(
            text("ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS kb_id BIGINT")
        )
        await conn.execute(
            text("ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS file_id BIGINT")
        )
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS group_id BIGINT"))
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS status SMALLINT DEFAULT 1")
        )
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP")
        )
        await conn.execute(
            text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS industries JSONB DEFAULT '[]'")
        )
        await conn.execute(
            text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '[]'")
        )
        await conn.execute(
            text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS company VARCHAR(200)")
        )
        await conn.execute(
            text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS position VARCHAR(100)")
        )
        await conn.execute(
            text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS wechat VARCHAR(100)")
        )
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMP")
        )
        # 软删除（回收站）
        await conn.execute(text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP"))
        await conn.execute(text("ALTER TABLE library_files ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP"))
        await conn.execute(
            text("ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP")
        )
        await conn.execute(
            text("ALTER TABLE library_folders ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP")
        )
        # 文件夹权限：与文件一致——owner_id + is_private（存量置为管理员私有，与文件行为对齐）
        await conn.execute(text("ALTER TABLE library_folders ADD COLUMN IF NOT EXISTS owner_id BIGINT"))
        await conn.execute(text("ALTER TABLE library_folders ADD COLUMN IF NOT EXISTS is_private BOOLEAN"))
        # 任务来源标记：ai_generated + source（manual/rule/ai_analysis），与 models/task.py 对齐
        await conn.execute(
            text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS ai_generated BOOLEAN DEFAULT FALSE")
        )
        await conn.execute(
            text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'manual'")
        )
        # 时区统一：历史 timestamptz 列转 naive TIMESTAMP（连接时区已为 UTC，转换值即 UTC）
        await conn.execute(text("ALTER TABLE tasks ALTER COLUMN due_date TYPE TIMESTAMP"))
        await conn.execute(text("ALTER TABLE tasks ALTER COLUMN completed_at TYPE TIMESTAMP"))
        await conn.execute(text("ALTER TABLE workflows ALTER COLUMN last_run_at TYPE TIMESTAMP"))
        # 报告生成进度：progress 提示列（前端轮询展示滚动进度）
        await conn.execute(text("ALTER TABLE reports ADD COLUMN IF NOT EXISTS progress TEXT"))
        # Notebook 软删（回收站）
        await conn.execute(text("ALTER TABLE notebooks ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP"))
        # 权限/分享：owner + 私有标记（is_private 可空，NULL=存量=团队可见；新行 ORM 默认 TRUE）
        await conn.execute(text("ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS owner_id BIGINT"))
        await conn.execute(text("ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS is_private BOOLEAN"))
        await conn.execute(text("ALTER TABLE library_files ADD COLUMN IF NOT EXISTS owner_id BIGINT"))
        await conn.execute(text("ALTER TABLE library_files ADD COLUMN IF NOT EXISTS is_private BOOLEAN"))
        # 同步（本地 App）：内容变更游标 + 内容哈希（变更检测/冲突判断）
        await conn.execute(text("ALTER TABLE library_files ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP"))
        await conn.execute(text("ALTER TABLE library_files ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64)"))
        await conn.execute(text("ALTER TABLE notebooks ADD COLUMN IF NOT EXISTS is_private BOOLEAN"))
        await conn.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS resource_type VARCHAR(20)"))
        await conn.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS resource_id BIGINT"))
        # 提醒通知去重键（规则 id + 业务对象 id）：不用渲染后标题去重，
        # 含 {{hours_left}} 等模板的标题每轮渲染结果不同会导致重复通知
        await conn.execute(text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS dedupe_key VARCHAR(200)"))
        # PR-H：上传自动入库——加 is_auto 列 + 部分唯一索引
        await conn.execute(
            text("ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS is_auto BOOLEAN DEFAULT FALSE")
        )
        # PR-H 缺陷修复：uq_kb_tenant_one_auto 早期被误建成全列唯一约束 (tenant_id, is_auto)，
        # 导致每个租户只能建一个普通知识库（再建报 duplicate key）。
        # 先删掉错误约束（连带其索引），下面再建正确的部分唯一索引（仅 is_auto=TRUE 唯一）。
        await conn.execute(
            text("ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS uq_kb_tenant_one_auto")
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_tenant_one_auto"
                " ON knowledge_bases (tenant_id) WHERE is_auto = TRUE"
            )
        )
        # PR-D：directly_return 高置信短答案
        await conn.execute(
            text(
                "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS directly_return_answer TEXT"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE knowledge_documents"
                " ADD COLUMN IF NOT EXISTS directly_return_similarity FLOAT"
            )
        )
        # dsh agent：会话绑定的 dsh 侧 session id（普通 RAG 会话为 NULL）
        await conn.execute(
            text("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS dsh_session_id VARCHAR(64)")
        )
        # blend 检索：tsvector 列 + GIN 索引 + 老 chunk 回填
        await conn.execute(
            text("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS search_vector tsvector")
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_chunks_search_vector_gin"
                " ON document_chunks USING gin (search_vector)"
            )
        )
        # 常用查询索引（幂等）
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw"
                " ON document_chunks USING hnsw (embedding vector_cosine_ops)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_chunks_content_trgm"
                " ON document_chunks USING gin (content gin_trgm_ops)"
            )
        )
        for index_sql in (
            "CREATE INDEX IF NOT EXISTS ix_customers_tenant ON customers (tenant_id)",
            "CREATE INDEX IF NOT EXISTS ix_followups_customer ON follow_up_records (customer_id)",
            "CREATE INDEX IF NOT EXISTS ix_tasks_tenant_status ON tasks (tenant_id, status)",
            "CREATE INDEX IF NOT EXISTS ix_tasks_user ON tasks (user_id)",
            "CREATE INDEX IF NOT EXISTS ix_opportunities_customer ON opportunities (customer_id)",
            "CREATE INDEX IF NOT EXISTS ix_documents_kb ON knowledge_documents (kb_id)",
            "CREATE INDEX IF NOT EXISTS ix_chunks_document ON document_chunks (document_id)",
            "CREATE INDEX IF NOT EXISTS ix_notifications_user ON notifications (user_id, is_read)",
            "CREATE INDEX IF NOT EXISTS ix_library_files_tenant_folder ON library_files (tenant_id, folder_id)",
            "CREATE INDEX IF NOT EXISTS ix_chat_messages_session ON chat_messages (session_id)",
            "CREATE INDEX IF NOT EXISTS ix_llm_call_logs_tenant_created ON llm_call_logs (tenant_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_error_logs_tenant_created ON error_logs (tenant_id, created_at)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_industries_tenant_name ON industries (tenant_id, name)",
            "CREATE INDEX IF NOT EXISTS ix_customers_name_trgm ON customers USING gin (name gin_trgm_ops)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_skills_tenant_name ON skills (tenant_id, name)",
            # chunk_questions：自动问题表的索引（HNSW 用 embedding 召回）
            "CREATE INDEX IF NOT EXISTS ix_chunk_questions_chunk ON chunk_questions (chunk_id)",
            "CREATE INDEX IF NOT EXISTS ix_chunk_questions_embedding_hnsw"
            " ON chunk_questions USING hnsw (embedding vector_cosine_ops)",
            # PR-E：skill 调用日志索引
            "CREATE INDEX IF NOT EXISTS ix_skill_call_logs_skill_name"
            " ON skill_call_logs (skill_name)",
            # PR-F：MCP server 索引
            "CREATE INDEX IF NOT EXISTS ix_mcp_servers_tenant_enabled"
            " ON mcp_servers (tenant_id, enabled)",
            # PR-G：Notebook / Note 索引
            "CREATE INDEX IF NOT EXISTS ix_notebooks_tenant_updated"
            " ON notebooks (tenant_id, updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_notebook_notes_notebook_sort"
            " ON notebook_notes (notebook_id, sort)",
            # 热查询复合索引（可观测性+性能批次，与模型 __table_args__ 对应）
            "CREATE INDEX IF NOT EXISTS ix_chat_sessions_user_updated"
            " ON chat_sessions (user_id, updated_at)",
            "CREATE INDEX IF NOT EXISTS ix_reports_tenant_created"
            " ON reports (tenant_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_agent_approvals_tenant_status"
            " ON agent_approvals (tenant_id, status)",
        ):
            await conn.execute(text(index_sql))

    async with AsyncSessionLocal() as session:
        # 旧文档一次性数据迁移（默认知识库 + 库文件 + 回填关联），幂等
        from app.services.kb import migrate_legacy_documents

        await migrate_legacy_documents(session)
        await session.commit()

    # blend 检索：回填老 chunk 的 search_vector（仅 NULL 时更新，幂等）
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "UPDATE document_chunks SET search_vector = to_tsvector('simple', content)"
                " WHERE search_vector IS NULL"
            )
        )
        if result.rowcount:
            logger.info("回填 search_vector: %s 行", result.rowcount)

    async with AsyncSessionLocal() as session:
        # 旧 industry 单列并入 industries 数组（幂等）；为无行业记录的租户写入预置清单
        from app.services.industry import migrate_legacy_industry, seed_industries

        await migrate_legacy_industry(session)
        await seed_industries(session)
        await session.commit()

    # 品牌配置单行（幂等）：系统名称 + 自定义 logo
    async with AsyncSessionLocal() as session:
        from app.models.brand_settings import BrandSettings

        if await session.get(BrandSettings, 1) is None:
            session.add(BrandSettings(id=1, system_name="榜样知识库"))
            await session.commit()

    # 同步字段回填（幂等）：存量文件按磁盘内容算 sha256，updated_at 回退 created_at
    async with AsyncSessionLocal() as session:
        from app.services.library_sync import backfill_sync_fields

        await backfill_sync_fields(session)
        await session.commit()

    # 权限回填（幂等，只补 owner_id 为 NULL 的存量行）：owner 归租户 admin
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE knowledge_bases SET owner_id = COALESCE(owner_id, ("
                "SELECT u.id FROM users u WHERE u.tenant_id = knowledge_bases.tenant_id"
                " AND u.role = 'admin' ORDER BY u.id LIMIT 1)) WHERE owner_id IS NULL"
            )
        )
        await session.execute(
            text(
                "UPDATE library_files SET owner_id = COALESCE(owner_id, ("
                "SELECT u.id FROM users u WHERE u.tenant_id = library_files.tenant_id"
                " AND u.role = 'admin' ORDER BY u.id LIMIT 1)) WHERE owner_id IS NULL"
            )
        )
        # 文件夹权限存量对齐：owner 归租户 admin，并默认私有（与文件一致，避免"谁都看得见"）
        await session.execute(
            text(
                "UPDATE library_folders SET owner_id = COALESCE(owner_id, ("
                "SELECT u.id FROM users u WHERE u.tenant_id = library_folders.tenant_id"
                " AND u.role = 'admin' ORDER BY u.id LIMIT 1)) WHERE owner_id IS NULL"
            )
        )
        await session.execute(
            text("UPDATE library_folders SET is_private = COALESCE(is_private, TRUE)")
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        tenant_count = await session.scalar(select(func.count()).select_from(Tenant))
        if tenant_count:
            # 存量库：角色体系种子 + 迁移（幂等）：三系统角色 / 默认团队收纳存量用户 / role='user'→'member'
            from app.services.roles import seed_roles

            await seed_roles(session)
            await session.commit()
            return
        tenant = Tenant(id=1, name="默认租户")
        session.add(tenant)
        # 显式 flush：无 relationship() 时 SQLAlchemy 不保证跨表 INSERT 按外键依赖排序，
        # 必须先落 tenants 再落 users / reminder_rules，否则触发外键违规
        await session.flush()
        admin = User(
            tenant_id=1,
            username="admin",
            password_hash=hash_password("admin123"),
            name="管理员",
            role="admin",
            must_change_password=True,  # 初始口令 admin123 仅用于首登，登录后强制改密
        )
        session.add(admin)
        # 示例提醒规则：7 天未跟进（默认关闭，由用户自行开启）
        session.add(
            ReminderRule(
                tenant_id=1,
                name="7天未跟进提醒",
                trigger_type="days_since_last_followup",
                trigger_config={"threshold_days": 7},
                action_config={
                    "template": "客户 {{customer_name}} 已 {{days}} 天未跟进，请尽快联系"
                },
                enabled=False,
            )
        )
        await session.commit()
        # 新库：角色体系种子（三系统角色 + 默认团队并把 admin 划入）
        from app.services.roles import seed_roles

        await seed_roles(session)
        await session.commit()
        logger.info("种子数据已写入：默认租户 / admin 用户 / 示例提醒规则 / 系统角色")
