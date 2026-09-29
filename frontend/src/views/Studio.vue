<template>
  <div class="studio">
    <!-- 移动端简条：来源抽屉 / 当前工作区名 / 刷新 / 工作区报告抽屉（桌面端无此条，工作区切换在全局侧栏） -->
    <div v-if="isMobile" class="m-bar">
      <el-tooltip content="选择知识库 / 文件" placement="bottom">
        <el-button :icon="Files" size="small" link @click="sourcesDrawer = true" />
      </el-tooltip>
      <span class="m-bar-title">{{ studio.currentNotebook?.name || '工作台' }}</span>
      <el-tooltip content="刷新">
        <el-button :icon="Refresh" size="small" link @click="refresh" />
      </el-tooltip>
      <el-tooltip content="笔记 / 报告" placement="bottom">
        <el-button :icon="Collection" size="small" link @click="sideDrawer = true" />
      </el-tooltip>
    </div>

    <!-- 主体：中对话 + 右笔记/报告（桌面端）；来源选择统一走抽屉 -->
    <div class="layout">
      <div class="col col-center">
        <!-- 未选中工作区：居中欢迎块（今日视图已独立为 /today 门户页） -->
        <div v-if="!studio.currentNotebook" class="empty-tip">
          <div class="empty-welcome">
            <el-icon :size="44" class="empty-welcome-icon"><Notebook /></el-icon>
            <p class="empty-welcome-text">在左侧边栏选择一个工作区开始，或新建一个</p>
            <el-button type="primary" :icon="Plus" @click="createNewNotebook">新建工作区</el-button>
          </div>
        </div>
        <div v-else class="chat-area">
          <!-- 消息区 -->
          <!-- 消息区：点击拦截在容器层——内链（/customers/5 等）走前端路由，外链新标签打开 -->
          <div class="messages" ref="messagesRef" @click="onMsgClick">
            <div v-if="!messages.length" class="welcome">
              <h2>🎓 {{ studio.currentNotebook.name }}</h2>
              <p class="hint">点击输入区上方「范围」选择知识库或文件，然后开始提问</p>
              <div class="samples">
                <el-button v-for="q in sampleQuestions" :key="q" @click="send(q)">{{ q }}</el-button>
              </div>
            </div>
            <div v-for="(m, idx) in messages" :key="idx" :class="['msg', m.role]">
              <div class="msg-content markdown-body" v-html="renderMsg(m)" />
              <!-- 工具调用状态：连续相同工具合并为一个 chip，×N 计数随调用弹跳 -->
              <div v-if="m.role === 'ai' && m.tools?.length" class="tool-tags">
                <el-tooltip v-for="(t, j) in m.tools" :key="j" :content="t.error" placement="top" :disabled="!t.error" :show-after="100">
                  <div class="tool-chip" :class="{ done: !t.active && !(t.failed && !t.done), error: !t.active && t.failed > 0 && t.done === 0 }">
                    <el-icon class="tool-chip-icon" :class="{ pulsing: t.active }" :size="15">
                      <component :is="toolIcon(t.name)" />
                    </el-icon>
                    <span v-if="t.active">正在{{ toolLabel(t.name) }}…</span>
                    <span v-else-if="t.failed && !t.done">{{ toolLabel(t.name) }} 失败</span>
                    <span v-else>已使用 {{ toolLabel(t.name) }}</span>
                    <span v-if="t.total > 1" :key="t.tick" class="tool-chip-count">×{{ t.total }}</span>
                  </div>
                </el-tooltip>
              </div>
              <!-- AI 思考中指示（agent 决策阶段） -->
              <div v-if="m.role === 'ai' && m.thinking && !m.content" class="thinking-indicator">
                <el-icon class="pulsing" :size="16"><Cpu /></el-icon>
                <span>AI 正在思考…</span>
              </div>
              <!-- AI 消息操作 -->
              <div v-if="m.role === 'ai'" class="msg-actions">
                <el-button link size="small" :icon="Notebook" @click="saveAsNote(m, idx)">保存到笔记</el-button>
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
              <!-- 引用来源：file_id 非空可点击直达文件预览 -->
              <div v-if="m.role === 'ai' && m.showSources && m.sources?.length" class="sources-panel-inline">
                <div
                  v-for="s in m.sources"
                  :key="s.chunk_id"
                  class="source-item"
                  :class="{ clickable: !!s.file_id }"
                  @click="s.file_id && openSourcePreview(s)"
                >
                  <div class="source-title">
                    <span :class="{ 'source-link': !!s.file_id }">{{ s.file_name || s.doc_title }}</span>
                    <span class="score">{{ Math.round((s.score || 0) * 100) }}%</span>
                  </div>
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
                <!-- 附件上传：快速上传到文档库默认目录，并直接关联+选中到本工作区对话范围 -->
                <el-dropdown trigger="click" @command="onAttachCommand">
                  <el-button link class="attach-btn" :loading="attaching" title="上传文件/文件夹，关联到本工作区">
                    <el-icon v-if="!attaching" :size="16"><Paperclip /></el-icon>
                  </el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="files">上传文件</el-dropdown-item>
                      <el-dropdown-item command="folder">上传文件夹</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
                <input ref="attachInput" type="file" multiple hidden @change="onAttachChange" />
                <input ref="attachDirInput" type="file" webkitdirectory hidden @change="onAttachChange" />
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
                  <!-- 「范围」入口：桌面端在输入区附近就地弹出 popover（无遮罩），移动端走抽屉 -->
                  <el-popover
                    v-if="!isMobile"
                    v-model:visible="sourcesPopover"
                    placement="top-start"
                    :width="360"
                    trigger="click"
                    :popper-style="{ padding: 0 }"
                  >
                    <template #reference>
                      <span class="scope-tags">
                        <el-tag size="small" class="scope-tag scope-entry">
                          <el-icon :size="12" style="margin-right: 2px; vertical-align: -1px"><Files /></el-icon>范围
                        </el-tag>
                        <el-tag v-if="studio.selectedKbIds.length" size="small" type="primary" class="scope-tag">
                          {{ studio.selectedKbIds.length }} 知识库
                        </el-tag>
                        <el-tag v-if="studio.selectedFileIds.length" size="small" type="success" class="scope-tag">
                          {{ studio.selectedFileIds.length }} 文件
                        </el-tag>
                      </span>
                    </template>
                    <div class="popover-sources">
                      <SourcesPanel />
                    </div>
                  </el-popover>
                  <template v-else>
                    <el-tag size="small" class="scope-tag scope-entry" @click="sourcesDrawer = true">
                      <el-icon :size="12" style="margin-right: 2px; vertical-align: -1px"><Files /></el-icon>范围
                    </el-tag>
                    <el-tag v-if="studio.selectedKbIds.length" size="small" type="primary" class="scope-tag" @click="sourcesDrawer = true">
                      {{ studio.selectedKbIds.length }} 知识库
                    </el-tag>
                    <el-tag v-if="studio.selectedFileIds.length" size="small" type="success" class="scope-tag" @click="sourcesDrawer = true">
                      {{ studio.selectedFileIds.length }} 文件
                    </el-tag>
                  </template>
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
          <button :class="{ active: studio.activeTab === 'notes' }" @click="studio.activeTab = 'notes'">笔记</button>
          <button :class="{ active: studio.activeTab === 'reports' }" @click="studio.activeTab = 'reports'">报告</button>
        </div>
        <NotesPanel v-show="studio.activeTab === 'notes'" />
        <ReportsPanel v-if="studio.activeTab === 'reports'" ref="reportsPanelRef" />
      </div>
    </div>

    <!-- 来源选择抽屉（仅移动端；桌面端用输入区旁的 popover；Agent 模式下面板整体禁用遮罩） -->
    <el-drawer v-if="isMobile" v-model="sourcesDrawer" title="选择知识库 / 文件" size="82%">
      <div class="drawer-sources" :class="{ 'sources-disabled': effectiveAgentMode }">
        <SourcesPanel />
      </div>
    </el-drawer>
    <!-- 移动端：笔记 / 报告抽屉 -->
    <el-drawer v-if="isMobile" v-model="sideDrawer" title="笔记 / 报告" size="88%">
      <div class="col-tabs">
        <button :class="{ active: studio.activeTab === 'notes' }" @click="studio.activeTab = 'notes'">笔记</button>
        <button :class="{ active: studio.activeTab === 'reports' }" @click="studio.activeTab = 'reports'">报告</button>
      </div>
      <NotesPanel v-show="studio.activeTab === 'notes'" />
      <ReportsPanel v-if="studio.activeTab === 'reports'" ref="reportsPanelRef" />
    </el-drawer>

    <!-- 引用来源直达文件预览 -->
    <FilePreview v-model="previewVisible" :file="previewFile" />
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Notebook, Plus, Refresh,
  Search, Link, Folder, MagicStick, Collection, Cpu, Files,
  CircleCheck, CircleClose, VideoPause, Promotion, Paperclip,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useStudioStore } from '../stores/studio'
import { useBrandStore } from '../stores/brand'
import { renderMarkdown } from '../utils/markdown'
import { toolNameMap } from '../utils/format'
import { submitFeedback } from '../api'
import { uploadLibraryFiles, uploadSummary } from '../api/libraryUpload'
import SourcesPanel from '../components/Studio/SourcesPanel.vue'
import NotesPanel from '../components/Studio/NotesPanel.vue'
import ReportsPanel from '../components/Studio/ReportsPanel.vue'
import FilePreview from '../components/FilePreview.vue'

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
const brand = useBrandStore()
const route = useRoute()
const router = useRouter()

// 消息区链接点击拦截：站内路径（如 /customers/5）走前端路由不整页刷新；外链新标签打开。
// 模型有时会把相对 url 脑补成绝对地址（http://host/customers/5），
// 因此 http(s) 链接的路径命中站内路由前缀时也按内链处理
const INTERNAL_PREFIXES = ['/customers', '/library', '/kbs', '/notebooks', '/reports']

function onMsgClick(e) {
  const a = e.target.closest('a')
  if (!a) return
  const href = (a.getAttribute('href') || '').trim()
  if (!href) return
  if (href.startsWith('/')) {
    e.preventDefault()
    router.push(href)
    return
  }
  if (/^https?:\/\//.test(href)) {
    try {
      const u = new URL(href)
      if (u.host === window.location.host || INTERNAL_PREFIXES.some((p) => u.pathname.startsWith(p))) {
        e.preventDefault()
        router.push(u.pathname + u.search)
        return
      }
    } catch { /* 非法 URL 按外链处理 */ }
    e.preventDefault()
    window.open(href, '_blank', 'noopener')
  }
}

// 引用来源直达文件预览
const previewVisible = ref(false)
const previewFile = ref(null)
function openSourcePreview(s) {
  previewFile.value = { id: s.file_id, file_name: s.file_name, file_type: s.file_type, file_size: s.file_size }
  previewVisible.value = true
}

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
// 移动端：聊天区全宽，笔记/报告进抽屉；来源选择桌面端走输入区旁 popover，移动端走抽屉
const isMobile = ref(window.innerWidth < 992)
const sourcesDrawer = ref(false)
const sourcesPopover = ref(false)
const sideDrawer = ref(false)
// 附件快速上传：文档库默认目录（不传 folder_id），上传后直接关联本工作区并纳入对话范围
const attachInput = ref(null)
const attachDirInput = ref(null)
const attaching = ref(false)
function onAttachCommand(cmd) {
  if (!studio.currentNotebook) {
    ElMessage.warning('请先创建/选择一个工作区')
    return
  }
  ;(cmd === 'folder' ? attachDirInput : attachInput).value?.click()
}
async function onAttachChange(e) {
  const files = [...(e.target.files || [])]
  e.target.value = ''
  if (!files.length) return
  attaching.value = true
  try {
    // webkitRelativePath 保留文件夹目录结构；单文件退化为文件名
    const paths = files.map((f) => f.webkitRelativePath || f.name)
    const res = await uploadLibraryFiles({ files, paths })
    const newIds = (res?.files || []).map((f) => f.id)
    if (newIds.length && studio.currentNotebook) {
      const existed = studio.currentNotebook.source_file_ids || []
      // 关联到本工作区（updateCurrentNotebook 内部会同步对话范围的选中态）
      await studio.updateCurrentNotebook({ source_file_ids: [...new Set([...existed, ...newIds])] })
    }
    ElMessage.success(uploadSummary(res))
  } catch { /* 拦截器已提示 */ } finally {
    attaching.value = false
  }
}
function onResize() {
  isMobile.value = window.innerWidth < 992
  if (!isMobile.value) {
    sideDrawer.value = false
  }
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
  let html = renderMarkdown(m.content || '')
  // 联网来源引用上标：模型按引用规范输出的 [序号](http…) 链接 → 上标样式，
  // title 携带原始网址（悬停可见），点击由 onMsgClick 拦截新标签打开
  return html.replace(
    /<a href="(https?:\/\/[^"]+)">(\d{1,2})<\/a>/g,
    '<sup class="cite"><a href="$1" title="$1">$2</a></sup>'
  )
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
  ElMessage.success('已保存到笔记')
}

onMounted(async () => {
  window.addEventListener('resize', onResize)
  await studio.initNotebooks()
  // 自动恢复上次打开的工作区时，一并恢复它的会话历史（store 有状态则不覆盖）
  if (studio.currentNotebook) {
    await studio.loadChatHistory(studio.currentNotebook.id)
  }
  scrollToBottom()
})

// 侧栏切换当前工作区时（组件已挂载），恢复该工作区的会话历史并滚到底部
watch(
  () => studio.currentNotebook?.id,
  async (id) => {
    if (!id) return
    await studio.loadChatHistory(id)
    scrollToBottom()
  }
)

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
</script>

<style scoped>
.studio {
  display: flex;
  flex-direction: column;
  /* 桌面端：顶栏已移除，只扣主区上下 padding（20+20） */
  height: calc((100vh - 40px) / var(--app-zoom, 1));
  background: var(--app-bg);
}

/* 移动端简条 */
.m-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  background: var(--app-card);
  border-bottom: 1px solid var(--app-border);
}
.m-bar-title {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--app-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  border-radius: var(--app-radius) 0 0 var(--app-radius);
}
.col-right {
  width: 360px;
  flex-shrink: 0;
  background: var(--studio-side-bg);
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
/* 引用来源可点击（file_id 非空时直达文件预览） */
.source-item.clickable {
  cursor: pointer;
  border-radius: 6px;
  padding: 6px 8px;
  margin: 0 -8px;
  transition: background 0.15s ease;
}
.source-item.clickable:hover {
  background: var(--app-surface);
}
.source-link {
  color: var(--el-color-primary);
}
.source-item.clickable:hover .source-link {
  text-decoration: underline;
}
/* 联网来源上标引用：[n](url) 渲染为上标，悬停经 title 显示原始网址 */
:deep(sup.cite) {
  margin: 0 1px;
}
:deep(sup.cite a) {
  color: var(--el-color-primary);
  text-decoration: none;
  font-weight: 600;
  padding: 0 1px;
}
:deep(sup.cite a:hover) {
  text-decoration: underline;
}
/* 消息正文中的站内链接（客户主页等） */
:deep(.msg-content a) {
  color: var(--el-color-primary);
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
  align-items: center;
  flex-wrap: wrap;
}
/* 附件上传按钮：与截图一致的回形针入口 */
.attach-btn {
  padding: 4px;
  color: var(--app-ink-2);
}
.attach-btn:hover {
  color: var(--el-color-primary);
}
/* 范围 tag 组：桌面端作为 popover 的锚点整体包裹 */
.scope-tags {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
/* 范围 tag：可点击，打开来源选择（桌面 popover / 移动端抽屉） */
.scope-tag {
  cursor: pointer;
}
.scope-entry {
  background: var(--app-surface);
}
/* 来源 popover 内容：固定高度内滚动（SourcesPanel 自身 height:100% + overflow） */
.popover-sources {
  height: min(420px, 55vh);
  overflow: hidden;
}

/* ========== 移动端（<992px）：聊天区全宽，笔记/报告进抽屉 ========== */
@media (max-width: 991px) {
  .studio {
    /* 顶栏(52px)+简条+底部标签栏(~66px)+内边距 */
    height: calc((100vh - 150px - env(safe-area-inset-bottom)) / var(--app-zoom, 1));
  }
  .col-center {
    min-width: 0;
    border-radius: var(--app-radius);
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
