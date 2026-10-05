import { defineStore } from 'pinia'
import { login as apiLogin, phoneLogin as apiPhoneLogin, getMe } from '../api'

// localStorage 里的 user 可能损坏（手工改过/旧版本格式），JSON.parse 抛错会导致 store 创建失败白屏
function loadLocalUser() {
  try {
    return JSON.parse(localStorage.getItem('user') || 'null')
  } catch {
    localStorage.removeItem('user')
    return null
  }
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    user: loadLocalUser(),
  }),
  actions: {
    _applyAuth(res) {
      this.token = res.access_token
      this.user = res.user || null
      localStorage.setItem('token', res.access_token)
      if (res.user) localStorage.setItem('user', JSON.stringify(res.user))
      // 登录响应未带 user 时兜底拉取
      if (!res.user) {
        return getMe().then((me) => {
          this.user = me
          localStorage.setItem('user', JSON.stringify(me))
        }).catch(() => { /* 忽略 */ })
      }
      return Promise.resolve()
    },
    async login(form) {
      const res = await apiLogin(form)
      await this._applyAuth(res)
    },
    // 手机号 + 验证码登录（后端 /auth/login/phone）
    async loginByPhone(form) {
      const res = await apiPhoneLogin(form)
      await this._applyAuth(res)
    },
    async fetchMe() {
      const me = await getMe()
      this.user = me
      localStorage.setItem('user', JSON.stringify(me))
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('token')
      localStorage.removeItem('user')
    },
  },
})
