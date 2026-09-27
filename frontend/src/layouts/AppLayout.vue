<template>
  <el-container class="layout">
    <!-- 桌面端固定侧边栏（可折叠） -->
    <el-aside v-if="!isMobile" :width="sidebarCollapsed ? '64px' : '216px'" class="aside">
      <div class="logo" :class="{ collapsed: sidebarCollapsed }">
        <img class="logo-img" :src="brandStore.logoUrl" :alt="brandStore.systemName" />
        <span v-if="!sidebarCollapsed" class="logo-text">{{ brandStore.systemName }}</span>
      </div>
      <el-menu :default-active="activeMenu" router class="side-menu" :collapse="sidebarCollapsed" :collapse-transition="false" :background-color="'transparent'" :text-color="'var(--app-ink-2)'">
        <template v-for="m in menus" :key="m.path">
          <!-- 客户管理：子菜单聚合 客户/任务提醒/工作流，导航更干净 -->
          <el-sub-menu v-if="m.children" :index="m.path">
            <template #title>
              <el-icon><component :is="m.icon" /></el-icon>
              <span>{{ m.label }}</span>
            </template>
            <el-menu-item v-for="c in m.children" :key="c.path" :index="c.path">
              <el-icon><component :is="c.icon" /></el-icon>
              <template #title>{{ c.label }}</template>
            </el-menu-item>
          </el-sub-menu>
          <el-menu-item v-else :index="m.path">
            <el-icon><component :is="m.icon" /></el-icon>
            <template #title>{{ m.label }}</template>
          </el-menu-item>
        </template>
        <el-sub-menu v-if="isAdmin" index="admin-group">
          <template #title>
            <el-icon><Setting /></el-icon>
            <span>系统管理</span>
          </template>
          <el-sub-menu v-for="g in adminGroups" :key="g.key" :index="'admin-' + g.key">
            <template #title>
              <span>{{ g.label }}</span>
            </template>
            <el-menu-item v-for="m in g.items" :key="m.path" :index="m.path">
              <el-icon><component :is="m.icon" /></el-icon>
              <template #title>{{ m.label }}</template>
            </el-menu-item>
          </el-sub-menu>
        </el-sub-menu>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-left">
          <el-tooltip :content="sidebarCollapsed ? '展开菜单' : '折叠菜单'" placement="bottom">
            <el-button v-if="!isMobile" link class="collapse-btn" :icon="sidebarCollapsed ? Expand : Fold" @click="toggleSidebar" />
          </el-tooltip>
          <img v-if="isMobile" class="logo-img logo-img-sm" :src="brandStore.logoUrl" :alt="brandStore.systemName" />
          <span class="page-title">{{ route.meta.title || '' }}</span>
        </div>
        <div class="header-right">
          <!-- 字号切换 -->
          <el-popover placement="bottom-end" :width="150" trigger="click">
            <template #reference>
              <span class="header-icon fs-trigger" title="字号">A</span>
            </template>
            <div class="font-size-panel">
              <div class="fs-opt-title">字号</div>
              <button
                v-for="(o, k) in FONT_SIZES"
                :key="k"
                class="fs-opt"
                :class="{ active: themeStore.fontSize === k }"
                @click="themeStore.setFontSize(k)"
              >
                <span class="fs-a" :style="{ fontSize: o.size }">A</span>
                <span>{{ o.label }}</span>
              </button>
            </div>
          </el-popover>
          <!-- 明暗切换 -->
          <el-tooltip :content="themeStore.mode === 'dark' ? '切换到亮色' : '切换到暗色'" placement="bottom">
            <el-icon :size="18" class="header-icon" @click="themeStore.toggleMode()">
              <Sunny v-if="themeStore.mode === 'dark'" />
              <Moon v-else />
            </el-icon>
          </el-tooltip>
          <!-- 主题色 -->
          <el-popover placement="bottom-end" :width="216" trigger="click">
            <template #reference>
              <el-icon :size="18" class="header-icon"><Brush /></el-icon>
            </template>
            <div class="accent-panel">
              <div class="accent-title">主题色</div>
              <div class="accent-list">
                <span
                  v-for="(v, k) in ACCENTS"
                  :key="k"
                  class="accent-swatch"
                  :class="{ active: themeStore.accent === k }"
                  :style="{ background: v.color }"
                  :title="v.label"
                  @click="themeStore.setAccent(k)"
                >
                  <el-icon v-if="themeStore.accent === k" :size="14" color="#fff"><Check /></el-icon>
                </span>
              </div>
            </div>
          </el-popover>
          <!-- 通知铃铛 -->
          <el-popover placement="bottom-end" :width="360" trigger="click" @show="loadNotifications">
            <template #reference>
              <el-badge :value="unreadCount" :hidden="!unreadCount" :max="99" class="bell-badge">
                <el-icon :size="18" class="header-icon"><Bell /></el-icon>
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
          <!-- 用户 -->
          <!-- trigger=click：悬停弹出在 zoom 缩放下会致 popper 定位偏移→页面抖动，改为点击展开 -->
          <el-dropdown trigger="click" @command="handleCommand">
            <span class="user-info">
              <el-icon><Avatar /></el-icon>
              <span v-if="!isMobile">{{ authStore.user?.name || authStore.user?.username || '用户' }}</span>
              <el-icon v-if="!isMobile"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">个人中心</el-dropdown-item>
                <el-dropdown-item command="syncapp">下载同步 App</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>

    <!-- 移动端底部标签栏 -->
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

    <!-- 更多菜单面板 -->
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
    <!-- 全局搜索（Ctrl+K） -->
    <GlobalSearch />
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  User, UserFilled, List, SetUp, Collection, Avatar, ArrowDown, Bell, Notebook,
  Sunny, Moon, Brush, Check, Files, MoreFilled, ArrowRight, Fold, Expand,
  Setting, Cpu, DataAnalysis, Monitor, WarningFilled, Delete, Tickets, MagicStick, Connection,
  Stamp,
} from '@element-plus/icons-vue'
import { ElMessageBox, ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { useBrandStore } from '../stores/brand'
import { useThemeStore, ACCENTS, FONT_SIZES } from '../stores/theme'
import UploadProgressPanel from '../components/UploadProgressPanel.vue'
import GlobalSearch from '../components/GlobalSearch.vue'
import { getNotifications, markNotificationRead, markAllNotificationsRead, downloadSyncApp, getSyncAppVersion } from '../api'
import { formatDateTime } from '../utils/format'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const brandStore = useBrandStore()
const themeStore = useThemeStore()

const menus = [
  { path: '/today', label: '今日', icon: Sunny },
  { path: '/studio', label: '工作台', icon: Notebook },
  { path: '/knowledge', label: '知识库', icon: Collection },
  { path: '/library', label: '文档库', icon: Files },
  // 客户板块聚合 CRM 相关页：客户/任务提醒/工作流（桌面端子菜单，移动端在"更多"里平铺）
  {
    path: '/customers', label: '客户管理', icon: User,
    children: [
      { path: '/customers', label: '客户列表', icon: User },
      { path: '/tasks', label: '任务提醒', icon: List },
      { path: '/workflows', label: '工作流', icon: SetUp },
    ],
  },
  { path: '/agent-approvals', label: 'Agent 审批', icon: Stamp },
]

// 移动端底部标签栏：前四项 + 更多
const tabItems = menus.slice(0, 4).map((m) => ({
  path: m.path,
  label: { 客户管理: '客户', 'AI 工作台': 'AI 工作台' }[m.label] || m.label,
  icon: m.icon,
}))
// "更多"面板平铺子菜单项
const moreMenus = menus.slice(4).flatMap((m) => m.children || [m])

// 系统管理菜单（仅 admin），按职能分组聚合，避免展开过长
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

// ========== 响应式 ==========
const isMobile = ref(window.innerWidth < 992)
const moreDrawer = ref(false)
function onResize() {
  isMobile.value = window.innerWidth < 992
  if (!isMobile.value) moreDrawer.value = false
}

// 侧边栏折叠（记住状态）
const sidebarCollapsed = ref(localStorage.getItem('sidebar-collapsed') === '1')
function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem('sidebar-collapsed', sidebarCollapsed.value ? '1' : '0')
}

const activeMenu = computed(() => {
  if (route.path.startsWith('/customers')) return '/customers'
  if (route.path.startsWith('/knowledge')) return '/knowledge'
  return route.path
})

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
    await ElMessageBox.confirm('确定退出登录吗？', '提示', { type: 'warning' })
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
.aside {
  background: var(--app-surface);
  border-right: 1px solid var(--app-line);
  transition: width 0.2s ease;
  overflow-x: hidden;
}
.logo {
  height: var(--app-header-h);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  overflow: hidden;
}
.logo.collapsed {
  justify-content: center;
  padding: 0 8px;
}
.collapse-btn {
  font-size: 18px;
  color: var(--app-ink-2);
  margin-right: 8px;
}
.collapse-btn:hover {
  color: var(--el-color-primary);
}
.logo-img {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  flex: none;
  box-shadow: 0 4px 12px rgba(37, 99, 235, 0.28);
}
.logo-img-sm {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  box-shadow: none;
}
.logo-text {
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--app-ink);
  white-space: nowrap;
}
.side-menu {
  border-right: none;
}
.side-menu:not(.el-menu--collapse) {
  background: transparent;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--app-header-h);
  border-bottom: 1px solid var(--app-line);
  background: var(--app-surface);
  padding: 0 20px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}
.page-title {
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 18px;
  flex: none;
}
.header-icon {
  cursor: pointer;
  color: var(--app-ink-2);
  vertical-align: middle;
  transition: color 0.15s ease;
}
.header-icon:hover {
  color: var(--el-color-primary);
}
.fs-trigger {
  font-size: 18px;
  font-weight: 400;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  user-select: none;
}
.font-size-panel {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.fs-opt-title {
  font-size: 12px;
  color: var(--app-ink-2);
  margin-bottom: 4px;
}
.fs-opt {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  border: none;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  color: var(--app-ink);
  width: 100%;
  text-align: left;
}
.fs-opt:hover {
  background: var(--app-bg);
}
.fs-opt.active {
  color: var(--el-color-primary);
  font-weight: 600;
}
.fs-a {
  width: 20px;
  text-align: center;
  font-weight: 700;
  line-height: 1;
}
.bell-badge {
  line-height: 1;
}
.user-info {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  color: var(--app-ink);
}
.main {
  background: var(--app-bg);
  padding: 20px;
  overflow-x: hidden;
}
.accent-title {
  font-weight: 600;
  margin-bottom: 10px;
}
.accent-list {
  display: flex;
  gap: 10px;
}
.accent-swatch {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.15s ease;
}
.accent-swatch:hover {
  transform: scale(1.12);
}
.accent-swatch.active {
  box-shadow: 0 0 0 2px var(--app-surface), 0 0 0 4px currentColor;
}
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

/* 移动端底部标签栏 */
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
  .header {
    padding: 0 14px;
  }
  .header-right {
    gap: 14px;
  }
  .main {
    padding: 14px;
    /* 给底部标签栏留出空间（含刘海屏安全区） */
    padding-bottom: calc(66px + env(safe-area-inset-bottom));
  }
}
</style>
