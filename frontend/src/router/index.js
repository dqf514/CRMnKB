import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
  },
  {
    // 同步 App「打开网页版」免登：一次性 code 换 token 后跳工作台
    path: '/sso',
    name: 'Sso',
    component: () => import('../views/Sso.vue'),
  },
  {
    path: '/',
    component: () => import('../layouts/AppLayout.vue'),
    redirect: '/today',  // 今日门户为系统首页
    children: [
      {
        path: 'today',
        name: 'Today',
        component: () => import('../views/Today.vue'),
        meta: { title: '今日' },
      },
      {
        path: 'customers',
        name: 'Customers',
        component: () => import('../views/Customers.vue'),
        meta: { title: '客户管理' },
      },
      {
        path: 'customers/:id',
        name: 'CustomerDetail',
        component: () => import('../views/CustomerDetail.vue'),
        meta: { title: '客户详情' },
      },
      {
        path: 'tasks',
        name: 'Tasks',
        component: () => import('../views/Tasks.vue'),
        meta: { title: '任务提醒' },
      },
      {
        path: 'workflows',
        name: 'Workflows',
        component: () => import('../views/Workflows.vue'),
        meta: { title: '工作流' },
      },
      {
        // 已整合进工作台：老入口 /reports 直接跳转
        path: 'reports',
        redirect: '/studio',
      },
      {
        path: 'profile',
        name: 'Profile',
        component: () => import('../views/Profile.vue'),
        meta: { title: '个人中心' },
      },
      {
        // Agent 审批：admin 处理全租户审批单，普通用户查看自己发起的（dsh Agent 敏感工具调用）
        path: 'agent-approvals',
        name: 'AgentApprovals',
        component: () => import('../views/AgentApprovals.vue'),
        meta: { title: 'Agent 审批' },
      },
      {
        path: 'knowledge',
        name: 'Knowledge',
        component: () => import('../views/Knowledge.vue'),
        meta: { title: '知识库' },
      },
      {
        path: 'knowledge/:id',
        name: 'KbDetail',
        component: () => import('../views/KbDetail.vue'),
        meta: { title: '知识库详情' },
      },
      {
        path: 'library',
        name: 'Library',
        component: () => import('../views/Library.vue'),
        meta: { title: '文档库' },
      },
      {
        // 已合并进工作台：老入口 /chat 直接跳转
        path: 'chat',
        redirect: '/studio',
      },
      // ========== 系统管理（admin） ==========
      {
        path: 'admin/users',
        name: 'AdminUsers',
        component: () => import('../views/admin/Users.vue'),
        meta: { title: '用户管理', admin: true },
      },
      {
        path: 'admin/groups',
        name: 'AdminGroups',
        component: () => import('../views/admin/Groups.vue'),
        meta: { title: '用户分组', admin: true },
      },
      {
        path: 'admin/llm/models',
        name: 'AdminLlmModels',
        component: () => import('../views/admin/LlmModels.vue'),
        meta: { title: '模型管理', admin: true },
      },
      {
        path: 'admin/llm/stats',
        name: 'AdminLlmStats',
        component: () => import('../views/admin/LlmStats.vue'),
        meta: { title: '用量监控', admin: true },
      },
      {
        path: 'admin/system',
        name: 'AdminSystem',
        component: () => import('../views/admin/System.vue'),
        meta: { title: '系统监控', admin: true },
      },
      {
        path: 'admin/errors',
        name: 'AdminErrors',
        component: () => import('../views/admin/Errors.vue'),
        meta: { title: '异常日志', admin: true },
      },
      {
        path: 'admin/recycle-bin',
        name: 'AdminRecycleBin',
        component: () => import('../views/admin/RecycleBin.vue'),
        meta: { title: '回收站', admin: true },
      },
      {
        path: 'admin/audit-logs',
        name: 'AdminAuditLogs',
        component: () => import('../views/admin/AuditLogs.vue'),
        meta: { title: '审计日志', admin: true },
      },
      {
        path: 'admin/skills',
        name: 'AdminSkills',
        component: () => import('../views/admin/Skills.vue'),
        meta: { title: 'Skill 管理', admin: true },
      },
      {
        path: 'studio',
        name: 'Studio',
        component: () => import('../views/Studio.vue'),
        meta: { title: '工作台' },
      },
      {
        path: 'admin/mcp-servers',
        name: 'AdminMcpServers',
        component: () => import('../views/admin/McpServers.vue'),
        meta: { title: 'MCP Server 管理', admin: true },
      },
      {
        path: 'admin/settings',
        name: 'AdminSettings',
        component: () => import('../views/admin/SystemSettings.vue'),
        meta: { title: '系统设置', admin: true },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (!token && to.path !== '/login' && to.path !== '/sso') {
    return '/login'
  }
  if (token && to.path === '/login') {
    return '/today'
  }
  // 系统管理页仅 admin 可访问
  if (to.matched.some((r) => r.meta?.admin)) {
    let role = null
    try {
      role = JSON.parse(localStorage.getItem('user') || 'null')?.role
    } catch { /* 忽略 */ }
    if (role !== 'admin') {
      ElMessage.warning('系统管理仅管理员可访问')
      return '/customers'
    }
  }
})

export default router
