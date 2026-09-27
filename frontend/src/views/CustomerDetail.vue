<template>
  <div v-loading="loading">
    <el-card>
      <template #header>
        <div class="card-header">
          <el-button :icon="ArrowLeft" @click="router.push('/customers')">返回列表</el-button>
          <span class="customer-name">{{ customer?.name || '客户详情' }}</span>
          <el-tag v-if="customer" :type="enumTagType(customerStatusMap, customer.status)">
            {{ enumLabel(customerStatusMap, customer.status) }}
          </el-tag>
        </div>
      </template>
      <el-descriptions v-if="customer" :column="3" border>
        <el-descriptions-item label="单位">{{ customer.company || '-' }}</el-descriptions-item>
        <el-descriptions-item label="职务">{{ customer.position || '-' }}</el-descriptions-item>
        <el-descriptions-item label="行业">
          <template v-if="customer.industries?.length">
            <el-tag v-for="t in customer.industries" :key="t" size="small" type="info" effect="plain" style="margin-right: 6px">{{ t }}</el-tag>
          </template>
          <span v-else>-</span>
        </el-descriptions-item>
        <el-descriptions-item label="标签">
          <template v-if="customer.tags?.length">
            <el-tag v-for="t in customer.tags" :key="t" size="small" style="margin-right: 6px">{{ t }}</el-tag>
          </template>
          <span v-else>-</span>
        </el-descriptions-item>
        <el-descriptions-item label="电话">{{ customer.phone || '-' }}</el-descriptions-item>
        <el-descriptions-item label="微信">{{ customer.wechat || '-' }}</el-descriptions-item>
        <el-descriptions-item label="邮箱">{{ customer.email || '-' }}</el-descriptions-item>
        <el-descriptions-item label="地址">{{ customer.address || '-' }}</el-descriptions-item>
        <el-descriptions-item label="来源">{{ customer.source || '-' }}</el-descriptions-item>
        <el-descriptions-item label="生日">{{ formatDate(customer.birthday) }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ formatDateTime(customer.created_at) }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card style="margin-top: 16px">
      <el-tabs v-model="activeTab">
        <!-- 跟进与商机 -->
        <el-tab-pane label="跟进与商机" name="followup">
          <el-row :gutter="16">
            <el-col :xs="24" :lg="12">
              <div class="pane-head">
                <span>跟进记录</span>
                <el-button type="primary" size="small" :icon="Plus" @click="openFollowupDialog">新增跟进</el-button>
              </div>
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
                  <div v-if="f.ai_summary" class="ai-summary">AI 摘要：{{ f.ai_summary }}</div>
                </el-timeline-item>
              </el-timeline>
              <el-empty v-else description="暂无跟进记录" :image-size="80" />
            </el-col>

            <el-col :xs="24" :lg="12">
              <div class="pane-head">
                <span>商机</span>
                <el-button type="primary" size="small" :icon="Plus" @click="openOppDialog">新增商机</el-button>
              </div>
              <el-table :data="opportunities" size="small">
                <el-table-column prop="name" label="名称" min-width="110" show-overflow-tooltip />
                <el-table-column label="金额" width="100">
                  <template #default="{ row }">¥{{ formatMoney(row.amount) }}</template>
                </el-table-column>
                <el-table-column label="阶段" width="100">
                  <template #default="{ row }">
                    <el-tag size="small" :type="enumTagType(opportunityStageMap, row.stage)">
                      {{ enumLabel(opportunityStageMap, row.stage) }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="预计成交" width="100">
                  <template #default="{ row }">{{ formatDate(row.expected_close_date) }}</template>
                </el-table-column>
                <el-table-column label="概率" width="70">
                  <template #default="{ row }">{{ row.probability ?? '-' }}%</template>
                </el-table-column>
                <el-table-column label="操作" width="60">
                  <template #default="{ row }">
                    <el-button link type="danger" size="small" @click="handleDeleteOpp(row)">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-empty v-if="!opportunities.length" description="暂无商机" :image-size="80" />
            </el-col>
          </el-row>
        </el-tab-pane>

        <!-- 客户文档 -->
        <el-tab-pane label="客户文档" name="docs">
          <el-alert
            type="info"
            :closable="false"
            title="支持文档/图片/音视频，图片与音视频将由 AI 自动识别转写后入库；其他格式仅存储。"
            style="margin-bottom: 12px"
          />
          <div class="pane-head">
            <span>文件列表</span>
            <el-button type="primary" size="small" :icon="Upload" @click="docUploadDialog = true">上传文件</el-button>
          </div>
          <el-table :data="customerFiles" v-loading="filesLoading" size="small" stripe>
            <el-table-column label="名称" min-width="160" show-overflow-tooltip>
              <template #default="{ row }">
                <el-link type="primary" :underline="false" @click="openPreview(row)">{{ row.file_name }}</el-link>
              </template>
            </el-table-column>
            <el-table-column label="大小" width="90">
              <template #default="{ row }">{{ formatFileSize(row.file_size) }}</template>
            </el-table-column>
            <el-table-column label="解析" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.supported ? 'success' : 'info'">
                  {{ row.supported ? '可解析' : '仅存储' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="上传时间" width="150">
              <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="170">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openPreview(row)">预览</el-button>
                <el-button link type="success" size="small" @click="openAssoc(row)">关联知识库</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!filesLoading && !customerFiles.length" description="暂无客户文档" :image-size="80" />
          <el-pagination
            class="pager"
            v-model:current-page="filesQuery.page"
            :page-size="filesQuery.page_size"
            :total="filesTotal"
            layout="total, prev, pager, next"
            small
            @current-change="loadCustomerFiles"
          />
        </el-tab-pane>

        <!-- 客户画像 -->
        <el-tab-pane label="客户画像" name="profile">
          <div v-loading="profileLoading" class="profile-pane">
            <template v-if="profile.status === 'idle'">
              <el-empty description="还没有客户画像">
                <el-button type="primary" :loading="generating" @click="handleGenerateProfile">生成画像</el-button>
              </el-empty>
            </template>
            <template v-else-if="profile.status === 'generating'">
              <div class="profile-generating">
                <el-icon class="is-loading" :size="26"><Loading /></el-icon>
                <p>AI 正在基于客户资料、跟进记录与文档生成画像，请稍候…</p>
              </div>
            </template>
            <template v-else-if="profile.status === 'failed'">
              <el-alert
                type="error"
                :closable="false"
                title="画像生成失败"
                :description="profile.error || '可能是 AI 服务未配置或暂不可用，其他功能不受影响。'"
                style="margin-bottom: 12px"
              />
              <el-button type="primary" :loading="generating" @click="handleGenerateProfile">重试</el-button>
            </template>
            <template v-else-if="profile.status === 'ready'">
              <div class="profile-head">
                <span class="profile-time">生成于 {{ formatDateTime(profile.updated_at) }}</span>
                <el-button size="small" :loading="generating" @click="handleGenerateProfile">重新生成</el-button>
              </div>
              <div class="markdown-body" v-html="renderMarkdown(profile.profile)"></div>
            </template>
          </div>
        </el-tab-pane>

        <!-- AI 对话（客户专属知识库） -->
        <el-tab-pane label="AI 对话" name="chat">
          <div class="chat-pane">
            <div v-if="chatKbLoading" v-loading="true" class="chat-loading"></div>
            <el-alert v-else-if="chatKbError" type="error" :closable="false" :title="chatKbError" />
            <template v-else>
              <div ref="chatListRef" class="chat-list">
                <el-empty
                  v-if="!chatMessages.length && !(chatKb?.doc_count > 0)"
                  description="该客户还没有资料，先在客户文档页签上传"
                  :image-size="80"
                />
                <div v-for="m in chatMessages" :key="m._key" class="msg-row" :class="m.role">
                  <div class="bubble">
                    <div v-if="m.role === 'user'" class="bubble-text">{{ m.content }}</div>
                    <template v-else>
                      <!-- 工具调用状态 -->
                      <div v-if="m.tools?.length" class="tool-tags">
                        <el-tag
                          v-for="(t, j) in m.tools"
                          :key="j"
                          size="small"
                          :type="t.status === 'done' ? 'success' : 'info'"
                          effect="plain"
                        >
                          <el-icon v-if="t.status !== 'done'" class="is-loading" :size="12"><Loading /></el-icon>
                          {{ t.status === 'done' ? `已使用：${toolLabel(t.name)}` : `正在${toolLabel(t.name)}…` }}
                        </el-tag>
                      </div>
                      <!-- AI 思考中（agent 决策阶段） -->
                      <div v-if="m.thinking && !m.content" class="thinking-indicator">
                        <el-icon class="is-loading" :size="14"><Loading /></el-icon>
                        <span>AI 正在思考…</span>
                      </div>
                      <div class="bubble-md markdown-body" v-html="renderMarkdown(m.content)"></div>
                      <span v-if="m.streaming" class="cursor"></span>
                      <div v-if="m.error" class="msg-error">{{ m.error }}</div>

                      <!-- 引用来源：file_id 非空可点击直达文件预览 -->
                      <div v-if="m.sources?.length" class="sources">
                        <div class="sources-toggle" @click="m.expanded = !m.expanded">
                          <el-icon><component :is="m.expanded ? ArrowUp : ArrowDown" /></el-icon>
                          引用来源（{{ m.sources.length }}）
                        </div>
                        <div v-show="m.expanded">
                          <div
                            v-for="(s, j) in m.sources"
                            :key="j"
                            class="source-item"
                            :class="{ clickable: !!s.file_id }"
                            @click="s.file_id && openSourcePreview(s)"
                          >
                            <div class="source-head">
                              <span class="source-title" :class="{ link: !!s.file_id }">
                                {{ s.file_name || s.doc_title || `文档 #${s.doc_id}` }}
                              </span>
                              <el-tag size="small" type="info">相关度 {{ Math.round((s.score || 0) * 100) }}%</el-tag>
                            </div>
                            <div class="source-excerpt">{{ s.excerpt }}</div>
                          </div>
                        </div>
                      </div>
                    </template>
                  </div>
                </div>
              </div>
              <div v-if="!chatMessages.length && chatKb?.doc_count > 0" class="chat-chips">
                <span v-for="q in quickQuestions" :key="q" class="chat-chip" @click="sendChat(q)">{{ q }}</span>
              </div>
              <div class="chat-input">
                <el-input
                  v-model="chatInput"
                  type="textarea"
                  :autosize="{ minRows: 1, maxRows: 4 }"
                  placeholder="只基于该客户的资料回答，Enter 发送（Shift+Enter 换行）"
                  :disabled="chatStreaming"
                  @keydown.enter.exact="handleChatEnter"
                />
                <el-button v-if="chatStreaming" type="danger" :icon="VideoPause" @click="stopChat">停止</el-button>
                <el-button v-else type="primary" :icon="Promotion" :disabled="!chatInput.trim()" @click="sendChat(chatInput)">发送</el-button>
              </div>
            </template>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>

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

    <!-- 文件预览 -->
    <FilePreview v-model="previewVisible" :file="previewFile" />
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Plus, Upload, UploadFilled, Loading, ArrowDown, ArrowUp, Promotion, VideoPause } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getCustomer, getFollowups, createFollowup,
  getOpportunities, createOpportunity, deleteOpportunity,
  getLibraryFiles, associateLibraryFiles, getKbs,
  getCustomerProfile, generateCustomerProfile, getCustomerKb,
} from '../api'
import { uploadLibraryFiles, uploadSummary } from '../api/libraryUpload'
import { askStream } from '../api/chatStream'
import { renderMarkdown } from '../utils/markdown'
import { ensureUploadFormats, isEnabledExt, enabledAcceptStr } from '../utils/uploadFormats'
import { useThemeStore } from '../stores/theme'
import FilePreview from '../components/FilePreview.vue'
import {
  customerStatusMap, followupTypeMap, opportunityStageMap,
  enumLabel, enumTagType, formatDate, formatDateTime, formatFileSize, formatMoney,
  toolNameMap,
} from '../utils/format'

function toolLabel(name) {
  return toolNameMap[name] || name
}

const route = useRoute()
const router = useRouter()
const customerId = route.params.id

const loading = ref(false)
const customer = ref(null)
const followups = ref([])
const opportunities = ref([])
const activeTab = ref('followup')

const followupDialog = ref(false)
const followupSaving = ref(false)
const followupFormRef = ref()
const followupForm = reactive({ type: 'call', content: '' })
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
  await followupFormRef.value.validate()
  followupSaving.value = true
  try {
    await createFollowup(customerId, { type: followupForm.type, content: followupForm.content })
    ElMessage.success('提交成功，AI 摘要生成中，稍后刷新查看')
    followupDialog.value = false
    followupForm.type = 'call'
    followupForm.content = ''
    const f = await getFollowups(customerId)
    followups.value = Array.isArray(f) ? f : (f?.items || [])
  } finally {
    followupSaving.value = false
  }
}

async function handleSaveOpp() {
  await oppFormRef.value.validate()
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
  await ElMessageBox.confirm(`确定删除商机「${row.name}」吗？`, '删除确认', { type: 'warning' })
  await deleteOpportunity(row.id)
  ElMessage.success('删除成功')
  const o = await getOpportunities({ customer_id: customerId })
  opportunities.value = Array.isArray(o) ? o : (o?.items || [])
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
    const res = await uploadLibraryFiles({ files: docFiles.value, customer_id: customerId })
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

// ========== AI 对话（客户专属知识库，流式问答见 api/chatStream.js） ==========
const quickQuestions = [
  '介绍一下这个客户',
  '这个客户喜欢什么',
  '最近和这位客户聊得怎么样',
  '这个客户的风险与机会',
]
const chatKb = ref(null)
const chatKbLoading = ref(false)
const chatKbError = ref('')
const chatSessionId = ref(null)
const chatMessages = ref([])
const chatInput = ref('')
const chatStreaming = ref(false)
const chatListRef = ref()
let chatKeySeq = 0
let chatAbortCtrl = null
let chatKbLoaded = false

// 进入 AI 对话页签时才加载专属知识库
watch(activeTab, (tab) => {
  if (tab === 'chat' && !chatKbLoaded) {
    chatKbLoaded = true
    loadChatKb()
  }
})

async function loadChatKb() {
  chatKbLoading.value = true
  chatKbError.value = ''
  try {
    const res = await getCustomerKb(customerId)
    if (!res?.id) throw new Error('no kb')
    chatKb.value = res
  } catch {
    chatKbError.value = '客户专属知识库获取失败，请稍后重试'
  } finally {
    chatKbLoading.value = false
  }
}

function toChatMsg(role, content) {
  return { _key: ++chatKeySeq, role, content, sources: [], tools: [], expanded: false, streaming: false, error: null, thinking: false }
}

async function scrollChatBottom() {
  await nextTick()
  const el = chatListRef.value
  if (el) el.scrollTop = el.scrollHeight
}

function handleChatEnter(e) {
  // 中文输入法选词期间的 Enter 不触发发送
  if (e.isComposing || e.keyCode === 229) return
  e.preventDefault()
  sendChat(chatInput.value)
}

async function sendChat(text) {
  const question = (text || '').trim()
  if (!question || chatStreaming.value || !chatKb.value?.id) return
  chatInput.value = ''
  chatMessages.value.push(toChatMsg('user', question))
  const aiMsg = toChatMsg('ai', '')
  aiMsg.streaming = true
  chatMessages.value.push(aiMsg)
  chatStreaming.value = true
  scrollChatBottom()

  chatAbortCtrl = new AbortController()
  try {
    for await (const frame of askStream({
      session_id: chatSessionId.value ?? undefined,
      question,
      kb_ids: [chatKb.value.id],
      signal: chatAbortCtrl.signal,
    })) {
      if (frame.type === 'meta') {
        if (frame.session_id) chatSessionId.value = frame.session_id
      } else if (frame.type === 'sources') {
        aiMsg.sources = frame.sources || []
      } else if (frame.type === 'token') {
        aiMsg.content += frame.content || ''
        scrollChatBottom()
      } else if (frame.type === 'thinking') {
        aiMsg.thinking = frame.status === 'start'
      } else if (frame.type === 'tool') {
        // 工具调用状态：start 追加标签，done 更新同名进行中的标签
        const pending = aiMsg.tools.find((x) => x.name === frame.name && x.status === 'start')
        if (frame.status === 'done' && pending) {
          pending.status = 'done'
        } else if (frame.status === 'start') {
          aiMsg.tools.push({ name: frame.name, status: 'start' })
        }
      } else if (frame.type === 'error') {
        aiMsg.error = frame.detail || '服务异常，请稍后重试。'
      }
    }
  } catch (e) {
    if (e?.name === 'AbortError') {
      if (!aiMsg.content) aiMsg.content = '（已停止生成）'
    } else {
      aiMsg.error = e?.message || '请求失败，请稍后重试。'
    }
  } finally {
    aiMsg.streaming = false
    chatStreaming.value = false
    chatAbortCtrl = null
    scrollChatBottom()
  }
}

function stopChat() {
  chatAbortCtrl?.abort()
}

// 引用来源直达文件预览（复用客户文档页签的 FilePreview 实例）
function openSourcePreview(s) {
  previewFile.value = { id: s.file_id, file_name: s.file_name, file_type: s.file_type, file_size: s.file_size }
  previewVisible.value = true
}

onMounted(() => {
  loadAll()
  loadCustomerFiles()
  loadProfile()
  initUploadFormats()
})
onUnmounted(() => {
  clearProfilePoll()
  chatAbortCtrl?.abort()
})
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.customer-name {
  font-size: 16px;
  font-weight: 600;
}
.pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  font-weight: 600;
}
.timeline {
  padding-left: 4px;
  max-height: 480px;
  overflow-y: auto;
}
.followup-content {
  margin-top: 6px;
  white-space: pre-wrap;
}
.ai-summary {
  margin-top: 4px;
  font-size: 12px;
  color: var(--app-ink-2);
}
.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
.upload-tip {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--app-ink-2);
}
.profile-pane {
  min-height: 240px;
}
.profile-generating {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  color: var(--app-ink-2);
}
.profile-generating p {
  margin: 14px 0 0;
}
.profile-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.profile-time {
  font-size: 13px;
  color: var(--app-ink-2);
}

/* AI 对话页签（气泡/来源样式与 Chat.vue 保持一致） */
.chat-loading {
  height: 320px;
}
.chat-list {
  height: 420px;
  overflow-y: auto;
  padding: 4px 2px;
}
.msg-row {
  display: flex;
  margin-bottom: 16px;
}
.msg-row.user {
  justify-content: flex-end;
}
.bubble {
  max-width: 78%;
  padding: 10px 14px;
  border-radius: 12px;
  background: var(--app-bg);
  border: 1px solid var(--app-line);
  line-height: 1.7;
  min-width: 0;
}
.msg-row.user .bubble {
  background: var(--el-color-primary);
  color: #fff;
  border-color: var(--el-color-primary);
}
.bubble-text {
  white-space: pre-wrap;
  word-break: break-word;
}
.cursor {
  display: inline-block;
  width: 8px;
  height: 16px;
  margin-left: 2px;
  vertical-align: text-bottom;
  background: var(--el-color-primary);
  animation: blink 0.9s steps(2) infinite;
}
@keyframes blink {
  50% { opacity: 0; }
}
.msg-error {
  color: var(--el-color-danger);
  font-size: 13px;
}
.tool-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.sources {
  margin-top: 10px;
  border-top: 1px dashed var(--app-line);
  padding-top: 8px;
}
.sources-toggle {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--el-color-primary);
  cursor: pointer;
}
.source-item {
  margin-top: 8px;
  padding: 8px 10px;
  background: var(--app-surface);
  border: 1px solid var(--app-line);
  border-radius: 8px;
}
.source-item.clickable {
  cursor: pointer;
}
.source-item.clickable:hover {
  border-color: var(--el-color-primary);
}
.source-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}
.source-title {
  font-size: 13px;
  font-weight: 600;
}
.source-title.link {
  color: var(--el-color-primary);
}
.source-excerpt {
  font-size: 12px;
  color: var(--app-ink-2);
  line-height: 1.5;
}
.chat-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 10px 0;
}
.chat-chip {
  padding: 6px 12px;
  border: 1px solid var(--el-color-primary-light-5);
  color: var(--el-color-primary);
  border-radius: 999px;
  font-size: 12px;
  cursor: pointer;
  background: var(--app-surface);
  transition: background 0.15s ease;
}
.chat-chip:hover {
  background: var(--el-color-primary-light-9);
}
.chat-input {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  margin-top: 10px;
}

@media (max-width: 767px) {
  .bubble {
    max-width: 88%;
  }
  .chat-list {
    height: 360px;
  }
}
</style>
