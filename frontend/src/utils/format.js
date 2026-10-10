// 状态/阶段枚举中文映射 + el-tag 颜色 + 时间格式化

export const customerStatusMap = {
  potential: { label: '潜在', type: 'info' },
  intention: { label: '意向', type: 'primary' },
  negotiating: { label: '洽谈中', type: 'warning' },
  closed: { label: '已成交', type: 'success' },
  lost: { label: '已流失', type: 'danger' },
}

export const followupTypeMap = {
  call: { label: '电话', type: 'primary' },
  meeting: { label: '会议', type: 'success' },
  email: { label: '邮件', type: 'warning' },
  visit: { label: '拜访', type: 'danger' },
}

export const opportunityStageMap = {
  prospecting: { label: '初步接触', type: 'info' },
  qualification: { label: '资格确认', type: 'primary' },
  proposal: { label: '方案报价', type: 'warning' },
  negotiation: { label: '商务谈判', type: 'warning' },
  closed_won: { label: '赢单', type: 'success' },
  closed_lost: { label: '输单', type: 'danger' },
}

export const documentStatusMap = {
  processing: { label: '处理中', type: 'warning' },
  ready: { label: '就绪', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  unsupported: { label: '仅存储', type: 'info' },
}

// 多模态摄入管线的解析方式
export const processingMethodMap = {
  text: { label: '文本解析', type: 'info' },
  email: { label: '邮件解析', type: 'primary' },
  ocr: { label: '本地OCR', type: 'warning' },
  vision_ocr: { label: '视觉识别', type: 'success' },
  image_describe: { label: '图片识别', type: 'success' },
  asr: { label: '音频转写', type: 'warning' },
  video_asr: { label: '视频转写', type: 'warning' },
}

export const kbTypeMap = {
  general: { label: '通用', type: 'primary' },
  customer: { label: '客户专属', type: 'warning' },
}

export const profileStatusMap = {
  idle: { label: '未生成', type: 'info' },
  generating: { label: '生成中', type: 'warning' },
  ready: { label: '已生成', type: 'success' },
  failed: { label: '生成失败', type: 'danger' },
}

// 客户 DDQ（尽调问卷）状态
export const ddqStatusMap = {
  none: { label: '未开始', type: 'info' },
  pending: { label: '进行中', type: 'warning' },
  completed: { label: '已完成', type: 'success' },
}

// 客户文档资料类型已改为后端动态配置（stores/docCategories.js），此处不再保留静态映射

export const taskTypeMap = {
  follow_up: { label: '跟进', type: 'primary' },
  meeting: { label: '会议', type: 'success' },
  call: { label: '电话', type: 'primary' },
  email: { label: '邮件', type: 'warning' },
  report: { label: '报告', type: 'info' },
  todo: { label: '待办', type: 'danger' },
}

export const taskPriorityMap = {
  high: { label: '高', type: 'danger' },
  medium: { label: '中', type: 'warning' },
  low: { label: '低', type: 'info' },
}

export const taskStatusMap = {
  pending: { label: '待处理', type: 'info' },
  in_progress: { label: '进行中', type: 'primary' },
  completed: { label: '已完成', type: 'success' },
  cancelled: { label: '已取消', type: 'danger' },
}

export const taskSourceMap = {
  manual: { label: '手动', type: 'info' },
  rule: { label: '规则', type: 'warning' },
  ai_analysis: { label: 'AI分析', type: 'primary' },
  capture: { label: '随手记', type: 'success' },
}

export const triggerTypeMap = {
  days_since_last_followup: { label: 'N天未跟进', type: 'warning' },
  opportunity_stagnant: { label: '商机停滞', type: 'primary' },
  task_due_soon: { label: '任务临期', type: 'danger' },
}

export const reportTypeMap = {
  customer_analysis: { label: '客户分析', type: 'primary' },
  sales_weekly: { label: '销售周报', type: 'success' },
  sales_monthly: { label: '销售月报', type: 'warning' },
  custom: { label: '自定义', type: 'info' },
}

export const reportStatusMap = {
  generating: { label: '生成中', type: 'warning' },
  revising: { label: '修改中', type: 'warning' },
  ready: { label: '已生成', type: 'success' },
  failed: { label: '失败', type: 'danger' },
}

export const workflowTriggerMap = {
  interval: { label: '间隔触发', type: 'info' },
  daily: { label: '每日定时', type: 'primary' },
  weekly: { label: '每周定时', type: 'success' },
  birthday: { label: '客户生日', type: 'danger' },
  condition: { label: '条件触发', type: 'warning' },
}

export const workflowActionMap = {
  send_email: { label: '发送邮件', type: 'primary' },
  create_task: { label: '创建任务', type: 'success' },
  create_notification: { label: '创建通知', type: 'warning' },
}

export const workflowFieldMap = {
  name: '客户名称',
  industry: '行业',
  status: '客户状态',
  source: '来源',
  phone: '电话',
  email: '邮箱',
}

export const workflowOpMap = {
  eq: '等于',
  ne: '不等于',
  contains: '包含',
  gt: '大于',
  lt: '小于',
}

export const workflowRunStatusMap = {
  success: { label: '成功', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  running: { label: '运行中', type: 'warning' },
}

export const userRoleMap = {
  admin: { label: '管理员', type: 'danger' },
  member: { label: '团队成员', type: 'primary' },
  individual: { label: '个人用户', type: 'warning' },
  user: { label: '普通用户', type: 'primary' },
}

export const llmProviderMap = {
  api: { label: 'API', type: 'primary' },
  ollama: { label: 'Ollama', type: 'success' },
}

export const llmModelTypeMap = {
  chat: { label: '对话', type: 'primary' },
  embed: { label: '嵌入', type: 'warning' },
  vision: { label: '视觉理解', type: 'success' },
  asr: { label: '语音识别', type: 'info' },
  rerank: { label: '重排序', type: 'danger' },
}

export const errorLevelMap = {
  error: { label: '错误', type: 'danger' },
  warning: { label: '警告', type: 'warning' },
}

// 聊天工具调用名 → 中文标签（未知 name 原样显示）
export const toolNameMap = {
  web_search: '联网搜索',
  web_fetch: '网页抓取',
  // dsh Agent 模式的 MCP 知识库 / CRM / 邮件工具
  mcp__kb__kb_search: '知识库检索',
  mcp__kb__kb_read_doc: '阅读文档',
  mcp__kb__kb_list: '列出知识库',
  mcp__kb__crm_list_customers: '客户名单',
  mcp__kb__crm_search_customers: '客户检索',
  mcp__kb__crm_get_customer: '查看客户',
  mcp__kb__crm_list_followups: '跟进清单',
  mcp__kb__crm_list_opportunities: '商机清单',
  mcp__kb__crm_list_tasks: '任务清单',
  mcp__kb__crm_stats: '经营统计',
  mcp__kb__crm_add_followup: '写跟进（需审批）',
  mcp__kb__crm_create_customer: '新建客户（需审批）',
  mcp__kb__crm_update_customer: '更新客户（需审批）',
  mcp__kb__crm_delete_customer: '删除客户（需审批）',
  mcp__kb__crm_create_opportunity: '新建商机（需审批）',
  mcp__kb__crm_create_task: '新建任务（需审批）',
  mcp__kb__mail_draft_create: '邮件草稿（需审批）',
  // 自定义工具：发现 / 调用 / AI 起草
  mcp__kb__skill_list: '列出工具',
  mcp__kb__skill_call: '调用自定义工具',
  mcp__kb__skill_create_api: '新建工具（需审批）',
}

// Agent 审批单状态
export const agentApprovalStatusMap = {
  pending: { label: '待审批', type: 'warning' },
  executed: { label: '已执行', type: 'success' },
  rejected: { label: '已拒绝', type: 'info' },
  failed: { label: '执行失败', type: 'danger' },
}

// 秒数 → 可读运行时长
export function formatUptime(seconds) {
  const s = Number(seconds)
  if (!s || isNaN(s) || s < 0) return '-'
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d > 0) return `${d} 天 ${h} 小时`
  if (h > 0) return `${h} 小时 ${m} 分钟`
  return `${m} 分钟`
}

// 客户标签预设（el-select allow-create 的初始选项，用户可自定义回车创建）
export const CUSTOMER_TAG_PRESETS = [
  '重点客户', 'VIP', '老客户', '新客户', '转介绍', '意向强烈', '价格敏感', '决策周期长',
]

export function enumLabel(map, key) {
  return map[key]?.label || key || '-'
}

export function enumTagType(map, key) {
  return map[key]?.type || 'info'
}

export function formatDateTime(val) {
  if (!val) return '-'
  const d = parseServerDate(val)
  if (!d) return val
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function formatDate(val) {
  if (!val) return '-'
  const d = parseServerDate(val)
  if (!d) return val
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// 后端返回 naive UTC 时间字符串（无 Z 后缀），解析时统一按 UTC 处理；
// 已带 Z 或 ±hh:mm 时区标识的字符串交给原生解析
export function parseServerDate(val) {
  if (!val) return null
  if (val instanceof Date) return isNaN(val.getTime()) ? null : val
  let s = String(val).trim()
  const hasTz = /([zZ]|[+-]\d{2}:?\d{2})$/.test(s)
  if (!hasTz && /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(s)) {
    s = s.replace(' ', 'T') + 'Z'
  }
  const d = new Date(s)
  return isNaN(d.getTime()) ? null : d
}

// 金额：两位小数 + 千分位
export function formatMoney(val) {
  const n = Number(val)
  if (isNaN(n)) return '-'
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function formatFileSize(bytes) {
  const n = Number(bytes)
  if (!n || isNaN(n) || n <= 0) return '-'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}
