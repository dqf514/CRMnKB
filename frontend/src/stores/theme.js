import { defineStore } from 'pinia'
import { getPreferences, updatePreferences } from '../api'

// 预设品牌色
export const ACCENTS = {
  blue: { label: '曜蓝', color: '#2563eb' },
  green: { label: '翡翠', color: '#059669' },
  purple: { label: '绛紫', color: '#7c3aed' },
  orange: { label: '琥珀', color: '#d97706' },
  red: { label: '茜红', color: '#dc2626' },
}

// 字号三档（全局缩放倍数）
export const FONT_SIZES = {
  small: { label: '小', size: '13px', zoom: 1 },
  medium: { label: '中', size: '15px', zoom: 1.1 },
  large: { label: '大', size: '17px', zoom: 1.2 },
}

// hex 颜色按比例与白色/黑色混合，生成 Element Plus 色阶
function mix(hex1, hex2, weight) {
  const parse = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)]
  const [r1, g1, b1] = parse(hex1)
  const [r2, g2, b2] = parse(hex2)
  const r = Math.round(r1 * weight + r2 * (1 - weight))
  const g = Math.round(g1 * weight + g2 * (1 - weight))
  const b = Math.round(b1 * weight + b2 * (1 - weight))
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`
}

function loadLocal() {
  try {
    return JSON.parse(localStorage.getItem('crm-theme') || '{}')
  } catch {
    return {}
  }
}

export const useThemeStore = defineStore('theme', {
  state: () => {
    const local = loadLocal()
    return {
      mode: local.mode === 'dark' ? 'dark' : 'light',
      accent: ACCENTS[local.accent] ? local.accent : 'blue',
      pageSize: Number(local.pageSize) || 20,
      fontSize: FONT_SIZES[local.fontSize] ? local.fontSize : 'small',
    }
  },
  getters: {
    primaryColor: (s) => ACCENTS[s.accent].color,
  },
  actions: {
    apply() {
      const root = document.documentElement
      root.classList.toggle('dark', this.mode === 'dark')
      const base = this.primaryColor
      root.style.setProperty('--el-color-primary', base)
      root.style.setProperty('--el-color-primary-dark-2', mix(base, '#000000', 0.8))
      // 暗色模式下浅色阶与深色底混合，亮色模式与白色混合
      const mixTo = this.mode === 'dark' ? '#141414' : '#ffffff'
      const levels = { 3: 0.7, 5: 0.5, 7: 0.3, 8: 0.2, 9: 0.1 }
      for (const [lv, w] of Object.entries(levels)) {
        root.style.setProperty(`--el-color-primary-light-${lv}`, mix(base, mixTo, w))
      }
      // 字号：用字体尺寸变量缩放（小=1 / 中=1.1 / 大=1.2）。
      // 不用 html zoom——zoom 会双重放大 Element Plus 弹出层坐标（菜单飘离、页面左移），
      // 也影响 100vh 布局。改为缩放 Element Plus 字体变量 + body 基础字号，布局保持不动。
      const scale = (FONT_SIZES[this.fontSize] || FONT_SIZES.small).zoom
      root.style.zoom = ''  // 清除旧 zoom（如有），回到正常渲染
      root.style.setProperty('--app-zoom', '1')  // 既有 100vh 补偿保持中性
      root.style.setProperty('--app-font-scale', String(scale))
      root.style.setProperty('--el-font-size-base', `${14 * scale}px`)
      root.style.setProperty('--el-font-size-small', `${12 * scale}px`)
      root.style.setProperty('--el-font-size-medium', `${14 * scale}px`)
      root.style.setProperty('--el-font-size-large', `${16 * scale}px`)
      root.style.setProperty('--el-font-size-extra-large', `${20 * scale}px`)
      root.setAttribute('data-font-size', this.fontSize)
    },
    persist() {
      localStorage.setItem('crm-theme', JSON.stringify({
        mode: this.mode,
        accent: this.accent,
        pageSize: this.pageSize,
        fontSize: this.fontSize,
      }))
      // 同步到后端偏好（失败静默，不影响本地生效）
      updatePreferences({
        mode: this.mode,
        accent: this.accent,
        page_size: this.pageSize,
        font_size: this.fontSize,
      }).catch(() => {})
    },
    setMode(mode) {
      this.mode = mode === 'dark' ? 'dark' : 'light'
      this.apply()
      this.persist()
    },
    toggleMode() {
      this.setMode(this.mode === 'dark' ? 'light' : 'dark')
    },
    setAccent(accent) {
      if (!ACCENTS[accent]) return
      this.accent = accent
      this.apply()
      this.persist()
    },
    setPageSize(n) {
      this.pageSize = n
      this.persist()
    },
    setFontSize(s) {
      if (!FONT_SIZES[s]) return
      this.fontSize = s
      this.apply()
      this.persist()
    },
    // 登录后拉取后端偏好，若与本地不同则以后端为准
    async syncFromServer() {
      try {
        const prefs = await getPreferences()
        if (!prefs) return
        const mode = prefs.mode === 'dark' ? 'dark' : 'light'
        const accent = ACCENTS[prefs.accent] ? prefs.accent : this.accent
        this.mode = mode
        this.accent = accent
        if (prefs.page_size) this.pageSize = Number(prefs.page_size) || this.pageSize
        if (FONT_SIZES[prefs.font_size]) this.fontSize = prefs.font_size
        this.apply()
        localStorage.setItem('crm-theme', JSON.stringify({
          mode: this.mode, accent: this.accent, pageSize: this.pageSize, fontSize: this.fontSize,
        }))
      } catch {
        /* 拉取失败用本地 */
      }
    },
  },
})
