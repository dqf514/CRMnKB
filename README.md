# 榜样知识库（内部知识库 + AI CRM）

一套集 **客户管理（CRM）+ 企业知识库（KB）+ AI 工作台** 于一体的企业级系统：支持多格式文档解析入库、语义检索问答、研究工作台（Notebook/笔记/报告）、内容权限与分享、多模态摄入、自动化提醒与工作流、系统监控与运维。

> 技术栈：Python + FastAPI 模块化单体（后端）+ Vue 3（前端）。历史设计文档见 `docs/`（v2.0 架构、需求规格说明书）。

## 技术栈

- **后端**：FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL 16 + pgvector + pg_trgm + asyncpg + httpx + pypdf / pymupdf / python-docx / openpyxl / xlrd / python-pptx / extract_msg + Playwright（PDF 导出）+ psutil
- **前端**：Vue 3 + Vite + Element Plus + Pinia + ECharts + markdown-it / highlight.js + mammoth + xlsx
- **LLM**：管理端注册管理（DB 优先，API Key 加密存储），环境变量兜底 —— OpenAI 兼容 API（默认 DeepSeek）/ 本地 Ollama
- **多模态摄入**：图片（视觉 OCR）、扫描版 PDF（pymupdf 渲染 + 视觉）、音频（ASR）、视频（ffmpeg 抽音轨 + ASR）、邮件（eml/msg + 附件递归）

## 快速启动

### 1. 启动数据库

```bash
docker compose up -d
```

### 2. 启动后端

```bash
cd backend
python -m venv .venv
# Windows Git Bash:
source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env   # 按需修改 LLM / ENV / CORS 配置
uvicorn app.main:app --reload --port 8100
```

首次启动自动建表（含 pgvector / pg_trgm 扩展、HNSW/GIN 索引）、写入种子数据（默认租户 + 管理员 `admin / admin123`）、幂等迁移与回填（含内容权限存量转私有等）。

Swagger：http://localhost:8100/docs （后端固定 8100 端口）

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173 ，使用 `admin / admin123` 登录。

## 功能总览

### 客户管理（CRM）
- 客户 / 跟进记录 / 商机 CRUD；跟进 AI 自动摘要 + 提取待办任务
- 行业字典（预置 22 项）、客户标签、Excel 导入导出、查重
- 客户画像：AI 生成五章节 Markdown 画像
- 自动化提醒引擎（超期未跟进 / 商机停滞 / 任务临期）与工作流引擎（5 种触发器 + 3 种动作）

### 知识库（KB）
- 文档库：文件夹树、多文件/目录上传、文件可挂客户
- 多知识库：通用库 + 客户专属库 + 自动资料库；文件资产与知识库解耦
- 解析 → 清洗 → 语义切片（512 字 + 64 重叠，Markdown 标题锚定）→ 向量化（HNSW）
- 多模态摄入：图片（视觉 OCR，tif 自动转 PNG）/ 扫描 PDF / 音频 / 视频 / 邮件（eml/msg + **PST 归档流式拆解**：逐封入库、后台解析、进度可见）均可解析入库，失败只标 failed 可重试
- **Office 全家桶解析**：docx/xlsx/xls/pptx + 老二进制 **doc/ppt**（olefile FIB 精确提取，装 LibreOffice 自动升级质量）
- **文档版本历史**：重解析前自动保存快照，可查看/恢复历史版本
- 在线预览：图片（tif 转 PNG）/ PDF / 音视频 / Word / **Excel 表格** / **doc·ppt·pptx 文本预览** / Markdown / 文本
- **本地同步 App**（sync-app/）：OneDrive 式双向同步本地文件夹 ⇄ 云端同步目录，文件更新自动触发 KB 重解析；托盘进度面板 / 动画图标 / SSO 免登 / 自动更新（网页头像菜单下载）

### AI 工作台（研究）
- 每个 Notebook 独立来源集（关联 KB/文件 + 可上传），笔记沉淀、保存为 KB 文档
- 会话续聊、SSE 流式输出、引用来源直达预览、有用/无用反馈
- **Agent 工具调用**：联网搜索（web_search：bing_cn/tavily/bing/bocha）、网页抓取、MCP 服务（文件系统/分步推理/知识图谱）
- **智能报告**：客户分析 / 销售周报 / 月报 / 自定义 HTML 报告；**文档 / 演示（PPTX）双布局预览**；下载 PPTX / PDF；对话式修改；流式生成 + 实时进度

### 检索管线
问题改写（指代消解）→ 按 kb_ids / file_ids 限定范围 → **混合检索**（blend：pgvector 召回 + tsvector BM25 精排；或向量 + pg_trgm 关键词 RRF 融合）→ **Rerank 三级降级**（专用 rerank 模型 → chat 打分 → RRF 序）→ Small2Big 上下文扩展 → 阈值兜底

### 权限与分享
- 内容默认**私有**（个人专属），支持 **只读 / 编辑 / 所有权** 三档授权 + **对团队可见**开关
- 覆盖：知识库 / 文档库文件 / Notebook（笔记继承）；分享即时通知、点击直达
- 管理员绕过 ACL；RAG 检索仅限用户可读范围（不泄露私有内容）

### 系统管理（仅 admin）
- 用户 / 分组、模型管理（chat/embed/vision/asr/rerank 五类，Key 加密、连通测试）、用量监控
- **系统监控**：CPU/内存/磁盘 + 表行数；**自动备份 + 一键恢复**；**清理空间 / 重建向量索引**；**阈值告警**（磁盘/内存/LLM 失败 → 通知管理员）
- 异常中心、回收站（客户/文件/知识库/**Notebook**）、审计日志、Skill 管理、MCP Server、**品牌设置**（系统名 + Logo）

### 前端体验
- 明暗双主题 + 5 品牌色、**字号三档（小/中/大）**、**侧边栏折叠**、全站响应式 + 移动端标签栏
- 顶栏品牌（名称/Logo）由管理端可配置，登录页/布局动态读取

## LLM 配置

模型在**系统管理 → 模型管理**注册（DB 优先，api_key 加密存储、脱敏显示、改动即时生效），未注册时回退 `backend/.env` 兜底。五类模型：

| 类型 | 用途 | 示例 |
|---|---|---|
| chat | 问答 / 报告 / 画像（需支持 function calling） | deepseek-chat / kimi / qwen |
| embed | 知识库向量化（不配则检索不可用） | bge-m3 / text-embedding-3-small |
| rerank | 检索精排（不配自动降级 chat 打分） | bge-reranker-v2-m3 |
| vision | 图片与扫描 PDF 识别（不配回退 chat） | qwen-vl / gpt-4o |
| asr | 音视频转写 | whisper 兼容接口 |

`backend/.env` 关键项：

```env
ENV=dev                              # 非 dev 时弱 JWT 密钥拒绝启动
DATABASE_URL=postgresql+asyncpg://crm:crm123@localhost:5432/crmnkb
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
JWT_SECRET=dev-secret-change-me
LLM_CHAT_TIMEOUT_SECONDS=180         # 长文报告/问答超时
PG_DOCKER_CONTAINER=crmnkb-postgres  # 备份用 PG 容器名
```

## 权限与安全要点

- JWT 鉴权；登录限流（DB 持久：账号+IP 15 分钟失败 5 次锁定，IP 全局熔断）
- 密码策略 min 8、改密后旧 JWT 失效；API Key 加密存储
- CORS 白名单、内容权限（私有/分享/团队可见）、RAG 范围隔离、文件令牌短时效（?t=）
- 回收站软删可恢复；彻底删除后自动 VACUUM，可手动重建向量索引

## API 简介（/api/v1，完整见 Swagger）

| 模块 | 说明 |
|---|---|
| /auth | 登录（限流 429）、当前用户、资料/密码/偏好 |
| /brand | 品牌（系统名 + Logo），公开读取 + admin 管理 |
| /users | 用户目录（分享选择用） |
| /customers, /industries | 客户 CRUD + 导入导出/查重/画像；行业字典 |
| /kbs, /library | 知识库与文档库；文档版本历史 `/kbs/{id}/documents/{doc}/versions` |
| /rag, /chat | 问答（SSE 流式），按 kb_ids/file_ids 限定范围 |
| /notebooks | 工作台 Notebook/笔记 |
| /reports | 报告：生成/查看/修订 + `/reports/{id}/pdf` + `/reports/{id}/pptx` |
| /permissions | 内容分享/授权/可见性（kb/file/notebook）+ 文件批量分享 |
| /workflows, /reminder-rules, /tasks, /notifications | 工作流、提醒、任务、通知 |
| /recycle-bin（admin） | 回收站（客户/文件/知识库/Notebook） |
| /admin（admin） | 用户/分组、模型、用量、系统监控、备份恢复、维护、告警、异常、审计、Skill、MCP |

## 项目结构

```
├── docker-compose.yml       # 开发：PostgreSQL 16 + pgvector
├── docker-compose.prod.yml  # 生产：postgres + backend + nginx 编排（见部署指南.md）
├── docs/                    # 历史设计文档（v2.0 架构 / 需求规格）
├── backend/
│   ├── app/api/             # 路由层（auth/brand/users/customers/kbs/library/rag/chat/notebooks/
│   │                        #   reports/permissions/recycle_bin/admin_* 等）
│   ├── app/services/        # 业务层（ingestion/kb/rag/chat/report/pdf/pptx/notebook/workflow/
│   │                        #   reminder/permissions/monitoring/backup/maintenance/audit/email 等）
│   ├── app/services/skills/ # Skill 框架（builtin 联网搜索/网页抓取 + api_skill + mcp_skill + registry）
│   ├── app/services/llm/    # LLM 抽象层（api/ollama 双实现 + factory + usage + 加密读取）
│   ├── app/models/          # 30+ 张表（含 resource_permissions / document_versions / login_attempts / brand_settings）
│   └── tests/               # 393 个测试用例（pytest）
├── mcp-local/               # 本地 MCP 服务包（filesystem/thinking/memory，运行依赖勿删）
└── frontend/                # Vue 3 前端（客户/知识库/文档库/工作台/任务/工作流/系统管理/个人中心）
```

## 测试

```bash
cd backend
pytest          # 393 个用例
```

## 更多

- 部署：见 `部署指南.md`（Docker 单机一键 / 云端双服务器 PG 分离）、`裸机部署指南.md`（Ubuntu 单机无 Docker）
- 企业化升级路线：见 `TODO.md`
