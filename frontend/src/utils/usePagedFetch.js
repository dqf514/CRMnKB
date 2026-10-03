// 列表/搜索请求防竞态：每次发起请求序号 +1，仅最新一次请求的响应允许写回数据，
// 避免快速切换过滤条件/翻页时，先发出的旧响应后返回覆盖新数据。
// 采用序号法（而非 AbortController）：实现简单，且不依赖 axios 传 signal 的链路改造。
import { ref } from 'vue'

export function usePagedFetch() {
  const loading = ref(false)
  let seq = 0

  // fetcher: () => Promise<res>；onData: (res) => void 仅在响应未过期时调用
  async function run(fetcher, onData) {
    const my = ++seq
    loading.value = true
    try {
      const res = await fetcher()
      if (my !== seq) return undefined // 已有更新的请求在途，丢弃过期响应
      onData?.(res)
      return res
    } finally {
      if (my === seq) loading.value = false
    }
  }

  return { loading, run }
}
