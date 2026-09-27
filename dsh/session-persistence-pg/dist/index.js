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
import z from '@deepseek-ai/schemastery';
import pg from 'pg';
import { SessionPersistence, SessionPersistenceRevision, SessionAlreadyExistsError, SessionAlreadyOwnedError, SessionHandleClosedError, SessionOwnershipLostError, SessionPersistenceNotFoundError, SessionReadOnlyError, assertContiguous, assertStoredId, assertVersion, materializeAppendBatch, materializeCreateHeader, validateStoredEvents, } from '@deepseek-ai/dsh-session-persistence';
import { SessionLogOffset, } from '@deepseek-ai/dsh-session';
/** 活体写批窗口：路由进来的活体事件最多滞留 200ms 即落库（与官方后端一致）。 */
export const LIVE_WRITE_BATCH_MAX_DELAY_MS = 200;
/** schema 名白名单校验（防 SQL 注入，schema 会拼进 SQL 文本）。 */
const SCHEMA_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
/**
 * 递归清除对象中所有字符串里的 U+0000 空字节。
 *
 * PostgreSQL jsonb 不允许 JSON 文本含 `\u0000` 转义（报 unsupported Unicode
 * escape sequence）。空字节常来自知识库文档切片等上游文本，经 LLM 上下文
 * 进入会话事件后会导致整条 append 写库失败、turn 直接报错。
 * 必须在 JSON.stringify 之前对值清洗——对序列化后的文本做替换会误伤
 * `\\u0000` 这种用户有意写入的合法转义。
 */
export function stripNullChars(value) {
    if (typeof value === 'string')
        return value.replace(/\u0000/g, '');
    if (Array.isArray(value))
        return value.map(item => stripNullChars(item));
    if (value !== null && typeof value === 'object') {
        const out = {};
        for (const [key, item] of Object.entries(value))
            out[key] = stripNullChars(item);
        return out;
    }
    return value;
}
/**
 * PostgreSQL 持久化后端。以插件形式加载，注册为 `ctx.sessionPersistence`。
 * 会话惰性物化：create 后本进程立即可见，首个 append 或 flush 才写库；
 * 未物化即崩溃的会话从未存在过。
 */
class PgSessionPersistence extends SessionPersistence {
    config;
    static Config = z.object({
        databaseUrl: z.string().required(),
        schema: z.string(),
    });
    /** 后端诊断名；遮蔽 Service.name 但不改变服务键。 */
    name = 'session-persistence-pg';
    pool;
    schema;
    initTask;
    /** 每个会话的写者：`null` 标记所有权已声明但句柄还在构造中。 */
    writers = new Map();
    /** 所有打开的句柄；卸载时统一关闭。 */
    openHandles = new Set();
    /** 已创建未物化的会话表。 */
    pending = new Map();
    constructor(ctx, config) {
        super(ctx);
        this.config = config;
        this.schema = config.schema ?? 'public';
        if (!SCHEMA_NAME_PATTERN.test(this.schema)) {
            throw new Error(`session-persistence-pg: 非法 schema 名 ${JSON.stringify(this.schema)}`);
        }
        this.pool = new pg.Pool({ connectionString: config.databaseUrl });
        this.install(ctx);
    }
    /** 表头/事件表的限定名。 */
    get headersTable() {
        return `${this.schema}.dsh_session_headers`;
    }
    get eventsTable() {
        return `${this.schema}.dsh_session_events`;
    }
    /**
     * 安装活体事件路由：发布的会话事件按 session id 进入对应写句柄的缓冲，
     * 检查点（session/flush）先排空缓冲再触发句柄自身的耐久屏障；
     * 会话销毁时关闭句柄。卸载时关闭全部残留句柄。
     */
    install(ctx) {
        ctx.on('session/event', (session, event) => {
            this.writers.get(session.id)?.enqueueLive(event, (error) => {
                ctx.logger.warn(`session-persistence-pg: 会话 "${session.id}" 后台写失败（事件保留在缓冲中）: ${String(error)}`);
            });
        });
        ctx.on('session/flush', (session) => {
            const writer = this.writers.get(session.id);
            if (writer === null || writer === undefined)
                return undefined;
            return (async () => {
                await writer.drainLive();
                await writer.flush();
            })();
        });
        ctx.on('session/disposed', (session) => {
            const writer = this.writers.get(session.id);
            if (writer === null || writer === undefined)
                return;
            writer.close().catch((error) => {
                ctx.logger.warn(`session-persistence-pg: 会话 "${session.id}" 关闭前排空失败: ${String(error)}`);
            });
        });
        ctx.effect(() => async () => {
            const errors = [];
            for (const handle of [...this.openHandles]) {
                try {
                    await handle.close();
                }
                catch (error) {
                    errors.push(error);
                }
            }
            await this.pool.end();
            if (errors.length > 0)
                throw new AggregateError(errors, `${this.name} 句柄关闭失败`);
        }, 'session-persistence-pg teardown');
    }
    /** 幂等建表（首次使用时惰性执行一次）。 */
    ensureSchema() {
        return this.initTask ??= (async () => {
            await this.pool.query(`CREATE SCHEMA IF NOT EXISTS "${this.schema}"`);
            // SessionHeader 不可变内容整体存 JSONB；继承前缀长度与事件数为伴随元数据，不进事件流。
            await this.pool.query(`
        CREATE TABLE IF NOT EXISTS ${this.headersTable} (
          session_id TEXT PRIMARY KEY,
          header JSONB NOT NULL,
          inherited_event_count BIGINT NOT NULL DEFAULT 0,
          event_count BIGINT NOT NULL DEFAULT 0,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )`);
            await this.pool.query(`
        CREATE TABLE IF NOT EXISTS ${this.eventsTable} (
          session_id TEXT NOT NULL REFERENCES ${this.headersTable}(session_id) ON DELETE CASCADE,
          seq BIGINT NOT NULL,
          event JSONB NOT NULL,
          PRIMARY KEY (session_id, seq)
        )`);
        })();
    }
    // --- SessionPersistence 服务 API ---
    /**
     * 创建新会话并取得写所有权。惰性物化：本进程立即可见，
     * 物理行在首个 append 或 flush 时落库。
     */
    async create(header, options) {
        options?.signal?.throwIfAborted();
        const snapshot = materializeCreateHeader(header);
        // 与官方编码器一致的 seeded/继承前缀校验：seeded 必须给继承长度，非 seeded 必须不给（或为 0）。
        if (snapshot.isSeeded && options?.inheritedEventCount === undefined) {
            throw new Error(`session "${snapshot.id}": isSeeded 元数据必须携带 inheritedEventCount`);
        }
        if (!snapshot.isSeeded && options?.inheritedEventCount !== undefined && options.inheritedEventCount !== 0) {
            throw new Error(`session "${snapshot.id}": 非 seeded 会话不得携带 inheritedEventCount`);
        }
        const inheritedEventCount = SessionLogOffset(options?.inheritedEventCount ?? 0);
        // 同步登记抢占，保证并发 create 只有一个赢家。
        if (this.writers.has(snapshot.id))
            throw new SessionAlreadyExistsError(snapshot.id);
        this.writers.set(snapshot.id, null);
        this.pending.set(snapshot.id, { header: snapshot, inheritedEventCount });
        try {
            await this.ensureSchema();
            options?.signal?.throwIfAborted();
            const existing = await this.pool.query(`SELECT 1 FROM ${this.headersTable} WHERE session_id = $1`, [snapshot.id]);
            if (existing.rowCount !== 0)
                throw new SessionAlreadyExistsError(snapshot.id);
        }
        catch (error) {
            this.pending.delete(snapshot.id);
            this.writers.delete(snapshot.id);
            throw error;
        }
        // 咨询锁推迟到物化时获取：未物化的会话没有可争夺的持久构件。
        return this.adopt(new PgSessionHandle(this, snapshot.id, snapshot, 'write', {
            cursor: 0, materialized: false, inheritedEventCount,
        }));
    }
    /** 打开已存在的会话。`read` 不取所有权；`write` 原子抢占单写者所有权。 */
    async open(id, access, options) {
        options?.signal?.throwIfAborted();
        await this.ensureSchema();
        options?.signal?.throwIfAborted();
        const pendingEntry = this.pending.get(id);
        if (pendingEntry !== undefined) {
            if (access === 'write')
                throw new SessionAlreadyOwnedError(id);
            return this.adopt(new PgSessionHandle(this, id, pendingEntry.header, 'read', {
                cursor: 0, materialized: false, inheritedEventCount: pendingEntry.inheritedEventCount,
            }));
        }
        const row = await this.pool.query(`SELECT header, inherited_event_count FROM ${this.headersTable} WHERE session_id = $1`, [id]);
        if (row.rowCount === 0)
            throw new SessionPersistenceNotFoundError(id);
        const meta = row.rows[0].header;
        assertStoredId(id, meta);
        const inheritedEventCount = SessionLogOffset(Number(row.rows[0].inherited_event_count));
        if (access === 'read') {
            // 读句柄不取所有权，校验推迟到首次 read（与官方后端一致）。
            return this.adopt(new PgSessionHandle(this, id, meta, 'read', {
                cursor: 0, materialized: true, inheritedEventCount,
            }));
        }
        // 写打开：进程内声明 + 跨进程咨询锁，双重单写者保证。
        if (this.writers.has(id))
            throw new SessionAlreadyOwnedError(id);
        this.writers.set(id, null);
        let lockClient;
        try {
            lockClient = await this.pool.connect();
            const locked = await lockClient.query('SELECT pg_try_advisory_lock(hashtextextended($1, 0)) AS ok', [id]);
            if (locked.rows[0].ok !== true)
                throw new SessionAlreadyOwnedError(id);
            // 写打开必须校验整份已存日志（拒绝未知词汇表 / 不支持格式版本），并定位续写游标。
            assertVersion(meta);
            const events = await this.readEvents(id);
            validateStoredEvents(meta, events);
            return this.adopt(new PgSessionHandle(this, id, meta, 'write', {
                cursor: events.length, materialized: true, inheritedEventCount,
            }, lockClient));
        }
        catch (error) {
            if (lockClient !== undefined) {
                try {
                    await lockClient.query('SELECT pg_advisory_unlock(hashtextextended($1, 0))', [id]);
                }
                catch { /* 连接可能已坏 */ }
                lockClient.release();
            }
            this.writers.delete(id);
            throw error;
        }
    }
    /** 服务级耐久屏障：排空并物化所有活跃写句柄；并发关闭中的句柄视为已冲刷。 */
    async flush() {
        const failures = [];
        for (const handle of [...this.openHandles]) {
            if (handle.access !== 'write')
                continue;
            try {
                await handle.drainLive();
                await handle.flush();
            }
            catch (error) {
                // 并发关闭的句柄由 close 自身完成耐久，按契约视为已冲刷。
                if (error instanceof SessionHandleClosedError)
                    continue;
                failures.push(error);
            }
        }
        if (failures.length === 1)
            throw failures[0];
        if (failures.length > 1)
            throw new AggregateError(failures, 'session-persistence-pg: 部分会话冲刷失败');
    }
    /** 轻量观察一个会话（不读事件流）。 */
    async stat(id, options) {
        options?.signal?.throwIfAborted();
        await this.ensureSchema();
        const pendingEntry = this.pending.get(id);
        if (pendingEntry !== undefined) {
            return { header: pendingEntry.header, revision: SessionPersistenceRevision(`pg:pending:${id}`) };
        }
        const row = await this.pool.query(`SELECT header, event_count FROM ${this.headersTable} WHERE session_id = $1`, [id]);
        if (row.rowCount === 0)
            return undefined;
        return this.snapshotOf(row.rows[0].header, Number(row.rows[0].event_count));
    }
    /** 列出本进程可见的全部会话：已物化的行 + 本进程未物化的创建。 */
    async list(options) {
        const signal = options?.signal;
        signal?.throwIfAborted();
        await this.ensureSchema();
        const rows = await this.pool.query(`SELECT header, event_count FROM ${this.headersTable}`);
        const snapshots = rows.rows.map(row => this.snapshotOf(row.header, Number(row.event_count)));
        const listed = new Set(snapshots.map(snapshot => snapshot.header.id));
        for (const [id, entry] of this.pending) {
            if (!listed.has(id)) {
                snapshots.push({ header: entry.header, revision: SessionPersistenceRevision(`pg:pending:${id}`) });
            }
        }
        signal?.throwIfAborted();
        return snapshots;
    }
    /** stat/list 共用的快照构造：revision 取事件数，append 后必变、无写时稳定。 */
    snapshotOf(header, eventCount) {
        return {
            header,
            revision: SessionPersistenceRevision(`pg:${header.id}:${eventCount}`),
            eventCount,
        };
    }
    // --- 句柄侧内部接口（包级私有，供 PgSessionHandle 调用） ---
    /** 登记句柄：写句柄进入活体路由表，全部句柄纳入卸载清扫。 */
    adopt(handle) {
        this.openHandles.add(handle);
        if (handle.access === 'write')
            this.writers.set(handle.id, handle);
        return handle;
    }
    /** 读取一个会话的完整已提交事件流（按 seq 升序）。 */
    async readEvents(id) {
        const rows = await this.pool.query(`SELECT event FROM ${this.eventsTable} WHERE session_id = $1 ORDER BY seq`, [id]);
        return rows.rows.map(row => row.event);
    }
    /** 读取一段已提交事件切片。 */
    async readSlice(id, offset, length) {
        const rows = await this.pool.query(`SELECT event FROM ${this.eventsTable} WHERE session_id = $1 ORDER BY seq LIMIT $2 OFFSET $3`, [id, length, offset]);
        return rows.rows.map(row => row.event);
    }
    /**
     * 为句柄争取跨进程写锁（物化路径用）。失败即所有权丢失。
     * @returns 持有锁的专用连接（句柄关闭时归还）。
     */
    async acquireWriteLock(id) {
        const client = await this.pool.connect();
        try {
            const locked = await client.query('SELECT pg_try_advisory_lock(hashtextextended($1, 0)) AS ok', [id]);
            if (locked.rows[0].ok !== true)
                throw new SessionOwnershipLostError(id);
            return client;
        }
        catch (error) {
            client.release();
            throw error;
        }
    }
    /**
     * 物化一个 created 会话：插入表头行与首批事件（单事务）。
     * @throws {SessionAlreadyExistsError} 他进程抢先物化了同一 id。
     */
    async materialize(header, inheritedEventCount, events) {
        const client = await this.pool.connect();
        try {
            await client.query('BEGIN');
            const inserted = await client.query(`INSERT INTO ${this.headersTable} (session_id, header, inherited_event_count, event_count)
         VALUES ($1, $2, $3, $4) ON CONFLICT (session_id) DO NOTHING`, [header.id, JSON.stringify(stripNullChars(header)), inheritedEventCount, events.length]);
            if (inserted.rowCount === 0) {
                throw new SessionAlreadyExistsError(header.id);
            }
            await this.insertEvents(client, header.id, events);
            await client.query('COMMIT');
            this.pending.delete(header.id);
        }
        catch (error) {
            try {
                await client.query('ROLLBACK');
            }
            catch { /* 连接可能已坏 */ }
            throw error;
        }
        finally {
            client.release();
        }
    }
    /** 向已物化会话追加一批事件并推进事件计数（单事务）。 */
    async appendEvents(id, events) {
        const client = await this.pool.connect();
        try {
            await client.query('BEGIN');
            await this.insertEvents(client, id, events);
            await client.query(`UPDATE ${this.headersTable} SET event_count = event_count + $2 WHERE session_id = $1`, [id, events.length]);
            await client.query('COMMIT');
        }
        catch (error) {
            try {
                await client.query('ROLLBACK');
            }
            catch { /* 连接可能已坏 */ }
            throw error;
        }
        finally {
            client.release();
        }
    }
    /** 单语句多行 INSERT 一批事件。 */
    async insertEvents(client, id, events) {
        if (events.length === 0)
            return;
        const values = [];
        const tuples = [];
        for (const [index, event] of events.entries()) {
            values.push(id, event.seq, JSON.stringify(stripNullChars(event)));
            tuples.push(`($${index * 3 + 1}, $${index * 3 + 2}, $${index * 3 + 3})`);
        }
        await client.query(`INSERT INTO ${this.eventsTable} (session_id, seq, event) VALUES ${tuples.join(', ')}`, values);
    }
    /** 句柄关闭时的后端簿记：释放进程内声明与句柄登记。 */
    releaseHandle(handle, materialized) {
        if (!materialized)
            this.pending.delete(handle.id);
        this.writers.delete(handle.id);
        this.openHandles.delete(handle);
    }
}
/**
 * 一个打开的会话通道。单所有者状态：read 不回退、write 可读己写、
 * close 幂等且不可取消；关闭后一切操作抛 SessionHandleClosedError。
 *
 * 所有写类操作（直接 append/flush、活体排空、close）都经过同一条
 * mutation 链串行化，活体路由与直接调用不会交错写库。
 */
class PgSessionHandle {
    backend;
    id;
    header;
    access;
    state;
    lockClient;
    closed = false;
    closing;
    ownershipLost = false;
    /** mutation 串行链：链自身永不拒绝（每轮的拒绝已由调用方观察）。 */
    chain = Promise.resolve();
    /** 路由进来的活体事件缓冲（持久化拥有的副本）。 */
    buffered = [];
    batchTimer;
    drainPaused = false;
    draining;
    constructor(backend, id, header, access, state, 
    /** 持有咨询锁的专用连接（仅写句柄物化后非空）。 */
    lockClient) {
        this.backend = backend;
        this.id = id;
        this.header = header;
        this.access = access;
        this.state = state;
        this.lockClient = lockClient;
        this.watchLockClient();
    }
    get inheritedEventCount() {
        return this.state.inheritedEventCount;
    }
    /** 读取日志切片；切片是合法前缀段，重复读不回退。 */
    async read(offset = 0, length) {
        this.assertOpen('read');
        if (!Number.isSafeInteger(offset) || offset < 0) {
            throw new TypeError(`read offset must be a non-negative safe integer, got ${String(offset)}`);
        }
        const limit = length ?? Number.MAX_SAFE_INTEGER;
        if (!Number.isSafeInteger(limit) || limit < 0) {
            throw new TypeError(`read length must be a non-negative safe integer, got ${String(length)}`);
        }
        // 未物化的 created 会话日志为空（首个 append 即物化）。
        if (!this.state.materialized)
            return { eventState: 'detached', events: [] };
        assertVersion(this.header);
        const events = await this.backend.readSlice(this.id, offset, limit);
        // 读路径 fail-closed：返回前校验词汇表与记录结构（就地 adopt + 冻结）。
        const validated = validateStoredEvents(this.header, events);
        // 每次读都是独立 JSON 解析出的副本，按“detached（调用方独占）”如实上报。
        return { eventState: 'detached', events: validated };
    }
    /** 追加连续批次。本后端 append 即在事务内持久化，返回即可被读到。 */
    async append(events) {
        this.assertOpen('append');
        const batch = materializeAppendBatch(events);
        return this.enqueueChain(async () => {
            this.assertWritable('append');
            await this.persistContiguous(batch);
        });
    }
    /**
     * 耐久屏障：append 已事务化持久化，这里只需把"空 created 会话"物化，
     * 使其对他进程可枚举。
     */
    async flush() {
        this.assertOpen('flush');
        return this.enqueueChain(async () => {
            this.assertWritable('flush');
            if (this.state.materialized)
                return;
            await this.ensureLock();
            await this.backend.materialize(this.header, this.state.inheritedEventCount, []);
            this.state.materialized = true;
        });
    }
    /**
     * 释放句柄：先把路由缓冲排空（其他纤维可能仍在发布事件，故循环排空），
     * 再释放咨询锁与所有权；未物化的创建随之抹除。幂等、不可取消。
     */
    close() {
        return this.closing ??= (async () => {
            this.closed = true;
            let drainFailure;
            for (;;) {
                try {
                    await this.drainLive();
                }
                catch (error) {
                    drainFailure = error;
                    break;
                }
                await this.chain;
                if (this.buffered.length === 0)
                    break;
            }
            await this.chain;
            const failures = [];
            if (drainFailure !== undefined) {
                failures.push(drainFailure instanceof Error ? drainFailure : new Error(String(drainFailure)));
            }
            const client = this.lockClient;
            this.lockClient = undefined;
            if (client !== undefined) {
                try {
                    await client.query('SELECT pg_advisory_unlock(hashtextextended($1, 0))', [this.id]);
                }
                catch (error) {
                    failures.push(error instanceof Error ? error : new Error(String(error)));
                }
                client.release();
            }
            this.backend.releaseHandle(this, this.state.materialized);
            if (failures.length > 1)
                throw new AggregateError(failures, `session "${this.id}": close 排空与放锁均失败`);
            if (failures[0] !== undefined)
                throw failures[0];
        })();
    }
    async [Symbol.asyncDispose]() {
        await this.close();
    }
    // --- 活体事件路由（仅写句柄；由后端的 session/* 监听器调用） ---
    /**
     * 缓冲一个已发布的活体会话事件；空闲时启动 200ms 批窗口，到点排空。
     * @param event - 活体事件（此处做持久化拥有的拷贝）。
     * @param reportBackgroundFailure - 批窗口到点排空失败的观察者（事件保留待重试）。
     */
    enqueueLive(event, reportBackgroundFailure) {
        if (this.closed)
            return;
        this.buffered.push(structuredClone(event));
        if (this.batchTimer !== undefined || this.drainPaused)
            return;
        this.batchTimer = setTimeout(() => {
            this.batchTimer = undefined;
            this.drainLive().catch(reportBackgroundFailure);
        }, LIVE_WRITE_BATCH_MAX_DELAY_MS);
        this.batchTimer.unref?.();
    }
    /**
     * 把活体缓冲排进 mutation 链；并发调用并入同一次排空，
     * 失败时批次按序保留，session/flush 会大声重试（检查点失败即阻止）。
     */
    drainLive() {
        return this.draining ??= this.drainBuffered().finally(() => {
            this.draining = undefined;
        });
    }
    async drainBuffered() {
        if (this.batchTimer !== undefined) {
            clearTimeout(this.batchTimer);
            this.batchTimer = undefined;
        }
        this.drainPaused = false;
        while (this.buffered.length > 0) {
            // 在链轮次内截取缓冲：排空期间到达的事件并入下一轮，保持顺序。
            await this.enqueueChain(async () => {
                const batch = this.buffered.splice(0);
                try {
                    await this.persistContiguous(materializeAppendBatch(batch));
                }
                catch (error) {
                    this.buffered = batch.concat(this.buffered);
                    this.drainPaused = true;
                    throw error;
                }
            });
        }
    }
    // --- 内部 ---
    /** 把一步写操作排进 mutation 链；调用方观察自己的拒绝，链自身不拒绝。 */
    enqueueChain(op) {
        const turn = this.chain.then(op);
        this.chain = turn.then(() => undefined, () => undefined);
        return turn;
    }
    /** 共享的耐久追加体：可写性、连续性、物化/追加落库、推进游标。 */
    async persistContiguous(batch) {
        if (this.access !== 'write')
            throw new SessionReadOnlyError(this.id, 'append');
        assertContiguous(this.id, batch, this.state.cursor);
        if (batch.length === 0)
            return;
        if (!this.state.materialized) {
            // 首个写：先抢跨进程锁，再物化表头 + 事件（单事务）。
            await this.ensureLock();
            await this.backend.materialize(this.header, this.state.inheritedEventCount, batch);
            this.state.materialized = true;
        }
        else {
            await this.backend.appendEvents(this.id, batch);
        }
        this.state.cursor += batch.length;
    }
    /** 惰性获取跨进程写锁；失败即所有权丢失。 */
    async ensureLock() {
        if (this.lockClient !== undefined)
            return;
        if (this.ownershipLost)
            throw new SessionOwnershipLostError(this.id);
        try {
            this.lockClient = await this.backend.acquireWriteLock(this.id);
        }
        catch (error) {
            this.ownershipLost = true;
            throw error;
        }
        this.watchLockClient();
    }
    /** 锁连接断开 = 跨进程所有权永久丢失：后续写操作拒绝，提示重开。 */
    watchLockClient() {
        this.lockClient?.on('error', () => {
            this.ownershipLost = true;
        });
    }
    assertOpen(operation) {
        if (this.closed)
            throw new SessionHandleClosedError(this.id, operation);
    }
    assertWritable(operation) {
        if (this.access === 'read')
            throw new SessionReadOnlyError(this.id, operation);
        if (this.ownershipLost)
            throw new SessionOwnershipLostError(this.id);
    }
}
export default PgSessionPersistence;
