// 客户列表 → 详情的导航上下文存取（sessionStorage，仅当前标签页会话有效）。
// 列表跳详情前保存：当前筛选/排序下的客户 id 序列 + 列表 query（筛选/页码/视图模式）；
// 详情页据 id 序列实现「上一个/下一个」切换，返回列表时据 query 恢复筛选上下文（恢复后清除）。
const KEY = 'customerNav'

// 保存导航上下文；隐私模式等写入失败静默（功能降级为无上下文，详情页隐藏切换按钮）
export function saveCustomerNav(payload) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(payload))
  } catch { /* 写入失败静默 */ }
}

// 读取导航上下文；无记录或内容损坏时返回 null
export function loadCustomerNav() {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return null
    const data = JSON.parse(raw)
    if (!data || typeof data !== 'object') return null
    return data
  } catch {
    return null
  }
}

// 清除导航上下文（列表页恢复 query 后调用，避免陈旧上下文串到后续浏览）
export function clearCustomerNav() {
  try {
    sessionStorage.removeItem(KEY)
  } catch { /* 忽略 */ }
}
