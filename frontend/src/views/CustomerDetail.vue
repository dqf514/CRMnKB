<template>
  <div class="detail-page" v-loading="loading">
    <!-- ========== 简历式客户头部 ========== -->
    <div v-if="customer" class="head-card">
      <div class="head-main">
        <div class="head-left">
          <el-tooltip content="返回列表" placement="bottom">
            <button class="back-btn" @click="router.push('/customers')">
              <el-icon><ArrowLeft /></el-icon>
            </button>
          </el-tooltip>
          <!-- 上一个/下一个：仅当从列表带浏览序列进入时显示（直接 URL 进入无序列则隐藏） -->
          <div v-if="hasNav" class="nav-switch">
            <el-tooltip content="上一个" placement="bottom">
              <button class="back-btn" :disabled="navIdx <= 0" @click="goNav(-1)">
                <el-icon><ArrowLeft /></el-icon>
              </button>
            </el-tooltip>
            <span class="nav-pos">{{ navIdx + 1 }} / {{ navIds.length }}</span>
            <el-tooltip content="下一个" placement="bottom">
              <button class="back-btn" :disabled="navIdx >= navIds.length - 1" @click="goNav(1)">
                <el-icon><ArrowRight /></el-icon>
              </button>
            </el-tooltip>
          </div>
        </div>
        <div class="avatar">{{ customer.name?.slice(0, 1) }}</div>
        <div class="head-id">
          <div class="name-row">
            <h1 class="cname">{{ customer.name }}</h1>
            <el-tooltip v-if="customer.is_private" content="私有客户：仅负责人与被授权成员可见" placement="top">
              <el-tag size="small" type="warning" effect="dark" class="private-tag">私有</el-tag>
            </el-tooltip>
            <el-select
              :model-value="customer.status"
              size="small"
              class="mini-select"
              :loading="statusSaving"
              :disabled="!canEdit"
              @change="handleStatusChange"
            >
              <el-option v-for="(v, k) in customerStatusMap" :key="k" :label="v.label" :value="k" />
            </el-select>
            <el-select
              :model-value="customer.ddq_status || 'none'"
              size="small"
              class="mini-select"
              :loading="ddqSaving"
              :disabled="!canEdit"
              @change="handleDdqChange"
            >
              <el-option v-for="(v, k) in ddqStatusMap" :key="k" :label="'DDQ·' + v.label" :value="k" />
            </el-select>
          </div>
          <div class="subtitle">
            {{ customer.company || '—' }}<template v-if="customer.position"> · {{ customer.position }}</template>
          </div>
          <div v-if="customer.industries?.length || customer.tags?.length" class="chips">
            <el-tag v-for="t in customer.industries" :key="'i' + t" size="small" type="info" effect="plain">{{ t }}</el-tag>
            <el-tag v-for="t in customer.tags" :key="'t' + t" size="small" effect="plain">{{ t }}</el-tag>
          </div>
        </div>
        <div class="head-actions">
          <el-button v-if="canManage" size="small" :icon="Share" @click="shareVisible = true">分享</el-button>
          <el-button v-if="canEdit" size="small" :icon="Edit" @click="editVisible = true">编辑</el-button>
          <el-button size="small" :icon="Message" @click="openEmailDialog">邮件草稿</el-button>
          <el-button v-if="canManage" size="small" type="danger" plain :icon="Delete" @click="handleDelete">删除</el-button>
        </div>
      </div>
      <div class="meta-row">
        <span class="meta"><el-icon><Phone /></el-icon>{{ customer.phone || '—' }}</span>
        <span class="meta"><el-icon><ChatDotRound /></el-icon>{{ customer.wechat || '—' }}</span>
        <span class="meta"><el-icon><Message /></el-icon>{{ customer.email || '—' }}</span>
        <span class="meta"><el-icon><Location /></el-icon>{{ customer.address || '—' }}</span>
        <span class="meta"><el-icon><Link /></el-icon>来源 {{ customer.source || '—' }}</span>
        <span class="meta"><el-icon><Present /></el-icon>{{ formatDate(customer.birthday) }}</span>
        <span class="meta"><el-icon><Clock /></el-icon>创建于 {{ formatDateTime(customer.created_at) }}</span>
      </div>
    </div>

    <!-- ========== 档案宫格：所有内容一页展示，面板内部滚动 ========== -->
    <div v-if="customer" class="grid">
      <!-- AI 简报：后台生成，刷新后轮询详情直到 ai_brief_at 变化 -->
      <section class="panel">
        <header class="panel-head">
          <span class="panel-title">AI 简报</span>
          <span class="panel-tools">
            <span v-if="customer.ai_brief_at" class="panel-note">{{ formatDateTime(customer.ai_brief_at) }}</span>
            <el-button size="small" text :icon="Refresh" :loading="briefRefreshing" @click="handleRefreshBrief">
              {{ customer.ai_brief ? '刷新' : '生成' }}
            </el-button>
          </span>
        </header>
        <div class="panel-body">
          <div v-if="customer.ai_brief" class="markdown-body compact-md" v-html="renderMarkdown(customer.ai_brief)"></div>
          <div v-else class="empty-line">还没有简报，点右上角「生成」，AI 将基于跟进与文档汇总客户近况。</div>
        </div>
      </section>

      <!-- 跟进记录 -->
      <section class="panel">
        <header class="panel-head">
          <span class="panel-title">跟进记录<b class="count">{{ followups.length }}</b></span>
          <el-button size="small" type="primary" plain :icon="Plus" @click="openFollowupDialog">新增跟进</el-button>
        </header>
        <div class="panel-body">
          <el-timeline v-if="followups.length" class="timeline">
            <el-timeline-item
              v-for="f in followups"
              :key="f.id"
              :timestamp="formatDateTime(f.created_at)"
              placement="top"
            >
              <el-tag size="small" :type="enumTagType(followupTypeMap, f.type)" style="margin-right: 8px">
                {{ enumLabel(followupTypeMap, f.type) }}
              </el-tag>
              <div class="followup-content">{{ f.content }}</div>
              <div v-if="f.next_step" class="next-step">下一步：{{ f.next_step }}</div>
              <div v-if="f.ai_summary" class="ai-summary">AI 摘要：{{ f.ai_summary }}</div>
            </el-timeline-item>
          </el-timeline>
          <div v-else class="empty-line">暂无跟进记录，点右上角「新增跟进」记下第一次接触。</div>
        </div>
      </section>

      <!-- 商机 + 待办 -->
      <div class="panel-stack">
        <section class="panel">
          <header class="panel-head">
            <span class="panel-title">商机<b class="count">{{ opportunities.length }}</b></span>
            <el-button size="small" type="primary" plain :icon="Plus" @click="openOppDialog">新增</el-button>
          </header>
          <div class="panel-body">
            <div v-for="row in opportunities" :key="row.id" class="opp-row">
              <div class="opp-main">
                <span class="opp-name" :title="row.name">{{ row.name }}</span>
                <el-button link type="danger" size="small" @click="handleDeleteOpp(row)">删除</el-button>
              </div>
              <div class="opp-sub">
                <span class="opp-amount">¥{{ formatMoney(row.amount) }}</span>
                <el-tag size="small" :type="enumTagType(opportunityStageMap, row.stage)">
                  {{ enumLabel(opportunityStageMap, row.stage) }}
                </el-tag>
                <span class="opp-meta">{{ row.probability ?? '-' }}%</span>
                <span class="opp-meta">{{ formatDate(row.expected_close_date) }}</span>
              </div>
            </div>
            <div v-if="!opportunities.length" class="empty-line">暂无商机</div>
          </div>
        </section>

        <section class="panel">
          <header class="panel-head">
            <span class="panel-title">待办<b class="count">{{ customerTasks.length }}</b></span>
            <el-button size="small" type="primary" plain :icon="Plus" @click="openTaskDialog">新增</el-button>
          </header>
          <div class="panel-body" v-loading="tasksLoading">
            <div v-for="row in customerTasks" :key="row.id" class="task-row">
              <el-checkbox
                :model-value="row.status === 'completed'"
                :disabled="row.status === 'completed' || row.status === 'cancelled'"
                @change="handleCompleteTask(row)"
              />
              <span class="task-title" :class="{ done: row.status === 'completed' }" :title="row.title">{{ row.title }}</span>
              <el-tag size="small" :type="enumTagType(taskPriorityMap, row.priority)" effect="plain">
                {{ enumLabel(taskPriorityMap, row.priority) }}
              </el-tag>
              <span class="task-due">{{ formatDate(row.due_date) }}</span>
              <el-tag v-if="row.ai_generated" size="small" type="primary" effect="plain">AI</el-tag>
            </div>
            <div v-if="!tasksLoading && !customerTasks.length" class="empty-line">暂无待办任务</div>
          </div>
        </section>
      </div>

      <!-- 客户文档 + 客户画像 -->
      <div class="panel-stack">
        <section class="panel">
          <header class="panel-head">
            <span class="panel-title">客户文档<b class="count">{{ filesTotal }}</b></span>
            <el-button size="small" type="primary" plain :icon="Upload" @click="docUploadDialog = true">上传</el-button>
          </header>
          <div class="panel-body" v-loading="filesLoading">
            <div v-for="row in customerFiles" :key="row.id" class="doc-row">
              <el-link
                type="primary"
                :underline="false"
                class="doc-name"
                :title="`${row.file_name} · ${formatDateTime(row.created_at)} · ${row.supported ? '可解析' : '仅存储'}`"
                @click="openPreview(row)"
              >{{ row.file_name }}</el-link>
              <el-tag v-if="row.category" size="small" :type="docCategoryStore.tagType(row.category)" effect="plain">
                {{ docCategoryStore.labelOf(row.category) }}
              </el-tag>
              <span class="doc-size">{{ formatFileSize(row.file_size) }}</span>
              <el-button link type="success" size="small" @click="openAssoc(row)">关联</el-button>
            </div>
            <div v-if="!filesLoading && !customerFiles.length" class="empty-line">
              暂无文档。上传后图片与音视频将由 AI 自动识别转写入库。
            </div>
          </div>
          <footer v-if="filesTotal > filesQuery.page_size" class="panel-foot">
            <el-pagination
              v-model:current-page="filesQuery.page"
              :page-size="filesQuery.page_size"
              :total="filesTotal"
              layout="prev, pager, next"
              small
              @current-change="loadCustomerFiles"
            />
          </footer>
        </section>

        <section class="panel">
          <header class="panel-head">
            <span class="panel-title">客户画像</span>
            <span class="panel-tools">
              <span v-if="profile.status === 'ready' && profile.updated_at" class="panel-note">{{ formatDateTime(profile.updated_at) }}</span>
              <el-button
                v-if="profile.status === 'ready' || profile.status === 'failed'"
                size="small"
                text
                :icon="Refresh"
                :loading="generating"
                @click="handleGenerateProfile"
              >重新生成</el-button>
            </span>
          </header>
          <div class="panel-body" v-loading="profileLoading">
            <div v-if="profile.status === 'idle'" class="empty-line">
              还没有画像。
              <el-button size="small" type="primary" plain :loading="generating" @click="handleGenerateProfile">生成画像</el-button>
            </div>
            <div v-else-if="profile.status === 'generating'" class="empty-line">
              <el-icon class="is-loading" :size="14"><Loading /></el-icon>
              AI 正在基于客户资料、跟进记录与文档生成画像…
            </div>
            <el-alert
              v-else-if="profile.status === 'failed'"
              type="error"
              :closable="false"
              title="画像生成失败"
              :description="profile.error || '可能是 AI 服务未配置或暂不可用，其他功能不受影响。'"
            />
            <div v-else-if="profile.status === 'ready'" class="markdown-body compact-md" v-html="renderMarkdown(profile.profile)"></div>
          </div>
        </section>
      </div>
    </div>

    <!-- 编辑客户抽屉（与列表页新增共用组件） -->
    <CustomerFormDrawer v-model="editVisible" :customer="customer" @saved="loadAll" />

    <!-- 新增跟进 -->
    <el-dialog v-model="followupDialog" title="新增跟进" width="min(90vw, 480px)">
      <el-form :model="followupForm" :rules="followupRules" ref="followupFormRef" label-width="80px">
        <el-form-item label="类型" prop="type">
          <el-select v-model="followupForm.type" style="width: 100%">
            <el-option v-for="(v, k) in followupTypeMap" :key="k" :label="v.label" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="内容" prop="content">
          <el-input v-model="followupForm.content" type="textarea" :rows="4" placeholder="记录本次跟进内容" />
        </el-form-item>
        <el-form-item label="下一步">
          <el-input v-model="followupForm.next_step" placeholder="下一步计划（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="followupDialog = false">取消</el-button>
        <el-button type="primary" :loading="followupSaving" @click="handleSaveFollowup">提交</el-button>
      </template>
    </el-dialog>

    <!-- 新增商机 -->
    <el-dialog v-model="oppDialog" title="新增商机" width="min(90vw, 480px)">
      <el-form :model="oppForm" :rules="oppRules" ref="oppFormRef" label-width="100px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="oppForm.name" placeholder="商机名称" />
        </el-form-item>
        <el-form-item label="金额" prop="amount">
          <el-input-number v-model="oppForm.amount" :min="0" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="阶段" prop="stage">
          <el-select v-model="oppForm.stage" style="width: 100%">
            <el-option v-for="(v, k) in opportunityStageMap" :key="k" :label="v.label" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="预计成交日期">
          <el-date-picker v-model="oppForm.expected_close_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="赢单概率(%)">
          <el-slider v-model="oppForm.probability" :max="100" show-input />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="oppDialog = false">取消</el-button>
        <el-button type="primary" :loading="oppSaving" @click="handleSaveOpp">提交</el-button>
      </template>
    </el-dialog>

    <!-- 客户文档上传 -->
    <el-dialog v-model="docUploadDialog" title="上传客户文档" width="min(90vw, 520px)" :close-on-click-modal="false">
      <el-upload
        ref="docUploadRef"
        :auto-upload="false"
        multiple
        drag
        :accept="acceptStr"
        :on-change="handleDocFileChange"
        :on-remove="(f) => (docFiles = docFiles.filter((x) => x !== f.raw))"
      >
        <el-icon size="40" style="color: var(--app-ink-2)"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽文件到此处，或 <em>点击选择</em>（可多选）</div>
      </el-upload>
      <div style="margin-top: 10px; display: flex; align-items: center; gap: 8px">
        <span style="font-size: 13px; color: var(--app-ink-2)">资料类型</span>
        <el-select v-model="docCategory" style="width: 160px" size="small">
          <el-option v-for="c in docCategoryStore.list" :key="c.value" :label="c.label" :value="c.value" />
        </el-select>
      </div>
      <p class="upload-tip">仅支持当前启用解析的格式；上传后自动归档到该客户并关联其专属知识库。</p>
      <template #footer>
        <el-button @click="docUploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="docUploading" :disabled="!docFiles.length" @click="handleDocUpload">上传</el-button>
      </template>
    </el-dialog>

    <!-- 关联知识库 -->
    <el-dialog v-model="assocDialog" title="关联知识库" width="min(90vw, 480px)">
      <p class="upload-tip">将「{{ assocFile?.file_name }}」追加关联到：</p>
      <el-select v-model="assocKbs" multiple placeholder="选择知识库" style="width: 100%">
        <el-option v-for="k in kbs" :key="k.id" :label="k.name" :value="k.id" />
      </el-select>
      <template #footer>
        <el-button @click="assocDialog = false">取消</el-button>
        <el-button type="primary" :loading="associating" :disabled="!assocKbs.length" @click="handleAssoc">关联</el-button>
      </template>
    </el-dialog>

    <!-- 新增待办任务（自动关联当前客户） -->
    <el-dialog v-model="taskDialog" title="新增任务" width="min(90vw, 480px)">
      <el-form :model="taskForm" :rules="taskRules" ref="taskFormRef" label-width="80px">
        <el-form-item label="标题" prop="title">
          <el-input v-model="taskForm.title" placeholder="任务标题" />
        </el-form-item>
        <el-form-item label="截止日期">
          <el-date-picker v-model="taskForm.due_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="taskForm.priority" style="width: 100%">
            <el-option v-for="(v, k) in taskPriorityMap" :key="k" :label="v.label" :value="k" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="taskDialog = false">取消</el-button>
        <el-button type="primary" :loading="taskSaving" @click="handleSaveTask">提交</el-button>
      </template>
    </el-dialog>

    <!-- AI 邮件草稿 -->
    <el-dialog v-model="emailDialog" title="AI 邮件草稿" width="min(92vw, 640px)">
      <el-form label-width="80px">
        <el-form-item label="意图">
          <el-input
            v-model="emailIntent"
            type="textarea"
            :rows="3"
            placeholder="介绍我们的新产品并约下周会议"
          />
        </el-form-item>
        <el-form-item label="语言">
          <el-radio-group v-model="emailLanguage">
            <el-radio value="zh">中文</el-radio>
            <el-radio value="en">English</el-radio>
            <el-radio value="zh_en">中英双语</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template v-if="emailDraftData">
        <el-form label-width="80px">
          <el-form-item label="主题">
            <el-input v-model="emailDraftData.subject" />
          </el-form-item>
          <el-form-item label="正文">
            <el-input v-model="emailDraftData.body" type="textarea" :rows="10" />
          </el-form-item>
        </el-form>
        <div class="email-actions">
          <el-button size="small" @click="copyEmail('subject')">复制主题</el-button>
          <el-button size="small" @click="copyEmail('body')">复制正文</el-button>
        </div>
      </template>
      <p class="upload-tip">AI 草稿仅供编辑参考，请自行检查后发送</p>
      <template #footer>
        <el-button @click="emailDialog = false">关闭</el-button>
        <el-button type="primary" :loading="emailGenerating" :disabled="!emailIntent.trim()" @click="handleEmailDraft">
          生成草稿
        </el-button>
      </template>
    </el-dialog>

    <!-- 文件预览 -->
    <FilePreview v-model="previewVisible" :file="previewFile" />

    <!-- 客户分享/私有控制（复用通用分享弹窗，resourceType=customer） -->
    <ShareDialog
      v-model="shareVisible"
      resource-type="customer"
      :resource="customer ? { ...customer, perm: customer.my_perm } : null"
      @changed="loadAll"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowLeft, ArrowRight, Plus, Upload, UploadFilled, Loading, Refresh, Message, Edit, Delete,
  Phone, ChatDotRound, Location, Link, Present, Clock, Share,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { confirmDanger } from '../utils/confirmDanger'
import { validateForm } from '../utils/validateForm'
import {
  getCustomer, updateCustomer, deleteCustomer, getFollowups, createFollowup,
  getOpportunities, createOpportunity, deleteOpportunity,
  getLibraryFiles, associateLibraryFiles, getKbs,
  getCustomerProfile, generateCustomerProfile,
  refreshCustomerBrief, emailDraft,
  getTasks, createTask, completeTask,
} from '../api'
import { uploadLibraryFiles, uploadSummary } from '../api/libraryUpload'
import { renderMarkdown } from '../utils/markdown'
import { ensureUploadFormats, isEnabledExt, enabledAcceptStr } from '../utils/uploadFormats'
import { useThemeStore } from '../stores/theme'
import FilePreview from '../components/FilePreview.vue'
import CustomerFormDrawer from '../components/CustomerFormDrawer.vue'
import ShareDialog from '../components/ShareDialog.vue'
import {
  customerStatusMap, followupTypeMap, opportunityStageMap,
  ddqStatusMap, taskPriorityMap,
  enumLabel, enumTagType, formatDate, formatDateTime, formatFileSize, formatMoney,
} from '../utils/format'
import { useDocCategoryStore } from '../stores/docCategories'
import { loadCustomerNav } from '../utils/customerNav'

// 资料类型列表（后端配置，管理端可增删改）
const docCategoryStore = useDocCategoryStore()

const route = useRoute()
const router = useRouter()
// 用 let 以便路由 id 变化时更新（同一路由不同 id 跳转时组件被复用）
let customerId = route.params.id

// 列表带来的浏览序列（sessionStorage）：上一个/下一个按序列 push 切换，
// 路由 id watch 会自动重新加载；直接 URL 进入无序列时隐藏切换按钮
const navIds = ref(loadCustomerNav()?.ids || [])
const navIdx = computed(() => navIds.value.findIndex((i) => String(i) === String(route.params.id)))
const hasNav = computed(() => navIds.value.length > 1 && navIdx.value >= 0)
function goNav(delta) {
  const target = navIds.value[navIdx.value + delta]
  if (target != null) router.push(`/customers/${target}`)
}

const loading = ref(false)
const customer = ref(null)
// 权限：my_perm 由后端下发（read/edit/owner；admin 恒为 owner）
const canEdit = computed(() => ['edit', 'owner'].includes(customer.value?.my_perm))
const canManage = computed(() => customer.value?.my_perm === 'owner')
const shareVisible = ref(false)
const followups = ref([])
const opportunities = ref([])
const editVisible = ref(false)

async function handleDelete() {
  const ok = await confirmDanger(`确定删除客户「${customer.value?.name}」吗？`)
  if (!ok) return
  await deleteCustomer(customerId)
  ElMessage.success('删除成功')
  router.push('/customers')
}

const followupDialog = ref(false)
const followupSaving = ref(false)
const followupFormRef = ref()
const followupForm = reactive({ type: 'call', content: '', next_step: '' })
const followupRules = {
  type: [{ required: true, message: '请选择类型', trigger: 'change' }],
  content: [{ required: true, message: '请输入跟进内容', trigger: 'blur' }],
}

const oppDialog = ref(false)
const oppSaving = ref(false)
const oppFormRef = ref()
const oppForm = reactive({ name: '', amount: 0, stage: 'prospecting', expected_close_date: '', probability: 50 })

function openFollowupDialog() {
  followupDialog.value = true
  nextTick(() => followupFormRef.value?.clearValidate())
}

function openOppDialog() {
  oppDialog.value = true
  nextTick(() => oppFormRef.value?.clearValidate())
}
const oppRules = {
  name: [{ required: true, message: '请输入商机名称', trigger: 'blur' }],
  stage: [{ required: true, message: '请选择阶段', trigger: 'change' }],
  // 金额不允许负数（el-input-number :min 只在步进/失焦时钳制，这里兜底校验）
  amount: [
    {
      validator: (r, v, cb) => (v == null || v >= 0 ? cb() : cb(new Error('金额不能为负数'))),
      trigger: 'blur',
    },
  ],
}

async function loadAll() {
  loading.value = true
  try {
    const [c, f, o] = await Promise.all([
      getCustomer(customerId),
      getFollowups(customerId),
      getOpportunities({ customer_id: customerId }),
    ])
    customer.value = c
    followups.value = Array.isArray(f) ? f : (f?.items || [])
    // 兼容后端返回数组或 {items,total} 两种结构
    opportunities.value = Array.isArray(o) ? o : (o?.items || [])
  } finally {
    loading.value = false
  }
}

async function handleSaveFollowup() {
  if (!(await validateForm(followupFormRef.value))) return
  followupSaving.value = true
  try {
    await createFollowup(customerId, {
      type: followupForm.type,
      content: followupForm.content,
      next_step: followupForm.next_step.trim() || null,
    })
    ElMessage.success('提交成功，AI 摘要生成中，稍后刷新查看')
    followupDialog.value = false
    followupForm.type = 'call'
    followupForm.content = ''
    followupForm.next_step = ''
    const f = await getFollowups(customerId)
    followups.value = Array.isArray(f) ? f : (f?.items || [])
  } finally {
    followupSaving.value = false
  }
}

async function handleSaveOpp() {
  if (!(await validateForm(oppFormRef.value))) return
  oppSaving.value = true
  try {
    await createOpportunity({
      customer_id: Number(customerId),
      name: oppForm.name,
      amount: oppForm.amount,
      stage: oppForm.stage,
      expected_close_date: oppForm.expected_close_date || null,
      probability: oppForm.probability,
    })
    ElMessage.success('创建成功')
    oppDialog.value = false
    Object.assign(oppForm, { name: '', amount: 0, stage: 'prospecting', expected_close_date: '', probability: 50 })
    const o = await getOpportunities({ customer_id: customerId })
    opportunities.value = Array.isArray(o) ? o : (o?.items || [])
  } finally {
    oppSaving.value = false
  }
}

async function handleDeleteOpp(row) {
  const ok = await confirmDanger(`确定删除商机「${row.name}」吗？`)
  if (!ok) return
  await deleteOpportunity(row.id)
  ElMessage.success('删除成功')
  const o = await getOpportunities({ customer_id: customerId })
  opportunities.value = Array.isArray(o) ? o : (o?.items || [])
}

// ========== 阶段 / DDQ 快捷切换（直接调 PUT 更新客户） ==========
const statusSaving = ref(false)
const ddqSaving = ref(false)

async function handleStatusChange(val) {
  statusSaving.value = true
  try {
    await updateCustomer(customerId, { status: val })
    customer.value.status = val
    ElMessage.success('阶段已更新')
  } finally {
    statusSaving.value = false
  }
}

async function handleDdqChange(val) {
  ddqSaving.value = true
  try {
    await updateCustomer(customerId, { ddq_status: val })
    customer.value.ddq_status = val
    ElMessage.success('DDQ 状态已更新')
  } finally {
    ddqSaving.value = false
  }
}

// ========== AI 简报（后台生成，每 3 秒轮询详情直到 ai_brief_at 变化，最多 60 秒） ==========
const briefRefreshing = ref(false)
let briefTimer = null

function clearBriefPoll() {
  if (briefTimer) {
    clearTimeout(briefTimer)
    briefTimer = null
  }
}

async function handleRefreshBrief() {
  if (briefRefreshing.value) return
  briefRefreshing.value = true
  const prevAt = customer.value?.ai_brief_at || null
  try {
    await refreshCustomerBrief(customerId)
    ElMessage.success('已提交生成，正在等待 AI 简报…')
    pollBrief(prevAt, Date.now())
  } catch {
    briefRefreshing.value = false
  }
}

function pollBrief(prevAt, startTs) {
  clearBriefPoll()
  if (Date.now() - startTs > 60000) {
    briefRefreshing.value = false
    ElMessage.warning('生成时间较长，请稍后手动刷新查看')
    return
  }
  briefTimer = setTimeout(async () => {
    try {
      const c = await getCustomer(customerId)
      if ((c?.ai_brief_at || null) !== prevAt) {
        customer.value = c
        briefRefreshing.value = false
        ElMessage.success('AI 简报已更新')
        return
      }
    } catch {
      /* 单次轮询失败继续等待 */
    }
    pollBrief(prevAt, startTs)
  }, 3000)
}

// ========== 待办（GET /tasks?customer_id=） ==========
const tasksLoading = ref(false)
const customerTasks = ref([])
const taskDialog = ref(false)
const taskSaving = ref(false)
const taskFormRef = ref()
const taskForm = reactive({ title: '', due_date: '', priority: 'medium' })
const taskRules = {
  title: [{ required: true, message: '请输入任务标题', trigger: 'blur' }],
}

async function loadCustomerTasks() {
  tasksLoading.value = true
  try {
    const res = await getTasks({ customer_id: customerId, page: 1, page_size: 100 })
    customerTasks.value = res.items || []
  } finally {
    tasksLoading.value = false
  }
}

async function handleCompleteTask(row) {
  try {
    await completeTask(row.id)
    ElMessage.success('已完成')
    loadCustomerTasks()
  } catch {
    /* 拦截器已提示 */
  }
}

function openTaskDialog() {
  taskDialog.value = true
  nextTick(() => taskFormRef.value?.clearValidate())
}

async function handleSaveTask() {
  if (!(await validateForm(taskFormRef.value))) return
  taskSaving.value = true
  try {
    await createTask({
      title: taskForm.title,
      type: 'follow_up',
      priority: taskForm.priority,
      customer_id: Number(customerId),
      due_date: taskForm.due_date ? new Date(taskForm.due_date).toISOString() : null,
    })
    ElMessage.success('创建成功')
    taskDialog.value = false
    Object.assign(taskForm, { title: '', due_date: '', priority: 'medium' })
    loadCustomerTasks()
  } finally {
    taskSaving.value = false
  }
}

// ========== AI 邮件草稿 ==========
const emailDialog = ref(false)
const emailGenerating = ref(false)
const emailIntent = ref('')
const emailLanguage = ref('zh')
const emailDraftData = ref(null)

function openEmailDialog() {
  emailDialog.value = true
}

async function handleEmailDraft() {
  emailGenerating.value = true
  try {
    const res = await emailDraft(customerId, { intent: emailIntent.value.trim(), language: emailLanguage.value })
    emailDraftData.value = { subject: res?.subject || '', body: res?.body || '' }
  } finally {
    emailGenerating.value = false
  }
}

async function copyEmail(field) {
  const text = emailDraftData.value?.[field]
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(field === 'subject' ? '主题已复制' : '正文已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择复制')
  }
}

// ========== 客户文档 ==========
const filesLoading = ref(false)
const customerFiles = ref([])
const filesTotal = ref(0)
const themeStore = useThemeStore()
const filesQuery = reactive({ page: 1, page_size: themeStore.pageSize })

async function loadCustomerFiles() {
  filesLoading.value = true
  try {
    const res = await getLibraryFiles({
      customer_id: customerId,
      page: filesQuery.page,
      page_size: filesQuery.page_size,
    })
    customerFiles.value = res.items || []
    filesTotal.value = res.total || 0
  } finally {
    filesLoading.value = false
  }
}

const docUploadDialog = ref(false)
const docUploading = ref(false)
const docUploadRef = ref()
const docFiles = ref([])
// 资料类型（上传时写入文档 category，默认"其他"）
const docCategory = ref('other')
// 当前启用解析格式（accept 过滤 + 选择校验）；拉取失败时不过滤
const acceptStr = ref('')
function initUploadFormats() {
  ensureUploadFormats().then((set) => {
    acceptStr.value = set ? enabledAcceptStr() : ''
  })
}

function handleDocFileChange(f) {
  if (!isEnabledExt(f.name)) {
    ElMessage.warning(`「${f.name}」不是当前支持解析的格式，已忽略`)
    docUploadRef.value?.handleRemove(f)
    return
  }
  docFiles.value.push(f.raw)
}

async function handleDocUpload() {
  if (!docFiles.value.length) return
  docUploading.value = true
  try {
    const res = await uploadLibraryFiles({ files: docFiles.value, customer_id: customerId, category: docCategory.value })
    docUploadDialog.value = false
    docFiles.value = []
    docUploadRef.value?.clearFiles()
    await ElMessageBox.alert(uploadSummary(res), '上传结果', { confirmButtonText: '知道了' })
    loadCustomerFiles()
  } finally {
    docUploading.value = false
  }
}

// 关联到其他知识库
const assocDialog = ref(false)
const associating = ref(false)
const assocFile = ref(null)
const assocKbs = ref([])
const kbs = ref([])

async function openAssoc(row) {
  assocFile.value = row
  assocKbs.value = []
  assocDialog.value = true
  if (!kbs.value.length) {
    const res = await getKbs()
    kbs.value = Array.isArray(res) ? res : (res?.items || [])
  }
}

async function handleAssoc() {
  associating.value = true
  try {
    const res = await associateLibraryFiles([assocFile.value.id], assocKbs.value)
    ElMessage.success(`关联完成：新增 ${res?.associated ?? 0} 个，已存在 ${res?.already ?? 0} 个`)
    assocDialog.value = false
  } finally {
    associating.value = false
  }
}

// ========== 在线预览 ==========
const previewVisible = ref(false)
const previewFile = ref(null)
function openPreview(row) {
  previewFile.value = { id: row.id, file_name: row.file_name, file_type: row.file_type, file_size: row.file_size }
  previewVisible.value = true
}

// ========== 客户画像 ==========
const profileLoading = ref(false)
const generating = ref(false)
const profile = reactive({ profile: '', status: 'idle', updated_at: null, error: '' })
let profileTimer = null

async function loadProfile() {
  profileLoading.value = true
  try {
    const res = await getCustomerProfile(customerId)
    Object.assign(profile, {
      profile: res?.profile || '',
      status: res?.status || 'idle',
      updated_at: res?.updated_at || null,
      error: res?.error || '',
    })
    scheduleProfilePoll()
  } finally {
    profileLoading.value = false
  }
}

function scheduleProfilePoll() {
  clearProfilePoll()
  if (profile.status === 'generating') {
    profileTimer = setTimeout(loadProfile, 3000)
  }
}

function clearProfilePoll() {
  if (profileTimer) {
    clearTimeout(profileTimer)
    profileTimer = null
  }
}

async function handleGenerateProfile() {
  generating.value = true
  try {
    await generateCustomerProfile(customerId)
    profile.status = 'generating'
    scheduleProfilePoll()
  } finally {
    generating.value = false
  }
}

onMounted(() => {
  loadAll()
  loadCustomerFiles()
  loadCustomerTasks()
  loadProfile()
  initUploadFormats()
  docCategoryStore.load()
})
// 同一路由不同客户 id 之间跳转时组件复用、onMounted 不再触发，监听 id 变化重新加载
watch(() => route.params.id, (id) => {
  if (!id || String(id) === String(customerId)) return
  customerId = id
  loadAll()
  loadCustomerFiles()
  loadCustomerTasks()
  loadProfile()
})
onUnmounted(() => {
  clearProfilePoll()
  clearBriefPoll()
})
</script>

<style scoped>
/* ========== 页面骨架：桌面端铺满视口，面板内部滚动 ========== */
.detail-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: calc(100vh / var(--app-zoom, 1) - 40px);
}

/* ========== 简历式头部 ========== */
.head-card {
  flex: none;
  background: var(--el-bg-color);
  border: 1px solid var(--app-line);
  border-radius: 14px;
  padding: 16px 20px 12px;
}
.head-main {
  display: flex;
  align-items: center;
  gap: 14px;
}
.back-btn {
  flex: none;
  width: 30px;
  height: 30px;
  border: 1px solid var(--app-line);
  border-radius: 8px;
  background: transparent;
  color: var(--app-ink-2);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: color 0.15s ease, border-color 0.15s ease;
}
.back-btn:hover {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary-light-5);
}
.back-btn:disabled {
  color: var(--app-ink-3);
  cursor: not-allowed;
  border-color: var(--app-line);
}
/* 头部左侧列：返回按钮 + 上一个/下一个切换组 */
.head-left {
  flex: none;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}
.nav-switch {
  display: flex;
  align-items: center;
  gap: 6px;
}
.nav-switch .back-btn {
  width: 24px;
  height: 24px;
  border-radius: 6px;
}
.nav-pos {
  font-size: 12px;
  color: var(--app-ink-2);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.avatar {
  flex: none;
  width: 52px;
  height: 52px;
  border-radius: 14px;
  background: linear-gradient(135deg, var(--el-color-primary), var(--el-color-primary-light-3));
  color: #fff;
  font-size: 22px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  letter-spacing: 0;
}
.head-id {
  min-width: 0;
  flex: 1;
}
.name-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.cname {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: var(--app-ink);
  line-height: 1.3;
}
.mini-select {
  width: 118px;
}
.subtitle {
  margin-top: 2px;
  font-size: 13px;
  color: var(--app-ink-2);
}
.chips {
  margin-top: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.head-actions {
  flex: none;
  display: flex;
  gap: 8px;
  align-items: center;
}
.meta-row {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--app-line);
  display: flex;
  flex-wrap: wrap;
  gap: 6px 20px;
}
.meta {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12.5px;
  color: var(--app-ink-2);
}
.meta .el-icon {
  color: var(--el-color-primary);
}

/* ========== 档案宫格 ========== */
.grid {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 1.05fr 1.25fr 1fr 1fr;
  gap: 12px;
}
.panel-stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
}
.panel-stack .panel {
  flex: 1;
}
.panel {
  background: var(--el-bg-color);
  border: 1px solid var(--app-line);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.panel-head {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--app-line);
}
.panel-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--app-ink);
}
.count {
  margin-left: 6px;
  font-size: 12px;
  font-weight: 700;
  color: var(--el-color-primary);
}
.panel-tools {
  display: flex;
  align-items: center;
  gap: 8px;
}
.panel-note {
  font-size: 11px;
  color: var(--app-ink-2);
}
.panel-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px 12px;
}
.panel-foot {
  flex: none;
  padding: 6px 12px;
  border-top: 1px solid var(--app-line);
  display: flex;
  justify-content: flex-end;
}
.empty-line {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 14px 4px;
  font-size: 12.5px;
  color: var(--app-ink-2);
  line-height: 1.6;
}

/* 跟进时间线（紧凑） */
.timeline {
  padding-left: 2px;
  margin-top: 4px;
}
.timeline :deep(.el-timeline-item__wrapper) {
  padding-left: 20px;
}
.timeline :deep(.el-timeline-item__timestamp) {
  font-size: 11px;
}
.followup-content {
  margin-top: 4px;
  font-size: 13px;
  white-space: pre-wrap;
  line-height: 1.6;
}
.next-step {
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--el-color-primary);
}
.ai-summary {
  margin-top: 4px;
  font-size: 12px;
  color: var(--app-ink-2);
}

/* 商机行 */
.opp-row {
  padding: 8px 4px;
  border-bottom: 1px dashed var(--app-line);
}
.opp-row:last-child {
  border-bottom: none;
}
.opp-main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.opp-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--app-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.opp-sub {
  margin-top: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.opp-amount {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--el-color-danger);
  font-variant-numeric: tabular-nums;
}
.opp-meta {
  font-size: 12px;
  color: var(--app-ink-2);
}

/* 待办行 */
.task-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 4px;
  border-bottom: 1px dashed var(--app-line);
}
.task-row:last-child {
  border-bottom: none;
}
.task-row :deep(.el-checkbox) {
  margin-right: 0;
  height: auto;
}
.task-title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--app-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-title.done {
  text-decoration: line-through;
  color: var(--app-ink-2);
}
.task-due {
  flex: none;
  font-size: 12px;
  color: var(--app-ink-2);
  font-variant-numeric: tabular-nums;
}

/* 文档行 */
.doc-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 4px;
  border-bottom: 1px dashed var(--app-line);
}
.doc-row:last-child {
  border-bottom: none;
}
.doc-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  justify-content: flex-start;
}
.doc-size {
  flex: none;
  font-size: 12px;
  color: var(--app-ink-2);
  font-variant-numeric: tabular-nums;
}

/* 紧凑 markdown（简报/画像） */
.compact-md {
  font-size: 13px;
  line-height: 1.65;
}
.compact-md :deep(h1),
.compact-md :deep(h2),
.compact-md :deep(h3) {
  font-size: 14px;
  margin: 10px 0 6px;
}

.email-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-bottom: 8px;
}
.upload-tip {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--app-ink-2);
}

/* ========== 中屏：两列两排 ========== */
@media (max-width: 1400px) {
  .grid {
    grid-template-columns: 1fr 1fr;
    grid-template-rows: 1fr 1fr;
  }
}

/* ========== 手机：单列自然流，页面可滚动 ========== */
@media (max-width: 767px) {
  .detail-page {
    height: auto;
  }
  .head-main {
    flex-wrap: wrap;
  }
  .head-actions {
    width: 100%;
    justify-content: flex-end;
  }
  .cname {
    font-size: 18px;
  }
  .grid {
    display: flex;
    flex-direction: column;
  }
  .panel {
    min-height: 160px;
  }
  .panel-body {
    max-height: 50vh;
  }
}
</style>
