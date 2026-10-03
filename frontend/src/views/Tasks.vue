<template>
  <div>
    <el-card>
      <el-tabs v-model="activeTab">
        <el-tab-pane label="任务列表" name="tasks">
          <div class="toolbar">
            <el-radio-group v-model="query.status" @change="handleSearch">
              <el-radio-button value="">全部</el-radio-button>
              <el-radio-button v-for="(v, k) in taskStatusMap" :key="k" :value="k">{{ v.label }}</el-radio-button>
            </el-radio-group>
            <el-button :icon="Download" style="margin-left: auto" @click="handleExport">导出 Excel</el-button>
            <el-button type="success" :icon="Plus" @click="openForm()">新增任务</el-button>
          </div>

          <el-table :data="list" v-loading="loading" stripe>
            <template #empty>
              <!-- 空态引导：无数据时给出一句提示 + 直达新建入口 -->
              <el-empty :image-size="80" description="暂无任务，创建任务后可在这里统一跟踪进度">
                <el-button type="primary" :icon="Plus" @click="openForm()">新增任务</el-button>
              </el-empty>
            </template>
            <el-table-column prop="title" label="标题" min-width="150" show-overflow-tooltip />
            <el-table-column label="关联客户" min-width="110" show-overflow-tooltip>
              <template #default="{ row }">{{ row.customer_name || '-' }}</template>
            </el-table-column>
            <el-table-column label="类型" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="enumTagType(taskTypeMap, row.type)">
                  {{ enumLabel(taskTypeMap, row.type) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="优先级" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="enumTagType(taskPriorityMap, row.priority)">
                  {{ enumLabel(taskPriorityMap, row.priority) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="截止时间" width="150">
              <template #default="{ row }">
                <span :class="{ 'due-soon': isDueSoon(row) }">{{ formatDateTime(row.due_date) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="enumTagType(taskStatusMap, row.status)">
                  {{ enumLabel(taskStatusMap, row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="来源" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="enumTagType(taskSourceMap, row.source)">
                  {{ enumLabel(taskSourceMap, row.source) }}
                </el-tag>
                <el-tag v-if="row.ai_generated" size="small" type="primary" effect="plain" style="margin-left: 4px">AI</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="160" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.status !== 'completed' && row.status !== 'cancelled'" link type="success" @click="handleComplete(row)">完成</el-button>
                <el-button link type="primary" @click="openForm(row)">编辑</el-button>
                <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>

          <el-pagination
            class="pager"
            v-model:current-page="query.page"
            v-model:page-size="query.page_size"
            :total="total"
            :page-sizes="[10, 20, 50]"
            layout="total, sizes, prev, pager, next"
            @size-change="onSizeChange"
            @current-change="loadList"
          />
        </el-tab-pane>

        <el-tab-pane label="提醒规则" name="rules">
          <div class="toolbar">
            <el-button type="warning" :icon="VideoPlay" :loading="runningRules" @click="handleRunRules">立即执行规则</el-button>
            <el-button type="success" :icon="Plus" style="margin-left: auto" @click="openRuleForm()">新增规则</el-button>
          </div>

          <el-table :data="rules" v-loading="rulesLoading" stripe>
            <template #empty>
              <!-- 空态引导：说明规则用途 + 直达新建入口 -->
              <el-empty :image-size="80" description="暂无提醒规则，创建后系统会按条件自动生成任务">
                <el-button type="warning" :icon="Plus" @click="openRuleForm()">新增规则</el-button>
              </el-empty>
            </template>
            <el-table-column prop="name" label="规则名称" min-width="140" show-overflow-tooltip />
            <el-table-column label="触发条件" min-width="150">
              <template #default="{ row }">
                <el-tag size="small" :type="enumTagType(triggerTypeMap, row.trigger_type)">
                  {{ enumLabel(triggerTypeMap, row.trigger_type) }}
                </el-tag>
                <span class="threshold">（{{ describeThreshold(row) }}）</span>
              </template>
            </el-table-column>
            <el-table-column label="任务模板" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.action_config?.template || '-' }}</template>
            </el-table-column>
            <el-table-column label="启用" width="80">
              <template #default="{ row }">
                <el-switch :model-value="row.enabled" @change="(val) => toggleRule(row, val)" />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openRuleForm(row)">编辑</el-button>
                <el-button link type="danger" @click="handleDeleteRule(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 新增 / 编辑任务 -->
    <el-dialog v-model="formDialog" :title="form.id ? '编辑任务' : '新增任务'" width="min(90vw, 560px)">
      <el-form :model="form" :rules="formRules" ref="formRef" label-width="90px">
        <el-form-item label="标题" prop="title">
          <el-input v-model="form.title" placeholder="任务标题" />
        </el-form-item>
        <el-form-item label="类型" prop="type">
          <el-select v-model="form.type" style="width: 100%">
            <el-option v-for="(v, k) in taskTypeMap" :key="k" :label="v.label" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级" prop="priority">
          <el-select v-model="form.priority" style="width: 100%">
            <el-option v-for="(v, k) in taskPriorityMap" :key="k" :label="v.label" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="关联客户" prop="customer_id">
          <el-select v-model="form.customer_id" style="width: 100%" clearable filterable placeholder="可选">
            <el-option v-for="c in customerOptions" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="截止时间" prop="due_date">
          <el-date-picker v-model="form.due_date" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" />
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input v-model="form.description" type="textarea" :rows="3" placeholder="任务描述" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 新增 / 编辑规则 -->
    <el-dialog v-model="ruleDialog" :title="ruleForm.id ? '编辑规则' : '新增规则'" width="min(90vw, 560px)">
      <el-form :model="ruleForm" :rules="ruleFormRules" ref="ruleFormRef" label-width="100px">
        <el-form-item label="规则名称" prop="name">
          <el-input v-model="ruleForm.name" placeholder="规则名称" />
        </el-form-item>
        <el-form-item label="触发类型" prop="trigger_type">
          <el-select v-model="ruleForm.trigger_type" style="width: 100%">
            <el-option v-for="(v, k) in triggerTypeMap" :key="k" :label="v.label" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="ruleForm.trigger_type === 'task_due_soon'" label="临期阈值(小时)">
          <el-input-number v-model="ruleForm.threshold_hours" :min="1" style="width: 100%" />
        </el-form-item>
        <el-form-item v-else label="阈值(天)">
          <el-input-number v-model="ruleForm.threshold_days" :min="1" style="width: 100%" />
        </el-form-item>
        <el-form-item label="任务模板">
          <el-input v-model="ruleForm.template" type="textarea" :rows="3" placeholder="生成任务的标题/内容模板" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="ruleForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ruleDialog = false">取消</el-button>
        <el-button type="primary" :loading="ruleSaving" @click="handleSaveRule">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, nextTick } from 'vue'
import { useThemeStore } from '../stores/theme'
import { Plus, VideoPlay, Download } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  getTasks, createTask, updateTask, completeTask, deleteTask,
  getReminderRules, createReminderRule, updateReminderRule, deleteReminderRule, runReminderRules,
  getCustomers,
} from '../api'
import {
  taskTypeMap, taskPriorityMap, taskStatusMap, taskSourceMap, triggerTypeMap,
  enumLabel, enumTagType, formatDateTime, parseServerDate,
} from '../utils/format'
import { exportRowsToExcel } from '../utils/tableExport'
import { usePagedFetch } from '../utils/usePagedFetch'
import { confirmDanger } from '../utils/confirmDanger'
import { validateForm } from '../utils/validateForm'
import { applyFieldErrors } from '../utils/applyFieldErrors'

// 服务器 UTC 时间 → 本地 naive 字符串（供 el-date-picker 显示）
function toLocalPickerValue(val) {
  const d = parseServerDate(val)
  if (!d) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// 本地 naive 选择时间 → 带时区 ISO（后端按 UTC 存）
function toServerIso(localStr) {
  if (!localStr) return null
  const d = new Date(localStr)
  return isNaN(d.getTime()) ? null : d.toISOString()
}

const activeTab = ref('tasks')

// ========== 任务列表 ==========
// 列表请求防竞态（序号法），loading 由 composable 管理
const { loading, run: runFetch } = usePagedFetch()
const list = ref([])

function handleExport() {
  const rows = list.value.map((r) => ({
    '标题': r.title,
    '关联客户': r.customer_name || '-',
    '类型': enumLabel(taskTypeMap, r.type),
    '优先级': enumLabel(taskPriorityMap, r.priority),
    '状态': enumLabel(taskStatusMap, r.status),
    '截止时间': r.due_date ? formatDateTime(r.due_date) : '',
    '创建时间': formatDateTime(r.created_at),
  }))
  exportRowsToExcel(rows, [], '任务清单')
}
const total = ref(0)
const themeStore = useThemeStore()
const query = reactive({ status: '', page: 1, page_size: themeStore.pageSize })

function onSizeChange() {
  query.page = 1
  loadList()
}

async function loadList() {
  await runFetch(
    () => {
      const params = { page: query.page, page_size: query.page_size }
      if (query.status) params.status = query.status
      return getTasks(params)
    },
    (res) => {
      list.value = res.items || []
      total.value = res.total || 0
    }
  )
}

function handleSearch() {
  query.page = 1
  loadList()
}

// 未完成且截止时间在 24 小时内（或已过期）标红
function isDueSoon(row) {
  if (!row.due_date || row.status === 'completed' || row.status === 'cancelled') return false
  const d = parseServerDate(row.due_date)
  if (!d) return false
  return d.getTime() - Date.now() < 24 * 3600 * 1000
}

// ========== 任务表单 ==========
const formDialog = ref(false)
const saving = ref(false)
const formRef = ref()
const emptyForm = { id: null, title: '', type: 'follow_up', priority: 'medium', customer_id: null, due_date: '', description: '' }
const form = reactive({ ...emptyForm })
const formRules = {
  title: [{ required: true, message: '请输入任务标题', trigger: 'blur' }],
  type: [{ required: true, message: '请选择类型', trigger: 'change' }],
  priority: [{ required: true, message: '请选择优先级', trigger: 'change' }],
}
const customerOptions = ref([])

async function loadCustomerOptions() {
  try {
    const res = await getCustomers({ page: 1, page_size: 200 })
    customerOptions.value = res.items || []
  } catch {
    /* 下拉加载失败不阻断 */
  }
}

function openForm(row) {
  Object.assign(form, emptyForm, row ? {
    id: row.id,
    title: row.title,
    type: row.type,
    priority: row.priority,
    customer_id: row.customer_id ?? null,
    due_date: row.due_date ? toLocalPickerValue(row.due_date) : '',
    description: row.description || '',
  } : {})
  formDialog.value = true
  nextTick(() => formRef.value?.clearValidate())
}

async function handleSave() {
  if (!(await validateForm(formRef.value))) return
  saving.value = true
  try {
    const data = {
      title: form.title,
      type: form.type,
      priority: form.priority,
      customer_id: form.customer_id || null,
      due_date: toServerIso(form.due_date),
      description: form.description || null,
    }
    try {
      if (form.id) {
        await updateTask(form.id, data)
        ElMessage.success('更新成功')
      } else {
        await createTask(data)
        ElMessage.success('创建成功')
      }
    } catch (e) {
      // 后端 422 字段级错误：就地标红对应表单项（拦截器另给一句总提示），不再继续
      if (applyFieldErrors(e, formRef.value)) return
      throw e
    }
    formDialog.value = false
    loadList()
  } finally {
    saving.value = false
  }
}

async function handleComplete(row) {
  if (!(await confirmDanger(`确定将任务「${row.title}」标记为已完成吗？`, '提示', { type: 'info' }))) return
  await completeTask(row.id)
  ElMessage.success('已完成')
  loadList()
}

async function handleDelete(row) {
  if (!(await confirmDanger(`确定删除任务「${row.title}」吗？`))) return
  await deleteTask(row.id)
  ElMessage.success('删除成功')
  loadList()
}

// ========== 提醒规则 ==========
const rules = ref([])
const rulesLoading = ref(false)
const runningRules = ref(false)

async function loadRules() {
  rulesLoading.value = true
  try {
    const res = await getReminderRules()
    rules.value = Array.isArray(res) ? res : (res?.items || [])
  } finally {
    rulesLoading.value = false
  }
}

function describeThreshold(row) {
  const cfg = row.trigger_config || {}
  if (row.trigger_type === 'task_due_soon') return `${cfg.threshold_hours ?? '-'} 小时内临期`
  return `${cfg.threshold_days ?? '-'} 天`
}

async function handleRunRules() {
  runningRules.value = true
  try {
    const res = await runReminderRules()
    const n = res?.tasks_created ?? res?.created_count ?? res?.created ?? res?.count ?? 0
    ElMessage.success(`执行完成，新建任务 ${n} 个`)
    loadList()
  } finally {
    runningRules.value = false
  }
}

// ========== 规则表单 ==========
const ruleDialog = ref(false)
const ruleSaving = ref(false)
const ruleFormRef = ref()
const emptyRuleForm = {
  id: null, name: '', trigger_type: 'days_since_last_followup',
  threshold_days: 7, threshold_hours: 24, template: '', enabled: true,
}
const ruleForm = reactive({ ...emptyRuleForm })
const ruleFormRules = {
  name: [{ required: true, message: '请输入规则名称', trigger: 'blur' }],
  trigger_type: [{ required: true, message: '请选择触发类型', trigger: 'change' }],
}

function openRuleForm(row) {
  Object.assign(ruleForm, emptyRuleForm, row ? {
    id: row.id,
    name: row.name,
    trigger_type: row.trigger_type,
    threshold_days: row.trigger_config?.threshold_days ?? 7,
    threshold_hours: row.trigger_config?.threshold_hours ?? 24,
    template: row.action_config?.template || '',
    enabled: row.enabled,
  } : {})
  ruleDialog.value = true
  nextTick(() => ruleFormRef.value?.clearValidate())
}

function buildRulePayload() {
  const trigger_config = ruleForm.trigger_type === 'task_due_soon'
    ? { threshold_hours: ruleForm.threshold_hours }
    : { threshold_days: ruleForm.threshold_days }
  return {
    name: ruleForm.name,
    trigger_type: ruleForm.trigger_type,
    trigger_config,
    action_config: { template: ruleForm.template },
    enabled: ruleForm.enabled,
  }
}

async function handleSaveRule() {
  if (!(await validateForm(ruleFormRef.value))) return
  ruleSaving.value = true
  try {
    if (ruleForm.id) {
      await updateReminderRule(ruleForm.id, buildRulePayload())
      ElMessage.success('更新成功')
    } else {
      await createReminderRule(buildRulePayload())
      ElMessage.success('创建成功')
    }
    ruleDialog.value = false
    loadRules()
  } finally {
    ruleSaving.value = false
  }
}

async function toggleRule(row, val) {
  await updateReminderRule(row.id, {
    name: row.name,
    trigger_type: row.trigger_type,
    trigger_config: row.trigger_config || {},
    action_config: row.action_config || {},
    enabled: val,
  })
  row.enabled = val
  ElMessage.success(val ? '已启用' : '已停用')
}

async function handleDeleteRule(row) {
  if (!(await confirmDanger(`确定删除规则「${row.name}」吗？`))) return
  await deleteReminderRule(row.id)
  ElMessage.success('删除成功')
  loadRules()
}

onMounted(() => {
  loadList()
  loadRules()
  loadCustomerOptions()
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
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
.due-soon {
  color: #f56c6c;
  font-weight: 600;
}
.threshold {
  font-size: 12px;
  color: #909399;
}
</style>
