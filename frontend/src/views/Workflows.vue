<template>
  <div>
    <div class="page-header">
      <h1 class="page-title-main">工作流</h1>
      <p class="page-sub">按触发方式自动执行动作：发邮件、建任务、发通知</p>
    </div>

    <div class="toolbar">
      <el-button :icon="Present" @click="openBirthdayTemplate">生日祝福模板</el-button>
      <el-button type="primary" :icon="Plus" style="margin-left: auto" @click="openForm()">新增工作流</el-button>
    </div>

    <div v-loading="loading">
      <el-row :gutter="16">
        <el-col v-for="w in list" :key="w.id" :xs="24" :sm="12" :lg="8">
          <el-card class="wf-card" shadow="never">
            <div class="wf-head">
              <span class="wf-name" :title="w.name">{{ w.name }}</span>
              <el-switch :model-value="w.enabled" @change="(val) => toggleWorkflow(w, val)" />
            </div>
            <div class="wf-desc">{{ w.description || '暂无描述' }}</div>
            <div class="wf-tags">
              <el-tag size="small" :type="enumTagType(workflowTriggerMap, w.trigger_type)">
                {{ enumLabel(workflowTriggerMap, w.trigger_type) }}<template v-if="triggerSummary(w)"> · {{ triggerSummary(w) }}</template>
              </el-tag>
              <el-tag size="small" effect="plain" :type="enumTagType(workflowActionMap, w.action_type)">
                {{ enumLabel(workflowActionMap, w.action_type) }}
              </el-tag>
            </div>
            <div class="wf-meta">
              <span>上次运行：{{ formatDateTime(w.last_run_at) }}</span>
            </div>
            <div class="wf-actions">
              <el-button link type="primary" size="small" @click="openForm(w)">编辑</el-button>
              <el-button link type="success" size="small" :loading="runningId === w.id" @click="handleRun(w)">立即运行</el-button>
              <el-button link size="small" @click="openRuns(w)">运行历史</el-button>
              <el-button link type="danger" size="small" @click="handleDelete(w)">删除</el-button>
            </div>
          </el-card>
        </el-col>
      </el-row>
      <el-empty v-if="!loading && !list.length" description="暂无工作流，点击右上角新增" />
    </div>

    <!-- 分步创建 / 编辑 -->
    <el-dialog v-model="formDialog" :title="form.id ? '编辑工作流' : '新增工作流'" :width="'min(90vw, 640px)'" :close-on-click-modal="false">
      <el-steps :active="step" align-center finish-status="success" class="wf-steps">
        <el-step title="基本信息" />
        <el-step title="触发方式" />
        <el-step title="触发条件" />
        <el-step title="动作配置" />
      </el-steps>

      <!-- ① 基本信息 -->
      <div v-show="step === 0" class="step-pane">
        <el-form :model="form" label-width="90px">
          <el-form-item label="名称" required>
            <el-input v-model="form.name" placeholder="工作流名称" />
          </el-form-item>
          <el-form-item label="描述">
            <el-input v-model="form.description" type="textarea" :rows="3" placeholder="这个工作流做什么" />
          </el-form-item>
          <el-form-item label="启用">
            <el-switch v-model="form.enabled" />
          </el-form-item>
        </el-form>
      </div>

      <!-- ② 触发方式 -->
      <div v-show="step === 1" class="step-pane">
        <el-form label-width="110px">
          <el-form-item label="触发类型" required>
            <el-select v-model="form.trigger_type" style="width: 100%">
              <el-option v-for="(v, k) in workflowTriggerMap" :key="k" :label="v.label" :value="k" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="form.trigger_type === 'interval'" label="间隔(分钟)" required>
            <el-input-number v-model="form.trigger.interval_minutes" :min="1" style="width: 100%" />
          </el-form-item>
          <el-form-item v-if="form.trigger_type === 'daily'" label="执行时间" required>
            <el-time-picker v-model="form.trigger.time" format="HH:mm" value-format="HH:mm" style="width: 100%" />
          </el-form-item>
          <template v-if="form.trigger_type === 'weekly'">
            <el-form-item label="星期" required>
              <el-select v-model="form.trigger.weekday" style="width: 100%">
                <el-option v-for="d in weekdays" :key="d.value" :label="d.label" :value="d.value" />
              </el-select>
            </el-form-item>
            <el-form-item label="执行时间" required>
              <el-time-picker v-model="form.trigger.time" format="HH:mm" value-format="HH:mm" style="width: 100%" />
            </el-form-item>
          </template>
          <el-alert
            v-if="form.trigger_type === 'birthday'"
            type="info"
            :closable="false"
            title="客户生日当天触发，对每位当天生日的客户执行一次动作。"
          />
          <el-alert
            v-if="form.trigger_type === 'condition'"
            type="info"
            :closable="false"
            title="按下一步配置的条件筛选客户，对匹配客户执行动作。"
          />
        </el-form>
      </div>

      <!-- ③ 触发条件 -->
      <div v-show="step === 2" class="step-pane">
        <template v-if="form.trigger_type === 'condition'">
          <div v-for="(c, i) in form.conditions" :key="c._key" class="cond-row">
            <el-select v-model="c.field" placeholder="字段" style="width: 130px">
              <el-option v-for="(label, f) in workflowFieldMap" :key="f" :label="label" :value="f" />
            </el-select>
            <el-select v-model="c.op" placeholder="比较" style="width: 110px">
              <el-option v-for="(label, o) in workflowOpMap" :key="o" :label="label" :value="o" />
            </el-select>
            <el-input v-model="c.value" placeholder="值" style="flex: 1" />
            <el-button link type="danger" :icon="Delete" @click="form.conditions.splice(i, 1)" />
          </div>
          <el-button :icon="Plus" size="small" @click="form.conditions.push({ _key: genKey(), field: 'industry', op: 'eq', value: '' })">
            添加条件
          </el-button>
        </template>
        <el-alert v-else type="info" :closable="false" title="当前触发方式无需配置条件，可直接进入下一步。" />
      </div>

      <!-- ④ 动作配置 -->
      <div v-show="step === 3" class="step-pane">
        <el-form label-width="100px">
          <el-form-item label="动作类型" required>
            <el-select v-model="form.action_type" style="width: 100%">
              <el-option v-for="(v, k) in workflowActionMap" :key="k" :label="v.label" :value="k" />
            </el-select>
          </el-form-item>

          <template v-if="form.action_type === 'send_email'">
            <el-form-item label="邮件主题" required>
              <el-input v-model="form.action.subject" placeholder="邮件主题" />
            </el-form-item>
            <el-form-item label="邮件正文" required>
              <el-input v-model="form.action.template" type="textarea" :rows="5" placeholder="正文内容" />
            </el-form-item>
            <el-alert type="info" :closable="false" title="支持占位符 {{customer_name}}，发送时替换为客户名称。" />
          </template>

          <template v-else-if="form.action_type === 'create_task'">
            <el-form-item label="任务标题" required>
              <el-input v-model="form.action.title" placeholder="生成的任务标题" />
            </el-form-item>
            <el-form-item label="任务类型">
              <el-select v-model="form.action.type" style="width: 100%">
                <el-option v-for="(v, k) in taskTypeMap" :key="k" :label="v.label" :value="k" />
              </el-select>
            </el-form-item>
            <el-form-item label="优先级">
              <el-select v-model="form.action.priority" style="width: 100%">
                <el-option v-for="(v, k) in taskPriorityMap" :key="k" :label="v.label" :value="k" />
              </el-select>
            </el-form-item>
            <el-form-item label="截止(天后)">
              <el-input-number v-model="form.action.due_in_days" :min="0" style="width: 100%" />
            </el-form-item>
          </template>

          <template v-else>
            <el-form-item label="通知标题" required>
              <el-input v-model="form.action.title" placeholder="通知标题" />
            </el-form-item>
            <el-form-item label="通知内容" required>
              <el-input v-model="form.action.template" type="textarea" :rows="4" placeholder="通知内容，支持 {{customer_name}} 占位符" />
            </el-form-item>
          </template>
        </el-form>
      </div>

      <template #footer>
        <el-button :disabled="step === 0" @click="step--">上一步</el-button>
        <el-button v-if="step < 3" type="primary" @click="nextStep">下一步</el-button>
        <el-button v-else type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 运行历史 -->
    <el-drawer v-model="runsDrawer" :title="`运行历史 - ${runsWorkflow?.name || ''}`" size="min(90vw, 560px)">
      <el-table :data="runs" v-loading="runsLoading" size="small">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="run-detail">{{ row.detail || '无详情' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="enumTagType(workflowRunStatusMap, row.status)">
              {{ enumLabel(workflowRunStatusMap, row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="matched_count" label="匹配数" width="80" />
      </el-table>
      <el-pagination
        class="pager"
        v-model:current-page="runsQuery.page"
        v-model:page-size="runsQuery.page_size"
        :total="runsTotal"
        layout="total, prev, pager, next"
        @current-change="loadRuns"
      />
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Plus, Delete, Present } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { confirmDanger } from '../utils/confirmDanger'
import {
  getWorkflows, createWorkflow, updateWorkflow, deleteWorkflow, runWorkflow, getWorkflowRuns,
} from '../api'
import {
  workflowTriggerMap, workflowActionMap, workflowFieldMap, workflowOpMap, workflowRunStatusMap,
  taskTypeMap, taskPriorityMap,
  enumLabel, enumTagType, formatDateTime,
} from '../utils/format'
import { useThemeStore } from '../stores/theme'

const weekdays = [
  { value: 1, label: '周一' }, { value: 2, label: '周二' }, { value: 3, label: '周三' },
  { value: 4, label: '周四' }, { value: 5, label: '周五' }, { value: 6, label: '周六' },
  { value: 7, label: '周日' },
]

// crypto.randomUUID 仅在安全上下文（HTTPS/localhost）可用，http 局域网 IP 访问时兜底为时间戳+随机串
function genKey() {
  return globalThis.crypto?.randomUUID?.() ?? `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

const loading = ref(false)
const list = ref([])
const runningId = ref(null)

async function loadList() {
  loading.value = true
  try {
    const res = await getWorkflows()
    list.value = Array.isArray(res) ? res : (res?.items || [])
  } finally {
    loading.value = false
  }
}

function triggerSummary(w) {
  const cfg = w.trigger_config || {}
  if (w.trigger_type === 'interval') return `每 ${cfg.interval_minutes ?? '-'} 分钟`
  if (w.trigger_type === 'daily') return cfg.time || ''
  if (w.trigger_type === 'weekly') {
    const d = weekdays.find((x) => x.value === cfg.weekday)
    return `${d?.label || ''} ${cfg.time || ''}`.trim()
  }
  return ''
}

// ========== 分步表单 ==========
const formDialog = ref(false)
const saving = ref(false)
const step = ref(0)
const emptyForm = () => ({
  id: null,
  name: '',
  description: '',
  enabled: true,
  trigger_type: 'daily',
  trigger: { interval_minutes: 60, time: '09:00', weekday: 1 },
  conditions: [],
  action_type: 'create_task',
  action: { subject: '', template: '', title: '', type: 'follow_up', priority: 'medium', due_in_days: 3 },
})
const form = reactive(emptyForm())

function resetForm(data) {
  Object.assign(form, emptyForm(), data || {})
  step.value = 0
}

function openForm(w) {
  if (w) {
    resetForm({
      id: w.id,
      name: w.name,
      description: w.description || '',
      enabled: w.enabled,
      trigger_type: w.trigger_type,
      trigger: {
        interval_minutes: w.trigger_config?.interval_minutes ?? 60,
        time: w.trigger_config?.time || '09:00',
        weekday: w.trigger_config?.weekday ?? 1,
      },
      conditions: (w.conditions || []).map((c) => ({ _key: genKey(), field: c.field, op: c.op, value: c.value })),
      action_type: w.action_type,
      action: {
        subject: w.action_config?.subject || '',
        template: w.action_config?.template || '',
        title: w.action_config?.title || '',
        type: w.action_config?.type || 'follow_up',
        priority: w.action_config?.priority || 'medium',
        due_in_days: w.action_config?.due_in_days ?? 3,
      },
    })
  } else {
    resetForm()
  }
  formDialog.value = true
}

// 生日祝福模板一键填充
function openBirthdayTemplate() {
  resetForm({
    name: '客户生日祝福邮件',
    description: '客户生日当天自动发送祝福邮件，维护客情关系。',
    enabled: true,
    trigger_type: 'birthday',
    action_type: 'send_email',
    action: {
      subject: '生日快乐，{{customer_name}}！',
      template: '尊敬的 {{customer_name}}：\n\n祝您生日快乐！感谢一直以来的信任与支持，期待继续携手同行。\n\n—— 您的专属服务团队',
      title: '', type: 'follow_up', priority: 'medium', due_in_days: 3,
    },
  })
  formDialog.value = true
}

function nextStep() {
  if (step.value === 0 && !form.name.trim()) {
    ElMessage.warning('请填写工作流名称')
    return
  }
  step.value = Math.min(3, step.value + 1)
}

function buildPayload() {
  let trigger_config = {}
  if (form.trigger_type === 'interval') trigger_config = { interval_minutes: form.trigger.interval_minutes }
  else if (form.trigger_type === 'daily') trigger_config = { time: form.trigger.time }
  else if (form.trigger_type === 'weekly') trigger_config = { weekday: form.trigger.weekday, time: form.trigger.time }

  let action_config = {}
  if (form.action_type === 'send_email') {
    action_config = { subject: form.action.subject, template: form.action.template }
  } else if (form.action_type === 'create_task') {
    action_config = {
      title: form.action.title,
      type: form.action.type,
      priority: form.action.priority,
      due_in_days: form.action.due_in_days,
    }
  } else {
    action_config = { title: form.action.title, template: form.action.template }
  }

  return {
    name: form.name,
    description: form.description || null,
    trigger_type: form.trigger_type,
    trigger_config,
    conditions: form.trigger_type === 'condition' ? form.conditions.map(({ _key, ...c }) => c) : [],
    action_type: form.action_type,
    action_config,
    enabled: form.enabled,
  }
}

async function handleSave() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写工作流名称')
    return
  }
  saving.value = true
  try {
    if (form.id) {
      await updateWorkflow(form.id, buildPayload())
      ElMessage.success('更新成功')
    } else {
      await createWorkflow(buildPayload())
      ElMessage.success('创建成功')
    }
    formDialog.value = false
    loadList()
  } finally {
    saving.value = false
  }
}

async function toggleWorkflow(w, val) {
  await updateWorkflow(w.id, {
    name: w.name,
    description: w.description,
    trigger_type: w.trigger_type,
    trigger_config: w.trigger_config || {},
    conditions: w.conditions || [],
    action_type: w.action_type,
    action_config: w.action_config || {},
    enabled: val,
  })
  w.enabled = val
  ElMessage.success(val ? '已启用' : '已停用')
}

async function handleRun(w) {
  runningId.value = w.id
  try {
    const res = await runWorkflow(w.id)
    ElMessage.success(`运行完成，匹配 ${res?.matched_count ?? 0} 个对象`)
    loadList()
  } finally {
    runningId.value = null
  }
}

async function handleDelete(w) {
  const ok = await confirmDanger(`确定删除工作流「${w.name}」吗？`)
  if (!ok) return
  await deleteWorkflow(w.id)
  ElMessage.success('删除成功')
  loadList()
}

// ========== 运行历史 ==========
const runsDrawer = ref(false)
const runsLoading = ref(false)
const runs = ref([])
const runsTotal = ref(0)
const runsWorkflow = ref(null)
const themeStore = useThemeStore()
const runsQuery = reactive({ page: 1, page_size: themeStore.pageSize })

function openRuns(w) {
  runsWorkflow.value = w
  runsQuery.page = 1
  runsDrawer.value = true
  loadRuns()
}

async function loadRuns() {
  runsLoading.value = true
  try {
    const res = await getWorkflowRuns(runsWorkflow.value.id, { page: runsQuery.page, page_size: runsQuery.page_size })
    runs.value = res.items || []
    runsTotal.value = res.total || 0
  } finally {
    runsLoading.value = false
  }
}

onMounted(loadList)
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
  align-items: center;
}
.wf-card {
  margin-bottom: 16px;
  transition: box-shadow 0.2s ease, transform 0.2s ease;
}
.wf-card:hover {
  box-shadow: var(--app-shadow-hover);
  transform: translateY(-2px);
}
.wf-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.wf-name {
  font-weight: 600;
  font-size: 15px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.wf-desc {
  color: var(--app-ink-2);
  font-size: 13px;
  margin-top: 8px;
  min-height: 20px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.wf-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.wf-meta {
  margin-top: 12px;
  font-size: 12px;
  color: var(--app-ink-2);
}
.wf-actions {
  margin-top: 10px;
  border-top: 1px solid var(--app-line);
  padding-top: 8px;
}
.wf-steps {
  margin-bottom: 22px;
}
.step-pane {
  min-height: 260px;
}
.cond-row {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
  align-items: center;
}
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
.run-detail {
  padding: 8px 16px;
  color: var(--app-ink-2);
  white-space: pre-wrap;
  font-size: 13px;
}
</style>
