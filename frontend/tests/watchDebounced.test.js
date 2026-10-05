import { describe, it, expect, vi, afterEach } from 'vitest'
import { ref, nextTick } from 'vue'
import { watchDebounced } from '../src/utils/watchDebounced.js'

afterEach(() => {
  vi.useRealTimers()
})

describe('watchDebounced 防抖即搜', () => {
  it('delay 内连续变化只触发一次回调（取最新值）', async () => {
    vi.useFakeTimers()
    const keyword = ref('')
    const cb = vi.fn()
    watchDebounced(keyword, cb, 300)

    keyword.value = 'a'
    await nextTick()
    vi.advanceTimersByTime(100)
    keyword.value = 'ab'
    await nextTick()
    vi.advanceTimersByTime(100)
    keyword.value = 'abc'
    await nextTick()
    vi.advanceTimersByTime(300)

    expect(cb).toHaveBeenCalledTimes(1)
    expect(cb).toHaveBeenCalledWith('abc', 'ab', expect.anything())
  })

  it('超过 delay 才触发；未到 delay 不触发', async () => {
    vi.useFakeTimers()
    const keyword = ref('')
    const cb = vi.fn()
    watchDebounced(keyword, cb, 300)

    keyword.value = 'x'
    await nextTick()
    vi.advanceTimersByTime(299)
    expect(cb).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(cb).toHaveBeenCalledTimes(1)
  })

  it('返回原 watch 的停止函数，停止后不再触发', async () => {
    vi.useFakeTimers()
    const keyword = ref('')
    const cb = vi.fn()
    const stop = watchDebounced(keyword, cb, 300)
    stop()
    keyword.value = 'y'
    await nextTick()
    vi.advanceTimersByTime(1000)
    expect(cb).not.toHaveBeenCalled()
  })
})
