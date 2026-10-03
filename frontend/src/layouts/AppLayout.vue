<template>
  <el-container class="layout">
    <!-- 桌面端固定侧边栏（240px，Manus 风：品牌 → 新的工作区/搜索 → 工作区列表 → 底部导航 → 用户行） -->
    <el-aside v-if="!isMobile" width="240px" class="aside">
      <!-- 品牌行 -->
      <div class="brand">
        <img class="brand-logo" :src="brandStore.logoUrl" :alt="brandStore.systemName" />
        <span class="brand-name">{{ brandStore.systemName }}</span>
      </div>

      <!-- 主操作：新的工作区 + 搜索 -->
      <div class="side-actions">
        <button class="btn-new-task" @click="createNewTask">
          <el-icon :size="15"><Plus /></el-icon>
          <span>新的工作区</span>
        </button>
        <button class="btn-search" @click="openGlobalSearch">
          <el-icon :size="15"><Search /></el-icon>
          <span>搜索</span>
          <kbd class="kbd">Ctrl K</kbd>
        </button>
      </div>

      <!-- 中部弹性滚动区：工作区列表；admin 处于 /admin 路由时替换为管理菜单 -->
      <div class="side-scroll">
        <template v-if="showAdminMenu">
          <!-- 返回工作台：恢复最近打开的工作区（studio 自动 reopen 上次的 Notebook） -->
          <button class="btn-back" @click="goBackFromAdmin">
            <el-icon :size="15"><Back /></el-icon>
            <span>返回工作台</span>
          </button>
          <div v-for="g in adminGroups" :key="g.key" class="side-group">
            <div class="side-group-label">{{ g.label }}</div>
            <div
              v-for="m in g.items"
              :key="m.path"
              class="nav-item"
              :class="{ active: isNavActive(m.path) }"
              @click="router.push(m.path)"
            >
              <el-icon :size="16"><component :is="m.icon" /></el-icon>
              <span>{{ m.label }}</span>
            </div>
          </div>
        </template>
        <template v-else>
          <div class="side-group-label">工作区</div>
          <SidebarWorkspaces />
        </template>
      </div>

      <!-- 底部导航组（admin 管理菜单展开时隐藏，避免与常规导航堆叠） -->
      <template v-if="!showAdminMenu">
        <div class="side-divider" />
        <nav class="side-nav">
        <template v-for="item in navItems" :key="item.key || item.path">
          <!-- 带子项的分组：点组头展开/收起，子项缩进排列 -->
          <template v-if="item.children">
            <div
              class="nav-item"
              :class="{ active: isGroupActive(item) }"
              @click="toggleGroup(item.key)"
            >
              <el-icon :size="16"><component :is="item.icon" /></el-icon>
              <span>{{ item.label }}</span>
              <el-icon :size="12" class="nav-caret" :class="{ open: groupOpen[item.key] }">
                <ArrowDown />
              </el-icon>
            </div>
            <div v-show="groupOpen[item.key]" class="nav-children">
              <div
                v-for="c in item.children"
                :key="c.path"
                class="nav-item nav-child"
                :class="{ active: isNavActive(c.path) }"
                @click="router.push(c.path)"
              >
                <el-icon :size="15"><component :is="c.icon" /></el-icon>
                <span>{{ c.label }}</span>
              </div>
            </div>
          </template>
          <div
            v-else
            class="nav-item"
            :class="{ active: isNavActive(item.path) }"
            @click="router.push(item.path)"
          >
            <el-icon :size="16"><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
          </div>
        </template>
        </nav>
      </template>

      <!-- 用户行：头像姓名（下拉：个人中心/同步App/退出）+ 通知 + 明暗 + 设置(admin) -->
      <div class="user-row">
        <el-dropdown trigger="click" @command="handleCommand">
          <span class="user-info">
            <el-icon><Avatar /></el-icon>
            <span class="user-name">{{ authStore.user?.name || authStore.user?.username || '用户' }}</span>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="profile">个人中心</el-dropdown-item>
              <el-dropdown-item command="syncapp">下载同步 App</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <div class="user-actions">
          <!-- 通知铃铛 -->
          <el-popover placement="top-end" :width="360" trigger="click" @show="loadNotifications">
            <template #reference>
              <el-badge :value="unreadCount" :hidden="!unreadCount" :max="99" class="bell-badge">
                <el-icon :size="17" class="icon-btn"><Bell /></el-icon>
              </el-badge>
            </template>
            <div class="notif-header">
              <span>通知</span>
              <el-button link type="primary" size="small" @click="handleReadAll">全部已读</el-button>
            </div>
            <div class="notif-list" v-loading="notifLoading">
              <div
                v-for="n in notifications"
                :key="n.id"
                class="notif-item"
                :class="{ unread: !n.is_read }"
                @click="handleRead(n)"
              >
                <div class="notif-title">{{ n.title }}</div>
                <div class="notif-content">{{ n.content }}</div>
                <div class="notif-time">{{ formatDateTime(n.created_at) }}</div>
              </div>
              <el-empty v-if="!notifLoading && !notifications.length" description="暂无通知" :image-size="60" />
            </div>
          </el-popover>
          <!-- 明暗切换（主题色/字号在「个人中心」里设置） -->
          <el-tooltip :content="themeStore.mode === 'dark' ? '切换到亮色' : '切换到暗色'" placement="top">
            <el-icon :size="17" class="icon-btn" @click="themeStore.toggleMode()">
              <Sunny v-if="themeStore.mode === 'dark'" />
              <Moon v-else />
            </el-icon>
          </el-tooltip>
          <!-- 系统设置入口（仅 admin） -->
          <el-tooltip v-if="isAdmin" content="系统设置" placement="top">
            <el-icon :size="17" class="icon-btn" @click="router.push('/admin/system')">
              <Setting />
            </el-icon>
          </el-tooltip>
        </div>
      </div>
    </el-aside>

    <el-container>
      <!-- 移动端顶栏：品牌 + 铃铛 -->
      <el-header v-if="isMobile" class="m-header">
        <div class="m-brand">
          <!-- admin 页：显眼的返回入口（移动端没有侧栏管理菜单） -->
          <el-icon v-if="showAdminMenu" :size="20" class="m-back" @click="goBackFromAdmin"><Back /></el-icon>
          <img class="brand-logo brand-logo-sm" :src="brandStore.logoUrl" :alt="brandStore.systemName" />
          <span class="brand-name">{{ brandStore.systemName }}</span>
        </div>
        <el-popover placement="bottom-end" :width="360" trigger="click" @show="loadNotifications">
          <template #reference>
            <el-badge :value="unreadCount" :hidden="!unreadCount" :max="99" class="bell-badge">
              <el-icon :size="18" class="icon-btn"><Bell /></el-icon>
            </el-badge>
          </template>
          <div class="notif-header">
            <span>通知</span>
            <el-button link type="primary" size="small" @click="handleReadAll">全部已读</el-button>
          </div>
          <div class="notif-list" v-loading="notifLoading">
            <div
              v-for="n in notifications"
              :key="n.id"
              class="notif-item"
              :class="{ unread: !n.is_read }"
              @click="handleRead(n)"
            >
              <div class="notif-title">{{ n.title }}</div>
              <div class="notif-content">{{ n.content }}</div>
              <div class="notif-time">{{ formatDateTime(n.created_at) }}</div>
            </div>
            <el-empty v-if="!notifLoading && !notifications.length" description="暂无通知" :image-size="60" />
          </div>
        </el-popover>
      </el-header>

      <el-main class="main">
        <!-- 沙箱环境常驻横幅 -->
        <el-alert
          v-if="brandStore.env === 'sandbox'"
          type="warning"
          :closable="false"
          title="沙箱环境：当前数据用于试用验证，正式上线前将清空"
          style="margin-bottom: 16px"
        />
        <router-view />
      </el-main>
    </el-container>

    <!-- 移动端底部标签栏：今日/工作台/知识库/客户/更多 -->
    <nav v-if="isMobile" class="tabbar">
      <div
        v-for="t in tabItems"
        :key="t.path"
        class="tab-item"
        :class="{ active: isTabActive(t.path) }"
        @click="router.push(t.path)"
      >
        <el-icon :size="20"><component :is="t.icon" /></el-icon>
        <span>{{ t.label }}</span>
      </div>
      <div class="tab-item" :class="{ active: isMoreActive }" @click="moreDrawer = true">
        <el-icon :size="20"><MoreFilled /></el-icon>
        <span>更多</span>
      </div>
    </nav>

    <!-- 更多菜单面板（移动端） -->
    <el-drawer v-model="moreDrawer" direction="btt" size="auto" title="更多功能" class="more-drawer">
      <div class="more-list">
        <div
          v-for="m in moreMenus"
          :key="m.path"
          class="more-item"
          @click="moreDrawer = false; router.push(m.path)"
        >
          <el-icon :size="20"><component :is="m.icon" /></el-icon>
          <span>{{ m.label }}</span>
          <el-icon :size="14" class="more-arrow"><ArrowRight /></el-icon>
        </div>
        <template v-if="isAdmin">
          <div class="more-section">系统管理</div>
          <template v-for="g in adminGroups" :key="g.key">
            <div class="more-group-label">{{ g.label }}</div>
            <div
              v-for="m in g.items"
              :key="m.path"
              class="more-item"
              @click="moreDrawer = false; router.push(m.path)"
            >
              <el-icon :size="20"><component :is="m.icon" /></el-icon>
              <span>{{ m.label }}</span>
              <el-icon :size="14" class="more-arrow"><ArrowRight /></el-icon>
            </div>
          </template>
        </template>
      </div>
    </el-drawer>

    <!-- 全局上传进度（右下角悬浮 + 侧边栏面板） -->
    <UploadProgressPanel />
    <!-- 全局搜索（Ctrl+K / 侧栏「搜索」按钮） -->
    <GlobalSearch />
  </el-container>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  User, UserFilled, List, SetUp, Collection, Avatar, ArrowDown, Bell, Notebook,
  Sunny, Moon, Files, MoreFilled, ArrowRight, Plus, Search,
  Setting, Cpu, DataAnalysis, Monitor, WarningFilled, Delete, Tickets, MagicStick, Connection,
  Stamp, Back,
} from '@element-plus/icons-vue'
import { ElMessageBox, ElMessage } from 'element-plus'
import { confirmDanger } from '../utils/confirmDanger'
import { useAuthStore } from '../stores/auth'
import { useBrandStore } from '../stores/brand'
import { useThemeStore } from '../stores/theme'
import { useStudioStore } from '../stores/studio'
import UploadProgressPanel from '../components/UploadProgressPanel.vue'
import GlobalSearch from '../components/GlobalSearch.vue'
import SidebarWorkspaces from '../components/SidebarWorkspaces.vue'
import { getNotifications, markNotificationRead, markAllNotificationsRead, downloadSyncApp, getSyncAppVersion } from '../api'
import { formatDateTime } from '../utils/format'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const brandStore = useBrandStore()
const themeStore = useThemeStore()
const studioStore = useStudioStore()

// 底部导航组：今日 / 知识中心（知识库+文档库）/ 客户管理（客户+任务+工作流）/ Agent 审批
const navItems = [
  { path: '/today', label: '今日', icon: Sunny },
  {
    key: 'knowledge', label: '知识中心', icon: Collection,
    children: [
      { path: '/knowledge', label: '知识库', icon: Collection },
      { path: '/library', label: '文档库', icon: Files },
    ],
  },
  {
    key: 'customers', label: '客户管理', icon: User,
    children: [
      { path: '/customers', label: '客户列表', icon: User },
      { path: '/tasks', label: '任务提醒', icon: List },
      { path: '/workflows', label: '工作流', icon: SetUp },
    ],
  },
  { path: '/agent-approvals', label: 'Agent 审批', icon: Stamp },
]

// 导航分组展开状态（默认展开）
const groupOpen = reactive({ knowledge: true, customers: true })
function toggleGroup(key) {
  groupOpen[key] = !groupOpen[key]
}

// 系统管理菜单（仅 admin），按职能分组聚合；admin 处于 /admin 路由时替换侧栏工作区列表
const isAdmin = computed(() => authStore.user?.role === 'admin')
const adminGroups = [
  {
    key: 'users',
    label: '用户权限',
    items: [
      { path: '/admin/users', label: '用户管理', icon: User },
      { path: '/admin/groups', label: '用户分组', icon: UserFilled },
      { path: '/admin/audit-logs', label: '审计日志', icon: Tickets },
    ],
  },
  {
    key: 'ai',
    label: 'AI 模型',
    items: [
      { path: '/admin/llm/models', label: '模型管理', icon: Cpu },
      { path: '/admin/llm/stats', label: '用量监控', icon: DataAnalysis },
      { path: '/admin/skills', label: 'Skill 管理', icon: MagicStick },
      { path: '/admin/mcp-servers', label: 'MCP Server', icon: Connection },
    ],
  },
  {
    key: 'ops',
    label: '系统运维',
    items: [
      { path: '/admin/system', label: '系统监控', icon: Monitor },
      { path: '/admin/errors', label: '异常日志', icon: WarningFilled },
      { path: '/admin/recycle-bin', label: '回收站', icon: Delete },
      { path: '/admin/settings', label: '系统设置', icon: Setting },
    ],
  },
]
const adminPaths = adminGroups.flatMap((g) => g.items.map((m) => m.path))
const showAdminMenu = computed(() => isAdmin.value && route.path.startsWith('/admin'))

// 从系统管理返回工作台：/studio 会自动恢复最近打开的工作区（studio store 记 LAST_NB_KEY）
function goBackFromAdmin() {
  router.push('/studio')
}

// ========== 侧栏主操作 ==========
// 「新的工作区」：新建工作区并跳到工作台（沿用 Studio 原 TopBar 的新建逻辑）
async function createNewTask() {
  const { value: name } = await ElMessageBox.prompt('输入新工作区名称', '新的工作区', {
    inputPattern: /.+/,
    inputErrorMessage: '名称不能为空',
    inputPlaceholder: '如：Q3 客户投诉分析',
  }).catch(() => ({ value: null }))
  if (!name) return
  const nb = await studioStore.createNotebook({
    name,
    description: '',
    source_kb_ids: [],
    source_file_ids: [],
  })
  ElMessage.success('已创建')
  await studioStore.openNotebook(nb.id)
  await studioStore.loadChatHistory(nb.id)
  router.push('/studio')
}

// 「搜索」：与 Ctrl+K 同一入口（GlobalSearch 监听该自定义事件）
function openGlobalSearch() {
  window.dispatchEvent(new Event('kb:open-global-search'))
}

// ========== 响应式 ==========
const isMobile = ref(window.innerWidth < 992)
const moreDrawer = ref(false)
function onResize() {
  isMobile.value = window.innerWidth < 992
  if (!isMobile.value) moreDrawer.value = false
}

// 移动端底部标签栏：今日/工作台/知识库/客户 + 更多
const tabItems = [
  { path: '/today', label: '今日', icon: Sunny },
  { path: '/studio', label: '工作台', icon: Notebook },
  { path: '/knowledge', label: '知识库', icon: Collection },
  { path: '/customers', label: '客户', icon: User },
]
// "更多"面板平铺项：文档库/任务提醒/工作流/Agent 审批
const moreMenus = [
  { path: '/library', label: '文档库', icon: Files },
  { path: '/tasks', label: '任务提醒', icon: List },
  { path: '/workflows', label: '工作流', icon: SetUp },
  { path: '/agent-approvals', label: 'Agent 审批', icon: Stamp },
]

function isNavActive(path) {
  return route.path === path || route.path.startsWith(path + '/')
}
function isGroupActive(item) {
  return (item.children || []).some((c) => isNavActive(c.path))
}
function isTabActive(path) {
  return route.path === path || route.path.startsWith(path + '/')
}
const isMoreActive = computed(() =>
  moreMenus.some((m) => isTabActive(m.path)) || adminPaths.some((p) => isTabActive(p))
)

// ========== 通知铃铛 ==========
const unreadCount = ref(0)
const notifications = ref([])
const notifLoading = ref(false)
let notifTimer = null
// 桌面通知：已弹过的最新通知 id（防重复弹）
let lastDesktopNotifiedId = Number(localStorage.getItem('last-desktop-notified-id') || 0)

async function pollUnread() {
  try {
    const res = await getNotifications({ unread_only: false })
    unreadCount.value = res.unread_count || 0
    // 有新的未读通知且浏览器已授权 → 弹桌面通知（页面在后台也能看到）
    const latestUnread = (res.items || []).find((n) => !n.is_read)
    if (
      latestUnread &&
      latestUnread.id > lastDesktopNotifiedId &&
      'Notification' in window &&
      Notification.permission === 'granted'
    ) {
      lastDesktopNotifiedId = latestUnread.id
      localStorage.setItem('last-desktop-notified-id', String(latestUnread.id))
      try {
        new Notification(latestUnread.title || '新通知', {
          body: latestUnread.content || '',
          icon: brandStore.logoUrl,
        })
      } catch { /* 部分环境不支持构造调用，忽略 */ }
    }
  } catch {
    /* 轮询失败静默 */
  }
}

async function loadNotifications() {
  notifLoading.value = true
  try {
    const res = await getNotifications({ unread_only: false })
    notifications.value = res.items || []
    unreadCount.value = res.unread_count || 0
  } finally {
    notifLoading.value = false
  }
}

async function handleRead(n) {
  if (!n.is_read) {
    await markNotificationRead(n.id)
    n.is_read = true
    unreadCount.value = Math.max(0, unreadCount.value - 1)
  }
  // 分享/报告/审批通知：点击直达对应资源（已读也允许点击跳转，报告跳到工作台并自动打开该报告）
  if (n.resource_type && n.resource_id) {
    if (n.resource_type === 'report') {
      router.push({ path: '/studio', query: { report: n.resource_id } })
    } else {
      const routeMap = { kb: `/knowledge/${n.resource_id}`, file: '/library', notebook: '/studio', agent_approval: '/agent-approvals' }
      const to = routeMap[n.resource_type]
      if (to) router.push(to)
    }
  } else if (n.resource_type === 'agent_approval') {
    // 审批通知的 resource_id 可能为空：仍跳到审批页
    router.push('/agent-approvals')
  }
}

async function handleReadAll() {
  await markAllNotificationsRead()
  notifications.value.forEach((n) => { n.is_read = true })
  unreadCount.value = 0
  ElMessage.success('已全部标记为已读')
}

async function handleCommand(cmd) {
  if (cmd === 'profile') {
    router.push('/profile')
  } else if (cmd === 'syncapp') {
    // 下载 OneDrive 式本地同步客户端（文件名随系统品牌名）
    try {
      const [ver, blob] = await Promise.all([getSyncAppVersion(), downloadSyncApp()])
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = ver?.filename || '知识库同步.exe'
      a.click()
      setTimeout(() => URL.revokeObjectURL(a.href), 1000)
      ElMessage.success('下载完成，双击安装包按向导设置即可开始同步')
    } catch (err) {
      if (err?.response?.status === 404) ElMessage.warning('同步客户端尚未发布，请联系管理员')
    }
  } else if (cmd === 'logout') {
    const ok = await confirmDanger('确定退出登录吗？', '提示')
    if (!ok) return
    authStore.logout()
    router.push('/login')
  }
}

onMounted(() => {
  themeStore.apply()
  themeStore.syncFromServer()
  brandStore.load()
  window.addEventListener('resize', onResize)
  // 桌面通知授权（只问一次；HTTPS/localhost 才支持，http 局域网 IP 下静默跳过）
  try {
    if (
      'Notification' in window &&
      Notification.permission === 'default' &&
      !localStorage.getItem('desktop-notify-asked')
    ) {
      localStorage.setItem('desktop-notify-asked', '1')
      Notification.requestPermission()
    }
  } catch { /* 不支持的环境静默 */ }
  pollUnread()
  // 每 30 秒轮询未读数
  notifTimer = setInterval(pollUnread, 30000)
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  if (notifTimer) clearInterval(notifTimer)
})
</script>

<style scoped>
.layout {
  height: 100vh;
  /* root zoom 放大时按比例回退，保证壳高度=物理视口高度，页面不再整体滚动 */
  height: calc(100vh / var(--app-zoom, 1));
  overflow: hidden;
}

/* ========== 侧栏（Manus 风：浅灰底、圆角菜单项） ========== */
.aside {
  background: var(--sidebar-bg);
  border-right: 1px solid var(--app-line);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 16px 10px;
}
.brand-logo {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  flex: none;
  box-shadow: 0 4px 12px rgba(37, 99, 235, 0.28);
}
.brand-logo-sm {
  width: 24px;
  height: 24px;
  box-shadow: none;
}
.brand-name {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--app-ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 主操作按钮 */
.side-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 4px 12px 10px;
}
.btn-new-task {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  padding: 8px 0;
  border: none;
  border-radius: 8px;
  background: var(--el-color-primary);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease;
}
.btn-new-task:hover {
  background: var(--el-color-primary-dark-2);
}
.btn-search {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 7px 10px;
  border: 1px solid var(--app-line);
  border-radius: 8px;
  background: var(--app-surface);
  color: var(--app-ink-2);
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease;
}
.btn-search:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}
.kbd {
  margin-left: auto;
  font-family: inherit;
  font-size: 11px;
  color: var(--app-ink-3);
  border: 1px solid var(--app-line);
  border-radius: 4px;
  padding: 1px 5px;
}

/* 管理菜单顶部的返回按钮：主色实心，与工作区列表区分明显 */
.btn-back {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  margin: 4px 0 10px;
  padding: 8px 0;
  border: none;
  border-radius: 8px;
  background: var(--el-color-primary);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease;
}
.btn-back:hover {
  background: var(--el-color-primary-dark-2);
}

/* 移动端顶栏返回箭头 */
.m-back {
  cursor: pointer;
  color: var(--el-color-primary);
  flex: none;
}

/* 中部滚动区：工作区列表 / 管理菜单 */
.side-scroll {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 2px 12px;
  min-height: 0;
}
.side-group {
  margin-bottom: 8px;
}
.side-group-label {
  font-size: 12px;
  color: var(--app-ink-3);
  padding: 8px 10px 4px;
}

.side-divider {
  height: 1px;
  background: var(--app-line);
  margin: 6px 16px;
  flex: none;
}

/* 底部导航组 */
.side-nav {
  flex: none;
  padding: 4px 12px 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 7px 10px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  color: var(--app-ink-2);
  transition: background 0.15s ease, color 0.15s ease;
  user-select: none;
}
.nav-item:hover {
  background: var(--sidebar-hover);
  color: var(--app-ink);
}
.nav-item.active {
  background: var(--app-surface);
  color: var(--app-ink);
  font-weight: 600;
  box-shadow: var(--app-shadow);
}
.nav-child {
  font-size: 12.5px;
  padding: 6px 10px;
}
.nav-children {
  padding-left: 14px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nav-caret {
  margin-left: auto;
  color: var(--app-ink-3);
  transition: transform 0.15s ease;
}
.nav-caret.open {
  transform: rotate(180deg);
}

/* 用户行 */
.user-row {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 16px;
  border-top: 1px solid var(--app-line);
}
.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: var(--app-ink);
  min-width: 0;
  font-size: 13px;
}
.user-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 84px;
}
.user-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: none;
}
.icon-btn {
  cursor: pointer;
  color: var(--app-ink-2);
  vertical-align: middle;
  transition: color 0.15s ease;
}
.icon-btn:hover {
  color: var(--el-color-primary);
}
.bell-badge {
  line-height: 1;
}

/* ========== 移动端顶栏（品牌 + 铃铛） ========== */
.m-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 52px;
  border-bottom: 1px solid var(--app-line);
  background: var(--app-surface);
  padding: 0 14px;
}
.m-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.main {
  background: var(--app-bg);
  padding: 20px;
  overflow-x: hidden;
}

/* ========== 通知弹层 ========== */
.notif-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  margin-bottom: 8px;
}
.notif-list {
  max-height: 360px;
  overflow-y: auto;
}
.notif-item {
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 4px;
}
.notif-item:hover {
  background: var(--app-bg);
}
.notif-item.unread {
  background: var(--el-color-primary-light-9);
}
html.dark .notif-item.unread {
  background: var(--el-color-primary-light-8);
}
.notif-item.unread .notif-title {
  font-weight: 600;
}
.notif-title {
  font-size: 13px;
  color: var(--app-ink);
}
.notif-content {
  font-size: 12px;
  color: var(--app-ink-2);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.notif-time {
  font-size: 12px;
  color: var(--app-ink-2);
  margin-top: 2px;
  opacity: 0.8;
}

/* ========== 移动端底部标签栏 ========== */
.tabbar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 100;
  display: flex;
  background: var(--app-surface);
  border-top: 1px solid var(--app-line);
  padding-bottom: env(safe-area-inset-bottom);
}
.tab-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 7px 0 8px;
  font-size: 11px;
  color: var(--app-ink-2);
  cursor: pointer;
  transition: color 0.15s ease;
  -webkit-tap-highlight-color: transparent;
}
.tab-item.active {
  color: var(--el-color-primary);
}

/* 更多面板 */
.more-list {
  display: flex;
  flex-direction: column;
  padding-bottom: env(safe-area-inset-bottom);
}
.more-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 6px;
  font-size: 15px;
  color: var(--app-ink);
  cursor: pointer;
  border-bottom: 1px solid var(--app-line);
}
.more-item:last-child {
  border-bottom: none;
}
.more-item:active {
  background: var(--app-bg);
}
.more-arrow {
  margin-left: auto;
  color: var(--app-ink-2);
}
.more-section {
  padding: 14px 6px 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--app-ink-2);
}
.more-group-label {
  padding: 10px 6px 4px;
  font-size: 11px;
  font-weight: 600;
  color: var(--el-color-primary);
  border-top: 1px solid var(--app-line);
}
.more-group-label:first-of-type {
  border-top: none;
}

@media (max-width: 991px) {
  .main {
    padding: 14px;
    /* 给底部标签栏留出空间（含刘海屏安全区） */
    padding-bottom: calc(66px + env(safe-area-inset-bottom));
  }
}
</style>
