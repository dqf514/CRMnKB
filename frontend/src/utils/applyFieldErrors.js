// 后端 422（FastAPI/pydantic 校验失败）返回形如
// { detail: [{ loc: ['body', 'name'], msg: 'Field required', ... }, ...] }
// 本工具把 loc 末段映射为表单字段，把错误就地显示到对应 el-form-item 上，
// 避免字段级错误全部退化成一条通用 toast。
//
// 用法（在保存的 catch 里最先调用）：
//   } catch (e) {
//     if (applyFieldErrors(e, formRef.value, { customer_name: 'customer_id' })) return
//     // 其余错误交给拦截器的通用 toast
//   }
// 返回 true=已按字段显示（已消费该错误）；false=不是可映射的 422，调用方走默认提示。
// 注意：依赖 el-form 的 fields（form-item 上下文），字段需带 prop 才能匹配；
// 重新打开弹窗时记得 clearValidate() 清掉上次的错误态（各页面已有此惯例）。

// pydantic 常见英文消息翻成中文，未匹配的返回原文
function cnMsg(msg) {
  if (!msg) return '参数不合法'
  if (msg === 'Field required') return '该字段为必填项'
  let m = msg.match(/^String should have at least (\d+) characters?/)
  if (m) return `至少输入 ${m[1]} 个字符`
  m = msg.match(/^String should have at most (\d+) characters?/)
  if (m) return `最多输入 ${m[1]} 个字符`
  m = msg.match(/^Input should be greater than or equal to (.+)$/)
  if (m) return `不能小于 ${m[1]}`
  m = msg.match(/^Input should be less than or equal to (.+)$/)
  if (m) return `不能大于 ${m[1]}`
  if (msg.startsWith('Input should be a valid email')) return '邮箱格式不正确'
  if (msg.startsWith('Input should be a valid integer')) return '请输入整数'
  if (msg.startsWith('Input should be a valid number')) return '请输入数字'
  return msg
}

export function applyFieldErrors(error, formRef, fieldMap = {}) {
  const detail = error?.response?.data?.detail
  if (error?.response?.status !== 422 || !Array.isArray(detail)) return false
  const fields = formRef?.fields || []
  let applied = false
  for (const item of detail) {
    const loc = Array.isArray(item.loc) ? item.loc : []
    // loc 形如 ['body', 'name']，取最后一段作为字段名，再经 fieldMap 换算成表单 prop
    const raw = loc[loc.length - 1]
    if (raw == null) continue
    const prop = fieldMap[raw] || raw
    const field = fields.find((f) => f.prop === prop)
    if (!field) continue
    field.validateState = 'error'
    field.validateMessage = cnMsg(item.msg)
    applied = true
  }
  return applied
}
