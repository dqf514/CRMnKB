// theme store：本地持久化的读取/回退逻辑（loadLocal 容错、非法值回退默认）
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('../src/api/index.js', () => ({
  getPreferences: vi.fn(),
  updatePreferences: vi.fn(async () => ({})),
}))

import { updatePreferences } from '../src/api/index.js'
import { useThemeStore, ACCENTS, FONT_SIZES } from '../src/stores/theme.js'

// apply() 会写 document.documentElement，node 环境给个最小桩
beforeEach(() => {
  globalThis.document = {
    documentElement: {
      classList: { toggle: vi.fn() },
      style: { setProperty: vi.fn() },
      setAttribute: vi.fn(),
    },
  }
  setActivePinia(createPinia())
})

describe('theme store 初始化', () => {
  it('localStorage 为空：全部走默认值', () => {
    const theme = useThemeStore()
    expect(theme.mode).toBe('light')
    expect(theme.accent).toBe('blue')
    expect(theme.pageSize).toBe(20)
    expect(theme.fontSize).toBe('small')
  })

  it('crm-theme 是损坏 JSON：容错回退默认值（不抛错）', () => {
    localStorage.setItem('crm-theme', '{broken json')
    const theme = useThemeStore()
    expect(theme.mode).toBe('light')
    expect(theme.accent).toBe('blue')
  })

  it('非法值逐项回退默认，合法值保留', () => {
    localStorage.setItem('crm-theme', JSON.stringify({
      mode: 'dark',
      accent: 'not-a-color',
      pageSize: 50,
      fontSize: 'huge',
    }))
    const theme = useThemeStore()
    expect(theme.mode).toBe('dark')
    expect(theme.accent).toBe('blue') // 非法回退
    expect(theme.pageSize).toBe(50)
    expect(theme.fontSize).toBe('small') // 非法回退
  })
})

describe('theme store 动作', () => {
  it('setAccent 合法值生效并持久化；非法值忽略', () => {
    const theme = useThemeStore()
    theme.setAccent('green')
    expect(theme.accent).toBe('green')
    expect(theme.primaryColor).toBe(ACCENTS.green.color)
    expect(JSON.parse(localStorage.getItem('crm-theme')).accent).toBe('green')
    expect(updatePreferences).toHaveBeenCalled()

    theme.setAccent('not-a-color')
    expect(theme.accent).toBe('green') // 未变
  })

  it('toggleMode 在亮/暗之间切换并写 class', () => {
    const theme = useThemeStore()
    theme.toggleMode()
    expect(theme.mode).toBe('dark')
    expect(document.documentElement.classList.toggle).toHaveBeenCalledWith('dark', true)
    theme.toggleMode()
    expect(theme.mode).toBe('light')
  })

  it('setFontSize 只接受 FONT_SIZES 里的档位', () => {
    const theme = useThemeStore()
    theme.setFontSize('large')
    expect(theme.fontSize).toBe('large')
    theme.setFontSize('xxxl')
    expect(theme.fontSize).toBe('large')
    expect(Object.keys(FONT_SIZES)).toContain(theme.fontSize)
  })
})
