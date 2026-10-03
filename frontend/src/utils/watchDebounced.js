// 防抖即搜：监听源（通常是 query.keyword），变化后 delay 毫秒内无新输入才触发回调，
// 避免每敲一个字就发一次请求；clearable 清空也会触发同一回调。
import { watch } from 'vue'

export function watchDebounced(source, cb, delay = 300) {
  let timer = null
  return watch(source, (...args) => {
    clearTimeout(timer)
    timer = setTimeout(() => cb(...args), delay)
  })
}
