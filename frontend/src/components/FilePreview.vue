<template>
  <el-dialog
    :model-value="modelValue"
    :fullscreen="isMobile"
    width="min(94vw, 960px)"
    top="4vh"
    class="file-preview-dialog"
    destroy-on-close
    @update:model-value="close"
    @open="load"
    @close="cleanup"
  >
    <template #header>
      <div class="fp-header">
        <span class="fp-name" :title="file?.file_name">{{ file?.file_name || '文件预览' }}</span>
        <span class="fp-size">{{ formatFileSize(file?.file_size) }}</span>
        <el-button
          size="small"
          :icon="Download"
          :disabled="!objectUrl && !rawBlob"
          @click="handleDownload"
        >下载</el-button>
        <template v-if="manageable">
          <el-button size="small" :icon="Edit" @click="handleRename">重命名</el-button>
          <el-button size="small" type="danger" :icon="Delete" @click="handleDelete">删除</el-button>
        </template>
      </div>
    </template>

    <div class="fp-body" v-loading="loading" element-loading-text="加载中…">
      <template v-if="!loading && !error">
        <!-- 图片 -->
        <div v-if="kind === 'image' || kind === 'image-png'" class="fp-center">
          <img :src="objectUrl" :alt="file?.file_name" class="fp-image" />
        </div>
        <!-- PDF -->
        <iframe v-else-if="kind === 'pdf'" :src="objectUrl" class="fp-frame" title="PDF 预览"></iframe>
        <!-- 音频 -->
        <div v-else-if="kind === 'audio'" class="fp-center">
          <audio :src="objectUrl" controls class="fp-audio"></audio>
        </div>
        <!-- 视频 -->
        <div v-else-if="kind === 'video'" class="fp-center">
          <video :src="objectUrl" controls class="fp-video"></video>
        </div>
        <!-- Markdown -->
        <div v-else-if="kind === 'markdown'" class="markdown-body fp-doc" v-html="renderMarkdown(textContent)"></div>
        <!-- HTML：blob URL（不透明源）+ sandbox 禁脚本，双保险防 XSS -->
        <iframe v-else-if="kind === 'html'" :src="objectUrl" sandbox="" class="fp-frame" title="HTML 预览"></iframe>
        <!-- Excel（xlsx 库渲染表格，多 sheet 切换） -->
        <div v-else-if="kind === 'excel'" class="fp-doc fp-excel">
          <el-tabs v-if="excelSheets.length > 1" v-model="excelActive" class="fp-excel-tabs">
            <el-tab-pane v-for="s in excelSheets" :key="s.name" :label="s.name" :name="s.name" />
          </el-tabs>
          <div class="fp-excel-table" v-html="excelHtml"></div>
        </div>
        <!-- Office 文本化预览（doc/ppt/pptx 后端提取正文） -->
        <div v-else-if="kind === 'office-text'" class="fp-doc">
          <el-alert type="info" :closable="false" title="简化文本预览，完整排版请下载查看" style="margin-bottom: 10px" />
          <pre class="fp-text">{{ officeText }}</pre>
        </div>
        <!-- 纯文本 -->
        <template v-else-if="kind === 'text'">
          <el-alert
            v-if="textTooBig"
            type="warning"
            :closable="false"
            title="文件超过 2MB，仅展示前 2MB 内容；完整内容请下载查看。"
            style="margin-bottom: 10px"
          />
          <pre class="fp-text">{{ textContent }}</pre>
        </template>
        <!-- Word -->
        <div v-else-if="kind === 'word'" class="fp-doc word-body" v-html="wordHtml"></div>
        <!-- 不支持 -->
        <div v-else class="fp-unsupported">
          <el-icon :size="44" color="var(--app-ink-2)"><Document /></el-icon>
          <p>该格式暂不支持在线预览</p>
          <p class="fp-unsupported-info">{{ file?.file_name }}（{{ formatFileSize(file?.file_size) }}）</p>
          <el-button type="primary" :icon="Download" @click="handleDownload">下载文件</el-button>
        </div>
      </template>
      <el-empty v-if="!loading && error" :description="error" />
    </div>
  </el-dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { Download, Document, Edit, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { confirmDanger } from '../utils/confirmDanger'
import * as mammoth from 'mammoth/mammoth.browser'
import * as XLSX from 'xlsx'
import {
  getLibraryFileContent, getFileContentToken, updateLibraryFile, deleteLibraryFile,
  getLibraryFilePreviewText, getLibraryFilePreviewPng,
} from '../api'
import { renderMarkdown } from '../utils/markdown'
import { previewKind, TEXT_PREVIEW_LIMIT } from '../utils/filePreview'
import { formatFileSize } from '../utils/format'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  // { id, file_name, file_type, file_size }
  file: { type: Object, default: null },
  // 可管理（重命名/删除）：文档库预览页传 true，其余只读
  manageable: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'changed'])

async function handleRename() {
  const { value } = await ElMessageBox.prompt('新文件名', '重命名', {
    inputValue: props.file?.file_name || '',
    inputPattern: /.+/,
    inputErrorMessage: '文件名不能为空',
  }).catch(() => ({ value: null }))
  if (!value || !props.file?.id) return
  try {
    await updateLibraryFile(props.file.id, { file_name: value })
    ElMessage.success('已重命名')
    emit('changed')
  } catch { /* 拦截器已提示 */ }
}

async function handleDelete() {
  if (!props.file?.id) return
  const ok = await confirmDanger(
    `确定删除文件「${props.file.file_name}」吗？文件及其在各知识库中的关联与切片将一并删除。`
  )
  if (!ok) return
  await deleteLibraryFile(props.file.id)
  ElMessage.success('已删除')
  emit('update:modelValue', false)
  emit('changed')
}

const isMobile = computed(() => window.innerWidth < 992)

const loading = ref(false)
const error = ref('')
const kind = ref('none')
const objectUrl = ref('')
const rawBlob = ref(null)
const textContent = ref('')
const textTooBig = ref(false)
const wordHtml = ref('')
const excelSheets = ref([])  // [{name, html}]
const excelActive = ref('')
const officeText = ref('')

const excelHtml = computed(() =>
  excelSheets.value.find((s) => s.name === excelActive.value)?.html || excelSheets.value[0]?.html || ''
)

function close(val) {
  emit('update:modelValue', val)
}

// 直链流式类别：先换短时效文件令牌 ?t=，浏览器原生分段加载（音视频可拖播、PDF 渐进渲染）。
// 不直接拼登录 JWT——登录 JWT 放进 URL 会被访问日志/Referer 泄露
const DIRECT_KINDS = ['image', 'pdf', 'audio', 'video']

async function directUrl(id) {
  const { token } = await getFileContentToken(id)
  return `/api/v1/library/files/${id}/content?t=${encodeURIComponent(token)}`
}

// mammoth 会保留 docx 里的链接且不过滤危险协议，预览渲染前剥掉 javascript:/data:/vbscript:
function sanitizeWordHtml(html) {
  return html.replace(/(href|src)=(["'])\s*(javascript|vbscript|data):/gi, '$1=$2#')
}

async function load() {
  if (!props.file?.id) return
  loading.value = true
  error.value = ''
  kind.value = previewKind(props.file.file_name, props.file.file_type)
  try {
    if (DIRECT_KINDS.includes(kind.value)) {
      // 直链模式：不拉 blob，交给浏览器原生流式
      objectUrl.value = await directUrl(props.file.id)
      return
    }
    // tif/tiff：后端转 PNG 后按图片预览
    if (kind.value === 'image-png') {
      const pngBlob = await getLibraryFilePreviewPng(props.file.id)
      objectUrl.value = URL.createObjectURL(pngBlob)
      return
    }
    // Office 文本化预览（doc/ppt/pptx）：后端提取正文，不拉原始文件
    if (kind.value === 'office-text') {
      const res = await getLibraryFilePreviewText(props.file.id)
      officeText.value = res.text || '（未提取到文本内容）'
      return
    }
    const blob = await getLibraryFileContent(props.file.id)
    rawBlob.value = blob
    if (kind.value === 'markdown' || kind.value === 'text') {
      if (kind.value === 'text' && blob.size > TEXT_PREVIEW_LIMIT) {
        textTooBig.value = true
        textContent.value = await blob.slice(0, TEXT_PREVIEW_LIMIT).text()
      } else {
        textTooBig.value = false
        textContent.value = await blob.text()
      }
    } else if (kind.value === 'word') {
      const arrayBuffer = await blob.arrayBuffer()
      const result = await mammoth.convertToHtml({ arrayBuffer })
      wordHtml.value = sanitizeWordHtml(result.value) || '<p>（空文档）</p>'
    } else if (kind.value === 'excel') {
      const wb = XLSX.read(await blob.arrayBuffer(), { type: 'array' })
      excelSheets.value = wb.SheetNames.map((name) => ({
        name,
        html: sanitizeWordHtml(XLSX.utils.sheet_to_html(wb.Sheets[name])),
      }))
      excelActive.value = wb.SheetNames[0] || ''
    } else if (kind.value !== 'none') {
      objectUrl.value = URL.createObjectURL(blob)
    }
  } catch (e) {
    error.value = '加载失败，请稍后重试'
    // blob 响应的错误体无法走拦截器的 detail 提取，这里兜底提示
    if (!e?.response) ElMessage.error('网络异常，加载失败')
  } finally {
    loading.value = false
  }
}

function cleanup() {
  if (objectUrl.value) {
    URL.revokeObjectURL(objectUrl.value)
    objectUrl.value = ''
  }
  rawBlob.value = null
  textContent.value = ''
  textTooBig.value = false
  wordHtml.value = ''
  excelSheets.value = []
  excelActive.value = ''
  officeText.value = ''
  error.value = ''
  kind.value = 'none'
}

async function handleDownload() {
  // 直链模式下按需拉取 blob 以下载原始文件名保存
  let blob = rawBlob.value
  if (!blob) {
    if (!props.file?.id) return
    try {
      blob = await getLibraryFileContent(props.file.id)
    } catch {
      ElMessage.error('下载失败，请稍后重试')
      return
    }
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = props.file?.file_name || 'download'
  a.click()
  // 延时释放，避免 Firefox 下下载尚未开始就回收
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
</script>

<style scoped>
.fp-header {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  margin-right: 24px;
}
.fp-name {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.fp-size {
  font-size: 12px;
  color: var(--app-ink-2);
  flex: none;
}
.fp-header .el-button {
  margin-left: auto;
  flex: none;
}
.fp-body {
  min-height: 200px;
}
.fp-center {
  display: flex;
  justify-content: center;
  align-items: flex-start;
}
.fp-image {
  max-width: 100%;
  max-height: 72vh;
  border-radius: 8px;
}
.fp-frame {
  width: 100%;
  height: 72vh;
  border: 1px solid var(--app-line);
  border-radius: 8px;
}
.fp-audio {
  width: 100%;
  max-width: 480px;
}
.fp-video {
  width: 100%;
  max-height: 72vh;
  border-radius: 8px;
  background: #000;
}
.fp-doc {
  max-height: 72vh;
  overflow-y: auto;
  padding: 4px 6px;
}
.fp-text {
  margin: 0;
  padding: 12px 14px;
  background: var(--app-bg);
  border: 1px solid var(--app-line);
  border-radius: 8px;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 72vh;
  overflow: auto;
}
.fp-unsupported {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 40px 0;
  color: var(--app-ink-2);
}
.fp-unsupported p {
  margin: 0;
}
.fp-unsupported-info {
  font-size: 13px;
}
/* Word 渲染排版 */
.word-body :deep(p) {
  margin: 8px 0;
  line-height: 1.8;
}
.word-body :deep(h1),
.word-body :deep(h2),
.word-body :deep(h3) {
  margin: 16px 0 8px;
}
.word-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 10px 0;
}
.word-body :deep(td),
.word-body :deep(th) {
  border: 1px solid var(--app-line);
  padding: 6px 10px;
}
.word-body :deep(img) {
  max-width: 100%;
}
/* Excel 表格预览 */
.fp-excel-tabs {
  margin-bottom: 4px;
}
.fp-excel-table {
  overflow: auto;
  max-height: 66vh;
}
.fp-excel-table :deep(table) {
  border-collapse: collapse;
  font-size: 13px;
}
.fp-excel-table :deep(td),
.fp-excel-table :deep(th) {
  border: 1px solid var(--app-line);
  padding: 4px 10px;
  white-space: nowrap;
}
.fp-excel-table :deep(tr:first-child td),
.fp-excel-table :deep(th) {
  background: var(--app-bg);
  font-weight: 600;
}
</style>
