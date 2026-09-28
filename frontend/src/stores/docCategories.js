// 客户文档资料类型（category）store：列表来自后端 system_settings 配置（管理端可增删改），
// 拉取失败/未配置时回退默认五项；未知 value 的标签回退显示原始字符串（删除类型不影响存量文件）。
import { defineStore } from 'pinia'
import { getDocCategories } from '../api'

const FALLBACK = [
  { value: 'company_intro', label: '公司介绍' },
  { value: 'monthly_report', label: '月报' },
  { value: 'factsheet', label: '产品资料' },
  { value: 'meeting_minutes', label: '会议纪要' },
  { value: 'other', label: '其他' },
]
// 标签颜色按列表顺序循环取色，动态类型也能有区分度
const TAG_TYPES = ['primary', 'success', 'warning', 'danger', 'info']

export const useDocCategoryStore = defineStore('docCategories', {
  state: () => ({ list: [], loaded: false }),
  actions: {
    async load(force = false) {
      if (this.loaded && !force) return
      try {
        const res = await getDocCategories()
        this.list = res?.items?.length ? res.items : FALLBACK
      } catch {
        this.list = FALLBACK
      }
      this.loaded = true
    },
    labelOf(value) {
      return this.list.find((c) => c.value === value)?.label || value
    },
    tagType(value) {
      const i = this.list.findIndex((c) => c.value === value)
      return i < 0 ? 'info' : TAG_TYPES[i % TAG_TYPES.length]
    },
  },
})
