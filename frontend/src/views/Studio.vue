<template>
  <div class="studio">
    <!-- TopBar -->
    <div class="topbar">
      <div class="topbar-left">
        <el-tooltip content="选择知识库 / 文件" placement="bottom">
          <el-button v-if="isMobile" :icon="Files" size="small" link @click="sourcesDrawer = true" />
        </el-tooltip>
        <!-- 工作区标签栏（类 OneNote：左右翻页；自己的/共享的用颜色区分；右键出菜单） -->
        <div class="nb-tabbar">
          <button class="nb-arrow" :disabled="!canScrollLeft" @click="scrollTabs(-1)" title="向左翻">
            <el-icon><ArrowLeft /></el-icon>
          </button>
          <div ref="tabsRef" class="nb-tabs" @scroll="updateScrollState">
            <button
              v-for="nb in studio.notebooks"
              :key="nb.id"
              class="nb-tab"
              :class="tabClass(nb)"
              :title="nb.name + (isMine(nb) ? '（我创建的）' : '（共享给我的）')"
              @click="openNotebookTab(nb.id)"
              @contextmenu.prevent="openTabContext(nb, $event)"
            >
              <span class="nb-tab-dot" />
              <span class="nb-tab-name">{{ nb.name }}</span>
              <span v-if="nb.note_count" class="nb-tab-count">{{ nb.note_count }}</span>
            </button>
            <!-- 新建工作区：与翻页箭头同族的圆形幽灵钮 -->
            <button class="nb-add" title="新建工作区" @click="createNewNotebook">
              <el-icon><Plus /></el-icon>
            </button>
            <span v-if="!studio.notebooks.length" class="nb-empty-hint">还没有工作区，点「+」新建</span>
          </div>
          <button class="nb-arrow" :disabled="!canScrollRight" @click="scrollTabs(1)" title="向右翻">
            <el-icon><ArrowRight /></el-icon>
          </button>
        </div>
      </div>

      <div class="topbar-actions">
        <el-tooltip content="工作区 / 报告" placement="bottom">
          <el-button v-if="isMobile" :icon="Collection" size="small" link @click="sideDrawer = true" />
        </el-tooltip>
        <el-tooltip content="刷新">
          <el-button :icon="Refresh" size="small" link @click="refresh" />
        </el-tooltip>
      </div>
    </div>

    <!-- 工作区标签右键菜单 -->
    <Teleport to="body">
      <div v-if="ctxMenu.show" class="nb-ctx-mask" @click="closeCtx" @contextmenu.prevent="closeCtx">
        <div class="nb-ctx" :style="{ top: ctxMenu.y + 'px', left: ctxMenu.x + 'px' }" @click.stop>
          <div class="nb-ctx-title">{{ ctxMenu.nb?.name }}</div>
          <button class="nb-ctx-item" @click="ctxRename"><el-icon><Edit /></el-icon>重命名</button>
          <button class="nb-ctx-item" @click="ctxShare"><el-icon><Share /></el-icon>共享</button>
          <button class="nb-ctx-item danger" @click="ctxDelete"><el-icon><Delete /></el-icon>删除</button>
        </div>
      </div>
    </Teleport>

    <!-- 主体 3 列（移动端仅保留聊天列，两侧面板进抽屉） -->
    <div class="layout">
      <div v-if="!isMobile" class="col col-left" :class="{ 'sources-disabled': effectiveAgentMode }">
        <SourcesPanel />
      </div>

      <div class="col col-center">
        <!-- 未选中工作区：居中欢迎块（今日视图已独立为 /today 门户页） -->
        <div v-if="!studio.currentNotebook" class="empty-tip">
          <div class="empty-welcome">
            <el-icon :size="44" class="empty-welcome-icon"><Notebook /></el-icon>
            <p class="empty-welcome-text">选择一个工作区开始，或新建一个</p>
            <el-button type="primary" :icon="Plus" @click="createNewNotebook">新建工作区</el-button>
          </div>
        </div>
        <div v-else class="chat-area">
          <!-- 消息区 -->
          <div class="messages" ref="messagesRef">
            <div v-if="!messages.length" class="welcome">
              <h2>🎓 {{ studio.currentNotebook.name }}</h2>
              <p class="hint">从左侧选择知识库或文件，然后开始提问</p>
              <div class="samples">
                <el-button v-for="q in sampleQuestions" :key="q" @click="send(q)">{{ q }}</el-button>
              </div>
            </div>
            <div v-for="(m, idx) in messages" :key="idx" :class="['msg', m.role]">
              <div class="msg-content markdown-body" v-html="renderMsg(m)" />
              <!-- 工具调用状态：连续相同工具合并为一个 chip，×N 计数随调用弹跳 -->
              <div v-if="m.role === 'ai' && m.tools?.length" class="tool-tags">
                <div v-for="(t, j) in m.tools" :key="j" class="tool-chip" :class="{ done: !t.active && !(t.failed && !t.done), error: !t.active && t.failed > 0 && t.done === 0 }">
                  <el-icon class="tool-chip-icon" :class="{ pulsing: t.active }" :size="15">
                    <component :is="toolIcon(t.name)" />
                  </el-icon>
                  <span v-if="t.active">正在{{ toolLabel(t.name) }}…</span>
                  <span v-else-if="t.failed && !t.done">{{ toolLabel(t.name) }} 失败</span>
                  <span v-else>已使用 {{ toolLabel(t.name) }}</span>
                  <span v-if="t.total > 1" :key="t.tick" class="tool-chip-count">×{{ t.total }}</span>
                </div>
              </div>
              <!-- AI 思考中指示（agent 决策阶段） -->
              <div v-if="m.role === 'ai' && m.thinking && !m.content" class="thinking-indicator">
                <el-icon class="pulsing" :size="16"><Cpu /></el-icon>
                <span>AI 正在思考…</span>
              </div>
              <!-- AI 消息操作 -->
              <div v-if="m.role === 'ai'" class="msg-actions">
                <el-button link size="small" :icon="Notebook" @click="saveAsNote(m, idx)">保存到工作区</el-button>
                <el-button v-if="m.sources?.length" link size="small" @click="m.showSources = !m.showSources">
                  {{ m.showSources ? '隐藏' : '查看' }}引用 ({{ m.sources.length }})
                </el-button>
                <template v-if="m.queryLogId">
                  <el-button link size="small" :class="{ 'fb-done': m.feedback === 'useful' }" @click="handleFeedback(m, 'useful')">
                    <el-icon><CircleCheck /></el-icon> 有用
                  </el-button>
                  <el-button link size="small" :class="{ 'fb-done': m.feedback === 'useless' }" @click="handleFeedback(m, 'useless')">
                    <el-icon><CircleClose /></el-icon> 无用
                  </el-button>
                  <span v-if="m.feedback" class="feedback-done">已反馈</span>
                </template>
              </div>
              <!-- 引用来源 -->
              <div v-if="m.role === 'ai' && m.showSources && m.sources?.length" class="sources-panel-inline">
                <div v-for="s in m.sources" :key="s.chunk_id" class="source-item">
                  <div class="source-title">{{ s.doc_title }} <span class="score">{{ Math.round((s.score || 0) * 100) }}%</span></div>
                  <div class="source-excerpt">{{ s.excerpt }}</div>
                </div>
              </div>
            </div>
          </div>

          <!-- 输入区 -->
          <div class="input-area">
            <el-input
              v-model="question"
              type="textarea"
              :rows="3"
              resize="none"
              placeholder="输入问题，回车发送，Shift+回车换行..."
              @keydown.enter.exact.prevent="onSend"
            />
            <div class="input-actions">
              <div class="scope-info">
                <!-- dsh Agent 模式：由 AI 自主规划检索与工具调用，不接受知识库/文件/深度思考参数。
                     后端 DSH_AGENT_ENABLED 关闭时禁用开关且强制走普通链路 -->
                <el-switch
                  v-model="agentMode"
                  size="small"
                  inline-prompt
                  active-text="Agent"
                  inactive-text="Agent"
                  :disabled="!brand.dshAgentEnabled"
                  style="--el-switch-off-color: #909399; margin-right: 8px"
                />
                <el-switch
                  v-model="thinkingMode"
                  size="small"
                  inline-prompt
                  active-text="深度思考"
                  inactive-text="快速回答"
                  :disabled="effectiveAgentMode"
                  style="--el-switch-off-color: #909399; margin-right: 8px"
                />
                <template v-if="effectiveAgentMode">
                  <el-tag size="small" type="warning">Agent 自主检索</el-tag>
                </template>
                <template v-else>
                  <el-tag v-if="studio.selectedKbIds.length" size="small" type="primary">
                    {{ studio.selectedKbIds.length }} 知识库
                  </el-tag>
                  <el-tag v-if="studio.selectedFileIds.length" size="small" type="success">
                    {{ studio.selectedFileIds.length }} 文件
                  </el-tag>
                </template>
              </div>
              <el-button v-if="loading" type="danger" :icon="VideoPause" @click="studio.stopChat(studio.currentNotebook?.id)">停止</el-button>
              <el-button v-else type="primary" :icon="Promotion" @click="onSend">发送</el-button>
            </div>
          </div>
        </div>
      </div>

      <div v-if="!isMobile" class="col col-right">
        <div class="col-tabs">
          <button :class="{ active: studio.activeTab === 'notes' }" @click="studio.activeTab = 'notes'">工作区</button>
          <button :class="{ active: studio.activeTab === 'reports' }" @click="studio.activeTab = 'reports'">报告</button>
        </div>
        <NotesPanel v-show="studio.activeTab === 'notes'" />
        <ReportsPanel v-if="studio.activeTab === 'reports'" ref="reportsPanelRef" />
      </div>
    </div>

    <!-- 移动端：来源选择抽屉 -->
    <el-drawer v-if="isMobile" v-model="sourcesDrawer" title="选择知识库 / 文件" size="82%">
      <div class="drawer-sources" :class="{ 'sources-disabled': effectiveAgentMode }">
        <SourcesPanel />
      </div>
    </el-drawer>
    <!-- 移动端：工作区 / 报告抽屉 -->
    <el-drawer v-if="isMobile" v-model="sideDrawer" title="工作区 / 报告" size="88%">
      <div class="col-tabs">
        <button :class="{ active: studio.activeTab === 'notes' }" @click="studio.activeTab = 'notes'">工作区</button>
        <button :class="{ active: studio.activeTab === 'reports' }" @click="studio.activeTab = 'reports'">报告</button>
      </div>
      <NotesPanel v-show="studio.activeTab === 'notes'" />
      <ReportsPanel v-if="studio.activeTab === 'reports'" ref="reportsPanelRef" />
    </el-drawer>

    <ShareDialog v-model="shareNotebookDialog" resource-type="notebook" :resource="shareNotebook" @changed="refresh" />
  </div>
</template>

<script setup>
import { ref, reactive, computed, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  Notebook, ArrowLeft, ArrowRight, Plus, Refresh, Edit, Delete, Share,
  Search, Link, Folder, MagicStick, Collection, Cpu, Files,
  CircleCheck, CircleClose, VideoPause, Promotion,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useStudioStore } from '../stores/studio'
import { useAuthStore } from '../stores/auth'
import { useBrandStore } from '../stores/brand'
import { renderMarkdown } from '../utils/markdown'
import { toolNameMap } from '../utils/format'
import { submitFeedback, updateNotebook, deleteNotebook } from '../api'
import ShareDialog from '../components/ShareDialog.vue'
import SourcesPanel from '../components/Studio/SourcesPanel.vue'
import NotesPanel from '../components/Studio/NotesPanel.vue'
import ReportsPanel from '../components/Studio/ReportsPanel.vue'

const TOOL_ICONS = { web_search: Search, web_fetch: Link }
function toolLabel(name) {
  return toolNameMap[name] || name
}
function toolIcon(name) {
  if (name.startsWith('mcp_filesystem')) return Folder
  if (name.startsWith('mcp_memory')) return Collection
  if (name.startsWith('mcp_thinking')) return MagicStick
  return TOOL_ICONS[name] || Cpu
}

const studio = useStudioStore()
const authStore = useAuthStore()
const brand = useBrandStore()
const route = useRoute()

// 对话状态存放在 studio store（按工作区隔离）：切换工作区/页面时流式回答不中断
const currentChat = computed(() => (studio.currentNotebook ? studio.chatOf(studio.currentNotebook.id) : null))
const messages = computed(() => currentChat.value?.messages || [])
const loading = computed(() => currentChat.value?.loading || false)
const question = ref('')
const messagesRef = ref(null)
const reportsPanelRef = ref(null)
// 深度思考开关：false 时后端向模型发送 enable_thinking=false 并剥离 <think> 输出，更快更省 token
const thinkingMode = ref(localStorage.getItem('kb_chat_thinking') !== '0')
watch(thinkingMode, (v) => localStorage.setItem('kb_chat_thinking', v ? '1' : '0'))
// dsh Agent 模式开关：开启后发问走 /ask/agent/stream，由 dsh 自主规划检索与工具调用；
// 状态存 localStorage 刷新保留，默认开启（显式关过才记住 '0'）
const agentMode = ref(localStorage.getItem('kb_chat_agent') !== '0')
watch(agentMode, (v) => localStorage.setItem('kb_chat_agent', v ? '1' : '0'))
// 生效中的 agent 模式：开关开 + 后端启用（DSH_AGENT_ENABLED）两者同时满足
const effectiveAgentMode = computed(() => agentMode.value && brand.dshAgentEnabled)
// 从通知点击跳转：/studio?report=<id> → 切到报告页并自动打开该报告
watch(
  () => route.query.report,
  async (id) => {
    if (!id) return
    studio.activeTab = 'reports'
    await nextTick()
    reportsPanelRef.value?.openViewById(Number(id))
  },
  { immediate: true }
)
const shareNotebookDialog = ref(false)
const shareNotebook = ref(null)
// 移动端：聊天区全宽，来源/工作区报告进抽屉
const isMobile = ref(window.innerWidth < 992)
const sourcesDrawer = ref(false)
const sideDrawer = ref(false)
function onResize() {
  isMobile.value = window.innerWidth < 992
  if (!isMobile.value) {
    sourcesDrawer.value = false
    sideDrawer.value = false
  }
  updateScrollState()
}
// 会话历史改由 store 管理（loadChatHistory 内部有防覆盖保护）

async function handleFeedback(m, rating) {
  if (m.feedback) return
  try {
    await submitFeedback({ query_log_id: m.queryLogId, rating })
    m.feedback = rating
  } catch { /* 拦截器已提示 */ }
}

const sampleQuestions = [
  '总结这些文档的核心观点',
  '基于以上内容给出 3 个行动方案',
  '对比分析不同来源的差异',
]

// ========== 工作区标签栏（类 OneNote） ==========
const tabsRef = ref(null)
const canScrollLeft = ref(false)
const canScrollRight = ref(false)

function updateScrollState() {
  const el = tabsRef.value
  if (!el) return
  canScrollLeft.value = el.scrollLeft > 2
  canScrollRight.value = el.scrollLeft + el.clientWidth < el.scrollWidth - 2
}

function scrollTabs(dir) {
  const el = tabsRef.value
  if (el) el.scrollBy({ left: dir * Math.max(el.clientWidth * 0.7, 160), behavior: 'smooth' })
}

// 工作区颜色：自己创建 / 他人共享
const currentUserId = computed(() => authStore.user?.id)
function isMine(nb) {
  return nb.created_by === currentUserId.value
}
function tabClass(nb) {
  return {
    active: studio.currentNotebook?.id === nb.id,
    mine: isMine(nb),
    shared: !isMine(nb),
  }
}

async function openNotebookTab(id) {
  if (studio.currentNotebook?.id === id) return
  await studio.openNotebook(id)
  await studio.loadChatHistory(id)
  scrollToBottom()
}

// 右键上下文菜单
const ctxMenu = reactive({ show: false, x: 0, y: 0, nb: null })
function openTabContext(nb, e) {
  ctxMenu.nb = nb
  ctxMenu.x = e.clientX
  ctxMenu.y = e.clientY
  ctxMenu.show = true
}
function closeCtx() {
  ctxMenu.show = false
}

async function ctxRename() {
  const nb = ctxMenu.nb
  closeCtx()
  if (!nb) return
  const { value: name } = await ElMessageBox.prompt('新名称', '重命名', {
    inputValue: nb.name,
    inputPattern: /.+/,
  }).catch(() => ({ value: null }))
  if (!name) return
  await updateNotebook(nb.id, { name })
  await studio.loadNotebooks()
  if (studio.currentNotebook?.id === nb.id) {
    studio.currentNotebook = { ...studio.currentNotebook, name }
  }
  ElMessage.success('已重命名')
}

function ctxShare() {
  const nb = ctxMenu.nb
  closeCtx()
  if (!nb) return
  shareNotebook.value = { id: nb.id, name: nb.name, owner_id: nb.created_by, is_private: nb.is_private, perm: nb.perm }
  shareNotebookDialog.value = true
}

async function ctxDelete() {
  const nb = ctxMenu.nb
  closeCtx()
  if (!nb) return
  await ElMessageBox.confirm(
    `确定删除工作区「${nb.name}」？将移入回收站，可随时恢复。`,
    '删除确认', { type: 'warning' }
  )
  await deleteNotebook(nb.id)
  studio.clearChat(nb.id)
  if (studio.currentNotebook?.id === nb.id) {
    studio.currentNotebook = null
  }
  await studio.loadNotebooks()
  ElMessage.success('已删除')
}

async function refresh() {
  await studio.loadNotebooks()
  if (studio.currentNotebook) {
    await studio.loadNotes()
  }
}

async function createNewNotebook() {
  const { value: name } = await ElMessageBox.prompt('输入新工作区名称', '新建', {
    inputPattern: /.+/,
    inputErrorMessage: '名称不能为空',
    inputPlaceholder: '如：Q3 客户投诉分析',
  }).catch(() => ({ value: null }))
  if (!name) return
  const nb = await studio.createNotebook({
    name,
    description: '',
    source_kb_ids: [],
    source_file_ids: [],
  })
  ElMessage.success('已创建')
  await studio.openNotebook(nb.id)
}

async function onSend() {
  const q = question.value.trim()
  if (!q || loading.value) return
  if (!studio.currentNotebook) {
    ElMessage.warning('请先选择工作区')
    return
  }
  await send(q)
}

async function send(q) {
  const nbId = studio.currentNotebook?.id
  if (!nbId) return
  question.value = ''
  scrollToBottom()
  // 流式循环在 store 内执行：组件卸载/切换工作区不中断，回来可直接看到结果
  // Agent 模式下 kbIds/fileIds/thinking 会被 store 忽略（后端不接受这些参数）
  await studio.sendChat(nbId, {
    question: q,
    kbIds: [...studio.selectedKbIds],
    fileIds: [...studio.selectedFileIds],
    thinking: thinkingMode.value,
    agent: effectiveAgentMode.value,
  })
}

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  // 注意：不要在此 abort 进行中的对话流，保持后台运行
})

function renderMsg(m) {
  if (m.role === 'user') {
    return `<div class="user-content">${escapeHtml(m.content)}</div>`
  }
  return renderMarkdown(m.content || '')
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

async function saveAsNote(m, idx) {
  if (!studio.currentNotebook) {
    ElMessage.warning('请先选择工作区')
    return
  }
  await studio.saveChatAsNote({
    question: messages.value[idx - 1]?.content || '',
    answer: m.content,
    sources: m.sources,
    session_id: null,
    query_log_id: m.queryLogId,
  })
  ElMessage.success('已保存到工作区')
}

onMounted(async () => {
  window.addEventListener('resize', onResize)
  await studio.initNotebooks()
  // 自动恢复上次打开的工作区时，一并恢复它的会话历史（store 有状态则不覆盖）
  if (studio.currentNotebook) {
    await studio.loadChatHistory(studio.currentNotebook.id)
  }
  await nextTick()
  updateScrollState()
  scrollToBottom()
})

// 流式进行中（新消息/新 token）自动滚到底部
watch(
  () => {
    const c = currentChat.value
    if (!c || !c.messages.length) return ''
    const last = c.messages[c.messages.length - 1]
    return `${c.messages.length}:${last?.content?.length || 0}`
  },
  () => scrollToBottom()
)

// 工作区列表变化/尺寸变化后更新翻页箭头状态
watch(() => studio.notebooks.length, async () => { await nextTick(); updateScrollState() })
</script>

<style scoped>
.studio {
  display: flex;
  flex-direction: column;
  height: calc((100vh - 120px) / var(--app-zoom, 1));
  background: var(--app-bg);
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  /* 底部无 padding：tab 下边缘贴住边框，active tab 才能与下方内容区连通 */
  padding: 10px 16px 0;
  background: var(--app-card);
  border-bottom: 1px solid var(--app-border);
}
.topbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

/* 工作区标签栏（类 OneNote，左右可翻页） */
.nb-tabbar {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
  align-self: stretch;
}
.nb-tabs {
  display: flex;
  align-items: flex-end;  /* tab 贴住 topbar 下边框 */
  gap: 4px;
  overflow-x: auto;
  scrollbar-width: none;
  flex: 1;
  min-width: 0;
  scroll-behavior: smooth;
}
.nb-tabs::-webkit-scrollbar {
  display: none;
}
.nb-arrow {
  flex: none;
  align-self: center;
  border: 1px solid var(--app-border);
  background: var(--app-card);
  color: var(--app-ink-2);
  width: 26px;
  height: 26px;
  border-radius: 50%;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s ease;
}
.nb-arrow:hover:not(:disabled) {
  background: var(--app-fill-1);
  color: var(--app-ink);
}
.nb-arrow:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.nb-tab {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  /* inactive：无边框无底色，不突兀；active 时才描边浮出 */
  border: 1px solid transparent;
  border-radius: 8px 8px 0 0;
  background: transparent;
  cursor: pointer;
  font-size: 13px;
  color: var(--app-ink-2);
  position: relative;
  white-space: nowrap;
  max-width: 180px;
  /* 下压 1px 盖住 topbar 边框，配合 active 的底边同色实现连通感 */
  margin-bottom: -1px;
  transition: background 0.15s ease, color 0.15s ease;
}
.nb-tab:hover {
  background: var(--app-fill-1);
}
.nb-tab.active {
  background: var(--app-surface);
  border-color: var(--app-border);
  border-bottom-color: var(--app-surface);  /* 与下方主区同色 → 视觉连通 */
  z-index: 1;
  color: var(--app-ink);
  font-weight: 600;
}
.nb-tab-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}
/* 自己创建：主色；共享给我的：琥珀色 */
.nb-tab.mine .nb-tab-dot { background: var(--el-color-primary); }
.nb-tab.shared .nb-tab-dot { background: #e6a23c; }
.nb-tab.mine.active { border-top: 2px solid var(--el-color-primary); }
.nb-tab.shared.active { border-top: 2px solid #e6a23c; }
.nb-tab-name {
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 130px;
}
.nb-tab-count {
  font-size: 11px;
  color: var(--app-ink-3);
  background: var(--app-fill-1);
  border-radius: 8px;
  padding: 0 5px;
}
/* 新建工作区钮：与翻页箭头同族的圆形幽灵钮，hover 主色描边+图标 */
.nb-add {
  flex: none;
  align-self: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  border: 1px solid var(--app-border);
  background: transparent;
  color: var(--app-ink-2);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-left: 2px;
  transition: all 0.15s ease;
}
.nb-add:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}
.nb-empty-hint {
  align-self: center;
  font-size: 13px;
  color: var(--app-ink-3);
  padding-left: 4px;
  padding-bottom: 6px;  /* tabs 贴底对齐后，提示文字视觉上抬一点居中 */
}

/* 右键上下文菜单 */
.topbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.layout {
  flex: 1;
  display: flex;
  overflow: hidden;
  background: var(--app-bg);
  gap: 0;
}
.col {
  height: 100%;
  overflow: hidden;
}
.col-left {
  width: 260px;
  flex-shrink: 0;
  background: var(--studio-side-bg);  /* 侧栏：稍深一档 */
  border-right: 1px solid var(--app-line);
}
/* Agent 模式下源选择不生效：遮罩禁用并提示（dsh 自主规划检索） */
.sources-disabled {
  position: relative;
  pointer-events: none;
  opacity: 0.45;
}
.sources-disabled::after {
  content: 'Agent 模式：AI 自主选择知识库';
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 16px;
  text-align: center;
  font-size: 13px;
  color: var(--app-ink-2);
}
.drawer-sources {
  height: 100%;
}
.col-center {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--app-surface);  /* 主区：白（最高层） */
  box-shadow: inset 0 0 0 1px var(--app-line);  /* 内描边强化分区 */
}
.col-right {
  width: 360px;
  flex-shrink: 0;
  background: var(--studio-side-bg);  /* 与左列对称 */
  border-left: 1px solid var(--app-line);
}
.col-tabs {
  display: flex;
  border-bottom: 1px solid var(--app-line);
  background: var(--app-card);
}
.col-tabs button {
  flex: 1;
  padding: 8px 0;
  border: none;
  background: transparent;
  font-size: 13px;
  color: var(--app-ink-2);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: color 0.15s;
}
.col-tabs button.active {
  color: var(--el-color-primary);
  border-bottom-color: var(--el-color-primary);
  font-weight: 600;
}
.empty-tip {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: var(--app-surface);
}
/* 未选中工作区时的居中欢迎块 */
.empty-welcome {
  text-align: center;
}
.empty-welcome-icon {
  color: var(--app-ink-2);
  opacity: 0.55;
}
.empty-welcome-text {
  color: var(--app-ink-2);
  font-size: 14px;
  margin: 12px 0 16px;
}
.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  background: var(--app-surface);  /* 显式白底与列背景区分 */
}
.welcome {
  text-align: center;
  margin-top: 60px;
}
.welcome h2 {
  font-size: 24px;
  margin-bottom: 8px;
}
.welcome .hint {
  color: var(--app-ink-2);
  margin-bottom: 24px;
}
.samples {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.msg {
  margin-bottom: 16px;
}
.msg.user {
  display: flex;
  justify-content: flex-end;  /* 用户消息靠右 */
}
.msg.user .msg-content {
  background: var(--el-color-primary-light-9);
  padding: 10px 14px;
  border-radius: 8px;
  display: inline-block;
  max-width: 80%;
}
.msg.ai .msg-content {
  padding: 8px 0;
}
.user-content {
  white-space: pre-wrap;
}
.msg-actions {
  margin-top: 6px;
  display: flex;
  gap: 8px;
  align-items: center;
}
.msg-actions .el-button.fb-done {
  color: var(--el-color-success);
}
.feedback-done {
  color: var(--el-color-success);
  font-size: 12px;
}
.sources-panel-inline {
  margin-top: 8px;
  background: var(--app-fill-1);
  padding: 8px;
  border-radius: 4px;
  border: 1px solid var(--app-border);
}
.source-item {
  padding: 6px 0;
  border-bottom: 1px dashed var(--app-border);
  font-size: 12px;
}
.source-item:last-child {
  border-bottom: none;
}
.source-title {
  font-weight: 500;
  margin-bottom: 2px;
}
.score {
  color: var(--el-color-primary);
  margin-left: 6px;
  font-size: 11px;
}
.source-excerpt {
  color: var(--app-ink-2);
  line-height: 1.5;
}
.tool-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.tool-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: 999px;
  font-size: 12px;
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-5);
  color: var(--el-color-primary);
}
.tool-chip.done {
  background: var(--el-color-success-light-9);
  border-color: var(--el-color-success-light-5);
  color: var(--el-color-success);
}
/* 工具调用失败（agent 模式 tool 帧 is_error=true） */
.tool-chip.error {
  background: var(--el-color-danger-light-9);
  border-color: var(--el-color-danger-light-5);
  color: var(--el-color-danger);
}
.tool-chip-icon.pulsing {
  animation: tool-pulse 1.1s ease-in-out infinite;
}
/* ×N 计数徽标：每次递增重新挂载（:key=tick）触发弹跳，给重复调用一点反馈感 */
.tool-chip-count {
  display: inline-block;
  margin-left: 2px;
  font-weight: 700;
  animation: tool-count-pop 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
}
@keyframes tool-count-pop {
  0% { transform: scale(1.7); opacity: 0.3; }
  100% { transform: scale(1); opacity: 1; }
}
@keyframes tool-pulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.25); opacity: 0.45; }
}
.thinking-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--app-ink-2);
  font-size: 13px;
  margin-top: 6px;
}
.thinking-indicator .pulsing {
  color: var(--el-color-primary);
  animation: tool-pulse 1.1s ease-in-out infinite;
}
.input-area {
  border-top: 1px solid var(--app-line);
  padding: 12px 16px;
  background: var(--app-fill-1);
}
.input-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}
.scope-info {
  display: flex;
  gap: 6px;
}

/* ========== 移动端（<992px）：聊天区全宽，侧栏进抽屉 ========== */
@media (max-width: 991px) {
  .studio {
    /* 桌面 120px ≈ 顶栏+内边距；移动再加底部标签栏(~66px)+内边距 */
    height: calc((100vh - 150px - env(safe-area-inset-bottom)) / var(--app-zoom, 1));
  }
  .topbar {
    padding: 8px 10px 0;
    gap: 8px;
  }
  .topbar-left {
    display: flex;
    align-items: center;
    gap: 2px;
    min-width: 0;
    flex: 1;
  }
  .nb-tab {
    max-width: 130px;
    font-size: 12px;
    padding: 5px 9px;
  }
  .nb-tab-name {
    max-width: 90px;
  }
  .topbar-actions {
    flex: none;
    gap: 4px;
  }
  .topbar-actions .el-button + .el-button {
    margin-left: 4px;
  }
  .col-center {
    min-width: 0;
  }
  .messages {
    padding: 14px;
  }
  .msg.user .msg-content {
    max-width: 90%;
  }
  .input-area {
    padding: 10px 12px;
  }
}
</style>

<!-- 右键菜单用 Teleport 挂到 body，作用域样式到不了，这里用全局样式 -->
<style>
.nb-ctx-mask {
  position: fixed;
  inset: 0;
  z-index: 3000;
  background: transparent;
}
.nb-ctx {
  position: fixed;
  min-width: 140px;
  background: var(--app-card);
  border: 1px solid var(--app-border);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
  padding: 6px;
  z-index: 3001;
}
.nb-ctx-title {
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 600;
  color: var(--app-ink-3);
  border-bottom: 1px solid var(--app-border);
  margin-bottom: 4px;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nb-ctx-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: none;
  background: transparent;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  color: var(--app-ink);
  text-align: left;
}
.nb-ctx-item:hover {
  background: var(--app-fill-1);
}
.nb-ctx-item.danger {
  color: var(--el-color-danger);
}
</style>