/**
 * PostgreSQL 会话持久化后端：实现 dsh 的 `ctx.sessionPersistence` 接缝。
 *
 * 存储模型：
 * - `dsh_session_headers`：一会话一行，不可变 SessionHeader 整体存 JSONB，
 *   另存 fork 继承前缀长度（inherited_event_count）与已提交事件数（event_count，
 *   供 stat/list 廉价读取并充当 revision 令牌）。
 * - `dsh_session_events`：append-only 事件流，主键 (session_id, seq) 保证连续不可改写。
 *
 * 单写者语义：写句柄物化/打开时在专用连接上持有
 * `pg_advisory_lock(hashtextextended(session_id, 0))`（会话级，进程崩溃自动释放），
 * 冲突按 `@deepseek-ai/dsh-session-persistence` 的错误模型抛出。
 *
 * 活体事件路由（与官方 jsonl 后端一致）：agent 生命周期发布会话后，
 * `session/event` 事件由本后端按 session id 路由进活跃写句柄的缓冲，
 * 200ms 批窗口或 `session/flush` 屏障（检查点策略调用）触发落库；
 * `session/disposed` 时关闭句柄（close 自身会把残余缓冲排空）。
 * append 在事务内即时持久化，因此 flush 仅为"空会话物化"屏障（接口允许）。
 * @module @kbcrm/dsh-session-persistence-pg
 */
import { Context } from '@deepseek-ai/cordis';
import z from '@deepseek-ai/schemastery';
import pg from 'pg';
import { SessionPersistence, type SessionAccess, type SessionHandle, type SessionHandleReadResult, type SessionPersistenceCreateOptions, type SessionPersistenceListOptions, type SessionPersistenceOpenOptions, type SessionPersistenceSnapshot, type SessionPersistenceStatOptions } from '@deepseek-ai/dsh-session-persistence';
import { type SessionEvent, type SessionHeader, type SessionId, type SessionLogOffset as SessionLogOffsetType } from '@deepseek-ai/dsh-session';
/** 活体写批窗口：路由进来的活体事件最多滞留 200ms 即落库（与官方后端一致）。 */
export declare const LIVE_WRITE_BATCH_MAX_DELAY_MS = 200;
/** 插件配置。 */
export interface Config {
    /** PostgreSQL 连接串，如 `postgresql://crm:crm123@localhost:5432/crmnkb`。可用 !!js 读环境变量。 */
    databaseUrl: string;
    /**
     * 表所在 schema（默认 `public`）。仅允许标识符字符，防止注入。
     * 表名固定带 `dsh_` 前缀，避免与业务表混淆。
     */
    schema?: string;
}
/**
 * 递归清除对象中所有字符串里的 U+0000 空字节。
 *
 * PostgreSQL jsonb 不允许 JSON 文本含 `\u0000` 转义（报 unsupported Unicode
 * escape sequence）。空字节常来自知识库文档切片等上游文本，经 LLM 上下文
 * 进入会话事件后会导致整条 append 写库失败、turn 直接报错。
 * 必须在 JSON.stringify 之前对值清洗——对序列化后的文本做替换会误伤
 * `\\u0000` 这种用户有意写入的合法转义。
 */
export declare function stripNullChars<T>(value: T): T;
/** 写句柄的内部状态。 */
interface WriteState {
    /** 下一个待写 seq（已提交日志长度）。 */
    cursor: number;
    /** 是否已物化（headers 表已有行）。create 惰性物化：首个 append 或 flush 才落库。 */
    materialized: boolean;
    /** fork 继承前缀长度，随表头行持久化，不进事件流。 */
    inheritedEventCount: SessionLogOffsetType;
}
/**
 * PostgreSQL 持久化后端。以插件形式加载，注册为 `ctx.sessionPersistence`。
 * 会话惰性物化：create 后本进程立即可见，首个 append 或 flush 才写库；
 * 未物化即崩溃的会话从未存在过。
 */
declare class PgSessionPersistence extends SessionPersistence {
    config: Config;
    static Config: z<Config>;
    /** 后端诊断名；遮蔽 Service.name 但不改变服务键。 */
    readonly name = "session-persistence-pg";
    private readonly pool;
    private readonly schema;
    private initTask;
    /** 每个会话的写者：`null` 标记所有权已声明但句柄还在构造中。 */
    private readonly writers;
    /** 所有打开的句柄；卸载时统一关闭。 */
    private readonly openHandles;
    /** 已创建未物化的会话表。 */
    private readonly pending;
    constructor(ctx: Context, config: Config);
    /** 表头/事件表的限定名。 */
    private get headersTable();
    private get eventsTable();
    /**
     * 安装活体事件路由：发布的会话事件按 session id 进入对应写句柄的缓冲，
     * 检查点（session/flush）先排空缓冲再触发句柄自身的耐久屏障；
     * 会话销毁时关闭句柄。卸载时关闭全部残留句柄。
     */
    private install;
    /** 幂等建表（首次使用时惰性执行一次）。 */
    private ensureSchema;
    /**
     * 创建新会话并取得写所有权。惰性物化：本进程立即可见，
     * 物理行在首个 append 或 flush 时落库。
     */
    create(header: SessionHeader, options?: SessionPersistenceCreateOptions): Promise<SessionHandle>;
    /** 打开已存在的会话。`read` 不取所有权；`write` 原子抢占单写者所有权。 */
    open(id: SessionId, access: SessionAccess, options?: SessionPersistenceOpenOptions): Promise<SessionHandle>;
    /** 服务级耐久屏障：排空并物化所有活跃写句柄；并发关闭中的句柄视为已冲刷。 */
    flush(): Promise<void>;
    /** 轻量观察一个会话（不读事件流）。 */
    stat(id: SessionId, options?: SessionPersistenceStatOptions): Promise<SessionPersistenceSnapshot | undefined>;
    /** 列出本进程可见的全部会话：已物化的行 + 本进程未物化的创建。 */
    list(options?: SessionPersistenceListOptions): Promise<readonly SessionPersistenceSnapshot[]>;
    /** stat/list 共用的快照构造：revision 取事件数，append 后必变、无写时稳定。 */
    private snapshotOf;
    /** 登记句柄：写句柄进入活体路由表，全部句柄纳入卸载清扫。 */
    private adopt;
    /** 读取一个会话的完整已提交事件流（按 seq 升序）。 */
    private readEvents;
    /** 读取一段已提交事件切片。 */
    readSlice(id: SessionId, offset: number, length: number): Promise<SessionEvent[]>;
    /**
     * 为句柄争取跨进程写锁（物化路径用）。失败即所有权丢失。
     * @returns 持有锁的专用连接（句柄关闭时归还）。
     */
    acquireWriteLock(id: SessionId): Promise<pg.PoolClient>;
    /**
     * 物化一个 created 会话：插入表头行与首批事件（单事务）。
     * @throws {SessionAlreadyExistsError} 他进程抢先物化了同一 id。
     */
    materialize(header: SessionHeader, inheritedEventCount: SessionLogOffsetType, events: readonly SessionEvent[]): Promise<void>;
    /** 向已物化会话追加一批事件并推进事件计数（单事务）。 */
    appendEvents(id: SessionId, events: readonly SessionEvent[]): Promise<void>;
    /** 单语句多行 INSERT 一批事件。 */
    private insertEvents;
    /** 句柄关闭时的后端簿记：释放进程内声明与句柄登记。 */
    releaseHandle(handle: PgSessionHandle, materialized: boolean): void;
}
/**
 * 一个打开的会话通道。单所有者状态：read 不回退、write 可读己写、
 * close 幂等且不可取消；关闭后一切操作抛 SessionHandleClosedError。
 *
 * 所有写类操作（直接 append/flush、活体排空、close）都经过同一条
 * mutation 链串行化，活体路由与直接调用不会交错写库。
 */
declare class PgSessionHandle implements SessionHandle {
    private readonly backend;
    readonly id: SessionId;
    readonly header: SessionHeader;
    readonly access: SessionAccess;
    private readonly state;
    /** 持有咨询锁的专用连接（仅写句柄物化后非空）。 */
    private lockClient?;
    private closed;
    private closing;
    private ownershipLost;
    /** mutation 串行链：链自身永不拒绝（每轮的拒绝已由调用方观察）。 */
    private chain;
    /** 路由进来的活体事件缓冲（持久化拥有的副本）。 */
    private buffered;
    private batchTimer;
    private drainPaused;
    private draining;
    constructor(backend: PgSessionPersistence, id: SessionId, header: SessionHeader, access: SessionAccess, state: WriteState, 
    /** 持有咨询锁的专用连接（仅写句柄物化后非空）。 */
    lockClient?: pg.PoolClient | undefined);
    get inheritedEventCount(): SessionLogOffsetType;
    /** 读取日志切片；切片是合法前缀段，重复读不回退。 */
    read(offset?: number, length?: number): Promise<SessionHandleReadResult>;
    /** 追加连续批次。本后端 append 即在事务内持久化，返回即可被读到。 */
    append(events: readonly SessionEvent[]): Promise<void>;
    /**
     * 耐久屏障：append 已事务化持久化，这里只需把"空 created 会话"物化，
     * 使其对他进程可枚举。
     */
    flush(): Promise<void>;
    /**
     * 释放句柄：先把路由缓冲排空（其他纤维可能仍在发布事件，故循环排空），
     * 再释放咨询锁与所有权；未物化的创建随之抹除。幂等、不可取消。
     */
    close(): Promise<void>;
    [Symbol.asyncDispose](): Promise<void>;
    /**
     * 缓冲一个已发布的活体会话事件；空闲时启动 200ms 批窗口，到点排空。
     * @param event - 活体事件（此处做持久化拥有的拷贝）。
     * @param reportBackgroundFailure - 批窗口到点排空失败的观察者（事件保留待重试）。
     */
    enqueueLive(event: SessionEvent, reportBackgroundFailure: (error: unknown) => void): void;
    /**
     * 把活体缓冲排进 mutation 链；并发调用并入同一次排空，
     * 失败时批次按序保留，session/flush 会大声重试（检查点失败即阻止）。
     */
    drainLive(): Promise<void>;
    private drainBuffered;
    /** 把一步写操作排进 mutation 链；调用方观察自己的拒绝，链自身不拒绝。 */
    private enqueueChain;
    /** 共享的耐久追加体：可写性、连续性、物化/追加落库、推进游标。 */
    private persistContiguous;
    /** 惰性获取跨进程写锁；失败即所有权丢失。 */
    private ensureLock;
    /** 锁连接断开 = 跨进程所有权永久丢失：后续写操作拒绝，提示重开。 */
    private watchLockClient;
    private assertOpen;
    private assertWritable;
}
export default PgSessionPersistence;
