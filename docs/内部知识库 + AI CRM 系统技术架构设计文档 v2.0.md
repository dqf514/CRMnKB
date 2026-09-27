# 内部知识库 + AI CRM 系统技术架构设计文档 v2.0

> **修订说明（v1.0 → v2.0）**
>
> v1.0 是立项初期的架构蓝图（Spring Cloud 微服务 + Nacos + MinIO + Redis + RabbitMQ 选型）。系统已按"先有骨架，再添血肉"的原则完成**第一至八阶段**开发并全部落地，最终形态为 **FastAPI 单体 + Vue 3 前端 + PostgreSQL/pgvector** 的模块化单体架构。v2.0 保留原文档结构，各章对照真实实现更新；与 v1.0 不同的历史决策以"**已调整**"标注并说明原因。
>
> 演进摘要：
>
> - **一期（MVP）**：客户管理 + 知识库问答核心闭环（FastAPI + JWT + pgvector 向量检索）
> - **二期**：混合检索（向量 + pg_trgm 关键词，RRF 融合）、Rerank、提醒规则引擎、报告生成、AI 回答反馈闭环
> - **三期**：多轮对话（会话/消息、问题改写指代消解）、工作流自动化、前端明暗双主题与品牌色
> - **四期**：文件资产与知识库解耦——多知识库、文档库（文件中心）、客户专属知识库、AI 客户画像；品牌更名"榜样CRM"
> - **五期**：系统管理端（用户/用户组）、LLM 模型注册与用量统计、异常日志
> - **六期**：Office/PDF/图片/音视频在线预览、RAG 命中测试
> - **七期**：行业标签与客户档案增强
> - **八期**：自查与加固——软删除与回收站、Excel 导入导出、客户查重、操作审计日志、上传上限/安全头/CORS 白名单等
> - **九期**：客户主页 AI 对话与来源直达——客户详情内置"AI 对话"页签（锁定专属知识库、快捷提问）；问答/对话的引用来源回填文件信息（file_id 等），可点击直达 FilePreview 在线预览
> - **十期**：多模态摄入管线——图片（视觉模型 OCR+描述）、扫描版 PDF（pymupdf 逐页渲染兜底）、音频（ASR 转写）、视频（ffmpeg 抽音轨再 ASR）均可解析入库；LLM 层新增 transcribe，模型注册扩为 chat/embed/vision/asr 四类
> - **十一期**：AI 工作台自定义报告与对话式编辑——type=custom 报告（需求描述 + 知识库范围 + 附件入"AI工作台资料库"）生成完整独立 HTML（纯 CSS 图表、禁 JS）；`/reports/{id}/revise` 对话式修改，历史版本回看（cap 20）；HTML 报告可导出 A4 PDF（Playwright 无头 Chromium 服务端打印，分页 CSS 优化）
> - **十二期**：Skill 系统与工具调用——可插拔技能框架（内置联网搜索/网页抓取 + 自定义 API skill，SSRF 拦截），对话侧 OpenAI function calling agent 循环（最多 3 轮），KB 未命中也可联网作答；无启用 skill 时行为逐字节不变
>
> 当前规模：**116 个 API 路由、27 张数据表、256 个 pytest 用例全绿**。

---

## 一、项目概述

### 1.1 目标定位

构建一套自研的"内部知识库 + AI CRM"一体化系统（产品名：**榜样CRM**），核心目标：

- **当前规模**：支撑20人团队高效使用
- **扩展目标**：架构设计具备水平扩展能力，可平滑支撑100人乃至更大规模
- **AI能力**：支持API大模型（如DeepSeek、GPT）和本地部署模型（如Qwen、Llama）灵活切换
- **核心功能**：客户管理 + 知识库检索增强生成（RAG）+ 报告自动生成 + 自动化提醒

以上目标均已实现：模型切换通过管理端"模型注册"动态配置（数据库优先，env 兜底），无需改代码重启。

### 1.2 设计原则

1. **借鉴开源，而非复制**：参考悟空AICRM、MaxKB、Dify、FastGPT、PandaWiki 等项目的业务抽象和架构思想，按自身业务需求定制开发
2. **先有骨架，再添血肉**：从MVP开始，优先实现"客户管理 + 知识库AI问答"核心闭环
3. **数据驱动反馈闭环**：内置AI回答评价机制（`ai_feedback` 表），沉淀真实反馈数据用于持续优化

---

## 二、整体技术架构

### 2.1 分层架构设计

**已调整**：v1.0 规划的微服务分层架构，实际落地为**模块化单体（Modular Monolith）**。20 人团队规模下微服务拆分收益不抵运维成本（呼应第十章"避免过度设计"），后端按业务域划分模块（`app/api` 路由层 / `app/services` 业务层 / `app/models` 模型层），未来确有规模需要时可按模块边界拆分服务。

```
┌─────────────────────────────────────────────────────────────┐
│                    应用层（前端）                            │
│   Vue 3 + Element Plus（明暗双主题/品牌色）+ 对话式AI工作台  │
└─────────────────────────────────────────────────────────────┘
                              │ /api（JWT Bearer 鉴权）
┌─────────────────────────────────────────────────────────────┐
│                 FastAPI 后端（单体，116 个路由）             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ CRM 域    │ │ 知识库域  │ │ RAG/对话 │ │ 自动化域      │  │
│  │客户/商机  │ │多知识库   │ │混合检索   │ │提醒/工作流    │  │
│  │跟进/画像  │ │文档库/切片│ │报告生成   │ │任务/通知      │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ 系统管理域：用户/用户组/模型注册/用量/异常/审计/回收站 │  │
│  └──────────────────────────────────────────────────────┘  │
│  横切：JWT 鉴权 · 租户隔离 · CORS 白名单 · 安全响应头 ·      │
│       上传大小校验(413) · LLM 不可用时优雅降级               │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                      数据层                                  │
│  ┌──────────────────┐ ┌──────────────┐ ┌────────────────┐ │
│  │ PostgreSQL 16    │ │  pgvector     │ │ 本地磁盘        │ │
│  │ （27 张业务表）   │ │（向量并入切片表│ │ data/uploads   │ │
│  │                  │ │  HNSW/GIN索引）│ │（分块流式写盘） │ │
│  └──────────────────┘ └──────────────┘ └────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 技术选型

| 层次 | v1.0 规划 | **实际实现** | 说明 |
| ---------- | ------------------------------ | --------------------------------- | --------------------------------- |
| **后端框架** | Spring Boot 3.x + Spring Cloud | **FastAPI（Python）+ SQLAlchemy 2.x 异步** | 已调整：AI/RAG 生态在 Python 侧更成熟，迭代快 |
| **服务注册发现** | Nacos | **无**（单体不需要） | 已调整 |
| **API网关** | Spring Cloud Gateway | **FastAPI 自身**（中间件 + 依赖注入） | 已调整 |
| **主数据库** | PostgreSQL 15+ | **PostgreSQL 16**（docker-compose 编排） | 一致 |
| **缓存** | Redis 7.x | **无**（当前规模无热点缓存需求） | 已调整：后续需要时再引入 |
| **向量数据库** | pgvector（起步）→ Milvus（扩展） | **pgvector**（向量并入 `document_chunks.embedding`，HNSW 索引） | 一致；百万级前无需迁移 |
| **文件存储** | MinIO | **本地磁盘 `data/uploads`**（UUID 文件名，1MB 分块流式写盘） | 已调整：单机部署够用；六期新增在线预览按路径流式回源 |
| **消息队列** | RabbitMQ / Kafka | **后台异步任务**（FastAPI BackgroundTasks + asyncio 定时调度） | 已调整：文档解析、画像/报告生成均为进程内异步 |
| **容器编排** | Docker + Kubernetes | **docker-compose**（db + backend + frontend 三服务） | 已调整：K8s 对当前规模过重 |
| **前端框架** | React / Vue 3 | **Vue 3 + Vite + Element Plus + Pinia** | 一致 |
| **AI编排框架** | LangChain / LangGraph | **自研轻量 pipeline**（`app/services/rag.py` 等，无框架依赖） | 已调整：流程完全可控，避免框架黑盒 |
| **本地模型部署** | Ollama / vLLM | **Ollama**（chat/embedding 均支持，与 OpenAI 兼容 API 可切换） | 一致 |
| **全文检索** | ES/BM25 | **pg_trgm**（GIN 索引，与向量检索 RRF 融合） | 已调整：复用 PG 即可，不引入 ES |

---

## 三、数据层设计

### 3.1 PostgreSQL 核心表结构

**实际共 27 张表**，按域分组：

| 域 | 表 |
|---|---|
| 租户与权限 | `tenants`、`users`、`user_groups` |
| CRM | `customers`、`opportunities`、`follow_up_records`、`industries` |
| 知识库 | `knowledge_bases`、`knowledge_documents`、`document_chunks`、`library_folders`、`library_files` |
| 对话与 RAG | `chat_sessions`、`chat_messages`、`rag_query_logs`、`ai_feedback` |
| 自动化 | `tasks`、`reminder_rules`、`notifications`、`workflows`、`workflow_runs` |
| 报告与系统 | `reports`、`llm_models`、`llm_call_logs`、`error_logs`、`audit_logs`、`skills` |

#### 3.1.1 租户与权限（多租户设计）

**已调整**：v1.0 规划的"共享数据库、独立 Schema"，实际落地为**共享库 + `tenant_id` 列隔离**（行级租户），`tenants` 表仅保留 id/name/status。所有业务表带 `tenant_id` 外键，查询经依赖注入统一注入租户条件，跨租户数据不可见。独立 Schema 在表数量增长后迁移成本过高，行级隔离对单部署形态足够。

权限模型：`users`（角色 admin/member）+ `user_groups`（用户组），管理端路由仅 admin 可访问。

#### 3.1.2 客户核心表

与 v1.0 设计基本一致，实际演进点：

- `customers`：`status` 状态机（potential/intention/negotiating/closed/lost）、`source`、`owner_id`、JSONB `attributes` 扩展字段；七期新增行业标签（`industries` 表，客户可打多个行业标签）；八期新增**查重**（名称/联系方式相似度）与 **Excel 导入导出**
- `follow_up_records`：跟进记录，`ai_summary` 由 LLM 自动生成
- `opportunities`：商机阶段推进触发工作流/提醒
- 客户画像：画像内容不落单独表，异步生成后存于客户记录（状态机 idle/generating/ready/failed，聚合内容上限 `PROFILE_MAX_CHARS=8000`）

### 3.2 知识库数据模型

**已调整**：四期按需求规格完成"文件资产与知识库解耦"改造，实际为三张核心表：

```sql
-- 文档库（文件中心）：文件夹树 + 文件资产
library_folders (id, tenant_id, parent_id, name, ...)              -- 任意层级树
library_files   (id, tenant_id, folder_id, customer_id NULL,       -- 物理资产，仅存一份
                 name, stored_name, size, mime, parseable, ...)

-- 知识库：逻辑集合
knowledge_bases (id, tenant_id, name, description, type)           -- type = general / customer
knowledge_documents (id, tenant_id, kb_id, library_file_id,        -- 文件在某知识库的引用
                     status, chunk_count)                            -- 移除即级联删切片
document_chunks (id, tenant_id, document_id, chunk_index,
                 content, embedding vector(1024))                  -- 向量并入切片表
```

- 一个文件可关联多个知识库；从知识库移除文档不删除文件本身
- 八期新增**软删除 + 回收站**：`customers` / `library_files` / `knowledge_bases` 三表加 `deleted_at`，删除进管理端回收站，可恢复/彻底删除（客户连带专属知识库）；**知识库文档移除与文件夹删除目前仍是物理删除**（回收站未覆盖，列入后续规划）
- 解析仅对支持格式（pdf/docx/txt/md）发生，其余标记"仅存储"（xlsx 仅用于客户 Excel 导入导出，不参与解析）

### 3.3 任务与提醒表

与 v1.0 设计一致：`tasks`（含 `ai_generated`/`source` 标记）、`reminder_rules`（`trigger_type` + JSONB 配置）、`notifications`（站内信铃铛）。三期新增 `workflows` / `workflow_runs`（可视化工作流，五类触发器）。

### 3.4 向量数据库设计

**已调整**：v1.0 规划的独立 `knowledge_vectors` 表 + IVFFlat 索引，实际实现为**向量列并入 `document_chunks`**，索引升级为 **HNSW**：

```sql
CREATE EXTENSION vector;
CREATE EXTENSION pg_trgm;  -- 关键词检索用

-- document_chunks.embedding vector(1024)  -- bge-m3 / EMBEDDING_DIM 可配
-- HNSW 向量索引 + content 列 pg_trgm GIN 索引
```

- 向量维度由 `EMBEDDING_DIM` 配置（bge-m3 = 1024；OpenAI text-embedding-3-small = 1536，改维度需重建表）
- 关键词检索用 `pg_trgm` 相似度 + GIN 索引，与向量检索结果做 RRF 融合
- 切片元数据（文档/知识库/租户过滤）直接走切片表外键，无需 JSONB 过滤

> **扩展路径**：当向量数据量突破百万级时，可迁移至 Milvus 等专业向量数据库（当前未触发）。

---

## 四、后端模块详细设计

### 4.1 模块划分与职责

**已调整**：微服务拆分改为单体内部模块，职责对应关系：

| v1.0 服务 | 实际模块 | 核心职责 |
| --------------------- | ----------------- | -------------------------------------------- |
| crm-service | `app/api/customers.py` 等 | 客户、商机、跟进记录、行业标签、画像、导入导出/查重 |
| knowledge-service | `app/api/library.py`、`knowledge.py` | 文档库、多知识库、文档解析、切片、向量化 |
| rag-service | `app/services/rag.py` | 查询改写、混合检索、RRF、Rerank、Small2Big、上下文构建 |
| ai-gateway | `app/services/llm.py` | 大模型统一调用（`chat` / `chat_stream` / `embed`） |
| task-service | `app/api/tasks.py`、`reminders.py`、`workflows.py` | 任务、提醒规则引擎、工作流、站内通知 |
| report-service | `app/services/report.py` | 报告生成（RAG + 模板），异步生成 |
| gateway-service | FastAPI 中间件 + 依赖 | JWT 鉴权、租户隔离、CORS、安全头、审计日志 |

### 4.2 模块间通信

- **同步**：单体内部直接函数调用，无 RPC 开销
- **异步**：文档解析、画像/报告生成走 BackgroundTasks；提醒规则引擎与工作流调度为 asyncio 定时循环（`REMINDER_INTERVAL_MINUTES=10` 分钟间隔）

### 4.3 AI Gateway（LLM 统一调用层）

**已调整**：v1.0 的 Java `LLMService` 接口，实际为 Python `app/services/llm.py`：

- 统一入口：`chat(messages)`、`chat_stream(messages)`（SSE 流式）、`embed(texts)`；十期新增 `transcribe(file_path)`——基类默认抛不支持，ApiLLM 走 OpenAI 兼容 `/audio/transcriptions`（whisper 兼容，multipart，timeout 300s），OllamaLLM 抛不支持；后续再增 `rerank(query, documents)`——ApiLLM 走 Jina/SiliconFlow/TEI 兼容 `POST {base_url}/rerank`（按 results[].index 归位取 relevance_score），Ollama 不支持（提示用 OpenAI 兼容服务）
- **模型选择优先级**：数据库 `llm_models` 注册表（管理端可增删改、设默认、填各 provider 的 base_url/key/模型名，支持 `fetch-remote` 拉取远端模型列表与 `test-config` 保存前试连通）→ env 配置兜底（`LLM_CHAT_PROVIDER=api|ollama` 等）；十期起 model_type 扩为 chat/embed/vision/asr 四类（后续再增 rerank 重排序，共五类），工厂新增 `resolve_vision_llm()`（vision 默认优先，未配置回退 chat 默认模型）、`resolve_asr_llm()`（仅 asr，未配置抛错）与 `resolve_rerank_llm()`（仅 rerank，**未配置返回 None 不抛**，检索管线据此走三级降级：专用 rerank 模型 → chat LLM 打分 → RRF 序）
- 每次调用写入 `llm_call_logs`（模型、token 用量、耗时；transcribe 记 model_type=asr），管理端"用量统计"按模型/日期聚合（echarts 图表）
- **工具调用（十二期）**：基类新增 `chat_with_tools()`（默认抛"不支持工具调用"）；ApiLLM 透传 OpenAI tools payload 并容错解析 tool_calls；OllamaLLM 抛不支持；InstrumentedLLM 统一埋点。配套 Skill 框架（`app/services/skills/`：base 协议 / builtin 联网搜索+网页抓取 / api_skill 自定义 API / registry 注册执行，`check_url_safe` SSRF 拦截），管理端 `/admin/skills` 维护启停与配置（skills 表，DB 行覆盖内置默认，敏感字段脱敏）
- **对话 agent 循环**：有启用 skill 时走工具循环（最多 3 轮：tool_calls → 执行 → 结果按 OpenAI 协议回注 → 再调；单工具失败写原因继续；轮尽后不带工具要最终回答）；KB 未命中也可联网作答；模型不支持工具调用自动回退原 RAG 路径；**无启用 skill 时行为逐字节不变**；SSE 新增 `{type:"tool"}` 帧（sources 后、token 前），前端气泡上方展示工具标签
- LLM 不可用时画像、报告、摘要等 AI 功能**优雅降级**（状态 failed，不影响其他功能）

---

## 五、RAG系统实现

### 5.1 RAG 整体流程（实际 pipeline）

```
用户问题（可带 kb_ids 限定知识库范围）
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  查询处理                                            │
│  1. 多轮对话问题改写（指代消解，RAG_QUERY_REWRITE，   │
│     失败自动回退原问题）                              │
│  2. kb_ids 过滤（不指定则检索该租户全部知识库）       │
└─────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  混合检索（RAG_HYBRID=true 时）                       │
│  ┌────────────┐    ┌────────────────┐               │
│  │ 向量检索    │ +  │ 关键词检索      │               │
│  │(pgvector    │    │(pg_trgm + GIN) │               │
│  │ 余弦/HNSW)  │    │                │               │
│  └────────────┘    └────────────────┘               │
│           RRF 融合排序（Reciprocal Rank Fusion）     │
└─────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  精排与上下文构建                                     │
│  1. Rerank 三级降级：专用 rerank 模型（五类模型之一，│
│     未配置返回 None）→ chat LLM 打 0~1 分 → RRF 序； │
│     任一级失败仅告警不影响主流程（RAG_RERANK 可关）   │
│  2. 相关度阈值（RAG_SCORE_THRESHOLD=0.7）：低于阈值   │
│     不调用生成，返回兜底话术                           │
│  3. Small2Big：命中切片拼接同文档相邻 ±N 切片         │
│     （RAG_NEIGHBOR_WINDOW=1）                        │
│  4. 上下文裁剪：按融合排序累计至 4000 字符上限         │
│     （RAG_MAX_CONTEXT_CHARS）                        │
└─────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  生成                                                │
│  LLM 生成答案 + 引用来源 + 自动追问建议；              │
│  引用来源经 attach_file_info 批量回填文件信息          │
│  （file_id 等，九期，前端可点击直达 FilePreview）；    │
│  写入 rag_query_logs（检索/生成全链路日志）；          │
│  用户可对回答评价"有用/无用"（ai_feedback）            │
└─────────────────────────────────────────────────────┘
```

说明：v1.0 提到的 HyDE、元数据过滤（部门/日期）未落地——kb_ids 范围过滤 + 混合检索已覆盖当前精度需求，列入后续规划。

**来源直达（九期）**：sources 构建后由 `attach_file_info()` 按 doc_id 批量查 `knowledge_documents` + `library_files`（排除软删，最多 2 次查询无 N+1），回填 `file_id/file_name/file_type/file_size`；file_id 为空或文件已软删时四字段为 None、保留 doc_title，前端据此降级为不可点击。`/rag/query` 与 `/chat/ask(/stream)`（SSE 与非流式共用 `_prepare`）均接入，`chat_messages.sources` JSONB 自然带新字段。前端客户详情新增"AI 对话"页签：懒加载锁定该客户专属知识库 kb_ids，内置四个快捷提问，与 AI 工作台同一套流式对话体验。

### 5.2 文档处理管道（Ingestion Pipeline）

**已调整**：未使用 LangChain，自研轻量管道。**十期起扩展为多模态摄入**，按扩展名分派产出文本后统一走"清洗 → 切片 → 向量化"：

- 文本：pdf（pypdf）、docx（python-docx）、txt/md 直读
- 图片（jpg/jpeg/png/webp/bmp/gif）：视觉模型识别——转录图中文字 + 描述内容（`PROMPT_IMAGE_OCR`）
- 扫描版 PDF 兜底：pypdf 提取总字符 < 页数×20 时，pymupdf 逐页渲染 PNG（限 50 页）→ 视觉模型逐页识别（`PROMPT_PDF_PAGE_OCR`）
- 音频（mp3/wav/m4a/ogg/flac/amr）：ASR 模型 `transcribe()`（whisper 兼容 /audio/transcriptions）
- 视频（mp4/mov/avi/mkv/webm）：检测 ffmpeg（`shutil.which`）抽音轨到临时 mp3 再 ASR（libmp3lame 失败兜底 `-acodec copy`，临时文件 finally 清理）；无 ffmpeg 明确 failed 文案
- 其余格式"仅存储"（含 xlsx，仅用于客户 Excel 导入导出）
- 清洗与切片：按**字符**切分（注意单位）——`CHUNK_SIZE=512` 字符、`CHUNK_OVERLAP=64` 字符，优先段落/句子边界
- 向量化：批量调用 `embed()`，写回 `document_chunks.embedding`
- 产出标识：doc.content 带 `[图片识别]/[扫描件识别]/[音频转写]/[视频转写]` 前缀；doc_metadata 记 `processing_method`（text/vision_ocr/image_describe/asr/video_asr）与 `processing_model`，接口透出供前端展示处理方式标签
- 解析状态机 processing/ready/failed/unsupported，前端每 3 秒轮询；任何失败只标 failed + 落 error_log，不阻塞上传

模型解析：`resolve_vision_llm()`（vision 默认模型优先，未配置**回退 chat 默认模型**，若其不支持图像则以 failed 降级写明原因）；`resolve_asr_llm()`（仅 asr 类型，未配置抛"未配置语音识别模型"）。生产建议注册 vision（qwen-vl / gpt-4o 类）与 asr（whisper 兼容接口）模型；视频处理需服务器安装 ffmpeg。

### 5.3 分块策略

| 参数 | 实际值 | 说明 |
| ------------- | ------------- | ------------------- |
| chunk_size | 512 **字符**（env 可配） | v1.0 写"tokens"，实现按字符，避免引入 tokenizer 依赖 |
| chunk_overlap | 64 **字符** | 防止上下文断裂 |
| Small2Big | 已实现 | 检索命中切片，生成时拼接同文档相邻 ±1 切片 |
| 元数据 | 文档/知识库/租户外键 | 检索过滤直接走 SQL 条件 |

### 5.4 混合检索实现

见 5.1：向量（pgvector 余弦距离）+ 关键词（pg_trgm 相似度）各取候选，RRF 融合，再经 Rerank 精排。`RAG_HYBRID=false` 可退化为纯向量检索。

### 5.5 兜底机制

已实现：

- 融合得分低于 `RAG_SCORE_THRESHOLD`（默认 0.7）时不调用大模型，返回"未在知识库中找到相关信息"类预设响应，避免幻觉
- 六期新增**命中测试**工具：管理端可输入问题直接查看召回切片与得分，用于调参
- `rag_query_logs` 记录每次问答的召回与得分，支撑效果复盘

---

## 六、自动化提醒模块

### 6.1 提醒类型

已实现：

| 提醒类型 | 触发条件 | 状态 |
| ---------- | ------------- | ------------------------------ |
| 跟进逾期提醒 | 客户距上次跟进超过 N 天 | ✅ 已实现 |
| 商机停滞提醒 | 商机在某一阶段停留超过 N 天 | ✅ 已实现 |
| 任务到期提醒 | 任务截止时间临近 | ✅ 已实现（站内通知） |
| 行为触发提醒 | 客户打开邮件/浏览官网 | 未实现（需埋点对接，后续规划） |
| AI 智能提醒 | 模型定期离线分析风险信号 | 未实现（后续规划） |

三期新增的**工作流**补充了五类触发器：客户创建、客户状态变更、商机阶段变更、跟进记录新增、定时计划；动作含创建任务、发送通知、发送邮件（SMTP，未配置时优雅降级）。`workflow_runs` 记录每次执行。

### 6.2 实现架构

**已调整**：Quartz/Drools 改为进程内实现：

```
asyncio 定时调度（REMINDER_INTERVAL_MINUTES=10 分钟）
    │
    ▼
规则引擎（自研：SQL 条件 + JSONB 参数）
    │
    ▼
规则匹配 ──▶ 生成任务/通知（tasks / notifications 表）
    │
    ▼
前端通知铃铛轮询展示；工作流另支持 SMTP 邮件
```

---

## 七、报告生成模块

### 7.1 报告类型与实现方式

| 报告类型 | 数据来源 | 状态 |
| ----------- | ---------- | ----------------- |
| 客户分析报告 | CRM 客户/商机/跟进 + 客户文档 | ✅ 已实现 |
| 销售周报/月报 | 跟进记录、成交数据 | ✅ 已实现（模板 + LLM） |
| 知识库问答报告 | 知识库 RAG 检索 | ✅ 已实现 |
| **自定义报告（custom）** | 用户需求描述 + 知识库/附件混合检索 | ✅ 已实现（十一期，HTML 格式 + 对话式修改） |
| 战略分析报告 | 多源数据 + 多 Agent 协同 | 未实现（后续规划） |

### 7.2 报告生成流程

与 v1.0 设计一致：参数 → 数据聚合（CRM 数据 + RAG 检索片段）→ Prompt 构建 → LLM 异步生成（BackgroundTasks）→ Markdown 存储 → 前端渲染（markdown-it + highlight.js）/导出。生成过程写入状态，前端轮询；LLM 未配置时 failed 优雅降级。

**自定义报告（十一期）**：

- 入口：`GET /reports/workbench-kb` 幂等创建租户级"AI工作台资料库"（type=workbench）；`POST /reports/generate` type=custom——prompt（1~2000 字）必填，kb_ids/file_ids 可选（批量校验租户归属与软删），title 取 prompt 前 30 字，params 存 {prompt, kb_ids, file_ids, format:"html"}
- 生成链路：`aggregate_custom`（向量 + pg_trgm RRF 混合检索 + **rerank 精排**（RAG_RERANK 开启时重排候选，三级降级失败回退 RRF 序；报告场景不做阈值过滤以保持素材广度），kb_ids 空则全租户，top_k 12，上下文 ≤4000 字符，sources 经 `attach_file_info` 回填 file_*）→ `build_custom_report_prompt`（完整独立 HTML、内联 CSS、纯 CSS 图表、禁 JS/代码围栏，`_strip_code_fence` 容错剥围栏）→ 后台任务，失败标 failed + error（embed 不可用降级）
- 对话式修改：`POST /reports/{id}/revise` 仅 custom 且 ready 可改（否则 400/409）；status revising → 后台 `revise_report`（当前 HTML 全文 + 修改指令 → 新 HTML）；旧版 append `params["revisions"]`（{instruction, content, created_at}，cap 20 丢最旧）；失败回 ready + error
- 前端：HTML 报告用 `<iframe sandbox="" :srcdoc>` 预览（禁脚本防注入），下载按 format 切 HTML/.md；revisions 历史可"查看此版本/回到最新"
- A4 PDF 导出（十一期增量）：`GET /reports/{id}/pdf` 仅 format=html 且 ready 可导（400/409/404 守卫，记 export/report 审计，RFC5987 中文文件名）；`services/pdf.py` 用 Playwright + 无头 Chromium 服务端打印（lazy import 缺失→503 安装提示，`asyncio.to_thread` 包裹，注入分页 CSS——@page A4、标题/表格/图片/代码块防跨页断裂）；Chromium 二进制装项目内 `data/ms-playwright/`（PLAYWRIGHT_BROWSERS_PATH 自包含）
- 审计：custom 生成 create/report、revise update/report（既有三种预置报告未补埋点，列入后续规划）

---

## 八、高可用与扩展性设计

### 8.1 水平扩展能力

当前为 docker-compose 单机部署（db + backend + frontend）。扩展路径：

- **后端无状态**：FastAPI 可多实例 + 反向代理负载均衡（定时任务需加分布式锁后再多开，当前单实例）
- **PostgreSQL**：主从复制 + 读写分离；pgvector 数据量大后迁移 Milvus
- **文件存储**：本地磁盘可平滑替换为 MinIO/S3（存储层已按路径抽象）

### 8.2 缓存策略

**已调整**：当前未引入 Redis。高频读取（模型注册表、系统配置）为进程内缓存 + DB 回源；后续出现热点再按 v1.0 规划补 Redis。

### 8.3 监控与告警

**已调整**（轻量替代 Prometheus + ELK）：

- `llm_call_logs`：LLM 调用量、token、耗时，管理端图表化
- `error_logs`：后端未捕获异常自动落库，管理端"异常日志"页查看
- `audit_logs`（八期新增）：关键操作审计（action / resource_type / resource_id / detail JSONB / ip）
- `rag_query_logs`：问答全链路日志

---

## 九、开发优先级与里程碑

### 第一阶段：MVP ✅ 已完成

- [x] 后端基础框架（FastAPI + JWT + 租户隔离）
- [x] CRM 核心 CRUD（客户、跟进记录）
- [x] 知识库文档上传、解析、切片（PDF/TXT/Markdown）
- [x] pgvector 向量检索
- [x] LLM 统一调用层（DeepSeek API / Ollama 可切换）
- [x] 对话式 AI 工作台（前端 + 后端 RAG 接口）

### 第二阶段：增强 ✅ 已完成

- [x] 混合检索（向量 + pg_trgm + RRF 融合）与 Rerank
- [x] Small2Big 上下文扩展
- [x] 自动化提醒模块（跟进逾期 + 商机停滞）
- [x] AI 跟进摘要、报告生成
- [x] AI 回答"有用/无用"反馈闭环

### 第三阶段：完善 ✅ 已完成

- [x] 多轮对话（会话管理 + 指代消解改写）
- [x] 工作流自动化（五类触发器）
- [x] 前端主题系统（明暗模式 + 品牌色）

### 第四阶段：知识库解耦 ✅ 已完成

- [x] 多知识库 + 文档库（文件中心），文件与知识库解耦
- [x] 客户专属知识库 + 客户文档归档
- [x] AI 客户画像；品牌更名"榜样CRM"

### 第五阶段：系统管理 ✅ 已完成

- [x] 管理端：用户/用户组管理
- [x] LLM 模型注册 + 用量统计、异常日志

### 第六阶段：在线预览 ✅ 已完成

- [x] Office（mammoth/openpyxl 转换）/PDF/图片/音视频在线预览
- [x] RAG 命中测试工具

### 第七阶段：行业档案 ✅ 已完成

- [x] 行业标签体系与客户档案增强

### 第八阶段：自查与加固 ✅ 已完成

- [x] 软删除 + 回收站（客户/库文件/知识库，恢复/彻底删除，客户连带专属知识库）
- [x] 客户 Excel 导入导出、查重
- [x] 操作审计日志；上传大小上限（413）、CORS 白名单、安全响应头、弱密钥拒绝启动

### 第九阶段：客户主页 AI 对话与来源直达 ✅ 已完成

- [x] 客户详情"AI 对话"页签：懒加载锁定专属知识库、快捷提问 chips、与 AI 工作台同款流式体验
- [x] 引用来源回填文件信息（attach_file_info，批量查询无 N+1），前端可点击直达 FilePreview 在线预览

### 第十阶段：多模态摄入管线 ✅ 已完成

- [x] LLM 层新增 transcribe（whisper 兼容 /audio/transcriptions），模型注册扩为 chat/embed/vision/asr 四类
- [x] 摄入按扩展名分派：图片视觉识别、扫描版 PDF pymupdf 兜底、音频 ASR、视频 ffmpeg 抽音轨再 ASR
- [x] 产出带前缀标识与 processing_method/processing_model 元数据；失败只标 failed 不阻塞上传

### 第十一阶段：AI 工作台自定义报告与对话式编辑 ✅ 已完成

- [x] type=custom 自定义报告：需求描述 + 知识库范围 + 附件（工作台资料库幂等创建），生成完整独立 HTML（纯 CSS 图表、禁 JS）
- [x] 对话式修改 `/reports/{id}/revise`：状态守卫（仅 custom 且 ready）、历史版本回看（cap 20）、iframe sandbox 预览防注入
- [x] HTML 报告导出 A4 PDF（`/reports/{id}/pdf`，Playwright 服务端打印 + 分页 CSS，400/409/404/503 守卫）

### 第十二阶段：Skill 系统与工具调用 ✅ 已完成

- [x] Skill 框架（内置联网搜索/网页抓取 + 自定义 API skill，SSRF 拦截）与管理端 `/admin/skills`（启停/配置脱敏/测试）
- [x] 对话 agent 工具循环（最多 3 轮，KB 未命中可联网作答；无启用 skill 零回归；不支持 function calling 自动回退）

---

## 十、关键风险与避坑指南

1. **RAG效果决定成败**：不要轻视知识库质量。初期投入时间整理干净、结构化的知识文档，比调优模型参数更重要。（八期实践印证：命中测试工具 + `rag_query_logs` 是调参必备）

2. **数据安全是红线**：涉及客户隐私的数据务必脱敏后处理，私有化部署是必须选项。（八期已补：审计日志、CORS 白名单、上传上限、安全头、非 dev 环境弱密钥拒绝启动）

3. **建立评测体系**：从第一天起准备标准化测试集（Golden Q&A），每次调整策略后用数据说话，避免凭感觉优化。（后端 256 个 pytest 用例保持全绿作为回归底线）

4. **避免过度设计**：目前20人团队规模，不要一上来就搞复杂的分布式事务和最终一致性方案。先确保功能可用，再优化性能和扩展性。（v1.0 的微服务/K8s/Redis/MQ 均未落地，正是这条原则的执行结果）

---

## 附录：参考开源项目

| 项目 | 参考价值 |
| ------- | ----------------- |
| 悟空AICRM | CRM业务模型、AI工作台设计思路 |
| FastGPT | 知识库RAG实现、文档处理管道 |
| Dify | AI工作流编排、文件与 dataset 解耦引用 |
| TradeOS | CRM与AI融合的设计哲学 |
| MaxKB | 知识库—文档—分段三级模型、解析状态机（四期参考） |
| PandaWiki | 多知识库独立门户形态（四期参考；网页/第三方来源导入列为后续扩展） |
