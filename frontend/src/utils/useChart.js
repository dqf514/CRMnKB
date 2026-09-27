// echarts 组合式封装：明暗主题跟随、窗口 resize、卸载销毁
import * as echarts from 'echarts'
import { onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useThemeStore } from '../stores/theme'

export function useChart(elRef) {
  const themeStore = useThemeStore()
  let chart = null
  let lastOption = null

  function ensure() {
    if (!chart && elRef.value) {
      chart = echarts.init(elRef.value, themeStore.mode === 'dark' ? 'dark' : null)
    }
    return chart
  }

  function setOption(option) {
    lastOption = option
    ensure()?.setOption({ backgroundColor: 'transparent', ...option }, true)
  }

  function onResize() {
    chart?.resize()
  }

  onMounted(() => {
    window.addEventListener('resize', onResize)
    if (lastOption) setOption(lastOption)
  })

  onUnmounted(() => {
    window.removeEventListener('resize', onResize)
    chart?.dispose()
    chart = null
  })

  // 明暗切换后重建图表
  watch(() => themeStore.mode, () => {
    chart?.dispose()
    chart = null
    if (lastOption) nextTick(() => setOption(lastOption))
  })

  return { setOption }
}
