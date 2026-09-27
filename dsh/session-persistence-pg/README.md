# @kbcrm/dsh-session-persistence-pg

DeepSeek Harness（dsh）的 PostgreSQL 会话持久化后端插件，实现 `ctx.sessionPersistence` 接缝
（`@deepseek-ai/dsh-session-persistence` 的 5 方法服务 + append-only 会话句柄）。
用于把 dsh agent 会话的表头与事件流落到业务 PostgreSQL，替代默认的 JSONL 文件后端。

## 存储模型

插件首次使用时幂等建表（`CREATE TABLE IF NOT EXISTS`），表名固定 `dsh_` 前缀，不与业务表混淆：

- `dsh_session_headers(session_id TEXT PRIMARY KEY, header JSONB NOT NULL, inherited_event_count BIGINT NOT NULL DEFAULT 0, event_count BIGINT NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`
  —— 不可变 SessionHeader 整体存 JSONB；fork 继承前缀长度与已提交事件数（stat/list 与 revision 令牌用）为伴随列。
- `dsh_session_events(session_id TEXT NOT NULL REFERENCES dsh_session_headers(session_id) ON DELETE CASCADE, seq BIGINT NOT NULL, event JSONB NOT NULL, PRIMARY KEY (session_id, seq))`
  —— append-only 事件流。

语义要点（与官方 jsonl 后端对齐）：

- **惰性物化**：`create` 后本进程立即可见（stat/list/read），首个 append 或 flush 才落库；未物化即关闭的会话被抹除。
- **单写者**：写打开/物化时在专用连接上持有 `pg_advisory_lock(hashtextextended(session_id, 0))`（会话级，进程崩溃自动释放）；冲突抛 `SessionAlreadyOwnedError`，锁连接断开后的写操作抛 `SessionOwnershipLostError`。
- **耐久模型**：append 在事务内即时持久化（批量 INSERT + 事件计数推进，单事务）；flush 只负责物化空会话（接口允许的 no-op 屏障）。
- **活体路由**：`session/event` 按 session id 路由进活跃写句柄缓冲（200ms 批窗口），`session/flush`（检查点策略）先排空再屏障，`session/disposed` 关闭句柄。
- **fail-closed**：读路径与写打开都经共享校验（`validateStoredEvents` / `assertVersion`），未知事件类型或不支持的格式版本拒绝解释，绝不误读。

## 配置

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `databaseUrl` | 是 | PostgreSQL 连接串，如 `postgresql://crm:crm123@localhost:5432/crmnkb`。patch yml 里可用 `!!js process.env.XXX` 读环境变量。 |
| `schema` | 否 | 表所在 schema，默认 `public`。仅允许标识符字符（防注入）。 |

## 安装到 dsh profile（方案甲：npm 官方 CLI + 外部插件）

```bash
# 1. 安装官方 CLI（项目内或全局均可）
npm install @deepseek-ai/dsh@0.1.7-rc.2

# 2. 构建本插件
cd session-persistence-pg && npm install && npm run build

# 3. 把插件装进 profile（profile 是 pnpm 管理的包目录；
#    dsh 的运行时拦截会把插件对 @deepseek-ai/* 的引用路由到 CLI 安装内副本，类身份一致）
export DSH_HOME=/path/to/dsh-home
dsh plugin --profile sdk add /d/AI/CRMnKB/dsh/session-persistence-pg

# 4. 用 patch 把默认 jsonl 后端换掉（patch 不允许改已有行的 name，故禁用原行 + insert 新行）
dsh --profile <name> --patch /d/AI/CRMnKB/dsh/patches/pg-session-persistence.patch.yml
```

patch 片段（`../patches/pg-session-persistence.patch.yml`）：

```yaml
- id: session-persistence-jsonl
  disabled: true

- insert:
    - id: session-persistence-pg
      name: '@kbcrm/dsh-session-persistence-pg'
      config:
        databaseUrl: !!js process.env.DSH_PG_URL ?? 'postgresql://crm:crm123@localhost:5432/crmnkb'
```

Python SDK 侧（Windows 下 `dsh_bin` 必须指 `.cmd` shim；`dsh_home`/`patches` 生效）：

```python
DeepSeekHarnessConfig(
    dsh_bin=r"D:\AI\CRMnKB\dsh\runtime\node_modules\.bin\dsh.cmd",
    dsh_home=r"D:\AI\CRMnKB\dsh\home",
    profile="sdk",
    patches=(r"D:\AI\CRMnKB\dsh\patches\pg-session-persistence.patch.yml",),
    env={"DSH_PG_URL": "postgresql://crm:crm123@localhost:5432/crmnkb"},
)
```

## 测试

```bash
# 需要一个可写的开发库（默认 postgresql://crm:crm123@localhost:5433/crmnkb，
# 可用 DSH_PG_TEST_URL 覆盖）。每个用例跑在独立临时 schema 上，跑完即删。
npm test
```

测试复用 dsh 官方共享契约套件（`tests/vendor/persistence-contract.ts`，vendored 自
`deepseek-harness@0.1.7-rc.2` 的 `packages/session/session-persistence/tests/contract.ts`，MIT，
仅把服务定义包的相对导入换成已发布包名）——覆盖 create/open/句柄语义、单写者、惰性物化、
连续性、fail-closed 词汇表、freshness、stat/list revision 一致性；另有后端专属用例
（跨实例双写者冲突、格式版本闸、dsh_ 表前缀、schema 注入防护）。

## 已知限制

- 咨询锁键为 64 位哈希，理论上存在碰撞可能（表现为偶发 `SessionAlreadyOwnedError`），实际可忽略。
- 不做历史格式版本迁移：只读写当前 `SESSION_FORMAT_VERSION`（v4）；旧版本日志按官方错误模型拒绝。
- dsh 0.1.7-rc.2 的 SDK JSON-RPC 只有 initialize / session/prompt / shutdown，
  跨进程"恢复已存会话"不在 SDK 暴露面内（与后端无关，jsonl 后端行为相同）。
