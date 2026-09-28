<template>
  <div class="today-page" v-loading="loading">
    <div class="today-grid">
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

      <!-- 右栏：团队今日动态 + 底部今日新增计数 -->
      <section class="col-feed rise d2">
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
import { Document, Paperclip } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { getDashboardToday, quickCapture, getDailyReport } from '../api'
import { uploadLibraryFiles } from '../api/libraryUpload'
import { ensureUploadFormats, isEnabledExt, enabledAcceptStr } from '../utils/uploadFormats'
import { formatDateTime } from '../utils/format'

const router = useRouter()
const authStore = useAuthStore()
const loading = ref(false)
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
  const dt = new Date(d)
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
    ElMessage.success(fileIds.length ? `已记入「随手记」笔记本（含 ${fileIds.length} 个附件）` : '已记入「随手记」笔记本')
    load()  // 刷新今日新增计数
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

async function load() {
  loading.value = true
  try {
    overview.value = await getDashboardToday()
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.today-page {
  max-width: 1200px;
  margin: 0 auto;
}
/* 三栏：台历 / 主栏 / 动态，栏间 1px 竖分割线 */
.today-grid {
  display: flex;
  align-items: stretch;
  background: var(--app-surface);
  border: 1px solid var(--app-line);
  border-radius: var(--app-radius-lg);
  box-shadow: var(--app-shadow);
  min-height: calc(100vh - var(--app-header-h) - 40px);
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
  font-size: 11px;
  color: var(--app-ink-2);
  opacity: 0.7;
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
.col-feed {
  flex: none;
  width: 320px;
  padding: 24px 20px;
  display: flex;
  flex-direction: column;
}
.feed-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--app-ink);
  margin-bottom: 6px;
}
.activity-list {
  flex: 1;
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
  font-size: 11px;
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

/* 移动端：纵向堆叠，台历块变横条（日期问候一行） */
@media (max-width: 991px) {
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
  .col-feed {
    width: auto;
    padding: 16px;
  }
}
</style>
