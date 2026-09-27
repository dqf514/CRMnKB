# dsh 基座实施方案 —— 以 DeepSeek Harness 重建榜样知识库（AI CRM + 知识库）

> 版本：v1.0 ｜ 状态：调研完成，待评审启动
> 上游依据：`docs/PRD-系统功能规格.md`（功能验收纲）、`docs/TDD-技术设计文档.md`（现状技术细节）、`docs/需求整理-9月14日录音.md`（9/14 变更需求）
> 调研对象：DeepSeek Harness v0.1.7-rc.2（MIT，pnpm monorepo，本地克隆 `D:/tmp/deepseek-harness`）

## 0. 决策记录（已定案，不再复议）

1. **以 dsh 为 Agent 运行时基座**，技术路线次要，以实现 PRD 为最高优先级。
2. **单租户**：不做多租户隔离，只做团队协作（用户/分组 + 内容级 ACL）。PRD 1.4 节的多租户模型降级为"单租户 + 团队协作"。
3. 现有 FastAPI 后端**保留为业务后端与鉴权边界**，dsh 作为内嵌 Agent 引擎引入，不推倒重来。

## 1. 为什么可行：四个关键未知数的源码级结论

调研前最大的四个风险，均已在 dsh 源码中得到确定答案。

### 1.1 会话存储能否换成 PostgreSQL？—— 能，且接口天然契合

dsh 的持久化 seam 是 `ctx.sessionPersistence`（`packages/session/session-persistence/src/index.ts`），整个接口只有 5 个方法：

- `create / open / flush / stat / list`，返回 `SessionHandle`（`read / append / flush / close`）。
- 语义是**按会话的 append-only 事件流**：Header 不可变（create 时定死），事件只追加，`flush` 才保证崩溃安全。

映射到 PostgreSQL 只需两张表：

```sql
-- 会话头（对应 SessionHeader，create 后不可变）
session_headers (session_id PK, format_version, created_at, parent_session_id, inherited_event_count, meta JSONB, ...)
-- 会话事件（append-only 日志）
session_events (session_id, seq BIGSERIAL, event JSONB, PRIMARY KEY (session_id, seq))
```

关键收益：

- PG 事务白送"撕裂免疫"（dsh 文件实现靠原子 rename 保证的语义，PG 一条 INSERT 就有）；
- 单写者语义用 `pg_advisory_xact_lock(hashtext(session_id))` 实现，与 `write ownership` 错误模型一一对应；
- 写入负载极低（dsh 内部 200ms 批窗口 + 每 step flush），PG 毫无压力；
- 共享校验逻辑可直接 import `packages/session/session-persistence/src/storage-contract.ts`，自写后端与官方文件后端跑同一套契约测试。

**团队协作落点**：`SessionHeader` 没有 user 字段（dsh 是单主体设计），但 PG 后端是我们自己写的——在同库加 `session_acl` / `user_id` 列即可，授权层与存储层同库同事务，比任何外挂方案都干净。

### 1.2 用户身份能否穿透进 dsh？—— 能，经网关 + 插件链注入

dsh 无用户概念，且 BrowserAuth（launchToken 换 HMAC cookie）无官方关闭开关。可行路径：

- **身份边界在网关**：浏览器只与 FastAPI（8100）通信，FastAPI 完成 JWT 鉴权后，以受信控制器身份连接 dsh（Python SDK 或 ACP stdio）。dsh 进程根本不暴露给浏览器，BrowserAuth 不启用。
- **身份注入扩展点**（dsh 插件，Cordis 体系）：
  - `agent/created` 事件：把 `user_id` 挂到 agent 上下文（WeakMap 或写入自定义会话事件，如 `session/meta {userId}`）；
  - 工具执行时 `exec.agent` 查回身份；
  - `tools/pre-execute`（waterfall）做策略拒绝（如非 admin 禁止某工具）；
  - `ctx.tools.guard()` 做最终门禁。
- **ACL 强制点全部留在 Python 侧**：dsh 看到的工具（MCP）由我们的 MCP server 提供，每个工具实现内部按 `user_id` 做 PRD 2.2 节的三档授权过滤。dsh 进程视为**不可信环境**，这与 PRD 的安全模型完全一致（RAG 检索仅限用户可读范围）。

### 1.3 前端 UI 怎么办？—— 不碰 dsh 的 React 壳，Vue 前端保留

dsh 的 Web UI 是 60+ 个 React 包组成的插件树（`apps/web` 薄壳 + `packages/client/*`），复用价值低、改造成本高。结论：

- 现有 Vue3 前端**整体保留**，只与 FastAPI 的 SSE 接口通信（现状已是如此，PRD 6.6 节的前端聊天体验不变）。
- 一个注意点：dsh 的事件**不是逐 token**，assistant 文本按提交块（block）到达。前端打字机效果改为按块模拟（逐字吐出每个 block），或接受块状渲染。这是体验上唯一可感知的变化。

### 1.4 部署形态？—— 单文件 runtime 进容器，跟现有 compose 融合

- `dsh web` 强制 127.0.0.1、拒绝 `--host 0.0.0.0`，无官方 Docker 物——但这恰好支持网关模式：dsh 本来就不该对外。
- **最佳打包形态**：Python wheel `deepseek-harness-runtime-bin` 提供的单文件 dsh 可执行程序（自带 Node 闭包）。打进 backend 镜像或作为 sidecar 容器均可，`DSH_HOME`（profile、会话文件）挂 named volume。
- 生产 compose 从三容器（postgres / backend / nginx）变为：postgres + backend（内含 dsh 子进程）+ nginx，形态不变。

### 1.5 接入面选型：Python SDK vs ACP

dsh **没有公开 HTTP REST API**，四个入口对比：

| 入口 | 协议 | 能否注册工具 | 适用 |
|---|---|---|---|
| Python SDK | stdio JSON-RPC，协议面极小（`initialize` / `session/prompt` / `shutdown` 三请求 + `session.event` 等四通知） | 否（工具走 MCP） | **首选**：FastAPI 内嵌长驻子进程，进程生命周期与 uvicorn 一致 |
| ACP server | 标准 ACP v1 stdio，可逐会话挂 MCP server、运行中切模型 | 间接（MCP） | 备选：需要逐会话差异化工具集时 |
| Web Host RPC | `/api/*` + WebSocket，内部协议 | — | 不推荐（内部协议不稳定） |
| headless | 一次性任务 | — | 报告生成等批处理场景可用 |

**推荐 Python SDK 为主**（协议面只有 3 请求 4 通知，v0.1 破坏性变更的暴露面最小），ACP 为备选。**MCP 是关键**：dsh 对 MCP 只消费不暴露——我们的 RAG/CRM 能力包装成一个 Streamable HTTP MCP server（挂在 8100 内部路由），在 dsh profile 的 `cordis.patch.yml` 里加一条 `@deepseek-ai/dsh-mcp-client` 配置即被消费，工具名形如 `mcp__kb__kb_search`。

## 2. 目标架构（网关模式）

```
浏览器 (Vue3, 不变)
   │ HTTPS / SSE
   ▼
FastAPI :8100  ── 唯一对外入口、JWT 鉴权、ACL 强制、审计落库
   │ ① 业务 REST（CRM/知识库/报告/自动化… 全部保留）
   │ ② 内嵌 Python SDK ──spawn──► dsh 长驻子进程 (127.0.0.1, 不对外)
   │                              │ ctx.sessionPersistence ← PG storage 插件（自写 npm 包）
   │                              │ approval/request ← 审批应答器插件（回调 FastAPI）
   │                              │ agent/created ← 身份注入插件（user_id 写入会话 meta）
   │                              ▼
   │                     dsh-mcp-client（cordis.patch.yml 配置）
   │                              │ Streamable HTTP
   ▼                              ▼
MCP server :8100/api/mcp ◄────────┘   （kb_search / kb_read / crm_query / mail_draft / …）
   │ 每个工具内部按 user_id 做 ACL 过滤（复用现有 services 层）
   ▼
PostgreSQL 16 + pgvector  ── 业务库（现状）+ session_headers/session_events（dsh 会话）+ session_acl
```

职责切分一句话：**dsh 只管"Agent 怎么思考"（模型编排、工具循环、会话持久化、审批钩子），FastAPI 只管"业务是什么、谁能看什么"（全部 PRD 功能面 + 安全面）。**

## 3. 需要自建的组件清单

| # | 组件 | 形态 | 接口要点 | 对应 PRD |
|---|---|---|---|---|
| 1 | PG storage 插件 | dsh Cordis 插件（TS） | 实现 `sessionPersistence` 5 方法 + `SessionHandle` 4 方法；复用官方 `storage-contract.ts` 契约测试；同库挂 `session_acl(user_id, session_id, level)` | 6.2 会话管理 |
| 2 | 身份注入插件 | dsh Cordis 插件 | `agent/created` 时从会话 meta 读 `userId`（由 SDK 调用方在 `session/prompt` 参数透传）；`tools/pre-execute` 按用户角色拒绝敏感工具 | 2.1 认证、2.2 权限 |
| 3 | 业务 MCP server | FastAPI 内部路由（Streamable HTTP） | 首批工具：`kb_search`（走现有 rag.py 管线，含 Small2Big/rerank）、`kb_read_doc`、`crm_query_customer`、`crm_add_followup`、`mail_draft_create`。全部复用现有 services 层，ACL 在工具内强制 | 4/5/6 章全部 |
| 4 | 审批应答器插件 | dsh Cordis 插件 | 注册 `approval/request` 应答器：POST 到 FastAPI 审批接口并阻塞等待回调；`approval/asked`/`approval/decided` 事件自动落会话日志（审计白送） | 9/14 录音一号需求（邮件草稿审批链）、FR-AUD |
| 5 | 会话事件→SSE 桥 | FastAPI 服务层 | Python SDK 收 `session.event` 通知，按块转发到前端 SSE；打字机效果前端按块模拟 | 6.6 前端聊天体验 |

**不需要改的**：RAG 检索管线（ingestion/rag/chat services 原样保留，只是从"被 FastAPI 路由调用"变为"被 MCP 工具调用"）、文档解析与多模态摄入、报告生成（HTML/PPTX/PDF）、工作流/提醒调度、系统管理与监控、Vue 前端。PRD 第 3、5、7、8、10、11 章的功能面几乎原封不动。

## 4. PRD 功能映射速查

| PRD 章节 | 功能 | 实现归属 | 变化量 |
|---|---|---|---|
| 2.1 认证/会话 | JWT、登录限流 | FastAPI（现状保留） | 无 |
| 2.2 内容权限 | 三档授权 + 团队可见 | FastAPI + MCP 工具内过滤 + `session_acl` | 小（新增会话授权表） |
| 3 CRM | 客户/跟进/商机/工作台 | FastAPI（现状保留）；Agent 侧经 `crm_*` MCP 工具 | 无/小 |
| 4-5 知识库/文档库 | 摄入、切片、预览、PST 拆解 | FastAPI services（现状保留） | 无 |
| 6 RAG 问答 | **核心改造点**：chat.py 的 LLM 循环交给 dsh；检索管线封装为 `mcp__kb__kb_search` | dsh + MCP | 大（但检索逻辑零改动） |
| 6.2 会话 | 会话列表/恢复/分叉 | dsh PG storage（分叉 = dsh 原生 fork：物复制前缀 + `session/end-seed`） | 中 |
| 7-8 Notebook/报告 | 研究工作台、HTML/PPTX/PDF | FastAPI（现状保留）；报告生成可用 dsh headless 模式触发 | 无/小 |
| 9 模型/Skills/MCP 管理 | 模型注册、Key 加密 | FastAPI 管理面保留；dsh profile 指向 OpenAI 兼容端点即可 | 小 |
| 10 自动化 | 工作流/提醒/任务/邮件 | FastAPI（现状保留） | 无 |
| 11 系统管理 | 监控/备份/回收站 | FastAPI（现状保留）；备份范围 + `session_*` 三表 | 小 |
| 录音需求（邮件草稿审批） | 草稿→审批→发送链 | dsh `approval/request` + FastAPI 审批接口 + 通知 | 新增，但两个基座都已备好钩子 |

## 5. 分阶段路线

**阶段 1 —— 骨架打通（2~3 周）**
1. backend 镜像集成 runtime-bin 单文件 dsh；FastAPI lifespan 里 spawn/回收 dsh 子进程（Python SDK）。
2. 自写 PG storage 插件（两表 + 官方契约测试跑通）。
3. MCP server 落地两个工具：`kb_search`、`kb_read_doc`（ACL 过滤内嵌）。
4. SSE 桥：`session.event` → 前端，按块渲染。
5. 验收：一个登录用户在前端提问，dsh 调 `kb_search` 命中其可读文档并流式作答，会话落 PG 可恢复。对应 PRD 6.1/6.2/6.6。

**阶段 2 —— 工具面 + 审批（3~4 周）**
1. 补齐 MCP 工具面：CRM 查询/写跟进、文档库检索、笔记读写（只读优先，写工具逐个过审批策略）。
2. 审批应答器插件 + FastAPI 审批接口 + 通知联动，落地邮件草稿审批链（9/14 录音一号需求）。
3. 身份注入插件 + `tools/pre-execute` 策略表（admin/普通用户工具白名单）。
4. `session_acl` 与团队可见开关接入，会话可分享给团队成员。对应 PRD 2.2、3、6.3、7。

**阶段 3 —— 高级能力（按需）**
1. Trajectory/多 Agent：评估 dsh 的 subagent 能力用于报告生成与研究工作台。
2. 报告生成迁移到 dsh headless（一次性任务天然契合）。
3. 反馈闭环（PRD 6.4）、chunk_questions 等现有增强全部经 MCP 工具接入。

## 6. 风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| dsh v0.1.x 接口破坏性变更 | 中 | 对接面收敛到 Python SDK 的 3 请求 4 通知 + `sessionPersistence` 5 方法 + `cordis.patch.yml` 一个条目；锁定版本升级需跑契约测试 |
| 事件非逐 token，流式体验变化 | 低 | 前端按块模拟打字机；或接受块渲染 |
| 单写者/并发模型与多用户会话冲突 | 低 | PG advisory lock 按 session 粒度；同一用户同会话串行本来就是产品语义 |
| dsh 子进程崩溃 | 中 | FastAPI lifespan 监督重启；会话在 PG，重启后 `open` 恢复；`flush` 语义保证最多丢 200ms 批窗口 |
| Node 依赖引入 Python 部署链 | 低 | runtime-bin 单文件自带 Node 闭包，无系统 Node 依赖 |
| BrowserAuth 无法关闭 | 低 | 网关模式下 dsh 不暴露 HTTP 面，该机制不启用 |

## 7. 部署变更

- `backend/Dockerfile`：增加 runtime-bin wheel 安装（单文件 dsh 可执行程序），其余不变。
- `docker-compose.prod.yml`：容器数量不变（postgres / backend / nginx）；backend 增加 `DSH_HOME` named volume（profile 配置与临时态）。
- 备份脚本：`pg_dump` 已覆盖新增 `session_*` 表，无需改动。
- 端口：对外仍只有 nginx 80；dsh 无任何对外监听。

## 8. 结论

四个未知数全部有解，且有两个超预期的好消息：

1. **PG storage 比预想更顺**——接口只有 5+4 个方法、写负载极低、官方契约测试可直接复用，是自写后端的最佳情况；
2. **审批外部化是一等扩展点**——9/14 录音里最大的一块新需求（邮件草稿审批链）在 dsh 侧零改造，`approval/request` 应答器 + 自动落日志的审计事件正好补齐 PRD 的 FR-AUD。

最大的两个注意点：assistant 事件按块而非逐 token（前端体验微调），以及 v0.1 阶段的接口稳定性（靠收敛对接面 + 锁版本控制）。建议按第 5 节阶段 1 立项启动。

## 9. 阶段 1 落地记录（2026-09，已验收）

阶段 1（骨架打通）已按第 5 节完成并验收：登录用户在前端提问，dsh 经 `kb_search` 命中其可读文档并流式作答，会话落 PG。本节记录**实际实现与上文方案的偏差**，后续阶段以本节为准。

### 9.1 实际落地形态

- **接入面**：Python SDK（stdio JSON-RPC），FastAPI 内嵌**每用户一个长驻 dsh 子进程**的进程池（`backend/app/services/dsh_bridge.py`，懒启动，shutdown 统一回收）。SDK 为同步 API，async 侧统一 `asyncio.to_thread` 包裹。
- **MCP server**：FastMCP streamable-http 挂在 `/api/mcp`（`backend/app/services/mcp_server.py`），首批工具 `kb_search` / `kb_read_doc`（只读，复用 rag.py 检索管线，ACL 在工具内按 user_id 强制）。
- **SSE 桥**：`POST /api/v1/chat/ask/agent/stream`，`session.event` 通知按块转发前端；`chat_sessions.dsh_session_id` 绑定 dsh 侧会话（init_db 幂等 ALTER 加列）。
- **PG storage 插件**：`@kbcrm/dsh-session-persistence-pg`（仓库 `dsh/session-persistence-pg/`，TS），实现 `sessionPersistence` 5+4 方法接缝，落库表 `dsh_session_headers` / `dsh_session_events`；vitest 契约测试 24 例通过。经 `dsh plugin --profile sdk add` 注册进 sdk profile，`base.yml` patch 禁用官方 jsonl 后端并启用 PG 后端。
- **验收结果**：后端 pytest 475 例通过；插件 vitest 24 例通过；开发机端到端冒烟（`dsh/smoke_pg.py`）通过。

### 9.2 与方案的主要偏差

1. **身份注入插件 → 每用户进程池替代**。方案第 3 节组件 #2（`agent/created` 身份注入插件 + `tools/pre-execute` 策略）没有实现。改为**每用户一个独立 dsh 进程**：进程启动时生成每用户 patch yml（`user_{id}.yml`），把携带该用户 dsh 专用 JWT（`aud=dsh-mcp`）的 MCP client 配置注入 profile。身份天然隔离在进程边界上，dsh 侧无需任何身份插件；ACL 强制点不变（MCP 工具内按令牌里的 user_id 过滤）。
2. **`session_acl` 表未建**。因为偏差 1——会话归属由进程边界 + `chat_sessions.dsh_session_id` 表达，PG 侧两张 `dsh_session_*` 表不挂 user 列。团队共享会话推迟到阶段 2 再评估（届时可在业务库侧做会话分享，不必动 dsh 存储）。
3. **模型走 llm-pi-ai 的 openai-completions 路由（路由名 `kbcrm`）**。每用户 patch 覆写 base bundle 里默认休眠的 llm-pi-ai 行，把 DB 注册的 chat 模型注册为 OpenAI 兼容路由；`baseURL`/`apiKey` 经 `!!js` 读进程环境变量（SDK 按进程注入），密钥不落 yml 文件。**`DSH_PROVIDER` 必须配 `kbcrm`**。
4. **SDK 无跨进程 resume → 会话轮换降级**。dsh 0.1.7 的 Python SDK 不支持在新进程里恢复既有会话（方案第 6 节"崩溃后 `open` 恢复"在 SDK 接入面不成立）。落地降级方案：后端重启后旧 dsh 会话自动轮换新 session id，并把近期对话历史并入首问作为上下文。会话事件仍完整落 PG，待阶段 2 评估 ACP 接入面（支持逐会话 attach）后解除该限制。

### 9.3 阶段 2 待办（更新）

1. **审批应答器插件**：`approval/request` 应答器回调 FastAPI 审批接口，落地邮件草稿审批链（9/14 录音一号需求）——方案不变，仍是最高优先级。
2. **MCP 写工具**：CRM 写跟进、笔记读写等，只读优先，写工具逐个过审批策略。
3. **ACP 切换评估**：评估从 Python SDK 切到 ACP stdio 接入面，解决跨进程 resume 限制（去掉会话轮换降级），并支持逐会话差异化工具集。
4. **MCP 令牌刷新机制**：现令牌写在每用户 patch yml 里随进程启动生效，长驻进程超过 `DSH_MCP_TOKEN_EXPIRE_MINUTES`（默认 7 天）后 MCP 调用 401，目前靠重建该用户 dsh 进程刷新；阶段 2 应做不重启进程的刷新（动态注入或短令牌 + 刷新钩子）。
5. 身份策略增强（可选）：若未来改为单进程多用户形态，再回到原方案的身份注入插件 + `tools/pre-execute` 策略表。

## 10. 阶段 2 ACP 切换落地记录（2026-09，已验收）

接入面从 Python SDK（stdio JSON-RPC 私有协议）切换到 **ACP v1**（`dsh --profile acp`，Python 官方客户端 `agent-client-protocol`，PyPI，pin `0.12.*`；dsh 侧 `@agentclientprotocol/sdk@1.4.0`）。9.3 待办第 3、4 项随之关闭。

### 10.1 与阶段 1 的差异

- **进程模型**：每用户一个 dsh 进程 → **单租户单 dsh ACP 进程**（`backend/app/services/acp_bridge.py`，懒启动、崩溃自愈、shutdown 时关 stdin 让 dsh 有界退出）。每用户 patch yml（`user_{id}.yml`）与令牌临期重建机制整体移除。
- **MCP 注入**：patch yml 静态声明 + Bearer 落盘 → **每会话 `session/new`/`session/resume` 的 `mcpServers` 参数动态挂载**（http 类型，headers 携带该用户**新签**的 dsh-mcp 令牌）。令牌不落盘；过期无需重建进程，下次会话激活自动换发（9.3 第 4 项解决）。
- **跨进程 resume**：SDK 不支持 → ACP `session/resume` + PG 持久化插件原生支持（9.3 第 3 项目标达成）。后端重启/进程崩溃后 resume 即恢复模型上下文；**resume 不回放历史 update**（历史展示靠 `chat_messages`，模型上下文由 dsh 从 `dsh_session_*` 表还原）。resume 要求 cwd 与创建时 realpath 一致，故所有会话共用 `DSH_WORKSPACE_ROOT` 作 cwd（不再按用户分目录）。resume 失败（会话不存在/cwd 漂移，含阶段 1 的 `u{id}_*` 旧会话）自动退回 `session/new` 并显式 UPDATE 回写 `chat_sessions.dsh_session_id`（流式生成器内 ORM 已 detach，不能改属性提交）。
- **模型路由**：llm-pi-ai kbcrm 路由（openai-completions）保留，变为**进程级静态 patch**（`acp-model.yml`，进程启动时按 DB 默认 chat 模型重写）+ 新增 `- id: acp` 行覆盖默认 provider/model（acp bundle 默认 deepseek-official/deepseek-v4-flash）。`DSH_PROVIDER` 配置项弃用（保留仅为兼容旧 .env）。注意 llm-pi-ai 拒绝未在 models 列表声明的模型 id，故 **DB 模型变更要进程重建后才生效**（崩溃自愈/后端重启时自然发生）。
- **权限应答**：ACP 的 `session/request_permission` 是 client 方向反向请求，必须应答——`mcp__kb__` 前缀（知识库只读工具）自动 `allow-once`，其余一律拒绝（优先 `reject-once`，无该选项则 cancelled）并记日志；写操作本就走后端 `agent_approvals` 审批链，不经 dsh 执行。fs/terminal 等 client 能力未声明，dsh 不会发起对应反向请求。
- **插件注册**：PG 会话持久化插件需另行注册进 **acp** profile（`dsh plugin --profile acp add link:...`，开发机已执行；生产由 docker-entrypoint.sh 幂等注册，标记文件改为 `.pg-plugin-installed-acp`）。`base.yml` patch 原样适用（acp = dsh-base + dsh-acp-app，目标行 id 都在 dsh-base）。
- **依赖**：`deepseek-harness-sdk`（vendor）不再安装进镜像，`backend/Dockerfile` 改为仅装 PyPI 的 `agent-client-protocol`；vendor 目录保留无害。

### 10.2 偏差与说明

1. **ACP 的 `session/set_config_option` 未使用**：模型选择在进程级 patch 固化（kbcrm 路由）。如需会话级切模型，可用该机制（会话响应的 `configOptions` 里有 `id: model` 的 select），暂不开放。
2. **阶段 1 存量会话不迁移**：旧 `u{id}_*` 会话的 cwd 是 `user_{id}` 子目录，与新统一 cwd 不匹配，resume 必失败 → 自动退回新会话（上下文丢失但流程不断）。
3. **SSE 帧格式不变**（meta/token/tool/done/error），前端零改动；ACP 事件映射：`agent_message_chunk`→token、`tool_call`→tool(start)、`tool_call_update`(completed/failed)→tool(done, is_error)，`usage_update`/plan/thought 不透传。

### 10.3 验收

- 冒烟脚本 `dsh/acp_smoke.py` 通过：session/new 挂 MCP（admin Bearer）→ prompt 触发 `mcp__kb__kb_search`（真实命中知识库 5 条）→ 杀进程（stdin EOF，rc=0）→ 新进程 `session/resume`（configOptions 显示路由恢复为 `["kbcrm","kimi-k2.6"]`）→ 追问验证上下文保留（正确答出上轮关键词「客户」）。
- 后端 pytest 516 例通过（`tests/test_acp_bridge.py` + `tests/test_agent_stream.py` 重写为假 ACP client，不起真实进程）。
