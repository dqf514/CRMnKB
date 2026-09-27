<template>
  <div>
    <el-card>
      <div class="toolbar">
        <el-radio-group v-model="queryStatus" @change="loadList">
          <el-radio-button value="pending">待审批</el-radio-button>
          <el-radio-button value="executed">已执行</el-radio-button>
          <el-radio-button value="rejected">已拒绝</el-radio-button>
          <el-radio-button value="failed">执行失败</el-radio-button>
          <el-radio-button value="">全部</el-radio-button>
        </el-radio-group>
        <el-button :icon="Refresh" style="margin-left: auto" @click="loadList">刷新</el-button>
      </div>

      <div v-loading="loading">
        <el-empty v-if="!loading && !list.length" description="暂无审批单" />
        <div v-for="a in list" :key="a.id" class="appr-item">
          <div class="appr-head">
            <el-tag size="small" type="primary" effect="plain">{{ toolLabel(a.tool_name) }}</el-tag>
            <span class="appr-summary">{{ a.summary }}</span>
            <el-tag size="small" :type="enumTagType(agentApprovalStatusMap, a.status)">
              {{ enumLabel(agentApprovalStatusMap, a.status) }}
            </el-tag>
          </div>
          <!-- 参数明细：key 友好翻译 + 值友好展示（对象序列化） -->
          <div v-if="argEntries(a).length" class="appr-args">
            <div v-for="[k, v] in argEntries(a)" :key="k" class="arg-row">
              <span class="arg-key">{{ argKeyLabel(k) }}</span>
              <span class="arg-val">{{ argValueText(v) }}</span>
            </div>
          </div>
          <div v-if="a.result" class="appr-result">执行结果：{{ argValueText(a.result) }}</div>
          <div v-if="a.decision_reason" class="appr-reason">审批意见：{{ a.decision_reason }}</div>
          <div class="appr-meta">
            <span v-if="isAdmin">发起人 #{{ a.requester_user_id }} · </span>
            <span>提交于 {{ formatDateTime(a.created_at) }}</span>
            <span v-if="a.decided_at"> · 处理于 {{ formatDateTime(a.decided_at) }}</span>
          </div>
          <!-- 仅 admin 且待审批时可决策：批准后后端自动执行（写跟进/发邮件） -->
          <div v-if="isAdmin && a.status === 'pending'" class="appr-actions">
            <el-button type="success" size="small" :loading="decidingId === a.id" @click="handleApprove(a)">批准并执行</el-button>
            <el-button type="danger" size="small" plain :loading="decidingId === a.id" @click="handleReject(a)">拒绝</el-button>
          </div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getAgentApprovals, decideAgentApproval } from '../api/agentApprovals'
import { agentApprovalStatusMap, toolNameMap, enumLabel, enumTagType, formatDateTime } from '../utils/format'
import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.user?.role === 'admin')

const list = ref([])
const loading = ref(false)
const decidingId = ref(null)
// 默认看待审批（最需要处理的）；审批单可能由他人决策，轮询保持状态新鲜
const queryStatus = ref('pending')

async function loadList() {
  loading.value = true
  try {
    const res = await getAgentApprovals(queryStatus.value ? { status: queryStatus.value } : {})
    list.value = Array.isArray(res) ? res : (res?.items || [])
  } finally {
    loading.value = false
  }
}

function toolLabel(name) {
  return toolNameMap[name] || name
}

// arguments JSON → [key, value] 列表（非对象时原样包一层）
function argEntries(a) {
  const args = a.arguments
  if (!args || typeof args !== 'object' || Array.isArray(args)) return []
  return Object.entries(args)
}

// 常见参数 key → 中文标签（写跟进 / 邮件草稿两类工具的字段）
const ARG_KEY_MAP = {
  customer_id: '客户 ID',
  content: '内容',
  note: '备注',
  followup_type: '跟进方式',
  type: '类型',
  to: '收件人',
  recipients: '收件人',
  cc: '抄送',
  subject: '主题',
  body: '正文',
}
function argKeyLabel(k) {
  return ARG_KEY_MAP[k] || k
}

// 参数/结果值友好展示：对象/数组序列化，其余转字符串
function argValueText(v) {
  if (v === null || v === undefined || v === '') return '-'
  if (typeof v === 'object') return JSON.stringify(v, null, 2)
  return String(v)
}

async function handleApprove(a) {
  await ElMessageBox.confirm(
    `确定批准「${a.summary}」吗？批准后系统将立即执行该操作。`,
    '批准确认', { type: 'warning' }
  )
  await decide(a, 'approve')
}

async function handleReject(a) {
  const { value } = await ElMessageBox.prompt('可填写拒绝原因（选填）', `拒绝「${a.summary}」`, {
    confirmButtonText: '确认拒绝',
    cancelButtonText: '取消',
    inputPlaceholder: '拒绝原因',
  }).catch(() => ({ value: null }))
  // 点取消中断；确认（含空原因）继续
  if (value === null) return
  await decide(a, 'reject', value || undefined)
}

async function decide(a, decision, reason) {
  decidingId.value = a.id
  try {
    await decideAgentApproval(a.id, { decision, reason })
    ElMessage.success(decision === 'approve' ? '已批准，正在执行' : '已拒绝')
    await loadList()
  } finally {
    decidingId.value = null
  }
}

let timer = null
onMounted(() => {
  loadList()
  // 30 秒轮询：普通用户等审批结果、admin 等新审批单，无需手动刷新
  timer = setInterval(loadList, 30000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
  align-items: center;
}
.appr-item {
  border: 1px solid var(--app-border, var(--el-border-color));
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 12px;
}
.appr-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.appr-summary {
  font-weight: 600;
  color: var(--app-ink, var(--el-text-color-primary));
  flex: 1;
  min-width: 0;
}
.appr-args {
  margin-top: 8px;
  background: var(--app-fill-1, var(--el-fill-color-light));
  border-radius: 6px;
  padding: 8px 10px;
}
.arg-row {
  display: flex;
  gap: 10px;
  font-size: 13px;
  padding: 2px 0;
}
.arg-key {
  flex: none;
  width: 72px;
  color: var(--app-ink-2, var(--el-text-color-regular));
}
.arg-val {
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--app-ink, var(--el-text-color-primary));
}
.appr-result,
.appr-reason {
  margin-top: 8px;
  font-size: 13px;
  color: var(--app-ink-2, var(--el-text-color-regular));
  white-space: pre-wrap;
  word-break: break-all;
}
.appr-meta {
  margin-top: 8px;
  font-size: 12px;
  color: var(--app-ink-3, var(--el-text-color-secondary));
}
.appr-actions {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}
</style>
