# AGENTS.md

> 面向 AI 编码代理的项目说明。读者对本项目零背景，请通读本文件后再动手改代码。
> 项目内注释与文档一律使用**中文**，新代码注释也请保持中文。

## 项目概览

**榜样知识库（内部知识库 + AI CRM）**：一套企业级单体仓库（monorepo），集客户管理（CRM）、企业知识库（KB）、AI 工作台（研究/报告）于一体。功能覆盖：多格式文档解析入库、语义检索问答（RAG）、Notebook 研究工作台、智能报告（HTML/PPTX/PDF）、内容权限与分享、多模态摄入（图片/扫描 PDF/音频/视频/邮件）、自动化提醒与工作流、系统监控运维。

主要目录：

```
├── backend/             # FastAPI 模块化单体后端（Python 3.13）
├── frontend/            # Vue 3 + Vite 前端
├── dsh/                 # dsh agent 基座支撑物：runtime/home/session-persistence-pg/patches/冒烟脚本（勿删，见下）
├── mcp-local/           # 本地 MCP 服务 npm 依赖包（filesystem/thinking/memory，运行依赖勿删）
├── docs/                # 历史设计文档（v2.0 架构、需求规格说明书，仅供参考）
├── docker-compose.yml       # 开发：仅 PostgreSQL 16 + pgvector
├── docker-compose.prod.yml  # 生产：postgres + backend + nginx 三容器编排
├── 部署指南.md          # 生产部署文档（Ubuntu + Docker）
├── README.md            # 功能总览与快速启动
└── TODO.md              # 企业化升级清单（含"已落地"清单）
```

## 技术栈

- **后端**：FastAPI 0.115 + SQLAlchemy 2.0（async，asyncpg）+ PostgreSQL 16（pgvector + pg_trgm 扩展）+ pydantic 2 / pydantic-settings + PyJWT + pwdlib[bcrypt] + httpx。文档解析：pypdf / pymupdf / python-docx / openpyxl / xlrd / python-pptx / extract_msg；PDF 导出用 Playwright（Chromium，浏览器二进制在 `backend/data/ms-playwright/`）；监控用 psutil；MCP 客户端用 `mcp` 包（注意：`sse-starlette` 被钉在 `<3`，新版要求 starlette>=0.49，与 fastapi 0.115 不兼容，见 `backend/requirements.txt` 注释）。
- **前端**：Vue 3 + Vite 6 + Element Plus + Pinia + vue-router 4 + ECharts + markdown-it / highlight.js + mammoth + xlsx。`type: "module"`，无 TypeScript。
- **LLM**：OpenAI 兼容 API（默认 DeepSeek）或本地 Ollama。模型在管理端"模型管理"注册（DB 优先，API Key 加密存储），未注册时回退 `backend/.env`。五类模型：chat / embed / rerank / vision / asr。

## 构建与运行命令

端口约定：**后端固定 8100，前端固定 5173，PostgreSQL 5432**。

> 开发机注意：本机 5432 被其他项目占用时，用 `dsh/docker-compose.dev.yml` override
> 把 crmnkb-postgres 映射到 **5433**：
> `docker compose -f docker-compose.yml -f dsh/docker-compose.dev.yml up -d`。
> dsh PG 持久化插件的开发库默认连接串就是 localhost:5433
> （`backend/data/dsh/patches/base.yml` 的 `!!js` 兜底值）。

```bash
# 1. 数据库（开发）
docker compose up -d

# 2. 后端（Windows Git Bash）
cd backend
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements-dev.txt   # 含运行时依赖（-r requirements.txt）+ pytest 等测试依赖
cp .env.example .env        # 按需修改
uvicorn app.main:app --reload --port 8100
# Swagger: http://localhost:8100/docs

# 3. 前端
cd frontend
npm install
npm run dev                 # http://localhost:5173，默认账号 admin / admin123
npm run build               # 产物到 frontend/dist/

# 4. 生产部署（详见 部署指南.md）
docker compose -f docker-compose.prod.yml up -d --build
```

## 测试

```bash
cd backend
pytest          # 475 个用例，pytest.ini 已配 asyncio_mode = auto

# dsh PG 会话持久化插件的契约测试（TS，vitest，24 例）
cd ../dsh/session-persistence-pg
npm test
```

- 测试位于 `backend/tests/`（约 30 个测试文件），`backend/conftest.py` 保证以 backend 为根导入 `app` 包。
- **测试不连真实数据库**：用 fake session（见 `tests/test_api_smoke.py` 的 `_FakeSession`）和 `monkeypatch` 打桩 LLM / 检索等外部依赖。新增测试应沿用此风格，不要起真实 PG。
- 前端**没有**测试、lint、格式化配置（package.json 只有 dev/build/preview），不要假设有 `npm test`。
- 验证后端改动：跑 `cd backend && pytest`；涉及 LLM 的服务层函数在测试里必须可打桩（通过模块级 import 后 monkeypatch，而非局部 import）。
- CI：`.github/workflows/ci.yml`（push 到 main / pull_request 触发），三个 job——后端 pytest（Python 3.13，装 `backend/requirements-dev.txt`）、dsh PG 持久化插件契约测试（Node 22，`npm ci && npm run build && npm test`）、前端构建（Node 22，`npm ci && npm run build`）。

## 代码组织（后端 `backend/app/`）

分层：**api（路由层）→ services（业务层）→ models（SQLAlchemy 表）/ schemas（pydantic DTO）**。

- `api/`：每个业务域一个路由文件（auth / brand / users / customers / followups / opportunities / industries / kbs / library / rag / chat / notebooks / reports / feedback / permissions / workflows / reminders / tasks / notifications / recycle_bin / admin_users / admin_llm / admin_skills / admin_mcp / admin_system / admin_errors / admin_audit），统一在 `main.py` 挂 `/api/v1` 前缀。`api/deps.py` 是公共依赖注入（当前用户、admin 校验等）。
- `services/`：业务逻辑核心。重点模块：
  - `ingestion.py`：文档解析→清洗→语义切片（512 字 + 64 重叠，Markdown 标题锚定）→向量化；多模态摄入也在此。
  - `rag.py` / `chat.py`：检索管线（问题改写→混合检索 blend→Rerank 三级降级→Small2Big 扩展→阈值兜底）与 SSE 流式问答。
  - `llm/`：LLM 抽象层（`base.py` + `api_llm.py` / `ollama_llm.py` 双实现 + `instrumented.py` + `factory.py` + `usage.py` 用量统计），模型配置 DB 优先、Key 加密。
  - `skills/`：Agent 工具调用框架（`builtin.py` 联网搜索/网页抓取 + `api_skill.py` + `mcp_skill.py` + `mcp_pool.py` MCP 连接池 + `mcp_discovery.py` + `registry.py`）。
  - `mcp_server.py`：知识库 MCP server（FastMCP streamable-http，挂在 `/api/mcp`），供 dsh 基座消费。只读工具：`kb_search` / `kb_read_doc` / `kb_list` / `crm_list_customers`（客户全量名单+总数）/ `crm_search_customers` / `crm_get_customer` / `crm_list_followups` / `crm_list_opportunities` / `crm_list_tasks` / `crm_stats`（经营概览统计）/ `skill_list`（已启用自定义工具概要，不含 config 防泄露密钥）/ `skill_call`（通用派发：调用 skill_list 列出的任意已启用工具）/ `web_search` / `web_fetch`；审批制写工具：`crm_create_customer` / `crm_update_customer` / `crm_delete_customer` / `crm_create_opportunity` / `crm_create_task` / `crm_add_followup` / `mail_draft_create` / `skill_create_api`（AI 起草 API 工具配置——name/method/url/headers/body/parameters，批准即建 `skills` 行并启用，运行时由 `ApiSkill` 执行含 SSRF 校验；只落审批单，admin 在 Agent 审批页批准后由 `agent_approvals` 执行器落库——建/改客户走字段白名单、软删可恢复，防越权改 tenant/owner；执行失败（failed）的审批单可由 admin 经 `POST /agent-approvals/{id}/retry` 重试执行）。dsh 专用 JWT（`aud=dsh-mcp`，security.py `create_mcp_token`）鉴权；KB 工具 ACL 在工具内按 user_id 强制，CRM 工具按 tenant_id 隔离（CRM 无内容级 ACL，租户内全员可见）。个人记忆工具（免审批，严格按 user_id 隔离）：`memory_save` / `memory_list` / `memory_search` / `memory_delete`。
  - `memory.py`：个人记忆（跨工作区长期偏好/事实）。存 `user_memories` 表（user_id + content + source[agent/manual] + chat_session_id 追溯）；每用户上限 200 条 FIFO 淘汰、单条 500 字、相同内容去重；agent 新会话（session/new）时把最近 30 条注入 prompt（`<user-memory>` 块，仅注入发给 dsh 的文本，落库仍是用户原始问题；resume 会话不重复注入）；个人中心「我的记忆」页可查看/编辑/删除/清空（`GET/POST/PUT/DELETE /auth/memories`）。
  - `acp_bridge.py`：dsh 桥接服务（**单租户单 dsh ACP 进程**，`dsh --profile acp`，PyPI 客户端 `agent-client-protocol`，asyncio 原生）；进程级 patch 在 `backend/data/dsh/patches/`（`acp-model.yml` 进程启动时按 DB 默认 chat 模型自动重写——llm-pi-ai kbcrm 路由 + acp 默认 provider/model，`base.yml` 禁用官方 jsonl 会话后端、启用 PG 持久化插件）；知识库 MCP 在每会话 `session/new`/`session/resume` 时动态挂载（headers 带该用户新签的 `aud=dsh-mcp` 令牌，不落盘）；`session/request_permission` 权限应答：只读白名单工具（kb_search/kb_read_doc/kb_list/crm_list_*/crm_search_*/crm_get_*/crm_stats/skill_list/memory_*）自动 allow，写工具/skill_call/web_search/web_fetch 及其余一律拒绝；`DSH_AGENT_ENABLED` 总开关默认关，`/chat/ask/agent/stream` 走 agent 模式（ChatSession.dsh_session_id 绑定 dsh 侧会话，init_db 幂等 ALTER 加列；resume 失败自动退回 session/new 并回写新 id）。`dsh_bridge.py` 为兼容壳，仅 re-export。
  - 其余：report（支持 Agent 模式：`params.agent=true` 时经 acp_bridge 由 dsh 多步检索/撰写 Markdown，再单独排版 HTML；输出退化或 agent 运行失败自动回退固定管线）/ pdf / pptx / notebook / workflow（daily/weekly 触发时间按服务器本地时间解释）/ reminder / permissions / monitoring / backup（备份目录名/manifest 一律 UTC，Z 后缀；定时自动备份开关 system_settings `backup_auto_enabled`（默认关，管理端 `GET/PUT /admin/system/auto-backup`），开启后由告警循环每个 UTC 日备份一次）/ maintenance / audit / email / customer_io / profile / file_context / ai_tasks 等。
  - CRM 反馈改造（2026-09）：`pipeline_brief.py`（AI 阶段简报：`POST /customers/{id}/brief/refresh`，跟进创建后自动刷新，写回 `customers.ai_brief/ai_brief_at`）；`email_draft.py`（AI 邮件草稿：`POST /customers/{id}/email-draft`，只生成不发送，写作规范存 system_settings `email_guide`，管理端「系统设置 → 邮件写作规范」维护）；客户 DDQ 状态（`customers.ddq_status`：none/pending/completed，列表可按 `ddq_status` 过滤）；跟进记录带 `next_step`；`GET /tasks` 支持 `customer_id` 过滤；沙箱重置 `POST /admin/system/reset-sandbox`（仅 ENV=dev/sandbox/test，先自动备份再 TRUNCATE 业务表，保留用户/模型/系统配置）；一键系统更新 `POST /admin/system/update`（admin 限定，`UPDATE_SCRIPT` 指向服务器脚本才启用——模板 `deploy/update.sh`：git pull → 按需 pip/前端构建 → 延迟 systemctl restart；脚本路径只走配置不接受传参，执行前落审计；前端「系统设置 → 系统更新」tab 在 enabled 时才显示）；`/brand` 公开配置下发 `env`（前端沙箱横幅）；文档资料类型 `doc_metadata["category"]`（上传可带 category，`PUT /library/files/{id}/category` 可改）；类型列表动态配置 `services/doc_categories.py`（system_settings `doc_categories`，默认五项，`GET /library/categories` 读取、管理端「系统设置 → 文档资料类型」`PUT /admin/settings/doc-categories` 整体替换增删改，上传/改标签按动态配置校验，删除类型不影响存量文件——未知值展示回退原始标识；前端 `stores/docCategories.js` 全局缓存）。
- `models/`：30+ 张表（resource_permissions / document_versions / login_attempts / brand_settings / chunk_questions / rag_query_log / workflow_run 等）。
- `core/`：`security.py`（密码哈希、JWT）、`crypto.py`（API Key 加密）。
- `config.py`：pydantic-settings，读 `backend/.env`，全部配置项有默认值与中文注释；新增配置项加到这里。注意它会在 import playwright 之前设置 `PLAYWRIGHT_BROWSERS_PATH` 指向 `backend/data/ms-playwright`。
- `main.py`：lifespan 里启动三个后台 asyncio 任务——提醒/工作流周期调度（`_reminder_loop`）、系统告警检查（`_alert_loop`）、启动恢复（重排滞留 processing 文档）；关闭时清理 MCP 连接池与 dsh ACP 进程。未捕获异常统一记 `error_logs` 表并返回 500。`/health` 含 DB ping（不可达返回 503）。

## dsh agent 基座（阶段 2：ACP 接入面）

dsh（DeepSeek Harness）作为 Agent 运行时被 FastAPI 内嵌管理：**单租户单 dsh ACP 子进程**（`dsh --profile acp`，标准 ACP v1 stdio，stdin EOF 即退出），经 PyPI 官方客户端 `agent-client-protocol`（pin `0.12.*`，asyncio 原生）驱动；会话经 `session/new`/`session/resume` 管理，PG 持久化插件支撑跨进程恢复（后端重启不丢 agent 上下文）。方案文档见 `docs/dsh基座实施方案.md`（文末有阶段 1/阶段 2 落地记录）。

- **ACP 客户端**：`agent-client-protocol`（PyPI，requirements.txt 已收）。阶段 1 的 `deepseek-harness-sdk`（vendor 副本 `backend/vendor/deepseek-harness-sdk/`）不再使用、不再安装进镜像，目录保留无害。
- **dsh 运行时**：npm 包 `@deepseek-ai/dsh@0.1.7-rc.2`（锁版本，要求 Node >=22.19 或 >=24）。开发机装在 `dsh/runtime/`，Windows 下 `DSH_BIN` 必须指 `.cmd` shim（`dsh/runtime/node_modules/.bin/dsh.cmd`）；Linux/容器指无后缀 shim。
- **PG 会话持久化插件** `@kbcrm/dsh-session-persistence-pg`：TS 源码在 `dsh/session-persistence-pg/`（`npm run build` → dist，`npm test` = vitest 契约测试 24 例），落库表 `dsh_session_headers` / `dsh_session_events`。需先 `dsh plugin --profile acp add <插件路径>` 注册进 DSH_HOME 的 **acp** profile（该命令把参数原样转发 pnpm，**pnpm 必须在 PATH**；sdk profile 的旧注册态仅阶段 1 遗留，不再使用）；`backend/data/dsh/patches/base.yml` 负责禁用官方 jsonl 后端、启用 PG 后端（`databaseUrl` 用 `!!js process.env.DSH_PG_URL ?? <默认>`）。
- **`dsh/` 目录**（勿删）：`runtime/` dsh 运行时 npm 安装、`home/` DSH_HOME（sdk/acp profile、插件注册态）、`session-persistence-pg/` PG 插件源码、`patches/` patch 素材（含 ACP 冒烟生成的 `acp-model.yml`）、`docker-compose.dev.yml` 开发库 5433 端口 override、`smoke_pg.py` PG 持久化冒烟脚本、`acp_smoke.py` ACP 端到端冒烟脚本、`venv/` 插件开发用 Python 环境。
- **对外端点**：`POST /api/v1/chat/ask/agent/stream`（SSE agent 问答）；`/api/mcp`（FastMCP streamable-http，Bearer 用户令牌 `aud=dsh-mcp`，KB 检索 + CRM 读写工具，KB 工具 ACL 在工具内按 user_id 强制、CRM 按 tenant_id 隔离，写工具走审批制）。

**DSH_\* 配置表**（`backend/app/config.py`，`backend/.env.example` 有注释）：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `DSH_AGENT_ENABLED` | `false` | 总开关；false 时 agent 端点 503、ACP 进程不启动 |
| `DSH_BIN` | 空（**必填**） | dsh 可执行文件路径；Windows 必须指 `.cmd` shim |
| `DSH_HOME` | `data/dsh/home` | dsh 运行时 home（profile/凭据/插件注册态） |
| `DSH_PATCHES_DIR` | `data/dsh/patches` | 进程级 patch yml 目录（base.yml 静态 PG patch + acp-model.yml 自动生成） |
| `DSH_WORKSPACE_ROOT` | `data/dsh/workspace` | agent 会话工作目录（所有会话共用同一 cwd：session/resume 要求 cwd 一致） |
| `DSH_PROVIDER` | `deepseek-official` | **ACP 阶段已弃用**（保留仅为兼容旧 .env）；模型路由固定为进程级 patch 的 llm-pi-ai kbcrm 路由 |
| `DSH_MCP_URL` | `http://127.0.0.1:8100/api/mcp` | 注入 dsh 的知识库 MCP server 地址 |
| `DSH_MCP_TOKEN_EXPIRE_MINUTES` | `10080`（7 天） | dsh 专用 MCP 令牌有效期；每次会话激活（session/new|resume）时新签，过期自动换发无需重建进程 |

**生产镜像集成**（`backend/Dockerfile`）：Node 24（`COPY --from=node:24-slim`）+ pnpm + `/opt/dsh-runtime`（npm 装 dsh）+ `/opt/dsh-plugins/session-persistence-pg`（vendor dist + `npm ci --omit=dev`）；ACP 客户端 `agent-client-protocol` 随 requirements.txt 安装（vendor SDK 不再安装）；`docker-entrypoint.sh` 在 `DSH_AGENT_ENABLED=true` 时幂等完成两件一次性初始化：播种 `base.yml`（母版 `backend/docker/dsh-base.yml`）、`dsh plugin --profile acp add` 注册 PG 插件（标记文件 `.pg-plugin-installed-acp`）。`docker-compose.prod.yml` 侧：`DSH_AGENT_ENABLED=true`、`DSH_PG_URL` 指向容器网络 postgres，named volume `dsh-home` 挂 `/app/data/dsh`。

## 数据库与迁移约定（重要）

- **没有 Alembic**。首次启动 `init_db()`（`backend/app/database.py`）自动 `create_all` 建表 + 创建 pgvector/pg_trgm 扩展 + HNSW/GIN 索引，并写种子数据（默认租户 + 管理员 `admin / admin123`）。
- 存量库升级走**幂等迁移**：在 `init_db()` 里追加 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...` 等语句（已有大量先例）。改模型字段时新库由 create_all 覆盖，老库必须在这里补幂等 ALTER，两边都要考虑。
- 向量维度由 `EMBEDDING_DIM` 配置（默认 1024，对应 bge-m3），换嵌入模型要注意维度一致。

## 代码风格约定

- 注释、文档、commit 信息均用**中文**；标识符用英文。
- 后端：Python，类型注解较全（`str | None` 等新语法，目标 Python 3.13）；异步优先（async def + AsyncSession）；配置一律走 `settings`，不读裸 `os.environ`。
- 前端：`frontend/src/` 下分 `api/`（axios 封装 `index.js`，SSE 在 `chatStream.js`，上传在 `libraryUpload.js`）、`views/`（页面，admin 子目录为系统管理页）、`components/`、`stores/`（Pinia）、`router/`、`layouts/`、`styles/`、`utils/`。Vue SFC + Composition API（`<script setup>`）。
- Vite 代理注意：target **必须写 `http://127.0.0.1:8100` 而非 localhost**——localhost 优先解析 IPv6 `::1`，而后端只监听 IPv4，会代理失败（`frontend/vite.config.js` 有注释说明）。

## 安全要点（改动时不得破坏）

- JWT 鉴权；非 dev 环境（`ENV != dev`）弱 `JWT_SECRET`（默认值或 <32 字符）**拒绝启动**（`settings.validate_jwt_secret()`，在 lifespan 里调用）。
- 登录限流持久化在 DB（`login_attempts` 表：账号维度不分 IP 15 分钟失败 5 次锁定，IP 全局熔断）。种子 admin 首登强制改密（`users.must_change_password`）。
- 密码策略最小 8 位，改密后旧 JWT 失效（`password_changed_at`）；LLM API Key 加密存储、脱敏显示。
- CORS 显式白名单（不开 credentials）；内容权限默认私有，三档授权（只读/编辑/所有权）+ 团队可见开关；**RAG 检索仅限用户可读范围**，改动检索代码时必须保留 ACL 过滤。
- 文件访问令牌短时效（`?t=`）；回收站软删（`deleted_at`）可恢复，彻底删除后自动 VACUUM。
- 管理员（admin）绕过 ACL；`/admin/*`、`/recycle-bin` 等路由必须保持 admin 校验。

## 部署

- 生产用 `docker-compose.prod.yml`：postgres（不对外暴露 5432）+ backend（Dockerfile 含 ffmpeg + Playwright Chromium + 中文字体 + Node 24/dsh 运行时/PG 会话持久化插件）+ frontend（nginx，暴露 80）。必须设置 `POSTGRES_PASSWORD` 环境变量。
- 详细步骤见 `部署指南.md`（Ubuntu + Docker 一键部署，含"dsh Agent 功能"一节）。
- 上传文件、备份、品牌资源、Playwright 浏览器都在 `backend/data/` 下（uploads / backups / brand / ms-playwright / mcp-workspace），生产用 named volume 持久化 uploads；dsh 运行时状态（profile/patches/workspace）用 named volume `dsh-home` 挂 `/app/data/dsh` 持久化。

## 其他注意事项

- `mcp-local/` 是本地 MCP 服务的 npm 依赖目录（`@modelcontextprotocol/server-filesystem` 等），后端 MCP 功能运行时依赖它，**不要删除**。
- `docs/` 下是历史设计文档，可能与当前实现有出入，以代码和 README 为准。
- `TODO.md` 记录了已落地能力与规划项，做完相关功能可更新其"已落地"清单。
