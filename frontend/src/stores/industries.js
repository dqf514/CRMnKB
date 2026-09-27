import { defineStore } from 'pinia'
import { getIndustries } from '../api'

// 行业字典轻量缓存：表单/筛选共用，行业设置变更后调 refresh() 刷新
export const useIndustryStore = defineStore('industries', {
  state: () => ({
    list: [],
    loaded: false,
  }),
  actions: {
    async load(force = false) {
      if (this.loaded && !force) return
      const res = await getIndustries({ enabled_only: true })
      this.list = Array.isArray(res) ? res : (res?.items || [])
      this.loaded = true
    },
    async refresh() {
      await this.load(true)
    },
  },
})
