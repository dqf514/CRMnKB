# 技术设计文档（TDD）—— 榜样知识库（内部知识库 + AI CRM）

> **文档性质**：由当前系统代码反向推导的技术设计文档，描述系统**已实现**的架构与技术细节。
> **配套文档**：`docs/PRD-系统功能规格.md`（功能基线）、`docs/需求整理-9月14日录音.md`（变更需求）。
> **版本**：v1.0（代码基线）｜ 整理日期：2026-08-21

---

## 1. 总体架构

### 1.1 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│ 客户端层                                                      │
│  浏览器 SPA（Vue3）        本地同步 App（Python/tkinter）       │
│  vite dev :5173 / nginx :80   双向同步 ⇄ /api/v1/library/*   │
└──────────────┬──────────────────────────────┬───────────────┘
               │ HTTPS/HTTP（同源 /api）        │ JWT（30 天长效）
┌──────────────▼──────────────────────────────▼───────────────┐
│ 接入层：nginx（生产）/ vite proxy（开发，target=127.0.0.1）     │
│  SSE：proxy_buffering off + read_timeout 300s                │
└──────────────┬──────────────────────────────────────────────┘
┌──────────────▼──────────────────────────────────────────────┐
│ 应用层：FastAPI 模块化单体（uvicorn :8100）                    │
│  api/ 路由层 → services/ 业务层 → models/ SQLAlchemy 模型      │
│  后台任务：_reminder_loop / _alert_loop / 启动恢复             │
└──────┬───────────────┬───────────────┬──────────┬───────────┘
       │               │               │          │
┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐ ┌─▼───────────┐
│ PostgreSQL16│ │ LLM 服务     │ │ MCP 服务     │ │ 外部服务     │
│ +pgvector   │ │ OpenAI 兼容/ │ │ stdio/SSE   │ │ SMTP/搜索引擎│
│ +pg_trgm    │ │ Ollama      │ │ 连接池       │ │             │
└─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘
```

### 1.2 端口与部署形态

| 形态 | 编排文件 | 服务 | 对外端口 |
|---|---|---|---|
| 开发 | `docker-compose.yml` | 仅 PostgreSQL 16 + pgvector | 5432（开发直连）；后端 8100 / 前端 5173 跑宿主机 |
| 单机生产 | `docker-compose.prod.yml` | postgres + backend + web(nginx) | 仅 80；PG 不暴露 |
| 云端双机 | `docker-compose.cloud.yml` | backend + web（PG 外置/RDS） | 仅 80 |
| 裸机 | `deploy/`（systemd + nginx） | 后端直接托管前端 dist（`FRONTEND_DIST`） | 单端口 |

### 1.3 技术栈选型

- **后端**：Python 3.13 / FastAPI 0.115（模块化单体）/ SQLAlchemy 2.0 async（asyncpg）/ pydantic 2 + pydantic-settings。
- **数据库**：PostgreSQL 16 + pgvector（HNSW 向量索引）+ pg_trgm（三元组模糊匹配）+ tsvector（`simple` 分词全文检索）。
- **LLM 接入**：OpenAI 兼容 API（默认 DeepSeek）+ 本地 Ollama 双实现；模型配置 DB 优先（Key Fernet 加密）、`.env` 兜底。
- **文档解析**：pypdf / pymupdf（扫描件渲染）/ python-docx / openpyxl / xlrd / python-pptx / extract_msg / olefile（老 Office）/ RapidOCR·Tesseract（可选本地 OCR）/ pypff 或 readpst（PST）。
- **PDF 导出**：Playwright Chromium（浏览器二进制在 `backend/data/ms-playwright`）。
- **前端**：Vue 3 + Vite 6 + Element Plus + Pinia + vue-router 4 + ECharts + markdown-it/highlight.js + mammoth + xlsx + cropperjs；`type: module`，无 TypeScript。
- **MCP**：`mcp>=1.8,<2`；**`sse-starlette<3` 钉版**（新版要求 starlette≥0.49，与 fastapi 0.115 不兼容）。

---

## 2. 后端架构

### 2.1 分层与目录

```
backend/app/
├── api/            # 路由层：31 个路由模块，统一 /api/v1 前缀；deps.py 公共依赖注入
├── services/       # 业务层：ingestion / rag / chat / kb / report / pdf / workflow / reminder
│   │               #   / permissions / monitoring / backup / maintenance / audit / email / pst / ocr …
│   ├── llm/        # LLM 抽象层：base + api_llm + ollama_llm + instrumented + factory + usage
│   └── skills/     # Agent 技能框架：builtin + api_skill + mcp_skill + mcp_pool + mcp_discovery + registry
├── models/         # 30+ 张 SQLAlchemy 表，base.py 仅声明 Base
├── schemas/        # pydantic DTO
├── core/           # security.py（JWT/bcrypt）、crypto.py（Fernet）
├── config.py       # pydantic-settings 全部配置（读 backend/.env）
├── database.py     # engine/session + init_db（建表/迁移/种子）
└── main.py         # 应用装配 + 后台任务 + 全局异常
```

**分层约定**：api 层只做参数校验/权限/响应组装，业务逻辑全部下沉 services；服务层函数模块级 import（保证测试可 monkeypatch）。

### 2.2 应用装配（main.py，251 行）

- 中间件仅 `CORSMiddleware`（显式白名单、`allow_credentials=False`）；鉴权/限流走依赖注入与服务层。
- 静态挂载：`/brand`（品牌资源）、`/avatars`（头像）公开；`FRONTEND_DIST` 非空且存在 index.html 时挂载 `/assets` + SPA 通配路由（**最后注册**，防抢占 API）。
- 异常处理：`@app.exception_handler(Exception)` 统一记 `error_logs`（module=http）并返回 500；HTTPException 走 FastAPI 默认。
- `GET /health`：`SELECT 1` DB ping，不可达 503。
- 31 个路由模块注册顺序：auth → brand → customers → … → dashboard → search。

### 2.3 生命周期（lifespan）

**启动**（顺序敏感）：
1. `validate_jwt_secret()`——非 dev 环境弱密钥（默认值或 <32 字符）**拒绝启动**；
2. 创建上传目录；
3. `init_db()`（try/except 包裹，DB 不可用不阻塞启动，靠 /health 暴露）；
4. `refresh_enabled_parse_exts()`（依赖 init_db 建表，必须在其后）；
5. 启动 3 个后台任务：`_reminder_loop`（提醒+工作流+晨报，默认 10min/轮）、`_alert_loop`（告警，默认 300s/轮）、`_recover_ingestion`（一次性：回填 supported 标记 + **串行**重排滞留 processing 文档——刻意串行防打爆视觉/嵌入模型）。

**关闭**：依次 cancel 三任务（suppress CancelledError）→ 关闭 MCP 连接池。

### 2.4 依赖注入（api/deps.py）

- `get_db`：每请求一个 AsyncSession（async generator）。
- `get_current_user`：Bearer JWT → decode → 用户存在且未停用 → **改密失效校验**（`iat >= password_changed_at`）。
- `get_current_user_with_query_token`：文件直链专用，Bearer 优先；`?t=` 必须是带 `file` 声明且与路径 file_id 一致的 5 分钟短令牌。
- `require_admin`：`role != "admin"` → 403；`/admin/*` 与 `/recycle-bin` 在 router 级强制。

### 2.5 数据库连接

```python
create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True,
    connect_args={"server_settings": {"timezone": "UTC"}})
```

- `pool_pre_ping` 剔除失效连接；连接时区强制 UTC，保证 `func.now()` 与手工 naive UTC 同基准。
- 连接池用 SQLAlchemy 默认（pool_size=5, max_overflow=10）。
- `expire_on_commit=False`。

---

## 3. 数据库设计

### 3.1 Schema 演进机制（无 Alembic）

- 新库：`Base.metadata.create_all` 全覆盖。
- 老库：`init_db()` 内追加**幂等迁移**——`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 为主体；两类特例：
  - `ALTER COLUMN ... TYPE TIMESTAMP`（时区统一：tasks.due_date/completed_at、workflows.last_run_at，配合连接时区 UTC 无损转换）；
  - `DROP CONSTRAINT IF EXISTS uq_kb_tenant_one_auto` + 重建**部分唯一索引**（修复早期误建全列唯一约束导致每租户只能建一个普通 KB 的缺陷）。
- **铁律：改模型字段必须 create_all 与幂等 ALTER 两处同步**。

### 3.2 索引清单（26 条，init_db 幂等创建）

| 索引 | 表 | 类型/列 | 用途 |
|---|---|---|---|
| ix_chunks_embedding_hnsw | document_chunks | HNSW (vector_cosine_ops) | 向量召回 |
| ix_chunks_search_vector_gin | document_chunks | GIN (search_vector) | blend BM25 |
| ix_chunks_content_trgm | document_chunks | GIN (gin_trgm_ops) | 关键词模糊 |
| ix_chunk_questions_embedding_hnsw | chunk_questions | HNSW | 问题向量检索 |
| ix_chunk_questions_chunk | chunk_questions | btree | 关联查询 |
| ix_customers_name_trgm | customers | GIN (gin_trgm_ops) | 客户查重 similarity |
| ix_customers_tenant / ix_tasks_tenant_status / ix_tasks_user / ix_notifications_user / ix_library_files_tenant_folder / ix_chat_messages_session / ix_opportunities_customer / ix_documents_kb / ix_chunks_document / ix_followups_customer | 各表 | btree | 常规过滤 |
| ix_llm_call_logs_tenant_created / ix_error_logs_tenant_created / ix_skill_call_logs_skill_name / ix_mcp_servers_tenant_enabled / ix_notebooks_tenant_updated / ix_notebook_notes_notebook_sort | 各表 | btree | 统计与列表 |
| uq_industries_tenant_name / uq_skills_tenant_name | industries / skills | UNIQUE | 租户内唯一 |
| uq_kb_tenant_one_auto | knowledge_bases | UNIQUE 部分索引 (tenant_id) WHERE is_auto | 每租户一个自动库 |

### 3.3 核心表分组（字段细节见 PRD 对应章节）

- **账户**：tenants / users（preferences JSONB、password_changed_at）/ user_groups / login_attempts / brand_settings / system_settings。
- **CRM**：customers（industries/tags JSONB、profile 三字段、deleted_at）/ follow_up_records / opportunities / industries。
- **知识库**：knowledge_bases（type/customer_id/is_auto/is_private）/ knowledge_documents（status 状态机、doc_metadata JSONB、directly_return_*）/ document_chunks（embedding + search_vector）/ chunk_questions / document_versions。
- **文档库**：library_files（content_hash/updated_at 同步游标）/ library_folders（自引用树）。
- **AI**：chat_sessions / chat_messages / rag_query_logs / ai_feedback / llm_models / llm_call_logs / skills / skill_call_logs / mcp_servers。
- **自动化**：workflows / workflow_runs / reminder_rules / tasks / notifications。
- **工作台**：notebooks / notebook_notes / reports（params JSONB 存演示版/修改历史）。
- **运维**：audit_logs / error_logs / resource_permissions。

### 3.4 种子与回填（init_db 阶段二~六，均幂等）

1. 旧文档迁移（无 kb_id 的历史文档 → 默认知识库 + 补 LibraryFile）；2. search_vector 回填（`to_tsvector('simple', content)`）；3. 行业迁移（旧 industry 列并入 industries 数组）+ 22 项预置行业种子；4. 品牌单行（id=1）；5. 同步字段回填（sha256/updated_at）；6. 权限回填（存量 owner 归租户首个 admin、文件夹 is_private 默认 TRUE）。
- 种子（仅 tenants 为空时）：默认租户 + `admin/admin123` + 示例提醒规则（默认关闭）。注意：tenants 需显式 flush 后再插 users（无 relationship 时 SQLAlchemy 不保证跨表插入顺序）。

---

## 4. 核心子系统设计

### 4.1 认证与安全

- **JWT**：HS256，`{sub, username, iat, exp}`；默认 12h，`long_lived=true` 30 天（同步 App）；文件令牌 5 分钟且绑定 file_id。
- **改密失效**：`password_changed_at` 时间戳比对 `iat`，用户自助改密与管理员重置均触发。
- **登录限流**（DB 持久化，跨 worker）：账号+IP 15min/5 次锁定；IP 15min/20 次熔断；成功清除记录；窗口自然过期。
- **密码**：bcrypt（pwdlib），最小 8 位。
- **API Key 加密**：Fernet，密钥 = base64url(SHA256(JWT_SECRET))，无新增环境变量；解密失败按明文兼容旧数据；脱敏规则 = 头 3 + `****` + 尾 4。
- **SSO**：一次性 code（60s、单次、进程内存——多 worker 已知局限）。

### 4.2 内容权限（ACL）

- 模型：`resource_permissions(tenant_id, resource_type, resource_id, user_id, permission)` 唯一约束 + 资源行 owner/is_private；等级 read<edit<owner。
- 判定：`max(owner 身份, ACL, 团队可见 read)`；**文件夹级联继承**（file 沿所在文件夹链、folder 沿祖先链取最高，只升不降，防环）。
- 批量解析 `accessible_ids`（owner ∪ 团队可见 ∪ ACL；folder 递归后代；file 沿链判定）供列表与 RAG 检索过滤；admin 全部 owner。
- RAG/Chat 入口必经 `accessible_ids` / `filter_accessible_ids`；检索 SQL 另排除软删 KB/文件——**检索安全双保险**。

### 4.3 文档摄入管线（ingestion.py）

```
上传/关联 → KnowledgeDocument(status=processing) → 后台 process_document
  ├─ PST → pst.py 流式拆解（每封邮件独立文档，并发≤3，进度写容器 metadata）
  ├─ 清旧 chunks（立即 commit，避免长事务堵 REINDEX CONCURRENTLY）
  ├─ _extract_text 按类型分派 → (raw_text, method, model)
  │    图片: tif→PNG → 本地OCR(≥10字) → 视觉模型
  │    PDF:  pypdf → 扫描件判定(<页数×20字) → pymupdf渲染(≤50页) → 本地OCR → 视觉逐页
  │    音频: ASR；视频: ffmpeg抽音轨→ASR
  │    邮件: 头+正文+附件递归（嵌套邮件不再展开）
  │    doc/ppt: LibreOffice headless → olefile FIB 精确提取兜底
  ├─ clean_text → chunk_text v2（Markdown 标题锚定/代码围栏掩码/智能断句，512+64）
  ├─ embed 分批 32（单批失败重试一次）→ 写 chunks → UPDATE search_vector
  ├─ chunk_questions 生成（每 chunk 3 问，chat LLM → embed，失败不拖垮主流程）
  └─ status=ready + 版本快照（重解析前存 document_versions）
任何异常 → status=failed + content=[处理失败] + error_logs，绝不抛出
```

- 格式开关：`system_settings.parse_formats_enabled`（5 组 35 扩展名）+ 进程内缓存；关闭格式仅存储；启用后存量重启补解析。
- 视觉复核：先成功提取再替换切片，失败保留旧内容记 `vision_review_error`。
- 同步 App 更新文件内容 → `mark_updated_and_reparse` 触发重解析（旧内容自动快照）。

### 4.4 RAG 检索管线（rag.py / chat.py）

**chat 完整管线**：改写（最近 6 条历史，指代消解，失败回退原问）→ 嵌入 → 混合检索 → 问题加分 → Rerank → Small2Big → 裁剪 → directly_return 检查 → 生成 → 落库。

- **blend（默认）**：pgvector 余弦召回 top_k×10（≤500，要求 embedding 与 search_vector 双非空）→ VALUES 回灌算 `ts_rank_cd(..., 32)` → **综合分 = (1−cosine) + BM25 相加融合** → DISTINCT ON 去重 → >0.5 阈值 → top_k；失败/空回退旧 RRF 路径（向量+trgm 各 top_k×2，k=60）。
- **keywords 模式**：`ts_rank_cd(plainto) + word_similarity`，过滤条件 `search_vector @@ tsq OR content % q`（命中两个 GIN 索引避免全表 similarity）。
- **chunk_questions 加分**：问题向量检索 top_k×2，命中 chunk `score += sim × 0.5`。
- **Rerank 三级降级**：专用 rerank 模型（解析兼容 Jina/SiliconFlow/Cohere/Voyage/TEI）→ chat LLM 打分（候选截 300 字，JSON 数组容错解析，clamp [0,1]）→ 放弃 rerank 走阈值。**rerank 成功不做硬阈值过滤**（本地模型分数不校准）。
- **Small2Big**：chunk_index ±1 窗口按文档合并，批量 SQL 消除 N+1；裁剪上限 4000 字（仅 chat；`/rag/query` 不裁剪）。
- **file_ids 直读通道**：≤50000 字直接全文进 prompt（跳过检索与嵌入），sources 标 direct；文件未解析完成返回专属话术。
- **directly_return**：top3 命中文档的高置信答案直接返回跳过 LLM。
- **兜底**：`grounded = max(score)≥0.5`；未命中 → 文件未解析话术 / 降级普通对话（声明未参考知识库，带 10 条历史）/ 固定话术「未在知识库中找到相关信息，建议转人工处理。」

### 4.5 聊天与 SSE（chat.py）

- SSE 帧：`meta{session_id}`（失效静默新建自愈）→ `status` → `sources{sources, grounded}` → 可选 `direct_return` → `thinking`/`tool`（Agent 事件，asyncio.Queue 0.3s 排空）→ `token`（`_ThinkStripper` 状态机跨 token 剥 think 块，残留 >4096 字符放行防吞字）→ `done{query_log_id}` / `error`；15s 心跳注释防 nginx 掐断。
- 会话校验/创建在流开始前完成并 commit（防流结束后事务回滚致外键悬空）。
- 每轮落库：rag_query_logs ×1 + chat_messages ×2；刷新 session.updated_at。
- **Agent 循环**：最多 3 轮 chat_with_tools_stream → tool_calls 逐个执行（失败不中断）→ 结果以 role=tool 回注 → 轮尽后无工具再要最终答案；模型不支持 function calling 回退普通对话。
- thinking 开关：`LLM_CHAT_THINKING_PARAM`（默认 enable_thinking）随请求体发 false；参数名置空则只剥输出。

### 4.6 LLM 抽象层（services/llm/）

- `base.py`：LLMService ABC（chat/embed 抽象；chat_stream 默认退化一次性；transcribe/chat_with_tools/rerank 默认抛不支持；`last_usage` 属性）。
- `api_llm.py`：OpenAI 兼容实现——chat/embed/chat_stream/chat_with_tools(+stream)/rerank/transcribe；超时 chat 180s / embed·rerank 60s / transcribe 300s；rerank 解析兼容多家返回格式。
- `ollama_llm.py`：`/api/chat`（NDJSON，usage 取 prompt_eval_count/eval_count）、`/api/embed`；工具/rerank/转写不支持。
- `factory.py`：缓存 `(model_type, tenant_id) → (version, instance)`，模型变更 `invalidate_llm_cache()` 递增版本清空；**每次调用新包 InstrumentedLLM**（防首个调用方 caller 被焊死）。
- 五类解析：chat/embed = DB 优先 → .env 兜底；vision = DB vision → chat 默认；rerank = DB 或 None（降级链）；asr = 仅 DB，未配置抛错。
- `instrumented.py`：全方法埋点写 llm_call_logs（失败 error 截 500 字）+ 失败同源写 error_logs（module=llm，供告警）。

### 4.7 Skills 与 MCP

- **Skill 基类**：name/description/parameters(JSON Schema)；`config.timeout` 每技能独立（默认 60s，钳 [1,600]）；结果截 3000 字。
- **内置**：web_search（tavily/bing/bocha/bing_cn 免 key 爬取/duckduckgo 兜底，统一前 5 条截 3000 字）；web_fetch（**SSRF 防护**：仅 http/https、IP 归一化判内网、DNS 解析任一内网即拒，防 DNS rebinding）。
- **API Skill**：`{{arg}}` 模板渲染 + SSRF + 方法白名单；已知键名缺陷（前端 params_schema / 后端 parameters）。
- **MCP 连接池**：进程单例懒连接，每 server 一长连接 + 锁串行化；异常标 broken 下次自动重建；协议降级补丁（2025-11-25 → 2025-06-18 防官方 Node 服务握手卡死）；transport/config 变更即断开置 unknown；删除 server 级联删派生 skill（名 `{server}__{tool}`）。

### 4.8 自动化引擎

- **调度**：`_reminder_loop` 默认 10min/轮：提醒规则（全租户）→ 到期工作流 → 晨报（8:30 后当日去重）；各段独立 try/except；工作流逐条 SAVEPOINT 隔离。
- **工作流**：5 触发器（interval/daily/weekly/birthday/condition，`should_run` 纯函数）× 3 动作（send_email/create_task/create_notification）；条件 DSL 白名单（6 字段 × 5 操作，AND；gt/lt 为字符串比较）；三层去重（邮件按工作流+客户+当天、任务按客户+标题+未完成、通知按用户+标题+当天），run 开始批量预取去重集合消除 N+1；接收人 = 客户 owner → 回退租户最早启用用户。
- **提醒规则**：3 种（N 天未跟进/商机停滞/任务临期）；前两种建任务+通知，临期仅通知（task_id+标题永久去重）。
- **AI 待办提取**：跟进创建后后台提取 JSON 待办（raw_decode 容错），建 Task（ai_generated=True, source=ai_analysis）。
- **邮件**：smtplib SSL/STARTTLS，30s 超时，asyncio.to_thread 包装；SMTP_HOST 空则优雅降级。

### 4.9 报告与 PDF

- 生成：BackgroundTasks 后台跑，**非 SSE——每 ~2s 增量写回 content+progress，前端 2s 轮询**；四类型（客户分析/周报/月报/自定义）；自定义走 file_ids 直读或嵌入检索（TOP_K 12，不做阈值过滤保素材广度）；zh_en 双语（data-lang 标记）。
- 演示版：正文截 12000 字 → LLM 转 16:9 HTML 幻灯片（自带翻页脚本）存 params.presentation_html。
- 对话式修改：旧版入 params.revisions（上限 20）；失败回滚 ready。
- PDF：Playwright sync API + `asyncio.to_thread`；文档版 A4 注入分页 CSS（标题后不分页/表格图片不跨页）；演示版 13.33×7.5in 横向；`PLAYWRIGHT_BROWSERS_PATH` 在 config.py import 时 setdefault（必须先于 playwright import）。

### 4.10 运维子系统

- **备份**：`pg_dump --clean --if-exists | gzip` + uploads/brand 打 tar.gz + manifest；`docker exec`（PG_DOCKER_CONTAINER 非空）或本机二进制（PGPASSWORD 传密）；保留最近 10 份；恢复 = 清目录解 tar（`filter="data"` 防穿越）+ psql 导入，破坏性。
- **维护**：asyncpg 直连 autocommit（VACUUM 不能在事务内）；vacuum / reindex（CONCURRENTLY 重建 3 个检索索引 + 补 VACUUM）/ cleanup（孤儿文件清理）。
- **告警**：磁盘 ≥85% / 内存 ≥90% / LLM 10min 内失败 ≥5 次 → 通知全体启用 admin；**仅状态翻转通知一次**。
- **异常中心**：log_error 独立 session 失败静默；message 500 字 / detail 2000 字截断。
- **回收站**：4 类实体软删（customer/file/kb/notebook）；恢复查同名冲突；彻底删除级联清理 + 后台 VACUUM；批量逐项独立事务。

---

## 5. 前端架构

### 5.1 工程化

- Vite 6 + `@vitejs/plugin-vue` 唯一插件；dev 代理 `/api`、`/brand` → `http://127.0.0.1:8100`（**必须 127.0.0.1**：localhost 优先解析 IPv6 ::1，后端仅监听 IPv4）。
- 无环境变量文件：API 全走同源 `/api` 相对路径（容器化免配置前提）。
- `index.html` 内联首屏免闪烁脚本：渲染前读 localStorage 应用主题/字号。

### 5.2 状态管理（Pinia）

| store | 职责 |
|---|---|
| auth | token/user（镜像 localStorage）、login/fetchMe/logout |
| theme | mode/accent/pageSize/fontSize；本地即时生效 + 静默同步后端偏好，登录后以后端为准 |
| brand | systemName/logoUrl（公开接口），同步 document.title |
| industries | 行业字典缓存（enabled_only） |
| uploads | 全局上传队列（逐文件顺序上传、字节进度、PST 进度轮询、切页不中断） |
| studio | 工作台中枢：notebooks/notes/对话按 notebookId 隔离（切页流式不中断）、session_id localStorage 续聊 |

### 5.3 HTTP 与 SSE

- axios：baseURL `/`、60s 超时（导入 300s/文件内容 120s）；请求注入 Bearer；401 清凭证跳登录；错误统一 ElMessage 透传 detail；下载走 blob。
- SSE：`fetch + ReadableStream`（axios 不支持流式 POST），按 `\n\n` 分帧解析 `data:` JSON，async generator 产出，支持 AbortController。

### 5.4 主题系统

- 明暗：`html.dark` + `:root`/`html.dark` 双套设计令牌。
- 品牌色 5 色：运行时 hex mix 生成 Element Plus 完整色阶（light-3/5/7/8/9 + dark-2）写 `:root`。
- 字号三档：缩放 `--app-font-scale`（1/1.1/1.2）+ `--el-font-size-*`，**不用 html zoom**（会致 EP 弹层坐标漂移、100vh 破版）。
- ECharts 封装 `useChart`：明暗跟随（重建实例）、resize 自适应、卸载 dispose。

### 5.5 工具模块（utils/）

- `format.js`：~20 个业务枚举的中文 label + 颜色映射；`parseServerDate`（naive UTC 补 Z 解析）等格式化函数。
- `markdown.js`：markdown-it 单例（html:false 防 XSS）+ highlight.js 仅 12 种语言控制体积。
- `filePreview.js`：扩展名 → 预览类别分派；`uploadFormats.js`：启用格式缓存过滤；`tableExport.js`：通用 xlsx 导出。

---

## 6. 同步 App（sync-app/）

- Python + tkinter/pystray/watchdog/httpx，PyInstaller 单文件 exe。
- **状态库**：同步目录下 `.sync-state/sync.db`（files: path/file_id/last_synced_hash；meta: cursor/token/sync_user）。
- **下行**：轮询 `GET /library/changes?since=`（游标=server_time）；按 file_id 跟踪移动/重命名/删除；冲突保留 `(本机冲突 时间戳)` 副本；回声（content_hash 一致）跳过。
- **上行**：watchdog 2s 去抖；修改先冲突预检（GET 单文件 hash），远端也改则本地改名冲突副本；更新内容触发 KB 重解析。
- **自动更新**：`/sync-app/version` 比对 sha256 → 下载校验 → bat 替换重启（仅冻结模式）。
- 忽略规则：`.sync-trash`/`.sync-state`/`~$*`/隐藏文件/临时扩展名。
- 托盘动画（12 帧旋转）+ 同步面板（TransferTracker 线程安全，保留 30 条）。

---

## 7. 部署架构

### 7.1 镜像

- **backend**（python:3.13-slim 单阶段）：apt 装 ffmpeg/curl/postgresql-client → pip 依赖（分离层利用缓存）→ `playwright install --with-deps chromium` + fonts-noto-cjk（PDF 中文必需）→ COPY app；EXPOSE 8100。
- **frontend**（两阶段）：node:20-alpine `npm ci` + build → nginx:1.27-alpine 托管 dist。
- 镜像内 Playwright 浏览器装默认路径；**运行挂载不得整目录覆盖 `/app/data`**（会遮蔽浏览器），只挂 uploads/brand/backups/sync_app 子卷（云端编排注释明示）。

### 7.2 nginx（frontend/nginx.conf）

- `client_max_body_size 100m`（对齐 MAX_UPLOAD_MB）；
- `/api/` → `http://backend:8100`：**`proxy_buffering off` + `proxy_read_timeout 300s`**（SSE 关键配置，与后端 LLM_CHAT_TIMEOUT_SECONDS=180 留余量）；
- `/` SPA 回退 index.html；`/assets/` 长缓存 7d immutable；
- 无 HTTPS（建议外层 Caddy 终结 TLS）。

### 7.3 生产要点

- 仅 web:80 对外；`POSTGRES_PASSWORD` 用 `:?` 语法强制注入；
- backend 走 `backend/.env`（required:false）+ 环境变量覆盖 DATABASE_URL（容器网络主机名 postgres）；
- 首次启动自动建表 + 种子；初始化顺序：admin 登录改密 → 模型管理注册五类模型（embed 最关键）→ 可选启用联网搜索 skill；
- 内存建议 ≥4GB（构建期 node 约需 2GB）。

---

## 8. 配置体系（config.py，pydantic-settings）

模块级副作用：`PLAYWRIGHT_BROWSERS_PATH` setdefault（必须先于 playwright import）。派生属性：upload_path/brand_path/backup_path/ocr_models_path/avatars_path/sync_app_path/frontend_dist_path/cors_origin_list。

| 分组 | 配置项（默认值） |
|---|---|
| 环境 | ENV(dev) |
| 数据库 | DATABASE_URL(postgresql+asyncpg://crm:crm123@localhost:5432/crmnkb) |
| JWT | JWT_SECRET(dev-secret-change-me)、JWT_EXPIRE_MINUTES(720) |
| 网络 | CORS_ORIGINS(localhost/127.0.0.1:5173)、MAX_UPLOAD_MB(100)、UPLOAD_DIR(data/uploads)、FRONTEND_DIST(空=关闭 SPA 托管) |
| LLM 兜底 | LLM_CHAT_PROVIDER(api)、LLM_EMBED_PROVIDER(ollama)、LLM_API_BASE_URL(deepseek)、LLM_API_KEY、LLM_API_CHAT_MODEL(deepseek-chat)、LLM_API_EMBED_MODEL(text-embedding-3-small)、LLM_CHAT_TIMEOUT_SECONDS(180)、OLLAMA_BASE_URL(localhost:11434)、OLLAMA_CHAT_MODEL(qwen2.5:7b)、OLLAMA_EMBED_MODEL(bge-m3) |
| RAG | EMBEDDING_DIM(1024)、RAG_SCORE_THRESHOLD(0.5)、RAG_TOP_K(5)、RAG_HYBRID(true)、RAG_SEARCH_MODE(blend)、RAG_NEIGHBOR_WINDOW(1)、RAG_QUERY_REWRITE(true)、RAG_RERANK(true)、RAG_MAX_CONTEXT_CHARS(4000)、RAG_DIRECT_FILE_MAX_CHARS(50000)、RAG_GENERATE_QUESTIONS(true)、RAG_QUESTIONS_PER_CHUNK(3) |
| 聊天 | CHAT_FALLBACK_TO_LLM(true)、CHAT_DEFAULT_THINKING(true)、LLM_CHAT_THINKING_PARAM(enable_thinking) |
| 切片 | CHUNK_SIZE(512)、CHUNK_OVERLAP(64) |
| OCR | OCR_PROVIDER(auto)、OCR_IMAGE_MIN_CHARS(10)、OCR_PDF_MIN_CHARS_PER_PAGE(20) |
| 业务 | PROFILE_MAX_CHARS(8000)、REMINDER_INTERVAL_MINUTES(10) |
| SMTP | SMTP_HOST/PORT(465)/USER/PASSWORD/FROM/USE_SSL(true) |
| 运维 | PG_DOCKER_CONTAINER(crmnkb-postgres)、ALERT_INTERVAL_SECONDS(300)、ALERT_DISK_PERCENT(85)、ALERT_MEM_PERCENT(90)、ALERT_LLM_FAIL_WINDOW_MIN(10)、ALERT_LLM_FAIL_THRESHOLD(5) |

---

## 9. 测试体系

- `pytest.ini`：`asyncio_mode=auto`；`conftest.py` 仅保证导入路径，**无共享 fixture**。
- 37 个测试文件平铺 `tests/`，443 个测试函数；重点覆盖：RAG 管线（blend/RRF/rerank/chunking/问题生成/direct_return/file_context）、多模态摄入（35 例）、权限（文件夹级联）、安全加固、Skills/MCP。
- **不连真实数据库**三件套：`_FakeSession`（execute 返回预设、scalar 固定 0 规避限流查询）+ `dependency_overrides`（替换 get_db/get_current_user）+ monkeypatch 打桩 **api 模块内已 import 的服务函数名**（关键约定：服务层必须模块级 import）。
- 前端无测试/lint/format 配置。

---

## 10. 关键设计决策（ADR 摘要）

| # | 决策 | 理由 / 权衡 |
|---|---|---|
| 1 | 模块化单体而非微服务 | 企业内网小规模部署；单进程 + 后台 asyncio 任务满足调度需求 |
| 2 | 无 Alembic，init_db 幂等迁移 | 简化部署（一键启动）；代价：改字段须两处同步 |
| 3 | naive UTC + 连接时区强制 UTC | 避免 timestamptz 历史包袱；前端统一 `parseServerDate` 补 Z |
| 4 | blend 检索 = 余弦 + BM25 **相加融合**（非 RRF） | 参照 MaxKB blend_search；RRF 作降级路径保留 |
| 5 | Rerank 成功不做硬阈值过滤 | 本地模型分数分布不校准，硬阈值误杀真实命中；相关性交给生成模型 |
| 6 | 解析失败只标 failed 不抛出 | 批量摄入场景单文件失败不阻断；配合启动恢复与手动重试 |
| 7 | 启动恢复串行处理 | 防打爆视觉/嵌入模型接口 |
| 8 | 清旧 chunks 立即 commit | 避免长事务持锁堵 REINDEX CONCURRENTLY |
| 9 | 报告进度用轮询而非 SSE | 后台任务跨请求存活，SSE 绑定请求生命周期不合适 |
| 10 | SSE 用 fetch 而非 axios | axios 不支持流式 POST |
| 11 | 字号缩放 CSS 变量而非 zoom | zoom 破坏 Element Plus 弹层定位与 100vh 布局 |
| 12 | 文件令牌独立签发（5min + 绑 file_id） | 登录 JWT 出现在 URL 会被日志/Referer 泄漏 |
| 13 | 前端无环境变量、同源 /api | 容器化免配置；dev 靠 vite 代理 |
| 14 | sse-starlette 钉 <3 | 新版要求 starlette≥0.49，与 fastapi 0.115 不兼容 |
| 15 | MCP 协议降级补丁 2025-11-25→2025-06-18 | 官方 Node MCP 服务器不认识新版协议导致握手卡死 |
| 16 | InstrumentedLLM 每次调用新包一层 | 缓存底层实例但 caller/user 不焊死，保证用量统计正确 |
