import { describe, it, expect } from 'vitest'
import { usePagedFetch } from '../src/utils/usePagedFetch.js'

// 手动控制 resolve 时机的 deferred
function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe('usePagedFetch 防竞态', () => {
  it('正常返回：onData 收到响应，loading 回落', async () => {
    const { loading, run } = usePagedFetch()
    const d = deferred()
    const seen = []
    const p = run(() => d.promise, (res) => seen.push(res))
    expect(loading.value).toBe(true)
    d.resolve({ list: [1] })
    await p
    expect(seen).toEqual([{ list: [1] }])
    expect(loading.value).toBe(false)
  })

  it('旧响应后返回时被丢弃，loading 由最新请求管', async () => {
    const { loading, run } = usePagedFetch()
    const d1 = deferred()
    const d2 = deferred()
    const seen = []
    const p1 = run(() => d1.promise, (res) => seen.push(['old', res]))
    const p2 = run(() => d2.promise, (res) => seen.push(['new', res]))
    // 新请求先返回
    d2.resolve('new-res')
    await p2
    expect(loading.value).toBe(false)
    // 旧请求后返回：不得写回，也不得再动 loading
    d1.resolve('old-res')
    const r1 = await p1
    expect(r1).toBeUndefined()
    expect(seen).toEqual([['new', 'new-res']])
    expect(loading.value).toBe(false)
  })

  it('fetcher 抛错：loading 回落且错误继续抛出', async () => {
    const { loading, run } = usePagedFetch()
    const p = run(async () => { throw new Error('boom') })
    await expect(p).rejects.toThrow('boom')
    expect(loading.value).toBe(false)
  })

  it('过期请求抛错：不影响最新请求的 loading', async () => {
    const { loading, run } = usePagedFetch()
    const d1 = deferred()
    const d2 = deferred()
    const p1 = run(() => d1.promise)
    const p2 = run(() => d2.promise)
    d1.reject(new Error('old fail'))
    await expect(p1).rejects.toThrow('old fail')
    expect(loading.value).toBe(true) // 最新请求仍在途
    d2.resolve('ok')
    await p2
    expect(loading.value).toBe(false)
  })
})
