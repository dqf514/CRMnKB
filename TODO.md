# 系统 TODO

> 2026-10 整合版：本轮自有体检 + `claude-Check-Report.md` 两份报告交叉验证后合并去重。
> 按执行批次排列（批内按投入产出比），每条带文件:行号。做完即删条目。
>
> **进度：五批体检修复全部完成**（第一批数据安全 10 项、第二批静默失效 14 项、第三批出站边界与依赖 5 项、第四批健壮性/并发/UI 一致性、第五批测试与工程护栏）。全量 788 pytest 通过 / 0 警告，前端 lint 0 error + vitest 41 例 + build 通过。

## 遗留尾巴（下轮顺手做）

- [ ] 启动恢复无逐文档兜底，DB 抖动恢复 task 静默死亡（`main.py` `_recover_loop`/`ingestion.py` requeue 路径）：逐文档 try/except + 记 error_logs
- [ ] 422 字段级映射（`utils/applyFieldErrors.js`）目前只接了客户/任务表单，待接：Knowledge、admin/Users、admin/Groups、admin/LlmModels、admin/McpServers、admin/Skills、Profile、CustomerDetail 的跟进/商机/任务弹窗
- [ ] `today_overview`/`daily_report` 仍按 UTC 日界统计（晨报已改本地日界），如需全站统一把 `dashboard._local_today_range()` 推广过去
- [ ] `admin_system.system_overview` 磁盘统计仍测 `.anchor`（容器 overlay），与 monitoring 同源问题，改测 uploads 卷
- [ ] `vision_review_document` 未纳入解析租约（process_document 已加 DB 租约互斥），如需要可复用同一租约键
- [ ] 自动备份多 worker 并发去重留了注释（单 worker 安全），等统一引入 advisory lock 模式后对齐
- [ ] `emit()` 修复后 else 分支 `cur_len = blk_len` 仍少计种子长度（存量次要计数偏差，`ingestion.py` `_build_chunks`）
- [ ] 从旧版（root 容器）Docker 部署首次升级后需一次性 `compose run --rm --user root backend chown -R app:app /app/data`（已写进部署指南，此处备忘）

## 第五批 — 测试与工程护栏（已完成）

> 已全部落地：ACL 真实实现测试（`test_permissions_acl_real.py`，aiosqlite 内存库 14 例）、模型字段↔迁移块对照（`test_model_migration_guard.py`）、JSONB 持久化回归（`test_report_params_persistence.py`）、测试警告 48→0（另清 7 处测试期真实连 PG 的漏网路径）、前端 ESLint flat config（0 error）+ Vitest 41 例（chatStream 帧解析/各 utils/stores）、零测试服务补测（monitoring/maintenance/library_sync/backup/dashboard +25 例，tar 穿越防护确认）、结构化日志 request-id + /metrics（Prometheus 文本）、热查询复合索引 ×3、api_llm httpx 按事件循环池化、运行产物 acp-model.yml 移出 git、vendor SDK/dsh_bridge 壳/DSH_PROVIDER 死代码清理、nginx 安全头三枚（CSP 待定）、Python 锁文件 requirements-lock.txt（产出未切换）。
> 遗留：package-lock registry 混杂（285 npmmirror + 12 npmjs.org 的 @cropper/* + 1 条 cdn.sheetjs.com 预期例外，未批量改）；降级路径同步记 error_logs 未做。

## P2 — 低优改进

- [ ] 记忆去重命中不刷新 updated_at，高频旧记忆可能被 FIFO 误删（`memory.py:59-60`）；注入块未转义 `</user-memory>` 可闭合注入（`:121-132`）；agent 写入 chat_session_id 恒 NULL 与文档不符（`mcp_server.py:1298`）
- [ ] `_write_process_patch` 模型名未转义插 YAML（`acp_bridge.py:187`）；`conn.prompt` 无单轮上限（`:374`）
- [ ] 笔记本存文档 KB 只按 tenant 校验且文件 owner_id=NULL 成孤儿（`notebook.py:205-228`）
- [ ] 客户下拉 page_size:200 上限 → 远程搜索下拉（ReportsPanel/Tasks/CustomerDetail 多处）
- [ ] blob 响应拦截器取不到 detail；SSE 401 跳登录无提示（`api/index.js:31`、`chatStream.js:24`）
- [ ] ~~authStore 初始化 JSON.parse 无容错白屏~~（已修，`stores/auth.js` loadLocalUser 容错+清除坏数据）；admin 守卫仅读 localStorage（纵深问题，后端有鉴权）
- [ ] 视觉复核/PST 轮询卸载清理不彻底（`KbDetail.vue:422`、`stores/uploads.js:131`）；chatStream generator 无 finally reader.cancel()
- [ ] 备份 RETENTION=10 硬编码（`backup.py:18`）；`crm_search_customers` LIKE 未转义通配符（`mcp_server.py:360`）
- [ ] 全局搜索「报告」跳转不带 `?report=id`（`GlobalSearch.vue:64`）
- [ ] mail_draft_create 工具名「草稿」执行器实际直发——审批文案误导（`agent_approvals.py:162-176`）；init_db 失败被吞带未迁移 schema 启动（`main.py` lifespan）
- [ ] ALTER COLUMN TYPE TIMESTAMP 无幂等保护每次启动执行（`database.py:120-122`）

## 规划中的能力（既有方向，未启动）

### 安全与合规
- [ ] SSO / LDAP / OAuth / MFA：对接 OIDC/SAML（钉钉/飞书/企业微信或 LDAP），可选/强制 MFA
- [ ] 内容安全（DLP）：上传时敏感信息扫描（身份证/手机号/密钥），告警或拦截
- [ ] 访问白名单：管理端可配 IP 白名单 / 租户级访问控制
- [ ] JWT 短效化 + httpOnly Cookie 迁移评估（long_lived 30 天令牌存 localStorage 窗口大）

### 可靠性 / 运维
- [ ] 健康检查深化：`/health` 加 DB / Ollama / LLM API / 嵌入可用性，区分 liveness/readiness
- [ ] 任务队列：摄取/报告/工作流从进程内 asyncio 迁到 Redis/ARQ 持久队列（重启不丢、可重试、可看进度）
- [ ] 多 worker 支持：LLM 缓存、MCP 池、登录限流共享化（Redis）——目前为进程内状态
- [ ] 日志治理：结构化 JSON 日志 + 日志轮转/聚合

### 协作 / 工作流
- [ ] 文档评论/批注：KB 文档、切片上讨论
- [ ] 订阅 / 变更通知：关注某 KB/文档，内容更新时收到通知
- [ ] 群组级权限：按用户分组批量授权（已有 user_groups，权限体系可扩展）
- [ ] 审批发布流：草稿 → 审核 → 发布（金融/法规行业）

### CRM / 业务功能
- [ ] 销售漏斗看板（Kanban）：商机阶段拖拽
- [ ] 客户 360 时间线：跟进/商机/任务/文档统一时间线
- [ ] 邮箱集成：workflow send_email 之外，双向邮件、收件箱、模板管理
- [ ] 日历/日程：跟进任务与日历联动（iCal/Outlook）
- [ ] KPI 仪表盘：销售趋势、知识库使用量、LLM 用量首页看板

### UI/UX
- [ ] 表格增强：列显示/密度自定义、行内编辑、跨页全选、批量编辑
- [ ] 快捷键：聊天发送、新建、搜索等
- [ ] PWA / 安装到桌面：离线 + 桌面图标
- [ ] 空状态 / 引导：各模块空状态给"下一步动作"
- [ ] 实时通知：30s 轮询 → WebSocket/SSE 即时推送
- [ ] 可访问性（a11y）：键盘导航、ARIA、对比度
- [ ] i18n：多语言

### 架构 / 工程
- [ ] API 版本化 + 客户端生成：OpenAPI 客户端、路由版本化
- [ ] HTTPS / 多环境部署：nginx 反向代理 + 证书
- [ ] 性能规模化：向量量化（halfvec/int8）、分片、embedding 模型升级（百万文档目标）

### 权限体系延伸
- [ ] 报告纳入权限/分享（报告列表/详情/删除已按 owner 过滤，授权分享体系待设计）
- [ ] 笔记独立权限（当前继承 Notebook）
