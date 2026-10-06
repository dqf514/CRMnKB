// customerNav：sessionStorage 存取的契约测试。
// 测试环境（node）没有 sessionStorage，这里用内存 Map 桩实现（与 tests/setup.js 的 localStorage 同款）。
import { describe, it, expect, beforeEach } from 'vitest'
import { saveCustomerNav, loadCustomerNav, clearCustomerNav } from '../src/utils/customerNav'

const store = new Map()
globalThis.sessionStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
}

beforeEach(() => store.clear())

describe('customerNav', () => {
  it('保存后可原样读回（id 序列 + query + 视图模式）', () => {
    const payload = { ids: [3, 5, 8], query: { keyword: '张', page: 2 }, viewMode: 'card' }
    saveCustomerNav(payload)
    expect(loadCustomerNav()).toEqual(payload)
  })

  it('无记录时返回 null', () => {
    expect(loadCustomerNav()).toBeNull()
  })

  it('内容损坏（非法 JSON / 非对象）时返回 null', () => {
    store.set('customerNav', '{oops')
    expect(loadCustomerNav()).toBeNull()
    store.set('customerNav', '123')
    expect(loadCustomerNav()).toBeNull()
  })

  it('清除后返回 null', () => {
    saveCustomerNav({ ids: [1] })
    clearCustomerNav()
    expect(loadCustomerNav()).toBeNull()
  })
})
