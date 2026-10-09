// auth store 的 JSON.parse 初始化路径 + 登录/登出对 localStorage 的读写
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('../src/api/index.js', () => ({
  login: vi.fn(),
  phoneLogin: vi.fn(),
  register: vi.fn(),
  onboarding: vi.fn(),
  getMe: vi.fn(),
}))

import { login as apiLogin, register as apiRegister, onboarding as apiOnboarding, getMe } from '../src/api/index.js'
import { useAuthStore } from '../src/stores/auth.js'

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('auth store', () => {
  it('localStorage 有合法 user JSON：初始 state 解析为对象', () => {
    localStorage.setItem('token', 't')
    localStorage.setItem('user', JSON.stringify({ id: 1, name: '管理员' }))
    const auth = useAuthStore()
    expect(auth.token).toBe('t')
    expect(auth.user).toEqual({ id: 1, name: '管理员' })
  })

  it('localStorage 无 user：初始为 null', () => {
    const auth = useAuthStore()
    expect(auth.token).toBe('')
    expect(auth.user).toBeNull()
  })

  it('登录响应带 user：写回 state 与 localStorage', async () => {
    apiLogin.mockResolvedValueOnce({
      access_token: 'tk-1',
      user: { id: 2, name: '张三' },
    })
    const auth = useAuthStore()
    await auth.login({ username: 'u', password: 'p' })
    expect(auth.token).toBe('tk-1')
    expect(auth.user).toEqual({ id: 2, name: '张三' })
    expect(localStorage.getItem('token')).toBe('tk-1')
    expect(JSON.parse(localStorage.getItem('user'))).toEqual({ id: 2, name: '张三' })
  })

  it('登录响应不带 user：兜底调用 getMe 拉取', async () => {
    apiLogin.mockResolvedValueOnce({ access_token: 'tk-2' })
    getMe.mockResolvedValueOnce({ id: 3, name: '兜底' })
    const auth = useAuthStore()
    await auth.login({ username: 'u', password: 'p' })
    expect(getMe).toHaveBeenCalledTimes(1)
    expect(auth.user).toEqual({ id: 3, name: '兜底' })
  })

  it('logout 清空 state 与 localStorage', async () => {
    apiLogin.mockResolvedValueOnce({ access_token: 'tk-3', user: { id: 4 } })
    const auth = useAuthStore()
    await auth.login({})
    auth.logout()
    expect(auth.token).toBe('')
    expect(auth.user).toBeNull()
    expect(localStorage.getItem('token')).toBeNull()
    expect(localStorage.getItem('user')).toBeNull()
  })

  it('注册响应带 user：与登录同样写回 state 与 localStorage', async () => {
    apiRegister.mockResolvedValueOnce({
      access_token: 'tk-reg',
      user: { id: 5, name: '新用户', preferences: { onboarded: false } },
    })
    const auth = useAuthStore()
    await auth.register({ phone: '13800000000', code: '123456' })
    expect(auth.token).toBe('tk-reg')
    expect(auth.user.preferences).toEqual({ onboarded: false })
    expect(localStorage.getItem('token')).toBe('tk-reg')
  })

  it('onboarding 返回新 token：整体替换旧凭证（设密码后旧 token 失效）', async () => {
    apiRegister.mockResolvedValueOnce({
      access_token: 'tk-old',
      user: { id: 6, preferences: { onboarded: false } },
    })
    apiOnboarding.mockResolvedValueOnce({
      access_token: 'tk-new',
      user: { id: 6, name: '张三', preferences: { onboarded: true } },
    })
    const auth = useAuthStore()
    await auth.register({ phone: '13800000000', code: '123456' })
    await auth.completeOnboarding({ name: '张三', password: 'password1' })
    expect(auth.token).toBe('tk-new')
    expect(auth.user.preferences).toEqual({ onboarded: true })
    expect(localStorage.getItem('token')).toBe('tk-new')
    expect(JSON.parse(localStorage.getItem('user')).preferences).toEqual({ onboarded: true })
  })
})
