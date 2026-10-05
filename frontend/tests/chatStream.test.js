// chatStream.js 手撸的 SSE 流解析是本前端最容易出 bug 的地方：
// 帧跨 chunk 分割、心跳注释行、非 JSON 帧、错误响应，这里逐一钉住行为。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { askStream, agentAskStream } from '../src/api/chatStream.js'

// 把若干字符串片段伪装成 fetch Response 的分块 body
function fakeResponse(chunks, { ok = true, status = 200, json } = {}) {
  const encoded = chunks.map((c) => new TextEncoder().encode(c))
  let i = 0
  return {
    ok,
    status,
    json: json ?? (async () => { throw new Error('not json') }),
    body: {
      getReader: () => ({
        read: async () =>
          i < encoded.length ? { done: false, value: encoded[i++] } : { done: true, value: undefined },
      }),
    },
  }
}

// 逐条收集生成器产物
async function collect(gen) {
  const out = []
  for await (const item of gen) out.push(item)
  return out
}

// 401 分支会写 window.location.href，node 环境给个桩
beforeEach(() => {
  globalThis.window = { location: { href: '' } }
})

describe('chatStream SSE 帧解析', () => {
  it('单 chunk 内多帧：逐帧解析为 JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => fakeResponse([
      'data: {"type":"token","text":"你"}\n\ndata: {"type":"token","text":"好"}\n\ndata: {"type":"done"}\n\n',
    ])))
    const frames = await collect(askStream({ question: 'q' }))
    expect(frames).toEqual([
      { type: 'token', text: '你' },
      { type: 'token', text: '好' },
      { type: 'done' },
    ])
  })

  it('帧跨 chunk 分割：缓冲拼接后仍能完整解析', async () => {
    const frame = 'data: {"type":"token","text":"hello world"}\n\n'
    const cut = Math.floor(frame.length / 2)
    vi.stubGlobal('fetch', vi.fn(async () => fakeResponse([frame.slice(0, cut), frame.slice(cut)])))
    const frames = await collect(askStream({ question: 'q' }))
    expect(frames).toEqual([{ type: 'token', text: 'hello world' }])
  })

  it('多字节字符被 chunk 边界切断：TextDecoder stream 模式兜底', async () => {
    const bytes = new TextEncoder().encode('data: {"type":"token","text":"中文"}\n\n')
    const cut = bytes.length - 20 // 切在"中文"的多字节序列中间
    // fakeResponse 接收字符串，这里直接构造字节级 reader
    let i = 0
    const parts = [bytes.slice(0, cut), bytes.slice(cut)]
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () =>
            i < parts.length ? { done: false, value: parts[i++] } : { done: true, value: undefined },
        }),
      },
    })))
    const frames = await collect(askStream({ question: 'q' }))
    expect(frames).toEqual([{ type: 'token', text: '中文' }])
  })

  it('心跳注释行与非 JSON data 行被忽略', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => fakeResponse([
      ': ping\n\ndata: not-json\n\ndata: {"type":"done"}\n\n',
    ])))
    const frames = await collect(askStream({ question: 'q' }))
    expect(frames).toEqual([{ type: 'done' }])
  })

  it('空 data 行与末尾无 \\n\\n 的残余帧处理', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => fakeResponse([
      'data:\n\ndata: {"type":"done"}\n\ndata: {"type":"orphan"', // 末尾残帧（未闭合）应被丢弃
    ])))
    const frames = await collect(askStream({ question: 'q' }))
    expect(frames).toEqual([{ type: 'done' }])
  })

  it('带 Authorization 头（localStorage 有 token 时）', async () => {
    localStorage.setItem('token', 't-123')
    const fetchMock = vi.fn(async () => fakeResponse(['data: {"type":"done"}\n\n']))
    vi.stubGlobal('fetch', fetchMock)
    await collect(askStream({ question: 'q' }))
    const [, init] = fetchMock.mock.calls[0]
    expect(init.headers.Authorization).toBe('Bearer t-123')
    expect(init.headers.Accept).toBe('text/event-stream')
  })

  it('HTTP 错误：detail 为字符串时抛出 detail 内容', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => fakeResponse([], {
      ok: false,
      status: 500,
      json: async () => ({ detail: '服务器开小差' }),
    })))
    await expect(collect(askStream({ question: 'q' }))).rejects.toThrow('服务器开小差')
  })

  it('HTTP 错误：错误体非 JSON 时回退状态码提示', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => fakeResponse([], { ok: false, status: 502 })))
    await expect(collect(askStream({ question: 'q' }))).rejects.toThrow('请求失败（502）')
  })

  it('401：清本地登录态并跳登录页，随后抛出错误', async () => {
    localStorage.setItem('token', 't-123')
    localStorage.setItem('user', '{}')
    vi.stubGlobal('fetch', vi.fn(async () => fakeResponse([], {
      ok: false,
      status: 401,
      json: async () => ({ detail: '未登录' }),
    })))
    await expect(collect(askStream({ question: 'q' }))).rejects.toThrow('未登录')
    expect(localStorage.getItem('token')).toBeNull()
    expect(localStorage.getItem('user')).toBeNull()
    expect(window.location.href).toBe('/login')
  })
})

describe('agentAskStream', () => {
  it('打到 agent 端点且不带 kb_ids/file_ids/thinking', async () => {
    const fetchMock = vi.fn(async () => fakeResponse(['data: {"type":"done"}\n\n']))
    vi.stubGlobal('fetch', fetchMock)
    await collect(agentAskStream({ session_id: 7, question: 'q' }))
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/chat/ask/agent/stream')
    expect(JSON.parse(init.body)).toEqual({ session_id: 7, question: 'q' })
  })

  it('askStream 空数组参数被裁剪为 undefined', async () => {
    const fetchMock = vi.fn(async () => fakeResponse(['data: {"type":"done"}\n\n']))
    vi.stubGlobal('fetch', fetchMock)
    await collect(askStream({ question: 'q', kb_ids: [], file_ids: [] }))
    const [, init] = fetchMock.mock.calls[0]
    const body = JSON.parse(init.body)
    expect(body.kb_ids).toBeUndefined()
    expect(body.file_ids).toBeUndefined()
  })
})
