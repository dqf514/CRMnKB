// 表单校验小工具：el-form validate() 校验失败会 reject，
// 直接 await 会把 rejection 打到 console；这里统一吞掉并返回布尔值。
// 返回 true=校验通过，false=校验未通过（el-form 已就地显示错误提示）
export async function validateForm(formRef) {
  if (!formRef) return true
  try {
    await formRef.validate()
    return true
  } catch {
    return false
  }
}
