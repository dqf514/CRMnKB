import { defineStore } from 'pinia'
import { getBrand } from '../api'

// 全局品牌（系统名称 + logo），登录页/布局动态读取，管理端可自定义
export const useBrandStore = defineStore('brand', {
  state: () => ({
    systemName: '知识库',  // 兜底占位（接口失败时用）；正常启动后即被服务端品牌名覆盖
    logoUrl: '/logo-256.png',
    loaded: false,
    dshAgentEnabled: false,  // 后端 DSH_AGENT_ENABLED 开关，随品牌公开配置下发
    smsLoginEnabled: false,  // 短信验证码登录开关（管理端「系统设置 → 登录与接入」）
    env: '',                 // 部署环境（dev/sandbox/prod），随品牌公开配置下发
  }),
  actions: {
    async load() {
      try {
        const res = await getBrand()
        this.apply(res)
      } catch {
        /* 读取失败用默认品牌 */
      }
      this.loaded = true
    },
    apply(res) {
      if (res?.system_name) this.systemName = res.system_name
      if (res?.logo_url) this.logoUrl = res.logo_url
      this.dshAgentEnabled = !!res?.dsh_agent_enabled
      this.smsLoginEnabled = !!res?.sms_login_enabled
      this.env = res?.env || ''
      if (document.title && document.title !== this.systemName) {
        document.title = this.systemName
      }
    },
  },
})
