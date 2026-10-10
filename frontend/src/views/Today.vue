<template>
  <div class="today-page" v-loading="loading">
    <div class="today-grid" v-if="!loadError">
      <!-- 左栏：台历块（大号日数 + 月份星期 + 问候 + 日报入口） -->
      <section class="col-cal rise">
        <div class="cal-day">{{ dayNum }}</div>
        <div class="cal-month">{{ monthText }} · {{ weekdayText }}</div>
        <div class="cal-greet">{{ greeting }}，{{ authStore.user?.name || authStore.user?.username }}</div>
        <el-button class="cal-report" size="small" :icon="Document" @click="openDailyReport">我的日报</el-button>
      </section>

      <!-- 中栏：随手记 + 统计行 + 待办与跟进 -->
      <section class="col-main rise d1">
        <div class="capture-row">
          <div class="capture-input-wrap">
            <el-input
              v-model="captureText"
              type="textarea"
              :rows="5"
              placeholder="随手记：想到什么先记下来（提到客户名会自动识别建议关联），可带附件"
              @keydown.ctrl.enter="handleCapture"
            />
            <div class="capture-bar">
              <el-tooltip content="添加附件（上传到文档库并随笔记归档）" placement="bottom">
                <el-button link class="attach-btn" :disabled="capturing" @click="attachInput?.click()">
                  <el-icon :size="16"><Paperclip /></el-icon>
                </el-button>
              </el-tooltip>
              <input ref="attachInput" type="file" multiple hidden :accept="acceptStr" @change="onAttachChange" />
              <span v-if="captureFiles.length" class="attach-count">{{ captureFiles.length }} 个附件</span>
              <span class="capture-hint">Ctrl+Enter 记下</span>
            </div>
            <div v-if="captureFiles.length" class="attach-chips">
              <el-tag
                v-for="(f, i) in captureFiles"
                :key="i"
                size="small"
                closable
                effect="plain"
                class="attach-chip"
                @close="captureFiles.splice(i, 1)"
              >{{ f.name }}</el-tag>
            </div>
          </div>
          <el-button type="primary" :loading="capturing" :disabled="!captureText.trim()" @click="handleCapture">
            记下
          </el-button>
        </div>
        <div v-if="capturedSuggestions.length" class="capture-sugg">
          <span class="capture-sugg-label">提到了：</span>
          <el-tag
            v-for="c in capturedSuggestions"
            :key="c.id"
            size="small"
            class="capture-chip"
            @click="router.push(`/customers/${c.id}`)"
          >{{ c.name }}</el-tag>
          <span class="capture-sugg-tip">点击进客户页补跟进记录</span>
        </div>

        <!-- 数字收进一行小字统计（原 4 张概览卡取消），只有逾期>0 标红 -->
        <div class="stats-line">
          <span>今日到期 {{ overview.today_tasks?.length || 0 }}</span>
          <span :class="{ danger: overview.overdue_tasks?.length }">逾期 {{ overview.overdue_tasks?.length || 0 }}</span>
          <span>久未跟进 {{ overview.stale_customers?.length || 0 }}</span>
          <span>未读通知 {{ overview.unread_notifications || 0 }}</span>
        </div>

        <div class="main-list">
          <template v-if="overview.today_tasks?.length || overview.overdue_tasks?.length">
            <!-- 逾期置顶，右侧红标天数 -->
            <div v-for="t in [...(overview.overdue_tasks || []), ...(overview.today_tasks || [])]" :key="t.id" class="task-row" @click="router.push('/tasks')">
              <el-tag v-if="t.priority === 'high'" size="small" type="danger" effect="plain">急</el-tag>
              <span class="task-title">{{ t.title }}</span>
              <span class="task-due">{{ formatDue(t.due_date) }}</span>
            </div>
          </template>
          <div v-else class="col-empty">今日没有到期任务 🎉</div>
          <template v-if="overview.stale_customers?.length">
            <div class="col-sub">久未跟进（{{ staleDays }}天+）</div>
            <div v-for="c in overview.stale_customers" :key="c.id" class="stale-row" @click="router.push(`/customers/${c.id}`)">
              <span class="stale-name">{{ c.name }}</span>
              <span class="stale-co">{{ c.company || '' }}</span>
              <span class="stale-days">{{ c.never ? '从未跟进' : `${c.days} 天未跟进` }}</span>
            </div>
          </template>
        </div>
      </section>

      <!-- 右栏：迷你日历（上）+ 团队今日动态（下），一屏内看完 -->
      <section class="col-right rise d2">
        <div class="cal-head">
          <span class="cal-title">日历</span>
          <span class="cal-month-label">{{ calMonthLabel }}</span>
          <div class="cal-actions">
            <el-button-group>
              <el-button size="small" :icon="ArrowLeft" aria-label="上一月" @click="shiftMonth(-1)" />
              <el-button size="small" @click="goToday">今天</el-button>
              <el-button size="small" :icon="ArrowRight" aria-label="下一月" @click="shiftMonth(1)" />
            </el-button-group>
            <el-tooltip content="订阅日历（ICS，可同步到 Outlook / Google / iPhone）" placement="bottom">
              <el-button size="small" :icon="Link" aria-label="订阅日历" @click="openSubscribe" />
            </el-tooltip>
          </div>
        </div>
        <div class="cal-weekdays">
          <span v-for="w in WEEKDAYS" :key="w">{{ w }}</span>
        </div>
        <div v-loading="calLoading" class="cal-grid">
          <div
            v-for="cell in calCells"
            :key="cell.key"
            class="cal-cell"
            :class="{ dim: !cell.inMonth, today: cell.isToday }"
            @click="openDay(cell)"
          >
            <div class="cal-cell-num">{{ cell.day }}</div>
            <div class="cal-cell-events">
              <div
                v-for="ev in cell.events.slice(0, 2)"
                :key="ev.id"
                class="cal-ev"
                :class="[`cal-ev-${ev.type}`, { done: ev.done }]"
              >
                <span class="cal-ev-dot"></span>
                <span class="cal-ev-text">{{ ev.title }}</span>
              </div>
              <div v-if="cell.events.length > 2" class="cal-ev-more">+{{ cell.events.length - 2 }}</div>
            </div>
          </div>
        </div>

        <div class="feed-title">团队今日动态</div>
        <div v-if="overview.activity?.length" class="activity-list">
          <div v-for="(a, i) in overview.activity" :key="i" class="activity-row">
            <span class="a-user">{{ a.user }}</span>
            <span class="a-action">{{ actionText(a) }}</span>
            <span class="a-time">{{ formatTime(a.created_at) }}</span>
          </div>
        </div>
        <div v-else class="col-empty">今天还没有团队动态</div>
        <div class="feed-foot">
          今日新增：文件 {{ overview.today_new?.files || 0 }} · 跟进 {{ overview.today_new?.followups || 0 }} · 笔记 {{ overview.today_new?.notes || 0 }} · 客户 {{ overview.today_new?.customers || 0 }}
        </div>
      </section>
    </div>

    <!-- 首屏加载失败：给重试入口（错误细节已由拦截器 toast 提示） -->
    <div v-else class="load-fail">
      <p>今日数据加载失败，请检查网络后重试</p>
      <el-button type="primary" @click="load">重试</el-button>
    </div>

    <!-- 某日事件清单弹窗 -->
    <el-dialog v-model="dayDialog" :title="dayDialogTitle" width="min(92vw, 420px)">
      <div v-if="dayEvents.length" class="day-events">
        <div v-for="ev in dayEvents" :key="ev.id" class="day-ev" @click="goEvent(ev)">
          <el-tag size="small" :type="evTagType(ev)" effect="light">{{ evTypeLabel(ev) }}</el-tag>
          <span class="day-ev-title" :class="{ done: ev.done }">{{ ev.title }}</span>
          <span v-if="ev.customer_name" class="day-ev-co">{{ ev.customer_name }}</span>
        </div>
      </div>
      <el-empty v-else description="当日暂无事件" :image-size="60" />
    </el-dialog>

    <!-- ICS 订阅弹窗：打开时才请求 token，不在首屏请求 -->
    <el-dialog v-model="subDialog" title="订阅日历" width="min(92vw, 560px)">
      <div v-loading="subLoading">
        <p class="sub-tip">把下面的地址添加到常用日历软件，即可同步任务到期、商机节点与客户生日：</p>
        <el-input v-model="subUrl" readonly placeholder="订阅地址获取中…">
          <template #append>
            <el-button :icon="CopyDocument" :disabled="!subUrl" @click="copySubUrl">复制</el-button>
          </template>
        </el-input>
        <ul class="sub-steps">
          <li><b>Outlook</b>：日历 → 添加日历 → 订阅自 web，粘贴上面的地址</li>
          <li><b>Google 日历</b>：其他日历 → 通过网址添加</li>
          <li><b>iPhone</b>：设置 → 日历 → 账户 → 添加已订阅日历</li>
        </ul>
        <p class="sub-note">订阅地址为只读；token 有效期 180 天，到期后回到本页重新获取即可。</p>
      </div>
    </el-dialog>

    <!-- 日报弹窗 -->
    <el-dialog v-model="reportDialog" :title="`日报 · ${reportData?.date || ''}`" width="min(92vw, 560px)">
      <div v-if="reportData" class="report-body">
        <div v-for="item in reportData.items" :key="item.user_id" class="report-item">
          <div class="report-user">{{ item.user_name }}</div>
          <div class="report-line">
            跟进 <b>{{ item.followups }}</b> 条 · 完成任务 <b>{{ item.tasks_done }}</b> 项 ·
            上传文件 <b>{{ item.files }}</b> 个 · 笔记 <b>{{ item.notes }}</b> 条 · 新增客户 <b>{{ item.customers_new }}</b> 位
          </div>
        </div>
        <el-empty v-if="!reportData.items.length" description="当日暂无数据" :image-size="60" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Document, Paperclip, ArrowLeft, ArrowRight, Link, CopyDocument } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { getDashboardToday, quickCapture, getDailyReport, getCalendarEvents, getCalendarFeedToken } from '../api'
import { uploadLibraryFiles } from '../api/libraryUpload'
import { ensureUploadFormats, isEnabledExt, enabledAcceptStr } from '../utils/uploadFormats'
import { formatDateTime, parseServerDate } from '../utils/format'
import { usePagedFetch } from '../utils/usePagedFetch'

const router = useRouter()
const authStore = useAuthStore()
const loading = ref(false)
const loadError = ref(false)
const overview = ref({})
const staleDays = 7

// 台历块：大号日数 + 中文月份/星期 + 时段问候
const now = new Date()
const dayNum = now.getDate()
const MONTH_CN = ['一月', '二月', '三月', '四月', '五月', '六月', '七月', '八月', '九月', '十月', '十一月', '十二月']
const monthText = MONTH_CN[now.getMonth()]
const weekdayText = now.toLocaleDateString('zh-CN', { weekday: 'long' })

const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})

function formatDue(d) {
  if (!d) return ''
  // 统一走 parseServerDate：后端 naive UTC 字符串按 UTC 解析，避免差 8 小时
  const dt = parseServerDate(d)
  if (!dt) return ''
  const today = new Date(); today.setHours(0, 0, 0, 0)
  return dt < today ? `逾期 ${Math.floor((today - dt) / 86400000)} 天` : '今日到期'
}
function formatTime(t) {
  return formatDateTime(t)?.slice(11, 16) || ''
}
const ACTION_TEXT = { create: '新建', update: '更新', delete: '删除', upload: '上传', restore: '恢复', export: '导出', reparse: '重解析', share: '分享' }
function actionText(a) {
  const verb = ACTION_TEXT[a.action] || a.action
  const name = a.detail?.file_name || a.detail?.name || a.detail?.title || a.detail?.username || ''
  return `${verb}${a.resource_type ? ' ' + ({ customer: '客户', file: '文件', kb: '知识库', notebook: '笔记', report: '报告', user: '用户' }[a.resource_type] || a.resource_type) : ''}${name ? `「${name}」` : ''}`
}

// ========== 随手记 ==========
const captureText = ref('')
const capturing = ref(false)
const capturedSuggestions = ref([])
// 附件：先选本地文件，点「记下」时上传到文档库默认目录，再把 file_ids 随笔记归档
const captureFiles = ref([])
const attachInput = ref(null)
// 当前启用解析格式（accept 过滤 + 选择校验）；拉取失败时不过滤
const acceptStr = ref('')
ensureUploadFormats().then((set) => {
  acceptStr.value = set ? enabledAcceptStr() : ''
})

function onAttachChange(e) {
  const files = [...(e.target.files || [])]
  e.target.value = ''
  for (const f of files) {
    if (!isEnabledExt(f.name)) {
      ElMessage.warning(`「${f.name}」不是当前支持解析的格式，已忽略`)
      continue
    }
    if (captureFiles.value.length >= 9) {
      ElMessage.warning('附件最多 9 个')
      break
    }
    captureFiles.value.push(f)
  }
}

async function handleCapture() {
  const text = captureText.value.trim()
  if (!text) return
  capturing.value = true
  try {
    // 有附件先上传文档库（默认目录），拿到 file_ids 随笔记归档
    let fileIds = []
    if (captureFiles.value.length) {
      const up = await uploadLibraryFiles({ files: captureFiles.value })
      fileIds = (up?.files || []).map((f) => f.id)
    }
    const res = await quickCapture({ text, file_ids: fileIds.length ? fileIds : undefined })
    capturedSuggestions.value = res.suggested_customers || []
    captureText.value = ''
    captureFiles.value = []
    const baseMsg = fileIds.length ? `已记入「随手记」笔记本（含 ${fileIds.length} 个附件）` : '已记入「随手记」笔记本'
    // 识别到提醒意图时后端已建任务（日历可见 + 临期通知），提示并刷新日历
    ElMessage.success(res.reminder ? `${baseMsg}，已创建提醒：${res.reminder.due_at_local}「${res.reminder.title}」` : baseMsg)
    load()  // 刷新今日新增计数
    if (res.reminder) loadCalendar()
  } finally {
    capturing.value = false
  }
}

// ========== 日报 ==========
const reportDialog = ref(false)
const reportData = ref(null)

async function openDailyReport() {
  reportData.value = await getDailyReport({ team: authStore.user?.role === 'admin' })
  reportDialog.value = true
}

// ========== 月历 ==========
// 事件类型配色/文案：任务蓝、商机橙、生日红；done 任务在格子里划线置灰
const CAL_TYPE = {
  task: { label: '任务', tag: 'primary' },
  opportunity: { label: '商机', tag: 'warning' },
  birthday: { label: '生日', tag: 'danger' },
}
const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']

const calCursor = ref(new Date())  // 当前显示月份（只用其年/月）
const calEvents = ref([])
// 复用分页防竞态思路：快速切月时丢弃过期响应
const { loading: calLoading, run: runCalFetch } = usePagedFetch()

const pad2 = (n) => String(n).padStart(2, '0')
// 后端 date 为服务器本地日期（YYYY-MM-DD），这里按本地时区生成同格式字符串，不做时区换算
function ymd(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

const calMonthLabel = computed(() => `${calCursor.value.getFullYear()} 年 ${calCursor.value.getMonth() + 1} 月`)

// 6 行 × 7 列固定网格（周一开头），含上/下月露头日期；事件按日期分组挂到格子
const calCells = computed(() => {
  const y = calCursor.value.getFullYear()
  const m = calCursor.value.getMonth()
  const startOffset = (new Date(y, m, 1).getDay() + 6) % 7  // getDay 周日=0 → 周一开头偏移
  const gridStart = new Date(y, m, 1 - startOffset)
  const todayStr = ymd(new Date())
  const evMap = {}
  for (const ev of calEvents.value) {
    if (ev.date) (evMap[ev.date] ||= []).push(ev)
  }
  const cells = []
  for (let i = 0; i < 42; i++) {
    const d = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + i)
    const key = ymd(d)
    cells.push({ key, day: d.getDate(), inMonth: d.getMonth() === m, isToday: key === todayStr, events: evMap[key] || [] })
  }
  return cells
})

function loadCalendar() {
  // start/end 取当月首尾各外延一周，覆盖网格里露头的上/下月日期
  const y = calCursor.value.getFullYear()
  const m = calCursor.value.getMonth()
  const start = new Date(y, m, 1)
  start.setDate(start.getDate() - 7)
  const end = new Date(y, m + 1, 0)
  end.setDate(end.getDate() + 7)
  runCalFetch(
    () => getCalendarEvents({ start: ymd(start), end: ymd(end) }),
    (res) => { calEvents.value = res?.events || [] },
  ).catch(() => { /* 拦截器已提示，日历保持旧数据 */ })
}

function shiftMonth(n) {
  calCursor.value = new Date(calCursor.value.getFullYear(), calCursor.value.getMonth() + n, 1)
  loadCalendar()
}
function goToday() {
  calCursor.value = new Date()
  loadCalendar()
}

// 某日事件清单
const dayDialog = ref(false)
const dayCell = ref(null)
const dayEvents = computed(() => dayCell.value?.events || [])
const dayDialogTitle = computed(() => (dayCell.value ? `${dayCell.value.key} 日程` : ''))

function openDay(cell) {
  dayCell.value = cell
  dayDialog.value = true
}
function evTypeLabel(ev) {
  return CAL_TYPE[ev.type]?.label || '事件'
}
function evTagType(ev) {
  return CAL_TYPE[ev.type]?.tag || 'info'
}
function goEvent(ev) {
  dayDialog.value = false
  if (ev.url) router.push(ev.url)
}

// ========== ICS 订阅 ==========
const subDialog = ref(false)
const subUrl = ref('')
const subLoading = ref(false)

async function openSubscribe() {
  subDialog.value = true
  if (subUrl.value || subLoading.value) return  // 已取过/在取不重复请求
  subLoading.value = true
  try {
    const res = await getCalendarFeedToken()
    subUrl.value = location.origin + (res?.path || '')
  } catch {
    /* 拦截器已提示 */
  } finally {
    subLoading.value = false
  }
}

async function copySubUrl() {
  if (!subUrl.value) return
  try {
    await navigator.clipboard.writeText(subUrl.value)
  } catch {
    // http 等非安全上下文没有 clipboard API，退回 textarea 选中复制
    const ta = document.createElement('textarea')
    ta.value = subUrl.value
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    ta.remove()
  }
  ElMessage.success('订阅地址已复制')
}

async function load() {
  loading.value = true
  loadError.value = false
  try {
    overview.value = await getDashboardToday()
  } catch {
    // 拦截器已 toast，这里切到重试态避免只剩空白页
    loadError.value = true
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  loadCalendar()
})
</script>

<style scoped>
.today-page {
  /* 与其他页面一致铺满内容区；桌面端控制在一屏内（列表内部滚动，页面不滚） */
  height: calc(100vh - var(--app-header-h) - 40px);
  display: flex;
  flex-direction: column;
}
/* 三栏：台历 / 主栏 / 右栏（迷你日历+动态），栏间 1px 竖分割线 */
.today-grid {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: stretch;
  background: var(--app-surface);
  border: 1px solid var(--app-line);
  border-radius: var(--app-radius-lg);
  box-shadow: var(--app-shadow);
}
.col-cal {
  flex: none;
  width: 220px;
  padding: 32px 24px;
  border-right: 1px solid var(--app-line);
  display: flex;
  flex-direction: column;
}
.cal-day {
  font-size: 72px;
  font-weight: 700;
  line-height: 1;
  font-variant-numeric: tabular-nums;
  color: var(--app-ink);
}
.cal-month {
  margin-top: 8px;
  font-size: 14px;
  color: var(--app-ink-2);
}
.cal-greet {
  margin-top: 16px;
  font-size: 15px;
  font-weight: 600;
  color: var(--app-ink);
}
.cal-report {
  margin-top: 20px;
  align-self: flex-start;
}
.col-main {
  flex: 1;
  min-width: 0;
  padding: 24px 28px;
  border-right: 1px solid var(--app-line);
  display: flex;
  flex-direction: column;
}
.main-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
.capture-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}
.capture-input-wrap {
  flex: 1;
  min-width: 0;
}
.capture-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}
.attach-btn {
  padding: 2px 4px;
  color: var(--app-ink-2);
}
.attach-btn:hover {
  color: var(--el-color-primary);
}
.attach-count {
  font-size: 12px;
  color: var(--app-ink-2);
}
.capture-hint {
  margin-left: auto;
  font-size: 12px;
  color: var(--app-ink-2);
}
.attach-chips {
  margin-top: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.attach-chip {
  max-width: 240px;
}
.capture-sugg {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.capture-sugg-label {
  font-size: 12px;
  color: var(--app-ink-2);
}
.capture-chip {
  cursor: pointer;
}
.capture-sugg-tip {
  font-size: 12px;
  color: var(--app-ink-2);
}
.stats-line {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  margin: 16px 0 10px;
  font-size: 12px;
  color: var(--app-ink-2);
}
.stats-line .danger {
  color: var(--el-color-danger);
  font-weight: 600;
}
.task-row, .stale-row, .activity-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 4px;
  border-bottom: 1px solid var(--app-line);
  cursor: pointer;
  font-size: 13px;
}
.task-row:hover, .stale-row:hover {
  background: var(--app-bg);
}
.task-title, .stale-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-due, .stale-days {
  font-size: 12px;
  color: var(--el-color-danger);
  flex: none;
}
.stale-co {
  font-size: 12px;
  color: var(--app-ink-2);
  flex: none;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.col-sub {
  font-size: 12px;
  font-weight: 600;
  color: var(--app-ink-2);
  margin: 12px 0 6px;
}
.col-empty {
  color: var(--app-ink-2);
  font-size: 13px;
  padding: 20px 0;
  text-align: center;
}
/* 首屏失败重试态 */
.load-fail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 80px 0;
  color: var(--app-ink-2);
  font-size: 14px;
}
.col-right {
  flex: none;
  width: 400px;
  padding: 16px 16px 14px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.feed-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--app-ink);
  margin: 14px 0 6px;
  padding-top: 12px;
  border-top: 1px solid var(--app-line);
}
.activity-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
.activity-row {
  cursor: default;
}
.a-user {
  font-weight: 600;
  flex: none;
}
.a-action {
  flex: 1;
  min-width: 0;
  color: var(--app-ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.a-time {
  font-size: 12px;
  color: var(--app-ink-2);
  flex: none;
}
.feed-foot {
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px solid var(--app-line);
  font-size: 12px;
  color: var(--app-ink-2);
}
.report-item {
  padding: 10px 0;
  border-bottom: 1px solid var(--app-line);
}
.report-user {
  font-weight: 600;
  margin-bottom: 4px;
}
.report-line {
  font-size: 13px;
  color: var(--app-ink-2);
}
.report-line b {
  color: var(--app-ink);
}

/* ========== 迷你月历（右栏上部） ========== */
.cal-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.cal-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--app-ink);
}
.cal-month-label {
  font-size: 12px;
  color: var(--app-ink-2);
  font-variant-numeric: tabular-nums;
}
.cal-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
}
.cal-weekdays {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  font-size: 12px;
  color: var(--app-ink-2);
  text-align: center;
  padding: 4px 0;
  border-top: 1px solid var(--app-line);
}
.cal-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  border-left: 1px solid var(--app-line);
  border-top: 1px solid var(--app-line);
}
.cal-cell {
  min-height: 54px;
  padding: 3px 4px;
  border-right: 1px solid var(--app-line);
  border-bottom: 1px solid var(--app-line);
  cursor: pointer;
  overflow: hidden;
}
.cal-cell:hover {
  background: var(--app-bg);
}
.cal-cell.dim .cal-cell-num {
  color: var(--app-ink-2);
  opacity: 0.5;
}
.cal-cell.today .cal-cell-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--el-color-primary);
  color: #fff;
}
.cal-cell-num {
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  color: var(--app-ink);
}
.cal-cell-events {
  margin-top: 2px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.cal-ev {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  line-height: 18px;
  border-radius: 4px;
  padding: 0 4px;
  min-width: 0;
}
.cal-ev-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.cal-ev-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cal-ev-task {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.cal-ev-task .cal-ev-dot { background: var(--el-color-primary); }
.cal-ev-opportunity {
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning-dark-2);
}
.cal-ev-opportunity .cal-ev-dot { background: var(--el-color-warning); }
.cal-ev-birthday {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}
.cal-ev-birthday .cal-ev-dot { background: var(--el-color-danger); }
.cal-ev.done {
  opacity: 0.55;
}
.cal-ev.done .cal-ev-text {
  text-decoration: line-through;
}
.cal-ev-more {
  font-size: 11px;
  color: var(--app-ink-2);
  padding-left: 4px;
}
/* 某日事件清单 */
.day-ev {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 4px;
  border-bottom: 1px solid var(--app-line);
  cursor: pointer;
  font-size: 13px;
}
.day-ev:hover {
  background: var(--app-bg);
}
.day-ev-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.day-ev-title.done {
  text-decoration: line-through;
  color: var(--app-ink-2);
}
.day-ev-co {
  flex: none;
  font-size: 12px;
  color: var(--app-ink-2);
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 订阅弹窗 */
.sub-tip {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--app-ink-2);
}
.sub-steps {
  margin: 14px 0 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--app-ink-2);
  line-height: 1.9;
}
.sub-steps b {
  color: var(--app-ink);
}
.sub-note {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--app-ink-2);
}

/* 三栏交错淡入上移（60-100ms 阶梯），reduce 时关闭 */
.rise {
  opacity: 0;
  animation: rise-in 0.4s ease forwards;
}
.rise.d1 { animation-delay: 0.08s; }
.rise.d2 { animation-delay: 0.16s; }
@keyframes rise-in {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  .rise { animation: none; opacity: 1; }
}

/* 移动端：纵向堆叠（恢复页面滚动），台历块变横条（日期问候一行） */
@media (max-width: 991px) {
  .today-page {
    height: auto;
  }
  .today-grid {
    flex-direction: column;
    min-height: 0;
  }
  .col-cal {
    width: auto;
    flex-direction: row;
    align-items: baseline;
    gap: 10px;
    padding: 16px;
    border-right: none;
    border-bottom: 1px solid var(--app-line);
  }
  .cal-day {
    font-size: 34px;
  }
  .cal-month {
    margin-top: 0;
  }
  .cal-greet {
    margin-top: 0;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .cal-report {
    margin-top: 0;
  }
  .col-main {
    padding: 16px;
    border-right: none;
    border-bottom: 1px solid var(--app-line);
  }
  .col-right {
    width: auto;
    padding: 16px;
  }
  /* 月历移动端：格子缩小，事件只留色点 */
  .cal-head {
    flex-wrap: wrap;
    gap: 8px;
  }
  .cal-actions {
    margin-left: 0;
    width: 100%;
    justify-content: space-between;
  }
  .cal-cell {
    min-height: 44px;
    padding: 2px 3px;
  }
  .cal-cell-num {
    font-size: 12px;
  }
  .cal-cell.today .cal-cell-num {
    width: 18px;
    height: 18px;
  }
  .cal-cell-events {
    flex-direction: row;
    flex-wrap: wrap;
    gap: 3px;
  }
  .cal-ev {
    padding: 0;
    background: none;
    line-height: 1;
  }
  .cal-ev-text {
    display: none;
  }
  .cal-ev-more {
    padding-left: 0;
  }
}
</style>
