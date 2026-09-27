import { defineStore } from 'pinia'
import { login as apiLogin, getMe } from '../api'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    user: JSON.parse(localStorage.getItem('user') || 'null'),
  }),
  actions: {
    async login(form) {
      const res = await apiLogin(form)
      this.token = res.access_token
      this.user = res.user || null
      localStorage.setItem('token', res.access_token)
      if (res.user) localStorage.setItem('user', JSON.stringify(res.user))
      // 登录响应未带 user 时兜底拉取
      if (!res.user) {
        try {
          const me = await getMe()
          this.user = me
          localStorage.setItem('user', JSON.stringify(me))
        } catch {
          /* 忽略 */
        }
      }
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
