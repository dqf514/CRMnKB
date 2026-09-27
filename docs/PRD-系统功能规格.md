# 产品需求文档（PRD）—— 榜样知识库（内部知识库 + AI CRM）

> **文档性质**：本 PRD 由当前系统代码反向推导而成（逆向工程），描述系统**已实现**的全部功能细节，作为后续迭代、验收与需求变更的基线。
> **来源**：`backend/app/`（FastAPI 后端）、`frontend/src/`（Vue 3 前端）、`sync-app/`（本地同步客户端）全量代码通读。
> **版本**：v1.0（代码基线）｜ 整理日期：2026-08-21
> **约定**：需求编号 `FR-<域>-<序号>`；配置项以 `backend/app/config.py` 默认值为"出厂值"；时间口径除注明外均为 naive UTC。

---

## 1. 产品概述

### 1.1 定位

面向企业内部的一体化平台，集三大能力：

1. **CRM 客户管理**：客户/跟进/商机/行业字典 + AI 画像与摘要 + 自动化提醒与工作流；
2. **企业知识库（KB）**：多格式文档解析入库、语义切片向量化、混合检索问答（RAG）；
3. **AI 工作台（Studio）**：Notebook 研究工作区、SSE 流式问答、智能报告（HTML/演示版 PDF）、Agent 工具调用（联网搜索/网页抓取/MCP）。

### 1.2 用户角色

| 角色 | 说明 |
|---|---|
| 普通用户（user） | 使用全部业务功能；管理自己的资源（默认私有）；无可视化注册入口，账号由管理员创建 |
| 管理员（admin） | 额外拥有 `/admin/*` 与 `/recycle-bin` 全部管理能力；**绕过内容 ACL**（可读租户内一切资源）；不能停用/降级/删除自己（防锁死） |

### 1.3 技术架构基线

- 后端：FastAPI 0.115 模块化单体 + SQLAlchemy 2.0（async/asyncpg）+ PostgreSQL 16（pgvector + pg_trgm）。
- 前端：Vue 3 + Vite 6 + Element Plus + Pinia + ECharts + markdown-it + mammoth + xlsx。
- LLM：OpenAI 兼容 API / 本地 Ollama；模型配置 DB 优先（加密存储），`.env` 兜底；五类模型 chat/embed/rerank/vision/asr。
- 部署：开发 `docker compose`（仅 PG）；生产 `docker-compose.prod.yml`（postgres + backend + nginx 三容器）。
- 客户端：`sync-app/`（Python+tkinter 的 OneDrive 式双向同步桌面 App，PyInstaller 单文件 exe）。

### 1.4 租户模型

单系统多租户（`tenant_id` 贯穿所有业务表）；种子数据为默认租户 + `admin / admin123`。**当前实现为单租户使用场景**（无租户管理 UI，用户名全局唯一）。

---

## 2. 全局机制

### 2.1 认证与会话（FR-AUTH）

| 编号 | 需求 |
|---|---|
| FR-AUTH-01 | 登录 `POST /api/v1/auth/login`，入参 `username/password/long_lived`；成功签发 JWT（HS256，payload `sub/username/iat/exp`），默认有效期 12 小时，`long_lived=true` 时 30 天（供同步 App） |
| FR-AUTH-02 | 登录限流（DB 持久化，跨 worker 生效）：同一账号+IP 15 分钟内失败 5 次锁定（429）；同一 IP 15 分钟内失败 20 次全局熔断（429）；登录成功清除该账号+IP 失败记录；失败记录靠 15 分钟窗口自然过期 |
| FR-AUTH-03 | 停用账号（status=0）登录返回 403「账号已停用」；持旧 token 访问返回 401 |
| FR-AUTH-04 | 密码策略：最小 8 位，bcrypt 哈希；用户自助改密与管理员重置密码均刷新 `password_changed_at`，**此前签发的全部 JWT 立即失效**（校验 `iat >= password_changed_at`） |
| FR-AUTH-05 | 改密接口 `PUT /auth/password`（旧密码校验，错误 400）；改密成功前端强制重新登录 |
| FR-AUTH-06 | 个人资料 `PUT /auth/profile`（name/email/avatar_url）；头像上传 `POST /auth/avatar`：png/jpg/webp/gif、≤5MB、**魔数校验**防伪装，存 `data/avatars/`，旧头像自动清理 |
| FR-AUTH-07 | 用户偏好 `GET/PUT /auth/preferences`：JSONB 整体读写，存主题模式/主题色/字号/每页条数（见 §11.3），登录后**以后端偏好为准**覆盖本地 |
| FR-AUTH-08 | SSO 免登（同步 App「打开网页版」）：`POST /auth/sso-code` 签发一次性 code（60 秒、单次使用、进程内存存储）；`POST /auth/sso-exchange` 兑换正式 JWT；前端 `/sso` 页自动完成兑换并跳工作台。**已知局限**：code 存进程内存，多 worker 部署不可跨进程兑换 |
| FR-AUTH-09 | 无注册接口、无后端登出（JWT 无状态）、无找回密码（仅管理员重置） |
| FR-AUTH-10 | 非 dev 环境下 `JWT_SECRET` 为默认值或 <32 字符时**拒绝启动** |

### 2.2 内容权限模型（FR-PERM）

| 编号 | 需求 |
|---|---|
| FR-PERM-01 | 资源类型四类：`kb`（知识库）、`file`（文档库文件）、`folder`（文件夹）、`notebook`（工作区）；资源行存 `owner_id`（notebook 为 `created_by`）与 `is_private` |
| FR-PERM-02 | 可见性：`is_private=True`（默认）= 私有，仅 owner + 被分享者；`False/NULL` = 团队可见（租户内全员只读） |
| FR-PERM-03 | ACL 表 `resource_permissions`（唯一约束 tenant+rtype+rid+user），三档权限 `read(1) < edit(2) < owner(3)`；判定取 max(owner 身份, ACL, 团队可见 read) |
| FR-PERM-04 | **文件夹级联继承**（类网盘）：folder/file 有效权限 = 自身与所有祖先文件夹权限的最高者（只升不降）；批量 `accessible_ids` 时 folder 向下递归后代、file 沿所在文件夹链向上判定 |
| FR-PERM-05 | 分享操作仅 owner/admin：`GET/POST/PUT/DELETE /permissions/{rtype}/{rid}/share...`、可见性开关 `PUT .../visibility`、批量文件分享 `POST /permissions/files/batch-share`；目标用户须同租户且未停用 |
| FR-PERM-06 | 分享/批量分享触发通知（type=share，含资源名与权限级，带跳转参数）；修改权限与可见性开关**不**发通知 |
| FR-PERM-07 | RAG/聊天检索强制 ACL：未指定范围时取用户可读 KB 集，指定时过滤无权 id；检索 SQL 另排除软删 KB/文件 |
| FR-PERM-08 | 管理员绕过 ACL（视为 owner）；`require_admin` 保护全部 `/admin/*` 与 `/recycle-bin` |

### 2.3 通知（FR-NTF）

- `notifications` 表：个人级（user_id 维度），字段含 type/title/content/is_read/task_id/resource_type/resource_id。
- 通知类型：`reminder`（提醒规则）、`workflow`（工作流）、`share`（分享）、`report`（报告完成/失败）、`system_alert`（系统告警，发全体 admin）、`morning_brief`（晨报）。
- API：分页列表（含 unread_count）、单条已读、全部已读；无删除接口。
- 前端：顶栏铃铛 badge（max 99）、30 秒轮询、浏览器桌面通知（授权一次、按 id 防重）、点击按 resource_type 跳转（report→工作台报告，kb→知识库详情，file→文档库，notebook→工作台）。

### 2.4 审计日志（FR-AUD）

- `audit_logs` 表：action/resource_type/resource_id/detail(JSONB)/ip/created_at；随主事务提交（回滚则审计同滚）。
- 覆盖动作：登录成败、用户增删改/重置密码/头像、客户增删改/导出、文件增删改、知识库增删、文档重解析/视觉复核/直返设置、报告创建/导出、Skill/MCP 增删改、回收站恢复/彻底删除、自定义报告创建。
- **分组（user_groups）增删改不记审计**（现状）。
- 查询：按操作/资源类型/用户/日期范围分页（仅 admin，只读无删除）。

### 2.5 全局基础设施

- **异常兜底**：未捕获异常统一写 `error_logs`（module=http）并返回 500「服务器内部错误」；`/health` 含 DB ping（不可达 503）。
- **后台任务**（lifespan 启动，关闭时 cancel）：① `_reminder_loop`（提醒+工作流+晨报，默认 10 分钟/轮）；② `_alert_loop`（告警，默认 300 秒/轮）；③ 启动恢复（回填 supported 标记 + 滞留 processing 文档重新排队解析）。
- **幂等迁移**：无 Alembic，`init_db()` 内 `ALTER TABLE ... IF NOT EXISTS` + 一次性数据迁移（旧 industry 列并入数组、旧文档迁移到默认知识库、存量权限转私有、同步字段回填等）。
- **CORS** 显式白名单、不开 credentials。
- **文件访问令牌**：`<img>/<audio>/<video>/pdf` 直链场景签发 5 分钟短时效 JWT（含 `file` 声明且与路径 file_id 强绑定），登录 JWT 禁止出现在 URL；内容响应带 `Accept-Ranges`（音视频拖播）、`nosniff`、RFC5987 中文文件名，html/svg 等附加 CSP sandbox 防存储型 XSS。

---

## 3. CRM 客户管理（FR-CRM）

### 3.1 客户

**数据模型 `customers`**：name（必填）、industries（JSONB 多选行业）、tags（JSONB）、company/position/wechat/phone/email/address/source、birthday（date，生日工作流用）、attributes（JSONB 自定义属性，导入"备注"存此）、status（`potential 潜在/intention 意向/negotiating 洽谈中/closed 已成交/lost 已流失`，pydantic 强校验）、profile/profile_status/profile_updated_at（AI 画像）、owner_id、deleted_at（软删）。旧单列 `industry` 已废弃（数据已迁移）。

| 编号 | 需求 |
|---|---|
| FR-CRM-01 | 客户分页列表：keyword 模糊匹配（name/phone/email/company，OR）、status/industry/tag（JSONB has_key）筛选，按创建时间倒序；**客户为租户共享池**，不按 owner 过滤 |
| FR-CRM-02 | 新增/编辑（部分更新）；所有变更记审计（更新记字段名列表） |
| FR-CRM-03 | 疑似查重 `GET /customers/duplicates`：pg_trgm `similarity(name)>0.4` 或电话精确匹配，score 取高者，Top5；前端在表单 name/phone 失焦时实时提示并可跳转疑似客户 |
| FR-CRM-04 | Excel 导入（模板 9 列固定：姓名/单位/职务/电话/邮箱/微信/行业/标签/备注；行业与标签支持中英文逗号分隔）：行级校验（姓名必填、邮箱/电话正则）；`mode=skip` 遇疑似重复跳过，`mode=overwrite` 以非空值覆盖最高分重复记录；返回 created/updated/skipped/errors 明细 |
| FR-CRM-05 | Excel 导出：与列表同款筛选，上限 10000 行 |
| FR-CRM-06 | 软删除：级联软删客户专属知识库与名下全部附件文件（回收站可整体恢复）；彻底删除走回收站 |
| FR-CRM-07 | 客户专属知识库 `GET /customers/{id}/kb`：幂等创建「{客户名}-专属知识库」（type=customer，团队可见） |
| FR-CRM-08 | **AI 客户画像**：`POST .../profile/generate` 触发后台生成（202，状态机 idle→generating→ready/failed，前端 3s 轮询）；输入 = 基础字段 + 全部商机 + 最近 20 条跟进 + 专属库 ready 文档全文（8000 字预算）；输出五章节 Markdown：`## 基本情况 / ## 需求与痛点 / ## 决策链与关键人 / ## 合作进展 / ## 风险与跟进建议`，资料不足须注明、禁止编造；失败写 error_logs 不抛出 |

### 3.2 跟进记录

- 模型：customer_id/user_id/type（`call 电话/meeting 会议/email 邮件/visit 拜访`）/content/ai_summary。
- **只增不删**（无更新/删除接口，时间线只读）。
- 新增后后台并行触发两个 AI 任务：
  - **一句话摘要**（≤50 字，caller=summary，失败静默）；
  - **待办提取**（caller=ai_tasks）：LLM 输出 JSON 数组 `[{title, due_date, priority}]`，容错解析（raw_decode 逐 `[` 尝试），创建 Task（type=follow_up、ai_generated=True、source=ai_analysis、关联客户与跟进人）。

### 3.3 商机

- 模型：customer_id/name/amount(Numeric 15,2)/stage/expected_close_date/probability(0-100)/owner_id。
- 阶段枚举（前端约束，**后端不校验**）：prospecting 初步接触 / qualification 资格确认 / proposal 方案报价 / negotiation 商务谈判 / closed_won 赢单 / closed_lost 输单。
- CRUD + 按 customer_id 过滤；删除为**硬删**（不进回收站）；前端无编辑 UI（后端有 PUT）。

### 3.4 行业字典

- 模型：name（租户内唯一）/sort/enabled；预置 22 项（互联网/IT、软件与信息服务、金融、保险、房地产与建筑、制造业、批发与零售、教育培训、医疗健康、交通运输与物流、住宿餐饮、文化传媒、能源公用事业、农林牧渔、政府公共事业、专业服务、通信、汽车、消费电子、服装纺织、化工新材料、其他）。
- 停用后不出现在下拉；删除不校验引用（前端过滤展示）。

### 3.5 工作台聚合（Dashboard）

- `GET /dashboard/today`：今日到期任务、逾期任务、超 7 天未跟进客户（含从未跟进，前 8 条）、今日新增（文件/跟进/客户/笔记）、未读通知数、今日动态（审计日志排除 login，前 15 条）。
- `GET /dashboard/daily-report`：按人统计跟进数/完成任务/上传文件/笔记/新增客户（新增客户用审计日志口径）；`team=true` 仅 admin 生效，否则静默降级为本人。
- `POST /dashboard/capture`：随手记（1-5000 字）存入个人「随手记」工作区（幂等创建），标题取首行 40 字；对客户名做子串匹配返回建议关联客户（≤5 个）。
- **晨报**：每日 8:30 后向每个启用用户发 morning_brief 通知（今日到期/逾期任务数），按当日去重。

### 3.6 前端交互要点

- 客户列表页：工具栏（搜索/状态/行业/标签筛选、行业设置、模板下载、导入、导出、新增）；表格（名称链接、行业多 tag、标签 2 个 + `+N`、状态彩 tag）；导入对话框含结果汇总与错误明细表。
- 客户详情页 4 个 Tab：**跟进与商机**（时间线 + AI 摘要灰字；商机表格 + 概率滑块）｜**客户文档**（上传自动归档客户 + 关联专属库；可追加关联其他 KB）｜**客户画像**（四态渲染 + Markdown）｜**AI 对话**（懒加载专属库，SSE 流式问答，4 个快捷问题 chip，引用可点击直达文件预览）。
- 标签预设：重点客户/VIP/老客户/新客户/转介绍/意向强烈/价格敏感/决策周期长（可自定义）。

---

## 4. 知识库（FR-KB）

### 4.1 知识库类型

| 类型 | 判定 | 创建方式 | 特性 |
|---|---|---|---|
| 通用库 | type=general | 用户手动创建（强制 is_private=True） | 常规 |
| 客户专属库 | type=customer + customer_id | 上传带 customer_id / 文件设置客户 / 客户详情页 时幂等创建 | 团队可见，名「{客户名}-专属知识库」 |
| 自动资料库 | is_auto=True（type 实写 workbench） | 上传未指定 KB 且无客户时自动创建 | 每租户一个（部分唯一索引），名「AI工作台资料库」，前端默认隐藏、不可删除，兼作报告附件库 |

### 4.2 文档与切片模型

- `knowledge_documents`：文档 = "库文件在某 KB 中的解析实例"（file_id + kb_id，同一文件可关联多 KB）；status 状态机 `processing → ready / failed`，旁路 `unsupported`（格式关闭/不支持，仅存储）；`content` 存解析文本快照（带 `[图片识别]` 等方式前缀，截 10000 字）；`doc_metadata` 记 processing_method/model/error/vision_reviewed_at/question_gen_*。
- `document_chunks`：chunk_index/content/embedding(Vector 1024)/search_vector（tsvector，SQL 回填）。
- `chunk_questions`：ingestion 时为每 chunk 用 chat LLM 生成 3 个候选问题（容错解析、截 1500 字输入）并向量化；检索命中时给对应 chunk 加分 `similarity × 0.5`（解决用户表述与原文不一致）。
- `document_versions`：重解析/视觉复核成功**前**自动保存旧内容快照（`[处理失败]` 内容不快照）。

### 4.3 功能需求

| 编号 | 需求 |
|---|---|
| FR-KB-01 | KB CRUD；删除为软删（owner 权限），自动库禁止删除；列表默认隐藏自动库（`include_auto` 可开） |
| FR-KB-02 | 关联文件 `POST /kbs/{id}/documents`：幂等去重（返回 associated/already），可解析文档立即后台排队解析 |
| FR-KB-03 | 重解析（failed/卡死文档重置排队；processing 中 409）；视觉复核（仅 ready 且 method ∈ ocr/image_describe/vision_ocr，先成功提取再替换切片，失败保留旧内容记 vision_review_error） |
| FR-KB-04 | 版本历史：列表/查看全文/恢复（恢复 = 快照写成新 md 文件重新解析，原文件不动） |
| FR-KB-05 | 高置信短答案（direct-return）：文档可设 `answer + similarity`，RAG top3 命中且相似度达标时**直接返回预设答案跳过 LLM**（目前仅 API，无前端入口） |
| FR-KB-06 | 命中测试 `POST /kbs/{id}/hit-test`：向量+关键词 RRF 融合，返回命中方式（vector/trgm/both）与得分；不写查询日志；embed 不可用时降级纯关键词并提示 |
| FR-KB-07 | 从 KB 移除文档仅删关联与切片，不动库文件与磁盘 |
| FR-KB-08 | 文档页签：状态/解析方式/复核标记展示、失败原因 tooltip、processing 时每 3s 自动轮询 |

---

## 5. 文档库与多模态摄入（FR-LIB）

### 5.1 文档库

- `library_files`：folder_id/customer_id/file_name/file_path（`data/uploads/<uuid>.<ext>`）/file_size/supported（可解析标记）/content_hash(sha256)/updated_at（同步游标）/owner_id/is_private/deleted_at。
- `library_folders`：自引用树（parent_id CASCADE）。
- 文件夹树 `GET /library/tree` 只返回可见文件夹并补齐祖先链；文件夹支持新建/重命名/移动（禁移入自身）/删除（非空需 recursive=true，递归逐文件真删）。
- 文件列表按可见集过滤，带 kb_count 与 perm；支持按文件夹/customer/keyword 筛选。
- 文件操作：重命名/移动/改所属客户（设置 customer_id 自动关联专属库并重解析）/软删（owner）。
- 批量：批量共享、批量移动、批量关联 KB、批量删除。

### 5.2 上传

- `POST /library/upload`（multipart：files[]/paths[]/folder_id?/customer_id?/kb_ids[]）：
  - 单文件上限 `MAX_UPLOAD_MB`=100MB（前端预过滤 + 后端流式写入双重校验），超限跳过该文件不中断整批（skipped_files 带原因）；无整批数量上限。
  - 1MB 分块流式落盘 + 同步算 sha256；PermissionError 重试一次。
  - 文件夹上传按 `paths` 幂等重建中间目录。
  - 关联规则：显式 kb_ids + 客户专属库；都未指定 → 归档到隐藏自动库。
- 前端：全局上传队列 store，**逐文件顺序上传**（5 分钟超时）、实时字节进度、右下悬浮球 + 抽屉面板、可切页不中断、失败可重试；PST 文件上传后轮询拆解进度。

### 5.3 多模态摄入管线

支持 35 个扩展名，分 5 组（可在系统设置逐格式开关，关闭的格式仅存储不解析，启用后存量文件重启时自动补解析）：

| 分组 | 格式 | 解析方式 |
|---|---|---|
| 文本与办公 | pdf docx doc txt md html htm xlsx xls pptx ppt | txt/md：utf-8→gb18030 回退；html：自研 parser 剥标签；pdf：pypdf；docx：段落；xlsx/xls：按工作表 Tab 拼接；pptx：逐页文本框+表格；**doc/ppt 老二进制**：LibreOffice headless 转换优先 → olefile FIB 精确提取兜底 |
| 邮件 | eml msg pst | eml/msg：头部（主题/发件人/收件人/日期）+ 正文（plain 优先）+ **附件递归解析**；pst：独立流式管线（见 5.4） |
| 图片 | jpg jpeg png webp bmp gif tif tiff | tif 先转 PNG；三级递进：本地 OCR（RapidOCR→Tesseract，≥10 字采纳）→ 视觉模型转录+描述 |
| 音频 | mp3 wav m4a ogg flac amr | ASR 模型转写 |
| 视频 | mp4 mov avi mkv webm | ffmpeg 抽音轨为 mp3 → ASR |

- **扫描版 PDF 判定**：提取文本 < 页数 × 20 字 → pymupdf 逐页渲染（zoom≈2，上限 50 页）→ 本地 OCR → 视觉模型逐页（单页失败跳过）。
- **语义切片 v2**（512 字 + 64 重叠，可配）：Markdown 标题 6 级锚定（代码围栏内 `#` 掩码防误判）、parent_chain 标题链、空行分段贪心合并、超长段智能断句（`。`.`!`?`\n` 优先级，至少保留一半）、section 首 chunk 加 `[标题1 > 标题2]` 前缀。
- **向量化**：embed 分批 32 条，单批失败重试一次；写 chunks 后 UPDATE search_vector；可选生成 chunk_questions。
- **失败处理**：任何异常只置 failed（content 记 `[处理失败] 原因`、metadata.error、写 error_logs），绝不抛出；前端可重试。
- **启动恢复**：滞留 processing 重排 + unsupported 但现已支持的重排 + 存量 supported 标记回填。

### 5.4 PST 邮件归档流式拆解

- pypff 优先（防 PyPI 同名包校验），无则 readpst CLI（Windows 走 WSL）；边拆边产 eml、每秒轮询输出目录流式入库。
- 每封邮件：落盘 eml → 建 LibraryFile（放 PST 同名文件夹）→ 跟随容器文档同 KB 建解析文档 → 排队解析（并发上限 3）。
- 进度写容器文档 metadata（state=extracting/ingesting/done/failed + done/total），前端 3s 轮询 `GET /library/files/{id}/pst-progress`。

### 5.5 在线预览与内容访问

- `GET /files/{id}/content`：FileResponse Range 分段；显式 Content-Type 映射表。
- `GET /files/{id}/preview-text`：Office（doc/ppt/pptx/xls/xlsx/docx）实时文本化预览（截 20000 字）。
- `GET /files/{id}/preview.png`：tif 转 PNG。
- `GET /files/{id}/token`：签发 5 分钟文件令牌（见 §2.5）。
- 前端 FilePreview 按类型分派：image/pdf/音视频 = 令牌直链原生渲染；docx = mammoth 转 HTML（消毒）；xlsx/csv = xlsx 库多 sheet；md = markdown-it；html = sandbox iframe；txt 等超 2MB 截断提示；其余给下载按钮。

### 5.6 同步 App（sync-app/，桌面客户端）

- Python + tkinter/pystray，PyInstaller 单文件 exe；OneDrive 式双向同步：本地文件夹 ⇄ 云端「同步-主机名」文件夹。
- 登录走 `long_lived` 30 天 token，401 自动重登；「打开网页版」走 SSO code 免登。
- 下行：每 30s（5-3600 可配）轮询 `GET /library/changes?since=`（游标 = server_time），按 file_id 跟踪移动/重命名/删除；冲突保留本地副本 ` (本机冲突 时间戳)`；本地有改动时云端删除 → 挪 `.sync-trash/日期/`。
- 上行：watchdog 2s 去抖；修改先 `GET /files/{id}` 冲突预检（content_hash），远端也改则本地改名冲突副本；内容更新 `PUT /files/{id}/content` 触发 KB 自动重解析（旧内容自动存版本快照）。
- 托盘：同步动画/暂停灰显/离线角标；同步面板显示活动传输列表；自动更新（`GET /sync-app/version` 比对 sha256 → 下载校验 → bat 替换重启）。
- 服务端安装包分发：`GET /sync-app/version|download`（取 `data/sync_app/` 最新 exe，下载文件名带品牌系统名）。
- **已知风险**：客户端 config.json 密码明文（仅限内网使用）。

---

## 6. RAG 检索与 AI 问答（FR-RAG）

### 6.1 检索管线（chat 完整管线）

**问题改写（最近 6 条历史，指代消解，失败回退原问题）→ 嵌入 → 混合检索 → 问题命中加分 → Rerank 三级降级 → Small2Big 扩展 → 长度裁剪 → directly_return 检查 → LLM/Agent 生成 → 落库**

- **混合检索 blend（默认，RAG_SEARCH_MODE=blend）**：pgvector 余弦召回 top_k×10（上限 500）→ 候选回灌算 `ts_rank_cd(BM25)` → **综合分 = (1−cosine) + BM25 相加融合** → DISTINCT ON 去重 → 综合分 > 0.5 阈值 → top_k；失败/空回退旧路径（向量 + pg_trgm 关键词 RRF k=60）。另有 embedding 纯向量 / keywords 纯 BM25 模式。
- **Rerank 三级降级**：专用 rerank 模型（Jina/SiliconFlow/TEI 兼容）→ chat LLM 打分（候选截 300 字，输出 0-1 JSON 数组，容错解析）→ 放弃 rerank 走阈值逻辑。**rerank 成功时不做硬阈值过滤**（本地模型分数不校准，相关性交给生成模型）。
- **Small2Big**：命中 chunk 向同文档相邻 ±1 chunk 扩展，重叠区间合并，批量 SQL 消除 N+1；上下文裁剪上限 4000 字。
- **阈值兜底**：`grounded = max(score) ≥ 0.5`；未命中时依次：文件未解析话术 → 降级普通对话（带最近 10 条历史，prompt 声明"未参考知识库"）→ 固定话术「未在知识库中找到相关信息，建议转人工处理。」
- **file_ids 直读快速通道**：所选文件按格式直读全文（总 ≤50000 字），跳过检索直接进 prompt，sources 标 direct；超限回退 RAG；文件未解析完成返回专属话术防误导。
- **directly_return**：top3 命中文档设了短答案且分数达标 → 跳过 LLM 直返（SSE 发 direct_return 帧）。

### 6.2 会话与接口

- `/rag/query`：单轮问答（无改写/rerank/裁剪/工具），写 rag_query_logs，返回 answer/sources/grounded/query_log_id。
- `/chat/sessions` CRUD：会话列表按 updated_at 倒序；消息含 role/content/sources/grounded/query_log_id。
- `/chat/ask`（非流式）与 **`/chat/ask/stream`（SSE 主流式入口）**：
  - SSE 帧：`meta{session_id}`（失效 session 静默新建自愈）→ `status`（阶段提示）→ `sources` → 可选 `direct_return` → `thinking` / `tool`（Agent 事件）→ `token`（增量，已剥 think 块）→ `done{query_log_id}` 或 `error{detail}`；15s 心跳防 nginx 掐断。
  - `thinking=false` 时向模型发 `enable_thinking=false` 并剥离输出 think 块（流式用状态机跨 token 安全剥离，残留超 4096 字符放行防吞字）。
  - 生成 prompt：system 要求只基于上下文、禁止编造、用 [1][2] 标注引用；user = 编号上下文 + **原始问题**（改写仅用于检索）；历史不进生成 prompt（仅用于改写与兜底对话）。
- 每轮落库：rag_query_logs 1 条 + chat_messages 2 条（user/assistant）。

### 6.3 Agent 工具调用

- 有启用 Skill 时走 Agent 循环，**最多 3 轮**：chat_with_tools_stream → 执行 tool_calls（失败写"调用失败"不中断）→ tool 结果回注 → 轮尽后无工具再要最终回答；模型不支持 function calling 回退普通对话。
- 系统提示追加 TOOL_GUIDE（知识库不足时主动用工具）。

### 6.4 反馈闭环

- `POST /feedback`（rating=useful/useless + comment，绑 query_log_id，同租户校验）；`GET /feedback/stats`（总数/有用率）。前端 AI 消息有"有用/无用"按钮（仅一次，反馈后变绿"已反馈"）。

### 6.5 全局搜索

- `GET /search/global?q=`（Ctrl+K 命令面板，250ms 防抖）：五组各 Top5——客户（name/company/phone/email）、文件（可读集，file_name）、笔记（可读工作区内，title/content）、任务（仅本人）、报告（仅本人）；键盘导航 + 按类型跳转路由。

### 6.6 前端聊天体验

- Studio 三栏：左来源面板 / 中聊天区 / 右工作区·报告；会话按工作区隔离存 Pinia，**切页流式不中断**；session_id 存 localStorage 续聊。
- 流式渲染：markdown-it 逐 token 渲染、状态文案（检索中/生成中）、工具 chip（联网搜索等图标区分）、思考脉冲指示、引用折叠面板（score% + excerpt，file_id 可点击预览）、保存到工作区/反馈按钮。
- 输入区：深度思考/快速回答开关（localStorage 持久）、范围标签、停止按钮（AbortController）、Enter 发送（中文输入法 composing 保护）。

---

## 7. 工作台 Notebook 与笔记（FR-NB）

- `notebooks`：name/description/source_kb_ids[]/source_file_ids[]/created_by/is_private/deleted_at（软删进回收站）；返回前**净化失效来源 ID**。
- `notebook_notes`：title/content/source_type(manual/chat/report)/source_ref(JSONB)/sort；**权限继承所属 notebook**；增改删触达 notebook.updated_at。
- 需求要点：
  - 来源集 = 多 KB + 多文件（JSONB 数组，可编辑）；打开工作区自动载入为对话检索范围，可临时勾选/取消，可"取消关联"写回；关联对话框支持**直接上传新文档**到文档库。
  - 聊天回答一键「保存到工作区」：标题取问题前 30 字，正文拼 `## 问题/## 回答/## 引用来源`（来源 ≤10 条），source_ref 记 session 与 query_log。
  - 笔记「保存为知识库文档」：写成 md 文件 → 建 LibraryFile + KnowledgeDocument → 走标准摄入管线 → 回写 source_ref。
  - 笔记编辑器：预览/编辑切换、保存、删除（硬删）；列表卡片带来源类型标签。
  - 工作区标签栏（类 OneNote）：固定「今日」+ 各工作区标签（自有主色点/共享琥珀点/note 数），右键菜单重命名/共享/删除；新用户自动建「未命名工作区」。

---

## 8. 智能报告（FR-RPT）

### 8.1 报告类型

| 类型 | 数据来源 | 格式 |
|---|---|---|
| customer_analysis 客户分析 | 客户资料 + 全部商机 + 最近 20 条跟进 | Markdown |
| sales_weekly 销售周报（默认近 7 天） | 范围内：新增客户数/跟进数/商机阶段分布/成交金额（stage=closed 求和） | Markdown |
| sales_monthly 销售月报（默认近 30 天） | 同上 | Markdown |
| custom 自定义（prompt 必填，1-2000 字） | file_ids 直读全文 或 嵌入检索（TOP_K 12、RRF、rerank，**不做阈值过滤**保持素材广度，上下文 4000 字） | 完整独立 HTML（内联 CSS、纯 HTML 图表、禁 JS/外部资源） |

### 8.2 生成与生命周期

- `POST /reports/generate`（201，BackgroundTasks 后台跑）；状态机 `generating → ready/failed`，修改态 `revising`。
- **流式生成 + 前端轮询**（非 SSE）：每 ~2 秒增量写回 content + progress（"AI 生成中… N 字"），前端 2s 轮询实现滚动生成；阶段进度（"正在汇总客户资料…"）。
- 语言参数 zh/en/**zh_en**（双语 HTML：元素标 `data-lang`，前端注入 CSS 一键切换）。
- 超时 30-600 秒可配（默认 180）。
- 完成/失败写通知（type=report）；失败 error 截 500 字 + error_logs，绝不抛出。
- **演示版**：正文（截 12000 字）经 LLM 转 16:9 HTML 幻灯片（每页一个 section，自带键盘/按钮翻页脚本），存 params.presentation_html；生成失败仅置空不阻断（前端禁用演示按钮）。
- **对话式修改**（仅 custom + ready）：`POST /reports/{id}/revise`（instruction 1-1000 字）→ 保持原排版只改内容重出完整 HTML；旧版入 `params.revisions`（上限 20 条，可查看历史/回到最新）；修改后重新生成演示版。
- **PDF 导出**（Playwright Chromium）：文档版 A4（注入分页 CSS：标题后不分页、表格/图片不跨页）；演示版横向 13.33×7.5in 一页一幻灯片；Playwright 缺失 503。两次导出均记审计。
- 删除为硬删；列表/详情仅租户隔离（**无资源级 ACL**，与 notebook 不同）。

### 8.3 前端

- 报告面板（Studio 右栏 tab）：类型/状态/进度滚动文本卡片，进行中 3s 轮询；生成对话框（类型/客户/日期范围/语言/超时）；自定义对话框（需求 textarea + KB 多选 + **附件上传**：自动探测/创建工作台资料库，附件 AI 解析中轮询，全部 ready 才能提交）。
- 查看抽屉：文档/演示布局切换、双语切换、全屏、PDF 下载、修改历史、对话式修改输入框；HTML 用 `iframe sandbox=""` 隔离（演示版放行脚本供翻页）。

---

## 9. LLM 模型管理 / Skills / MCP（FR-LLM）

### 9.1 模型管理（仅 admin）

- `llm_models`：name/provider(api|ollama)/model_type(chat|embed|vision|asr|rerank)/base_url/api_key（**Fernet 加密**，密钥由 JWT_SECRET 派生 SHA-256）/model/is_default（同类互斥，后端维护）/enabled。
- CRUD + 设为默认 + **连通测试**（已保存/未保存配置均可测；asr 不支持测试需真实音频验证）+ **拉取远端模型列表**（api 走 `/models`、ollama 走 `/api/tags`）。
- Key 脱敏显示（头 3 + `****` + 尾 4）；编辑时空 Key = 不修改。
- **选择回退链**：chat/embed = DB 优先 → `.env` 兜底；vision = DB vision → 回退 chat 默认；rerank = DB 或 None（调用方降级 chat 打分）；asr = 仅 DB，未配置直接报错。
- 调用埋点：InstrumentedLLM 每次调用记 `llm_call_logs`（model/caller/tokens/latency/success/error），失败同时写 error_logs（warning，供告警）；流式调用无 token 计数。
- 用量监控 `GET /admin/llm/stats?days=`（1-90 天）：总调用/成功率/平均延迟/总 Tokens/按模型/按日/按 caller/最近 5 条失败；前端 ECharts 三图（趋势双线、模型环形、来源柱状）。

### 9.2 Skills（仅 admin）

- `skills` 表：name（租户唯一）/type(builtin|api|mcp)/enabled（**默认 False**）/config(JSONB)/timeout(10-300s)。
- 内置两个：
  - **web_search**：provider 支持 tavily/bing/bocha（博查国内）/bing_cn（免 key 爬 cn.bing.com）/duckduckgo（默认兜底，国内不可达时前端告警）；统一返回前 5 条"标题+摘要+链接"，截 3000 字。
  - **web_fetch**：抓取网页剥标签取正文；**SSRF 防护**（仅 http/https、IP 归一化判内网拒绝、域名 DNS 解析任一内网即拒，防 DNS rebinding）。
- 自定义 API Skill：method/url/headers/body 模板（`{{arg}}` 占位）+ 参数 JSON Schema；URL 过 SSRF；响应 JSON 美化。**已知缺陷**：前端写 `config.params_schema`、后端读 `config.parameters`，键名不一致。
- 配置脱敏（键名含 key/secret/token/password 的值脱敏）；内置 skill 首次保存自动建行；不可删除内置（仅禁用）；测试按钮真实执行一次（caller=test）。
- 调用统计：近 30 天各 skill 调用数/失败/均延迟/最近调用。
- 前端管理页：启用开关、web_search provider 选择、参数 JSON 编辑、测试对话框。

### 9.3 MCP（仅 admin）

- `mcp_servers`：name/transport(stdio|sse)/config（stdio: command/args/env；sse: url/headers/auth_token）/enabled（默认 False）/status(unknown/connected/error/disconnected)/discovered_tools(JSONB)。
- 连接池：进程内单例、懒连接、每 server 一条长连接 + 锁串行化、断线自动重建；stdio 起本地子进程、sse 带 Bearer；**协议降级补丁**（LATEST_PROTOCOL_VERSION=2025-11-25 时降为 2025-06-18 防握手卡死）；应用关闭时清理。
- 操作：连接（试连 + 刷新工具发现）/断开/**同步工具**（每个 MCP 工具注册为一行 skill，名 `{server}__{tool}`）/查看工具；删除 server 级联删派生 skill。
- transport/config 变更即断开旧连接置 unknown。
- `mcp-local/` 为本地 MCP server npm 依赖，运行时不可删。

---

## 10. 自动化（FR-AUTO）

### 10.1 工作流（5 触发器 × 3 动作）

- 触发器：**interval**（间隔 N 分钟，默认 60）｜**daily**（HH:mm，默认 09:00）｜**weekly**（星期 1-7 + 时间）｜**birthday**（客户生日当天，内置条件判定月日相同）｜**condition**（每轮评估，条件 DSL 过滤客户）。
- 条件 DSL（白名单防注入）：字段限 name/industry/status/source/phone/email；操作限 eq/ne/contains/gt/lt（**gt/lt 为字符串比较**）；多条件 AND。
- 动作：**send_email**（subject+template，{{customer_name}} 占位；无邮箱跳过；同工作流同客户当天去重；SMTP 未配置整轮 failed）｜**create_task**（标题模板/type/priority/due_in_days；同客户同标题未完成的 rule 任务去重；接收人 = 客户 owner → 回退租户最早启用用户）｜**create_notification**（同用户同标题当天去重）。
- 执行：`run_workflow` 逐客户执行、SAVEPOINT 隔离、绝不抛出；记 WorkflowRun（status/matched_count/多行 detail）；调度循环默认 10 分钟/轮（daily/weekly 精度 = 调度间隔）。
- 前端：4 步分步表单（基本信息→触发方式→触发条件→动作配置）；「生日祝福模板」一键填充；立即运行（返回匹配数）；运行历史抽屉（detail 展开）。
- API：CRUD + `POST /{id}/run` + `GET /{id}/runs` 分页。

### 10.2 提醒规则（3 种）

- **days_since_last_followup**（N 天未跟进，默认 7；从未跟进按创建时间算）：建任务（source=rule）+ 通知，按（客户+标题+未完成）去重。
- **opportunity_stagnant**（商机 N 天未更新，默认 7；closed/lost/won 终态跳过）：同上。
- **task_due_soon**（任务 N 小时内到期，默认 24）：**仅建通知**（关联原任务），按（task_id+标题）永久去重。
- 模板渲染 `{{placeholder}}`，未知占位符原样保留；`POST /reminder-rules/run` 手动跑本租户全部规则。
- 前端在任务页第二个 Tab 管理。

### 10.3 任务

- 字段：customer_id?/user_id/title/description/type(follow_up|meeting|call|email|report)/priority(high|medium|low)/due_date/status(pending|in_progress|completed|cancelled)/ai_generated/source(manual|rule|ai_analysis)/completed_at。
- 来源三渠道：手动创建、规则/提醒创建、AI 从跟进记录提取。
- 完成自动写 completed_at；删除为**物理删除**（不进回收站）。
- 前端：状态筛选、导出 Excel（当前页）、到期标红（24h 内或已逾期）、来源/AI 标签；客户可搜索下拉（前 200）。

### 10.4 邮件发送

- SMTP 配置（HOST/PORT=465/USER/PASSWORD/FROM/USE_SSL）；SSL 或 STARTTLS；30s 超时；纯文本；asyncio.to_thread 包装；未配置时调用方降级。

---

## 11. 系统管理与运维（FR-ADM，全部仅 admin）

### 11.1 用户与分组

- 用户：分页列表（keyword/分组筛选）、创建（用户名全局唯一 409、初始密码 ≥8 位）、更新（name/email/role/group_id/status）、重置密码（使旧 JWT 失效）、删除（**9 类业务数据检查**：客户/商机/跟进/任务/通知/会话/报告/检索日志/反馈，有则 409 建议停用；无数据先解绑 llm_call_logs 再删）。
- 防锁死：不能停用/降级/删除自己（前端同步禁用控件）。
- 分组：CRUD；有成员不可删（400）；不记审计（现状）。

### 11.2 系统监控

- `GET /admin/system/overview`：运行时长、Python 版本、CPU（0.1s 采样）/内存/磁盘（上传目录所在分区）、数据库总大小 + 12 张核心表行数；psutil 失败优雅降级为 None；前端 30s 自动刷新、三色分档进度环。

### 11.3 备份与恢复

- 立即备份（后台）：`pg_dump --clean --if-exists | gzip` → `db.sql.gz` + uploads/brand 打 `uploads.tar.gz` + manifest.json；走 `docker exec`（PG_DOCKER_CONTAINER 配置）或本机 pg_dump（PGPASSWORD 传密）；**只保留最近 10 份**。
- 恢复（破坏性，强确认）：先清 uploads/brand 再解 tar（防目录穿越），psql 导入重建；建议维护窗口执行。
- 备份列表显示名称/时间/DB 大小/上传目录大小。

### 11.4 维护

- `POST /admin/system/maintenance?action=`：vacuum（VACUUM ANALYZE）/ reindex（CONCURRENTLY 重建 HNSW + 2 个 GIN 索引后补 VACUUM）/ cleanup（清理"磁盘有、库里无"的孤儿上传文件，同步返回数量与字节）。
- asyncpg 直连 autocommit（VACUUM 不能在事务内），statement_timeout 兜底。

### 11.5 告警

- `_alert_loop`（默认 300s/轮）：磁盘 ≥85% / 内存 ≥90% / LLM 10 分钟内失败 ≥5 次 → 通知全体启用 admin（type=system_alert）；**仅状态翻转时通知一次**（回落重置）。

### 11.6 异常中心

- `error_logs`：level(error/warning)/module(http/llm/ingestion/profile/report/pst…)/message(500 字)/detail(2000 字)/resolved；来源：HTTP 未捕获异常、摄入失败、画像失败、报告失败、LLM 失败。
- 查询（级别/状态筛选、本租户+全局）、单条/全部标记已解决。

### 11.7 回收站

- 仅 4 类实体进回收站：客户/文件/知识库/工作区（任务、报告、会话不进）。
- 恢复：清 deleted_at；同名冲突检查（客户/KB 按租户同名、文件按同文件夹同名）409；恢复客户连带恢复专属 KB 与附件。
- 彻底删除：客户 → 级联删专属 KB 文档/切片；文件 → 删各 KB 文档+切片+磁盘文件；KB/工作区 → 删行级联；完成后后台 VACUUM；支持批量（逐项独立事务，返回成功/失败明细）。

### 11.8 系统设置

- 品牌设置：系统名（1-100 字，空回退「榜样知识库」）+ Logo（png/jpg/webp/svg ≤5MB，只保留一个文件）；`GET /brand` 公开读取（登录页/布局/浏览器标题）。
- 解析文件格式：5 组 30+ 扩展名逐项开关（关闭的格式仅存储不解析；启用后存量重启时补解析）。

---

## 12. 前端框架与体验（FR-FE）

### 12.1 路由与导航

- 主菜单 6 项：工作台（首页）/知识库/文档库/客户管理/任务提醒/工作流；系统管理（仅 admin）三组：用户权限（用户/分组/审计）、AI 模型（模型/用量/Skill/MCP）、系统运维（监控/异常/回收站/设置）。
- 守卫：无 token 跳登录；admin 路由校验角色；`/reports`、`/chat` 老入口重定向到工作台。
- 布局：可折叠侧边栏（216↔64px，localStorage 持久）；顶栏 = 页标题 + 字号/明暗/主题色/通知/用户下拉（个人中心/下载同步 App/退出）；移动端 <992px 底部标签栏 + "更多"抽屉。
- 全局组件：上传进度面板、Ctrl+K 全局搜索。

### 12.2 主题系统

- 明暗双主题（html.dark + 双套设计令牌）；5 品牌色（曜蓝/翡翠/绛紫/琥珀/茜红，运行时计算 Element Plus 完整色阶）；字号三档（13/15/17px，缩放 CSS 变量而非 zoom——避免弹层坐标漂移）；每页条数偏好（10/20/50）。
- 偏好 localStorage 即时生效 + 静默同步后端，登录后以后端为准。

### 12.3 HTTP 层

- axios 实例：自动注入 Bearer；401 清凭证跳登录；错误统一 ElMessage 透传 detail；文件下载走 blob。
- SSE 用 fetch + ReadableStream（axios 不支持流式 POST），支持 AbortController 中断。

---

## 13. 非功能性需求

| 类别 | 需求 |
|---|---|
| 安全 | JWT + 改密失效；登录限流 DB 持久化；API Key Fernet 加密；密码 bcrypt ≥8 位；CORS 白名单；内容默认私有 + 三档授权；RAG 检索 ACL 隔离；文件令牌 5 分钟短时效 + 文件绑定；内容响应 CSP/nosniff 加固；SSRF 防护（web_fetch/API Skill）；web_fetch 前端消毒；非 dev 弱密钥拒绝启动 |
| 性能 | 向量 HNSW 索引 + tsvector/trgm GIN 索引；embed 分批 32；检索批量 SQL 消除 N+1；LLM 调用缓存（版本失效）；上传 1MB 分块流式；PST 并发 3 限流 |
| 可靠 | 解析失败不阻断（标 failed 可重试）；启动恢复滞留任务；LLM/检索多级降级链；后台任务 SAVEPOINT 隔离；备份保留 10 份 |
| 可用性 | 全站响应式 + 移动端标签栏；SSE 15s 心跳；流式切页不中断；中文输入法保护；桌面通知 |
| 可运维 | 系统监控 + 阈值告警；异常中心；审计日志；备份/恢复/维护一键化 |

---

## 14. 已知偏差与缺口（代码现状，供迭代参考）

1. 商机 stage/probability 后端无枚举/范围校验，仅靠前端约束。
2. 跟进记录只增不删；客户无批量操作、无负责人变更 UI（owner_id 仅写入）。
3. direct-return（高置信短答案）仅有 API，无前端入口。
4. 知识库列表页"共享"仅 owner 可见；列表页删除按钮存在方法但未挂 UI（删除在详情页）；KbOut 无 customer_name 致客户名显示为死代码。
5. 文档状态机无独立 pending（新建即 processing）；自动库 type 实写 `workbench` 而非注释中的 auto。
6. API Skill 参数 Schema 前后端键名不一致（params_schema vs parameters），自定义 API Skill 参数或不可用。
7. 报告无资源级 ACL（租户内全员可见全部报告）；报告删除为硬删。
8. SSO code 进程内存存储，多 worker 部署不可跨进程兑换。
9. 同步 App 客户端密码本地明文存储（限内网）。
10. 分组增删改不记审计；前端分组成员数列后端未返回。
11. 无用户注册/找回密码/后端登出接口。
12. chunk_questions.hit_num 定义了命中统计但检索链路未写入递增。
13. 工作流条件 DSL 的 gt/lt 为字符串比较，不支持数值/日期。
14. 任务删除为物理删除，不进回收站。

---

## 附录 A：路由全表

| 路径 | 页面 | 权限 |
|---|---|---|
| /login /sso | 登录 / SSO 免登 | 公开 |
| /studio | 工作台（首页，含今日视图/聊天/笔记/报告） | 登录 |
| /knowledge、/knowledge/:id | 知识库列表 / 详情 | 登录 |
| /library | 文档库 | 登录 |
| /customers、/customers/:id | 客户列表 / 详情 | 登录 |
| /tasks | 任务提醒（任务 + 提醒规则两 Tab） | 登录 |
| /workflows | 工作流 | 登录 |
| /profile | 个人中心（资料/密码/偏好） | 登录 |
| /admin/users、/admin/groups、/admin/audit-logs | 用户/分组/审计 | admin |
| /admin/llm/models、/admin/llm/stats、/admin/skills、/admin/mcp-servers | 模型/用量/Skill/MCP | admin |
| /admin/system、/admin/errors、/admin/recycle-bin、/admin/settings | 监控/异常/回收站/设置 | admin |

## 附录 B：核心配置项（默认值）

`EMBEDDING_DIM=1024`、`RAG_TOP_K=5`、`RAG_SCORE_THRESHOLD=0.5`、`RAG_SEARCH_MODE=blend`、`RAG_NEIGHBOR_WINDOW=1`、`RAG_MAX_CONTEXT_CHARS=4000`、`RAG_DIRECT_FILE_MAX_CHARS=50000`、`CHUNK_SIZE=512`、`CHUNK_OVERLAP=64`、`MAX_UPLOAD_MB=100`、`JWT_EXPIRE_MINUTES=720`、`LLM_CHAT_TIMEOUT_SECONDS=180`、`REMINDER_INTERVAL_MINUTES=10`、`ALERT_INTERVAL_SECONDS=300`、`ALERT_DISK_PERCENT=85`、`ALERT_MEM_PERCENT=90`、`ALERT_LLM_FAIL_WINDOW_MIN=10`、`ALERT_LLM_FAIL_THRESHOLD=5`、`PDF_MAX_OCR_PAGES=50`、`PG_DOCKER_CONTAINER=crmnkb-postgres`。
