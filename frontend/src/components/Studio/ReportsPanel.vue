<template>
  <div class="rp">
    <div class="rp-toolbar">
      <span class="rp-title">智能报告</span>
      <div class="rp-actions-top">
        <el-button size="small" type="success" :icon="MagicStick" @click="openCompose">自定义</el-button>
        <el-button size="small" type="primary" :icon="Plus" @click="openGenerate">生成</el-button>
      </div>
    </div>

    <!-- 报告列表（紧凑卡片） -->
    <div class="rp-list" v-loading="loading">
      <div v-for="r in list" :key="r.id" class="rp-item" @click="openView(r)">
        <div class="rp-head">
          <span class="rp-name" :title="r.title">{{ r.title }}</span>
          <el-tag size="small" :type="enumTagType(reportTypeMap, r.type)">{{ enumLabel(reportTypeMap, r.type) }}</el-tag>
        </div>
        <div class="rp-meta">
          <el-tag v-if="r.status === 'generating' || r.status === 'revising'" size="small" type="warning" effect="light">
            <el-icon class="is-loading" style="margin-right: 4px"><Loading /></el-icon>{{ r.progress || enumLabel(reportStatusMap, r.status) }}
          </el-tag>
          <span v-else class="rp-status" :class="'st-' + r.status">{{ enumLabel(reportStatusMap, r.status) }}</span>
          <span>{{ formatDateTime(r.created_at) }}</span>
        </div>
        <div class="rp-actions" @click.stop>
          <el-button link size="small" @click="openView(r)">查看</el-button>
          <el-button link type="danger" size="small" @click="handleDelete(r)">删除</el-button>
        </div>
      </div>
      <el-empty v-if="!loading && !list.length" description="暂无报告" :image-size="60" />
    </div>

    <!-- 生成报告 -->
    <el-dialog v-model="genDialog" title="生成报告" width="min(90vw, 520px)" append-to-body>
      <el-form :model="genForm" :rules="genRules" ref="genFormRef" label-width="80px">
        <el-form-item label="报告类型" prop="type">
          <el-select v-model="genForm.type" style="width: 100%">
            <el-option v-for="(v, k) in reportTypeMap" :key="k" :label="v.label" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="genForm.type === 'customer_analysis'" label="客户" prop="customer_id">
          <el-select v-model="genForm.customer_id" style="width: 100%" filterable placeholder="选择客户">
            <el-option v-for="c in customerOptions" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-else label="日期范围" prop="dateRange">
          <el-date-picker
            v-model="genForm.dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="报告语言">
          <el-radio-group v-model="genForm.language">
            <el-radio-button value="zh">中文</el-radio-button>
            <el-radio-button value="en">English</el-radio-button>
            <el-radio-button value="zh_en">中英双语</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="超时(秒)">
          <el-input-number v-model="genForm.timeout" :min="30" :max="600" :step="30" style="width: 140px" />
          <span class="form-tip">AI 生成超时上限，长报告建议调大</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="genDialog = false">取消</el-button>
        <el-button type="primary" :loading="generating" @click="handleGenerate">生成</el-button>
      </template>
    </el-dialog>

    <!-- 自定义报告（知识库 + 附件 → HTML） -->
    <el-dialog
      v-model="composeDialog"
      title="自定义报告"
      width="min(92vw, 620px)"
      append-to-body
      :close-on-click-modal="false"
      @closed="clearAttachPoll"
    >
      <el-form :model="composeForm" :rules="composeRules" ref="composeFormRef" label-width="90px">
        <el-form-item label="报告需求" prop="prompt">
          <el-input v-model="composeForm.prompt" type="textarea" :rows="3" placeholder="如：基于产品资料库生成一份竞品对比分析报告" />
        </el-form-item>
        <el-form-item label="知识库">
          <el-select v-model="composeForm.kb_ids" multiple collapse-tags :max-collapse-tags="2" style="width: 100%" placeholder="不选则检索全部知识库">
            <el-option v-for="k in kbOptions" :key="k.id" :label="k.name" :value="k.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="报告语言">
          <el-radio-group v-model="composeForm.language">
            <el-radio-button value="zh">中文</el-radio-button>
            <el-radio-button value="en">English</el-radio-button>
            <el-radio-button value="zh_en">中英双语</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="超时(秒)">
          <el-input-number v-model="composeForm.timeout" :min="30" :max="600" :step="30" style="width: 140px" />
          <span class="form-tip">长报告（HTML 排版）生成较慢，可调大</span>
        </el-form-item>
        <el-form-item label="附件">
          <div class="attach-area">
            <el-upload :auto-upload="false" multiple :show-file-list="false" :on-change="handleAttachChange">
              <el-button :icon="Upload">上传附件</el-button>
            </el-upload>
            <div v-if="attachments.length" class="attach-list">
              <div v-for="(a, i) in attachments" :key="i" class="attach-item">
                <span class="attach-name">{{ a.name }}</span>
                <span v-if="a.status === 'uploading'" class="attach-status"><el-icon class="is-loading" :size="13"><Loading /></el-icon>上传中</span>
                <span v-else-if="a.status === 'processing'" class="attach-status"><el-icon class="is-loading" :size="13"><Loading /></el-icon>AI 分析中</span>
                <span v-else-if="a.status === 'ready'" class="attach-status ok"><el-icon :size="13"><CircleCheck /></el-icon>就绪</span>
                <span v-else class="attach-status bad">解析失败</span>
                <el-button link type="danger" size="small" @click="attachments.splice(i, 1)">移除</el-button>
              </div>
            </div>
            <p class="attach-tip">图片与音视频附件将由 AI 自动识别转写；全部就绪后才能生成。</p>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="composeDialog = false">取消</el-button>
        <el-button type="primary" :loading="composing" :disabled="!canCompose" @click="handleCompose">生成</el-button>
      </template>
    </el-dialog>

    <!-- 查看报告 -->
    <el-drawer v-model="viewDrawer" :title="currentReport?.title || '报告详情'" size="min(92vw, 720px)" @closed="closeView">
      <div v-loading="viewLoading" class="view-body">
        <template v-if="currentReport">
          <el-alert v-if="currentReport.status === 'failed'" type="error" :title="currentReport.error || '报告生成失败'" :closable="false" style="margin-bottom: 12px" />
          <template v-else>
            <div class="view-toolbar">
              <el-radio-group v-model="viewLayout" size="small">
                <el-radio-button value="doc">文档</el-radio-button>
                <el-radio-button value="slides" :disabled="!hasPresentation">演示</el-radio-button>
              </el-radio-group>
              <!-- 中英双语：HTML 文档一键切换显示语言 -->
              <el-radio-group v-if="isBilingual && isHtmlReport" v-model="previewLang" size="small" class="lang-toggle">
                <el-radio-button value="both">双语</el-radio-button>
                <el-radio-button value="zh">中文</el-radio-button>
                <el-radio-button value="en">English</el-radio-button>
              </el-radio-group>
              <el-button size="small" :icon="FullScreen" @click="enterFullscreen"
                :disabled="viewLayout === 'slides' ? !hasPresentation : !previewContent">全屏</el-button>
              <el-button v-if="canDownloadPdf" size="small" :icon="Printer" :loading="pdfLoading"
                :disabled="currentReport.status !== 'ready'" @click="handleDownloadPdf">下载 PDF</el-button>
            </div>
            <!-- 生成/修改中：实时滚动进度 -->
            <div v-if="currentReport.status === 'generating' || currentReport.status === 'revising'" class="gen-progress">
              <el-icon class="is-loading" :size="16"><Loading /></el-icon>
              <span>{{ currentReport.progress || (currentReport.status === 'revising' ? 'AI 修改中…' : 'AI 生成中…') }}</span>
            </div>
            <!-- 文档布局 -->
            <template v-if="viewLayout === 'doc'">
              <iframe v-if="isHtmlReport && previewContent" class="report-frame" sandbox="" :srcdoc="srcdocContent"></iframe>
              <div v-else-if="previewContent" class="markdown-body" v-html="renderedContent"></div>
              <div v-else-if="currentReport.status === 'ready'" class="revising-mask"><span>（空报告）</span></div>
            </template>
            <!-- 演示布局：独立 16:9 HTML 演示文稿（自带导航脚本） -->
            <div v-else-if="hasPresentation" class="slide-deck">
              <iframe class="pres-frame" sandbox="allow-scripts" :srcdoc="presentationHtml"></iframe>
              <div class="pres-hint">点击幻灯片后可用 ← → 方向键翻页 · 推荐进入全屏观看</div>
            </div>
            <div v-else-if="viewLayout === 'slides'" class="revising-mask"><span>该报告暂无演示版，请重新生成</span></div>

            <template v-if="currentReport.type === 'custom'">
              <div v-if="currentReport.revisions?.length" class="rev-list">
                <div class="rev-head">修改历史</div>
                <div v-for="(rev, i) in currentReport.revisions" :key="i" class="rev-item">
                  <span class="rev-time">{{ formatDateTime(rev.created_at) }}</span>
                  <span class="rev-text" :title="rev.instruction">{{ rev.instruction }}</span>
                  <el-button link type="primary" size="small" @click="viewingRev = rev">查看此版本</el-button>
                </div>
                <el-button v-if="viewingRev" link type="warning" size="small" @click="viewingRev = null">回到最新</el-button>
              </div>
              <div class="revise-box">
                <el-input v-model="reviseInput" placeholder="输入修改要求，如：把第二部分换成表格形式" :disabled="currentReport.status !== 'ready'" @keydown.enter.exact="handleReviseEnter" />
                <el-button type="primary" :loading="revising" :disabled="currentReport.status !== 'ready' || !reviseInput.trim()" @click="handleRevise">修改</el-button>
              </div>
            </template>
          </template>
        </template>
      </div>
    </el-drawer>

    <!-- 全屏阅读 / 演示 -->
    <el-dialog v-model="fullscreen" fullscreen :show-close="false" class="report-fullscreen">
      <template #header>
        <div class="fs-header">
          <span class="fs-title">{{ currentReport?.title || '报告' }}</span>
          <el-button :icon="Close" circle @click="fullscreen = false" />
        </div>
      </template>
      <iframe v-if="viewLayout === 'doc' && isHtmlReport && previewContent" class="fs-frame" sandbox="" :srcdoc="srcdocContent"></iframe>
      <div v-else-if="viewLayout === 'doc' && previewContent" class="fs-markdown markdown-body" v-html="renderedContent"></div>
      <iframe v-else-if="viewLayout === 'slides' && hasPresentation" class="fs-pres" sandbox="allow-scripts" :srcdoc="presentationHtml"></iframe>
      <div v-else class="fs-empty">（暂无内容）</div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { Plus, Loading, MagicStick, Upload, CircleCheck, Printer, FullScreen, Close } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  generateReport, getReports, getReport, deleteReport, getCustomers,
  getKbs, getKbDocuments, getWorkbenchKb, reviseReport, exportReportPdf, exportReportPresentationPdf,
} from '../../api'
import { uploadLibraryFiles } from '../../api/libraryUpload'
import { renderMarkdown } from '../../utils/markdown'
import { reportTypeMap, reportStatusMap, enumLabel, enumTagType, formatDateTime } from '../../utils/format'

const loading = ref(false)
const list = ref([])
const total = ref(0)

const genDialog = ref(false)
const generating = ref(false)
const genFormRef = ref()
const genForm = reactive({ type: 'customer_analysis', customer_id: null, dateRange: null, timeout: 180, language: 'zh' })
const genRules = {
  type: [{ required: true, message: '请选择报告类型', trigger: 'change' }],
  customer_id: [{ required: true, message: '请选择客户', trigger: 'change' }],
  dateRange: [{ required: true, message: '请选择日期范围', trigger: 'change' }],
}
const customerOptions = ref([])

const viewDrawer = ref(false)
const viewLoading = ref(false)
const currentReport = ref(null)
const fullscreen = ref(false)
// 布局：doc=文档（A4 HTML/markdown） / slides=演示（独立 16:9 HTML 演示文稿）
const viewLayout = ref('doc')
const hasPresentation = computed(() => !!(currentReport.value?.params?.presentation_html))
const presentationHtml = computed(() => currentReport.value?.params?.presentation_html || '')

function enterFullscreen() {
  fullscreen.value = true
}

let pollTimer = null

const renderedContent = computed(() => renderMarkdown(previewContent.value))

async function loadList() {
  loading.value = true
  try {
    const res = await getReports({ page: 1, page_size: 100 })
    list.value = res.items || []
    total.value = res.total || 0
    schedulePolling()
  } finally {
    loading.value = false
  }
}
// 供外部（生成成功后）刷新
async function reload() {
  await loadList()
}

function schedulePolling() {
  clearPolling()
  if (list.value.some((r) => r.status === 'generating' || r.status === 'revising')) {
    pollTimer = setTimeout(loadList, 3000)
  }
}
function clearPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
}

async function loadCustomerOptions() {
  try {
    const res = await getCustomers({ page: 1, page_size: 200 })
    customerOptions.value = res.items || []
  } catch { /* 下拉失败不阻断 */ }
}

function openGenerate() {
  Object.assign(genForm, { type: 'customer_analysis', customer_id: null, dateRange: null, timeout: 180, language: 'zh' })
  genDialog.value = true
  nextTick(() => genFormRef.value?.clearValidate())
}

async function handleGenerate() {
  await genFormRef.value.validate()
  generating.value = true
  try {
    const data = { type: genForm.type, timeout: genForm.timeout, language: genForm.language }
    if (genForm.type === 'customer_analysis') data.customer_id = genForm.customer_id
    else { data.start_date = genForm.dateRange?.[0]; data.end_date = genForm.dateRange?.[1] }
    await generateReport(data)
    ElMessage.success('已提交生成，请稍候')
    genDialog.value = false
    loadList()
  } finally { generating.value = false }
}

// ========== 自定义报告 ==========
const composeDialog = ref(false)
const composing = ref(false)
const composeFormRef = ref()
const composeForm = reactive({ prompt: '', kb_ids: [], timeout: 180, language: 'zh' })
const composeRules = { prompt: [{ required: true, message: '请描述报告需求', trigger: 'blur' }] }
const kbOptions = ref([])
const workbenchKb = ref(null)
const attachments = ref([])
let attachTimer = null

const canCompose = computed(() => !attachments.value.some((a) => a.status !== 'ready'))

async function openCompose() {
  Object.assign(composeForm, { prompt: '', kb_ids: [], timeout: 180, language: 'zh' })
  attachments.value = []
  composeDialog.value = true
  nextTick(() => composeFormRef.value?.clearValidate())
  if (!kbOptions.value.length) {
    try {
      const res = await getKbs()
      kbOptions.value = Array.isArray(res) ? res : (res?.items || [])
    } catch { /* 可不用 KB */ }
  }
  if (!workbenchKb.value) {
    try {
      workbenchKb.value = await getWorkbenchKb(false)
      if (workbenchKb.value?.doc_count > 0) composeForm.kb_ids = [workbenchKb.value.id]
    } catch { /* 附件上传不可用 */ }
  }
}

async function handleAttachChange(uploadFile) {
  if (!workbenchKb.value?.id) {
    try { workbenchKb.value = await getWorkbenchKb(true) }
    catch { ElMessage.warning('工作台资料库未就绪，暂不能上传附件'); return }
  }
  const att = reactive({ name: uploadFile.name, file_id: null, status: 'uploading' })
  attachments.value.push(att)
  try {
    await uploadLibraryFiles({ files: [uploadFile.raw], kb_ids: [workbenchKb.value.id] })
    att.status = 'processing'
    scheduleAttachPoll()
  } catch { att.status = 'failed' }
}

function scheduleAttachPoll() {
  clearAttachPoll()
  if (attachments.value.some((a) => a.status === 'processing' || a.status === 'uploading')) {
    attachTimer = setTimeout(pollAttachments, 3000)
  }
}
function clearAttachPoll() {
  if (attachTimer) { clearTimeout(attachTimer); attachTimer = null }
}

async function pollAttachments() {
  if (!workbenchKb.value?.id) return
  try {
    const res = await getKbDocuments(workbenchKb.value.id)
    const docs = Array.isArray(res) ? res : (res?.items || [])
    attachments.value.forEach((a) => {
      if (a.status !== 'processing' && a.status !== 'uploading') return
      const d = docs.find((x) => x.file_name === a.name)
      if (!d) return
      if (d.status === 'ready') { a.status = 'ready'; a.file_id = d.file_id ?? d.id }
      else if (d.status === 'failed') a.status = 'failed'
    })
  } catch { /* 下轮再试 */ }
  scheduleAttachPoll()
}

async function handleCompose() {
  await composeFormRef.value.validate()
  composing.value = true
  try {
    const data = { type: 'custom', prompt: composeForm.prompt.trim(), timeout: composeForm.timeout, language: composeForm.language }
    if (composeForm.kb_ids.length) data.kb_ids = composeForm.kb_ids
    const fileIds = attachments.value.filter((a) => a.status === 'ready' && a.file_id).map((a) => a.file_id)
    if (fileIds.length) data.file_ids = fileIds
    await generateReport(data)
    ElMessage.success('已提交生成，请稍候')
    composeDialog.value = false
    loadList()
  } finally { composing.value = false }
}

// ========== 查看 ==========
async function openView(row) {
  viewDrawer.value = true
  viewLoading.value = true
  viewingRev.value = null
  try {
    currentReport.value = await getReport(row.id)
    scheduleViewPoll()
  } finally { viewLoading.value = false }
}

let viewTimer = null
function scheduleViewPoll() {
  clearViewPoll()
  if (currentReport.value && ['generating', 'revising'].includes(currentReport.value.status)) {
    // 2s 轮询：生成中 content 增量写回，滚动展示实时进度
    viewTimer = setTimeout(refreshView, 2000)
  }
}
async function refreshView() {
  if (!currentReport.value) return
  try {
    currentReport.value = await getReport(currentReport.value.id)
    if (currentReport.value.status === 'ready') loadList()
  } catch { /* 下轮再试 */ }
  scheduleViewPoll()
}
function clearViewPoll() {
  if (viewTimer) { clearTimeout(viewTimer); viewTimer = null }
}
function closeView() {
  clearViewPoll()
  viewingRev.value = null
  reviseInput.value = ''
  viewLayout.value = 'doc'
  previewLang.value = 'both'
}

// ========== 对话式修改 ==========
const reviseInput = ref('')
const revising = ref(false)
const viewingRev = ref(null)

const isHtmlReport = computed(() => currentReport.value?.format === 'html')
const previewContent = computed(() => viewingRev.value?.content ?? currentReport.value?.content)
// 下载 PDF：文档视图仅 HTML 报告可导出 A4；演示视图导出 16:9 演示 PDF
const canDownloadPdf = computed(() =>
  (viewLayout.value === 'doc' && isHtmlReport.value) ||
  (viewLayout.value === 'slides' && hasPresentation.value)
)

// 中英双语：按 data-lang 向 srcdoc 注入 CSS，HTML 文档一键切换显示语言
const previewLang = ref('both')
const isBilingual = computed(() => currentReport.value?.params?.language === 'zh_en')
const srcdocContent = computed(() => {
  const raw = previewContent.value || ''
  if (!isBilingual.value || previewLang.value === 'both') return raw
  const hide = previewLang.value === 'zh' ? '[data-lang="en"]{display:none!important}' : '[data-lang="zh"]{display:none!important}'
  const style = `<style>${hide}</style>`
  if (/<head([^>]*)>/i.test(raw)) return raw.replace(/<head([^>]*)>/i, `<head$1>${style}`)
  if (/<body([^>]*)>/i.test(raw)) return raw.replace(/<body([^>]*)>/i, `<body$1>${style}`)
  return style + raw
})

function handleReviseEnter(e) {
  if (e.isComposing || e.keyCode === 229) return
  handleRevise()
}

async function handleRevise() {
  const instruction = reviseInput.value.trim()
  if (!instruction || !currentReport.value) return
  revising.value = true
  try {
    await reviseReport(currentReport.value.id, instruction)
    reviseInput.value = ''
    viewingRev.value = null
    currentReport.value.status = 'revising'
    ElMessage.success('已提交修改，AI 修改中…')
    scheduleViewPoll()
  } finally { revising.value = false }
}

function handleDownload() {
  const r = currentReport.value
  const content = previewContent.value
  if (!r || !content) return
  const html = r.format === 'html'
  const blob = new Blob([content], { type: html ? 'text/html;charset=utf-8' : 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${r.title || 'report'}.${html ? 'html' : 'md'}`
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const pdfLoading = ref(false)
async function handleDownloadPdf() {
  const r = currentReport.value
  if (!r || r.status !== 'ready') return
  pdfLoading.value = true
  try {
    // 演示视图导出 16:9 演示 PDF；文档视图导出 A4 正文 PDF
    const isPres = viewLayout.value === 'slides'
    const blob = isPres ? await exportReportPresentationPdf(r.id) : await exportReportPdf(r.id)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${r.title || 'report'}.pdf`
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (err) {
    let detail = ''
    try {
      const data = err?.response?.data
      if (data instanceof Blob) detail = JSON.parse(await data.text())?.detail || ''
    } catch { /* 忽略 */ }
    ElMessage.error(typeof detail === 'string' && detail ? detail : 'PDF 导出失败，请稍后重试')
  } finally { pdfLoading.value = false }
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确定删除报告「${row.title}」吗？`, '删除确认', { type: 'warning' })
  await deleteReport(row.id)
  ElMessage.success('删除成功')
  loadList()
}

onMounted(() => {
  loadList()
  loadCustomerOptions()
})
onUnmounted(() => {
  clearPolling()
  clearViewPoll()
  clearAttachPoll()
})

// 供外部（通知点击跳转）直接打开指定报告
async function openViewById(reportId) {
  await openView({ id: reportId })
}

defineExpose({ reload, openViewById })
</script>

<style scoped>
.rp {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: transparent;
}
.rp-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--app-line);
}
.rp-title {
  font-weight: 600;
  font-size: 13px;
}
.rp-actions-top {
  display: flex;
  gap: 6px;
}
.rp-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}
.rp-item {
  padding: 8px 10px;
  margin-bottom: 6px;
  border-radius: 6px;
  background: var(--app-card);
  border: 1px solid var(--app-line);
  cursor: pointer;
  transition: border-color 0.15s;
}
.rp-item:hover { border-color: var(--el-color-primary-light-5); }
.rp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-bottom: 4px;
}
.rp-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 500;
}
.rp-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  font-size: 11px;
  color: var(--app-ink-2);
}
.rp-status.st-generating, .rp-status.st-revising { color: var(--el-color-warning); }
.rp-status.st-failed { color: var(--el-color-danger); }
.rp-status.st-ready { color: var(--el-color-success); }
.rp-actions {
  margin-top: 6px;
  text-align: right;
}
.view-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; margin-bottom: 12px; }
.view-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  justify-content: space-between;
  margin-bottom: 12px;
}
.slide-deck { min-height: 55vh; display: flex; flex-direction: column; }
.pres-frame {
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #fff;
  border: 1px solid var(--app-line);
  border-radius: 10px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.06);
}
.pres-hint {
  margin-top: 10px;
  text-align: center;
  font-size: 12px;
  color: var(--app-ink-2);
}
.fs-pres {
  width: 100%;
  height: calc((100vh - 80px) / var(--app-zoom, 1));
  border: none;
  border-radius: 8px;
  background: #fff;
}
.view-body { min-height: 200px; }
.report-frame { width: 100%; height: 60vh; min-height: 320px; border: 1px solid var(--app-line); border-radius: var(--app-radius); background: #fff; }
.fs-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-right: 4px;
}
.fs-title {
  font-size: 16px;
  font-weight: 600;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fs-frame {
  width: 100%;
  height: calc((100vh - 80px) / var(--app-zoom, 1));
  border: none;
  border-radius: 8px;
  background: #fff;
}
.fs-markdown {
  padding: 24px 48px;
  max-width: 900px;
  margin: 0 auto;
}
.fs-empty {
  padding: 120px 0;
  text-align: center;
  color: var(--app-ink-2);
}
.revising-mask { display: flex; align-items: center; justify-content: center; gap: 10px; padding: 80px 0; color: var(--app-ink-2); }
.gen-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--el-color-warning);
  font-size: 13px;
  margin-bottom: 12px;
}
.form-tip {
  margin-left: 8px;
  font-size: 12px;
  color: var(--app-ink-2);
}
.attach-area { width: 100%; }
.attach-list { margin-top: 10px; }
.attach-item { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border: 1px solid var(--app-line); border-radius: 8px; margin-bottom: 6px; font-size: 13px; }
.attach-name { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.attach-status { display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--app-ink-2); flex: none; }
.attach-status.ok { color: var(--el-color-success); }
.attach-status.bad { color: var(--el-color-danger); }
.attach-tip { margin: 8px 0 0; font-size: 12px; color: var(--app-ink-2); }
.rev-list { margin-top: 16px; border-top: 1px dashed var(--app-line); padding-top: 10px; }
.rev-head { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.rev-item { display: flex; align-items: center; gap: 8px; font-size: 12px; margin-bottom: 6px; }
.rev-time { color: var(--app-ink-2); flex: none; }
.rev-text { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.revise-box { display: flex; gap: 10px; margin-top: 14px; border-top: 1px solid var(--app-line); padding-top: 12px; }
.markdown-body { line-height: 1.8; color: var(--app-ink); }
.markdown-body :deep(h1) { font-size: 20px; border-bottom: 1px solid var(--app-line); padding-bottom: 8px; }
.markdown-body :deep(p) { margin: 8px 0; }
.markdown-body :deep(th) { background: var(--app-bg); }
.markdown-body :deep(code) { background: var(--app-bg); }
</style>
