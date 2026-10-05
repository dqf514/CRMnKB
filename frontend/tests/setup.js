// 测试环境（node）没有浏览器 localStorage，用内存 Map 桩实现。
// chatStream / auth store / theme store 都在模块加载或状态初始化时读它。
import { beforeEach } from 'vitest'

const store = new Map()

globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => {
    store.set(k, String(v))
  },
  removeItem: (k) => {
    store.delete(k)
  },
  clear: () => {
    store.clear()
  },
}

// 每个用例前清空，避免用例间状态串扰
beforeEach(() => {
  store.clear()
})
