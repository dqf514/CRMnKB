/**
 * PostgreSQL 后端的集成测试：复用 dsh 共享契约套件 + 后端专属用例。
 * 需要一个可写的开发库（默认本机 5433 的 crmnkb，见 ../docker-compose.dev.yml），
 * 每个契约用例跑在独立的临时 schema 上，跑完即删，不碰业务表。
 */

import { randomBytes } from 'node:crypto'
import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import pg from 'pg'
import { SESSION_FORMAT_VERSION, SessionId } from '@deepseek-ai/dsh-session'
import {
  SessionAlreadyOwnedError,
  SessionFormatUnsupportedError,
  type SessionPersistence,
} from '@deepseek-ai/dsh-session-persistence'
import PgSessionPersistence from '../src/index.ts'
import { meta, oneTurnLog, runPersistenceContract } from './vendor/persistence-contract.ts'

/** 开发库连接串；可用环境变量覆盖。 */
const DATABASE_URL = process.env.DSH_PG_TEST_URL ?? 'postgresql://crm:crm123@localhost:5433/crmnkb'

/** 每个后端实例一套独立 schema，保证“全新空存储”。 */
function makeSchemaName(): string {
  return `dsh_test_${randomBytes(4).toString('hex')}`
}

async function dropSchema(schema: string): Promise<void> {
  const client = new pg.Client({ connectionString: DATABASE_URL })
  await client.connect()
  try {
    await client.query(`DROP SCHEMA IF EXISTS "${schema}" CASCADE`)
  } finally {
    await client.end()
  }
}

/** 共享契约套件：create/open/句柄语义、单写者、惰性物化、fail-closed、freshness 等。 */
runPersistenceContract('pg', async () => {
  const schema = makeSchemaName()
  const instance = async (): Promise<{ persistence: SessionPersistence; dispose: () => Promise<void> }> => {
    const ctx = new Context()
    const fiber = await ctx.plugin(PgSessionPersistence, { databaseUrl: DATABASE_URL, schema })
    return {
      persistence: ctx.sessionPersistence,
      dispose: async () => { await fiber.dispose() },
    }
  }
  const primary = await instance()
  return {
    persistence: primary.persistence,
    dispose: async () => {
      await primary.dispose()
      await dropSchema(schema)
    },
    reopen: instance,
  }
})

/** 后端专属用例。 */
describe('session-persistence-pg 专属行为', () => {
  it('跨实例双写者冲突：第二个写打开被拒，锁在关闭后释放', async () => {
    const schema = makeSchemaName()
    const ctxA = new Context()
    const ctxB = new Context()
    try {
      await ctxA.plugin(PgSessionPersistence, { databaseUrl: DATABASE_URL, schema })
      await ctxB.plugin(PgSessionPersistence, { databaseUrl: DATABASE_URL, schema })
      const m = meta('cross-instance-owner', '/work')
      const writer = await ctxA.sessionPersistence.create(m)
      await writer.append(oneTurnLog())
      // 另一个实例（模拟另一个进程）抢占同一会话：咨询锁拒绝。
      await expect(ctxB.sessionPersistence.open(m.id, 'write')).rejects.toBeInstanceOf(SessionAlreadyOwnedError)
      // 读不受写所有权影响。
      const reader = await ctxB.sessionPersistence.open(m.id, 'read')
      expect((await reader.read()).events).toHaveLength(6)
      await reader.close()
      // 原写者关闭释放锁后，另一实例可接管续写。
      await writer.close()
      const taken = await ctxB.sessionPersistence.open(m.id, 'write')
      expect(taken.header.id).toBe(m.id)
      await taken.close()
    } finally {
      await ctxA.fiber.dispose()
      await ctxB.fiber.dispose()
      await dropSchema(schema)
    }
  })

  it('格式版本闸：存储的未来版本在写打开与读时拒绝', async () => {
    const schema = makeSchemaName()
    const ctx = new Context()
    try {
      await ctx.plugin(PgSessionPersistence, { databaseUrl: DATABASE_URL, schema })
      const m = meta('future-version', '/work')
      const writer = await ctx.sessionPersistence.create(m)
      await writer.append(oneTurnLog())
      await writer.close()
      // 直接篡改存储的版本号，模拟更新 harness 写入的日志。
      const client = new pg.Client({ connectionString: DATABASE_URL })
      await client.connect()
      await client.query(
        `UPDATE "${schema}".dsh_session_headers SET header = jsonb_set(header, '{version}', $2) WHERE session_id = $1`,
        [m.id, String(SESSION_FORMAT_VERSION + 1)])
      await client.end()

      await expect(ctx.sessionPersistence.open(m.id, 'write')).rejects.toBeInstanceOf(SessionFormatUnsupportedError)
      const reader = await ctx.sessionPersistence.open(m.id, 'read')
      await expect(reader.read()).rejects.toBeInstanceOf(SessionFormatUnsupportedError)
      await reader.close()
    } finally {
      await ctx.fiber.dispose()
      await dropSchema(schema)
    }
  })

  it('只建 dsh_ 前缀表，不碰业务表', async () => {
    const schema = makeSchemaName()
    const ctx = new Context()
    try {
      await ctx.plugin(PgSessionPersistence, { databaseUrl: DATABASE_URL, schema })
      await ctx.sessionPersistence.list()
      const client = new pg.Client({ connectionString: DATABASE_URL })
      await client.connect()
      const tables = await client.query(
        `SELECT tablename FROM pg_tables WHERE schemaname = $1 ORDER BY tablename`, [schema])
      await client.end()
      expect(tables.rows.map(row => row.tablename)).toEqual(['dsh_session_events', 'dsh_session_headers'])
    } finally {
      await ctx.fiber.dispose()
      await dropSchema(schema)
    }
  })

  it('非法 schema 名在启动时拒绝（防注入）', async () => {
    const ctx = new Context()
    await expect(
      ctx.plugin(PgSessionPersistence, { databaseUrl: DATABASE_URL, schema: 'x; DROP TABLE users' }),
    ).rejects.toThrow(/非法 schema/)
    await ctx.fiber.dispose()
  })
})
