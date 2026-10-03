// 危险操作二次确认：取消（点取消/关闭/Esc）时静默返回 false，
// 不再让 ElMessageBox.confirm 的 rejection 打到 console（unhandled rejection）。
import { ElMessageBox } from 'element-plus'

// 返回 true=用户确认，false=用户取消
export async function confirmDanger(message, title = '删除确认', options = {}) {
  try {
    await ElMessageBox.confirm(message, title, { type: 'warning', ...options })
    return true
  } catch {
    return false
  }
}
