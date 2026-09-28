import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
  baseURL: '/',
  timeout: 60000,
})

// 请求拦截器：携带 token
request.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截器：统一错误处理，401 跳登录
request.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const status = error.response?.status
    if (status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      if (window.location.pathname !== '/login') {
        ElMessage.error('登录已过期，请重新登录')
        window.location.href = '/login'
      }
    } else {
      const detail = error.response?.data?.detail
      ElMessage.error(typeof detail === 'string' ? detail : '请求失败，请稍后重试')
    }
    return Promise.reject(error)
  }
)

// ========== 品牌（系统名称 + logo） ==========
export const getBrand = () => request.get('/api/v1/brand')
export const updateBrand = (data) => request.put('/api/v1/brand', data)
export const uploadBrandLogo = (file) => {
  const formData = new FormData()
  formData.append('file', file)
  return request.post('/api/v1/brand/logo', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
// 个人头像：前端裁剪为正方形后上传（返回 UserOut）
export const uploadAvatar = (file) => {
  const formData = new FormData()
  formData.append('file', file)
  return request.post('/api/v1/auth/avatar', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
// 系统设置：解析文件格式开关
export const getParseFormats = () => request.get('/api/v1/admin/settings/formats')
export const updateParseFormats = (data) => request.put('/api/v1/admin/settings/formats', data)
export const getLoginIntegrations = () => request.get('/api/v1/admin/settings/login-integrations')
export const updateLoginIntegrations = (data) => request.put('/api/v1/admin/settings/login-integrations', data)
// 邮件写作规范（注入 AI 邮件草稿生成）
export const getEmailGuide = () => request.get('/api/v1/admin/settings/email-guide')
export const updateEmailGuide = (guide) => request.put('/api/v1/admin/settings/email-guide', { guide })
// 文档资料类型（全员可读列表 / 管理端整体替换）
export const getDocCategories = () => request.get('/api/v1/library/categories')
export const updateDocCategories = (items) => request.put('/api/v1/admin/settings/doc-categories', { items })
// 上传端：当前启用解析的格式清单（登录用户即可读，供选择过滤）
export const getUploadFormats = () => request.get('/api/v1/library/upload-formats')

// ========== 权限 / 分享 ==========
export const getUsers = () => request.get('/api/v1/users')
export const getResourcePermissions = (rtype, rid) => request.get(`/api/v1/permissions/${rtype}/${rid}/users`)
export const shareResource = (rtype, rid, data) => request.post(`/api/v1/permissions/${rtype}/${rid}/share`, data)
export const updateShare = (rtype, rid, userId, data) => request.put(`/api/v1/permissions/${rtype}/${rid}/share/${userId}`, data)
export const revokeShare = (rtype, rid, userId) => request.delete(`/api/v1/permissions/${rtype}/${rid}/share/${userId}`)
export const setVisibility = (rtype, rid, is_private) => request.put(`/api/v1/permissions/${rtype}/${rid}/visibility`, { is_private })
export const batchShareFiles = (fileIds, data) => request.post('/api/v1/permissions/files/batch-share', { file_ids: fileIds, ...data })

// ========== 认证 ==========
export const login = (data) => request.post('/api/v1/auth/login', data)
export const sendSmsCode = (phone) => request.post('/api/v1/auth/sms-code', { phone })
export const phoneLogin = (data) => request.post('/api/v1/auth/login/phone', data)
export const getMe = () => request.get('/api/v1/auth/me')
export const getPreferences = () => request.get('/api/v1/auth/preferences')
export const updatePreferences = (data) => request.put('/api/v1/auth/preferences', data)
export const updateProfile = (data) => request.put('/api/v1/auth/profile', data)
export const updatePassword = (data) => request.put('/api/v1/auth/password', data)

// ========== 客户 ==========
export const getCustomers = (params) => request.get('/api/v1/customers', { params })
export const getCustomer = (id) => request.get(`/api/v1/customers/${id}`)
export const createCustomer = (data) => request.post('/api/v1/customers', data)
export const updateCustomer = (id, data) => request.put(`/api/v1/customers/${id}`, data)
export const deleteCustomer = (id) => request.delete(`/api/v1/customers/${id}`)
// 导入导出 / 查重（响应为文件流的接口用 responseType:'blob'）
export const getCustomerImportTemplate = () =>
  request.get('/api/v1/customers/import-template', { responseType: 'blob' })
export const importCustomers = (file, mode) => {
  const formData = new FormData()
  formData.append('file', file)
  return request.post('/api/v1/customers/import', formData, {
    params: { mode },
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
  })
}
export const exportCustomers = (params) =>
  request.get('/api/v1/customers/export', { params, responseType: 'blob' })
export const getCustomerDuplicates = (params) => request.get('/api/v1/customers/duplicates', { params })
// AI 客户简报：后台生成（202），前端轮询详情刷新
export const refreshCustomerBrief = (id) => request.post(`/api/v1/customers/${id}/brief/refresh`)
// AI 邮件草稿：body {intent, language} → {subject, body}
export const emailDraft = (id, data) => request.post(`/api/v1/customers/${id}/email-draft`, data)

// ========== 跟进 ==========
export const getFollowups = (customerId) => request.get(`/api/v1/customers/${customerId}/followups`)
export const createFollowup = (customerId, data) =>
  request.post(`/api/v1/customers/${customerId}/followups`, data)

// ========== 商机 ==========
export const getOpportunities = (params) => request.get('/api/v1/opportunities', { params })
export const createOpportunity = (data) => request.post('/api/v1/opportunities', data)
export const updateOpportunity = (id, data) => request.put(`/api/v1/opportunities/${id}`, data)
export const deleteOpportunity = (id) => request.delete(`/api/v1/opportunities/${id}`)

// ========== RAG ==========
export const ragQuery = (data) => request.post('/api/v1/rag/query', data)

// ========== 会话（流式接口见 src/api/chatStream.js） ==========
export const createChatSession = (data) => request.post('/api/v1/chat/sessions', data)
export const getChatSessions = () => request.get('/api/v1/chat/sessions')
export const deleteChatSession = (id) => request.delete(`/api/v1/chat/sessions/${id}`)
export const getChatMessages = (id) => request.get(`/api/v1/chat/sessions/${id}/messages`)

// ========== 反馈 ==========
export const submitFeedback = (data) => request.post('/api/v1/feedback', data)

// ========== 任务 ==========
export const getTasks = (params) => request.get('/api/v1/tasks', { params })
export const createTask = (data) => request.post('/api/v1/tasks', data)
export const updateTask = (id, data) => request.put(`/api/v1/tasks/${id}`, data)
export const completeTask = (id) => request.post(`/api/v1/tasks/${id}/complete`)
export const deleteTask = (id) => request.delete(`/api/v1/tasks/${id}`)

// ========== 提醒规则 ==========
export const getReminderRules = () => request.get('/api/v1/reminder-rules')
export const createReminderRule = (data) => request.post('/api/v1/reminder-rules', data)
export const updateReminderRule = (id, data) => request.put(`/api/v1/reminder-rules/${id}`, data)
export const deleteReminderRule = (id) => request.delete(`/api/v1/reminder-rules/${id}`)
export const runReminderRules = () => request.post('/api/v1/reminder-rules/run')

// ========== 通知 ==========
export const getNotifications = (params) => request.get('/api/v1/notifications', { params })
export const markNotificationRead = (id) => request.post(`/api/v1/notifications/${id}/read`)
export const markAllNotificationsRead = () => request.post('/api/v1/notifications/read_all')

// ========== 报告 ==========
export const generateReport = (data) => request.post('/api/v1/reports/generate', data)
export const getReports = (params) => request.get('/api/v1/reports', { params })
export const getReport = (id) => request.get(`/api/v1/reports/${id}`)
export const deleteReport = (id) => request.delete(`/api/v1/reports/${id}`)
// 自定义报告：对话式修改 / 工作台资料库
export const reviseReport = (id, instruction) => request.post(`/api/v1/reports/${id}/revise`, { instruction })
export const getWorkbenchKb = (create = true) => request.get(`/api/v1/reports/workbench-kb?create=${create}`)
// HTML 报告导出 A4 PDF（仅 format=html 且 ready 可用）
export const exportReportPdf = (id) => request.get(`/api/v1/reports/${id}/pdf`, { responseType: 'blob' })
// 演示版（16:9 幻灯片）导出横向 PDF，一页一幻灯片
export const exportReportPresentationPdf = (id) => request.get(`/api/v1/reports/${id}/presentation.pdf`, { responseType: 'blob' })
// 同步客户端安装包（OneDrive 式本地同步 App；文件名随系统品牌名）
export const getSyncAppVersion = () => request.get('/api/v1/sync-app/version')
export const downloadSyncApp = () => request.get('/api/v1/sync-app/download', { responseType: 'blob' })

// ========== 每日工作台 / 全局搜索 / 随手记 ==========
export const getDashboardToday = () => request.get('/api/v1/dashboard/today')
export const getDailyReport = (params) => request.get('/api/v1/dashboard/daily-report', { params })
export const quickCapture = (data) => request.post('/api/v1/dashboard/capture', data)
export const globalSearch = (q) => request.get('/api/v1/search/global', { params: { q } })

// ========== 工作流 ==========
export const getWorkflows = () => request.get('/api/v1/workflows')
export const createWorkflow = (data) => request.post('/api/v1/workflows', data)
export const updateWorkflow = (id, data) => request.put(`/api/v1/workflows/${id}`, data)
export const deleteWorkflow = (id) => request.delete(`/api/v1/workflows/${id}`)
export const runWorkflow = (id) => request.post(`/api/v1/workflows/${id}/run`)
export const getWorkflowRuns = (id, params) => request.get(`/api/v1/workflows/${id}/runs`, { params })

// ========== 知识库（多库模型） ==========
export const getKbs = () => request.get('/api/v1/kbs')
export const createKb = (data) => request.post('/api/v1/kbs', data)
export const updateKb = (id, data) => request.put(`/api/v1/kbs/${id}`, data)
export const deleteKb = (id) => request.delete(`/api/v1/kbs/${id}`)
export const getKbDocuments = (id) => request.get(`/api/v1/kbs/${id}/documents`)
export const associateKbDocuments = (id, fileIds) => request.post(`/api/v1/kbs/${id}/documents`, { file_ids: fileIds })
export const removeKbDocument = (id, docId) => request.delete(`/api/v1/kbs/${id}/documents/${docId}`)
export const reparseKbDocument = (id, docId) => request.post(`/api/v1/kbs/${id}/documents/${docId}/reparse`)
export const visionReviewKbDocument = (id, docId) => request.post(`/api/v1/kbs/${id}/documents/${docId}/vision-review`)
export const getKbDocumentChunks = (id, docId) => request.get(`/api/v1/kbs/${id}/documents/${docId}/chunks`)
// 文档版本历史
export const getDocVersions = (kbId, docId) => request.get(`/api/v1/kbs/${kbId}/documents/${docId}/versions`)
export const getDocVersion = (kbId, docId, vid) => request.get(`/api/v1/kbs/${kbId}/documents/${docId}/versions/${vid}`)
export const restoreDocVersion = (kbId, docId, vid) => request.post(`/api/v1/kbs/${kbId}/documents/${docId}/versions/${vid}/restore`)
// 命中测试
export const hitTestKb = (id, data) => request.post(`/api/v1/kbs/${id}/hit-test`, data)

// ========== 文档库（文件中心） ==========
export const getLibraryTree = () => request.get('/api/v1/library/tree')
export const createLibraryFolder = (data) => request.post('/api/v1/library/folders', data)
export const updateLibraryFolder = (id, data) => request.put(`/api/v1/library/folders/${id}`, data)
export const deleteLibraryFolder = (id, recursive = true) =>
  request.delete(`/api/v1/library/folders/${id}`, { params: { recursive } })
export const getLibraryFiles = (params) => request.get('/api/v1/library/files', { params })
// 文件内容（带 token 拉 blob，前端生成 Object URL 预览）
export const getLibraryFileContent = (id) =>
  request.get(`/api/v1/library/files/${id}/content`, { responseType: 'blob', timeout: 120000 })
// 短时效（5 分钟）文件访问令牌：<img>/<video>/<pdf> 直链流式预览用，避免把登录 JWT 放进 URL
export const getFileContentToken = (id) => request.get(`/api/v1/library/files/${id}/token`)
// Office 文本化预览（doc/ppt/pptx/xls/xlsx 后端提取正文）
export const getLibraryFilePreviewText = (id) => request.get(`/api/v1/library/files/${id}/preview-text`)
// tif/tiff 转 PNG 预览
export const getLibraryFilePreviewPng = (id) =>
  request.get(`/api/v1/library/files/${id}/preview.png`, { responseType: 'blob', timeout: 120000 })
// PST 拆解进度（上传面板轮询）
export const getPstProgress = (id) => request.get(`/api/v1/library/files/${id}/pst-progress`)
export const updateLibraryFile = (id, data) => request.put(`/api/v1/library/files/${id}`, data)
export const deleteLibraryFile = (id) => request.delete(`/api/v1/library/files/${id}`)
export const associateLibraryFiles = (fileIds, kbIds) =>
  request.post('/api/v1/library/associate', { file_ids: fileIds, kb_ids: kbIds })

// ========== 客户画像 / 专属知识库 ==========
export const getCustomerProfile = (id) => request.get(`/api/v1/customers/${id}/profile`)
export const generateCustomerProfile = (id) => request.post(`/api/v1/customers/${id}/profile/generate`)
export const getCustomerKb = (id) => request.get(`/api/v1/customers/${id}/kb`)

// ========== 系统管理（admin） ==========
// 用户
export const getAdminUsers = (params) => request.get('/api/v1/admin/users', { params })
export const createAdminUser = (data) => request.post('/api/v1/admin/users', data)
export const updateAdminUser = (id, data) => request.put(`/api/v1/admin/users/${id}`, data)
export const resetAdminUserPassword = (id, newPassword) =>
  request.put(`/api/v1/admin/users/${id}/password`, { new_password: newPassword })
export const deleteAdminUser = (id) => request.delete(`/api/v1/admin/users/${id}`)
// 分组
export const getAdminGroups = () => request.get('/api/v1/admin/groups')
export const createAdminGroup = (data) => request.post('/api/v1/admin/groups', data)
export const updateAdminGroup = (id, data) => request.put(`/api/v1/admin/groups/${id}`, data)
export const deleteAdminGroup = (id) => request.delete(`/api/v1/admin/groups/${id}`)
// 大模型
export const getLlmModels = () => request.get('/api/v1/admin/llm/models')
export const createLlmModel = (data) => request.post('/api/v1/admin/llm/models', data)
export const updateLlmModel = (id, data) => request.put(`/api/v1/admin/llm/models/${id}`, data)
export const deleteLlmModel = (id) => request.delete(`/api/v1/admin/llm/models/${id}`)
export const setLlmModelDefault = (id) => request.post(`/api/v1/admin/llm/models/${id}/default`)
export const testLlmModel = (id) => request.post(`/api/v1/admin/llm/models/${id}/test`)
// 远程模型列表拉取 / 表单未保存配置测试
export const fetchRemoteModels = (payload) => request.post('/api/v1/admin/llm/models/fetch-remote', payload)
export const testLlmConfig = (payload) => request.post('/api/v1/admin/llm/models/test-config', payload)
// 用量 / 系统监控 / 异常日志
export const getLlmStats = (params) => request.get('/api/v1/admin/llm/stats', { params })
export const getSystemOverview = () => request.get('/api/v1/admin/system/overview')
// 数据库维护：vacuum 回收空间 / reindex 重建向量索引（后台执行）
export const runMaintenance = (action) => request.post('/api/v1/admin/system/maintenance', null, { params: { action } })
// 备份 / 恢复
export const createBackup = () => request.post('/api/v1/admin/system/backups')
export const getBackups = () => request.get('/api/v1/admin/system/backups')
export const restoreBackup = (name) => request.post(`/api/v1/admin/system/backups/${name}/restore`)
// 沙箱数据重置（危险操作，仅 dev/sandbox 环境可用）
export const resetSandbox = () => request.post('/api/v1/admin/system/reset-sandbox')

// 系统更新（裸机部署；UPDATE_SCRIPT 未配置时 enabled=false，前端不显示按钮）
export const getSystemUpdateInfo = () => request.get('/api/v1/admin/system/update-info')
export const checkSystemUpdate = () => request.post('/api/v1/admin/system/check-update')
export const runSystemUpdate = () => request.post('/api/v1/admin/system/update')
export const getAdminErrors = (params) => request.get('/api/v1/admin/errors', { params })
export const resolveAdminError = (id) => request.post(`/api/v1/admin/errors/${id}/resolve`)
export const resolveAllAdminErrors = () => request.post('/api/v1/admin/errors/resolve_all')
// 回收站
export const getRecycleBin = (params) => request.get('/api/v1/recycle-bin', { params })
export const restoreRecycleItem = (type, id) => request.post(`/api/v1/recycle-bin/${type}/${id}/restore`)
export const deleteRecycleItem = (type, id) => request.delete(`/api/v1/recycle-bin/${type}/${id}`)
// 批量彻底删除（单项失败不影响其余）
export const purgeRecycleBatch = (items) => request.post('/api/v1/recycle-bin/purge', { items })
// 审计日志
export const getAuditLogs = (params) => request.get('/api/v1/admin/audit-logs', { params })
// Skill 管理
export const getAdminSkills = () => request.get('/api/v1/admin/skills')
export const createSkill = (data) => request.post('/api/v1/admin/skills', data)
export const updateSkill = (id, data) => request.put(`/api/v1/admin/skills/${id}`, data)
export const deleteSkill = (id) => request.delete(`/api/v1/admin/skills/${id}`)
export const testSkill = (id, args) => request.post(`/api/v1/admin/skills/${id}/test`, { args })

// ========== MCP server 管理（PR-F）==========
export const getMcpServers = () => request.get('/api/v1/admin/mcp-servers')
export const createMcpServer = (data) => request.post('/api/v1/admin/mcp-servers', data)
export const updateMcpServer = (id, data) => request.put(`/api/v1/admin/mcp-servers/${id}`, data)
export const deleteMcpServer = (id) => request.delete(`/api/v1/admin/mcp-servers/${id}`)
export const connectMcpServer = (id) => request.post(`/api/v1/admin/mcp-servers/${id}/connect`)
export const disconnectMcpServer = (id) => request.post(`/api/v1/admin/mcp-servers/${id}/disconnect`)
export const syncMcpTools = (id) => request.post(`/api/v1/admin/mcp-servers/${id}/sync-tools`)
export const getMcpTools = (id) => request.get(`/api/v1/admin/mcp-servers/${id}/tools`)

// ========== Notebook / Note（PR-G Studio，工作区）==========
export const getNotebooks = () => request.get('/api/v1/notebooks')
export const createNotebook = (data) => request.post('/api/v1/notebooks', data)
export const getNotebook = (id) => request.get(`/api/v1/notebooks/${id}`)
export const updateNotebook = (id, data) => request.put(`/api/v1/notebooks/${id}`, data)
export const deleteNotebook = (id) => request.delete(`/api/v1/notebooks/${id}`)
export const getNotebookNotes = (id) => request.get(`/api/v1/notebooks/${id}/notes`)
export const createNotebookNote = (id, data) => request.post(`/api/v1/notebooks/${id}/notes`, data)
export const updateNote = (id, data) => request.put(`/api/v1/notebooks/notes/${id}`, data)
export const deleteNote = (id) => request.delete(`/api/v1/notebooks/notes/${id}`)
export const createNoteFromChat = (data) => request.post('/api/v1/notebooks/notes/from-chat-message', data)
export const saveNoteAsDocument = (id, kb_id) =>
  request.post(`/api/v1/notebooks/notes/${id}/save-as-document`, { kb_id })

// ========== 行业 ==========
export const getIndustries = (params) => request.get('/api/v1/industries', { params })
export const createIndustry = (data) => request.post('/api/v1/industries', data)
export const updateIndustry = (id, data) => request.put(`/api/v1/industries/${id}`, data)
export const deleteIndustry = (id) => request.delete(`/api/v1/industries/${id}`)

export default request
