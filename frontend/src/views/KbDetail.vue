<template>
  <div>
    <el-card>
      <div class="detail-head">
        <el-button :icon="ArrowLeft" @click="router.push('/knowledge')">返回</el-button>
        <span class="kb-name">{{ kb?.name || '知识库详情' }}</span>
        <el-tag v-if="kb" size="small" :type="enumTagType(kbTypeMap, kb.type)">
          {{ enumLabel(kbTypeMap, kb.type) }}
        </el-tag>
        <el-button v-if="kb" :icon="Edit" @click="openRename">重命名</el-button>
        <el-button v-if="kb" type="danger" :icon="Delete" @click="handleDeleteKb">删除</el-button>
        <el-button type="primary" :icon="Plus" style="margin-left: auto" @click="openAssociate">关联文件</el-button>
        <el-button :icon="Aim" @click="hitDialog = true">命中测试</el-button>
      </div>
      <div v-if="kb?.description" class="kb-desc">{{ kb.description }}</div>

      <el-table :data="docs" v-loading="loading" stripe style="margin-top: 12px">
        <el-table-column prop="title" label="名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="file_type" label="格式" width="90" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.status === 'processing'" type="warning" size="small">
              <el-icon class="is-loading" style="margin-right: 4px"><Loading /></el-icon>处理中
            </el-tag>
            <el-tooltip v-else-if="row.status === 'failed'" :content="row.error || '解析失败'" placement="top">
              <el-tag size="small" type="danger">失败</el-tag>
            </el-tooltip>
            <el-tag v-else size="small" :type="enumTagType(documentStatusMap, row.status)">
              {{ enumLabel(documentStatusMap, row.status) }}
            </el-tag>
            <div v-if="row.processing_method" class="method-text">
              {{ enumLabel(processingMethodMap, row.processing_method) }}
              <el-tag v-if="row.vision_reviewed_at" size="small" type="success" effect="plain" style="margin-left: 4px">已复核</el-tag>
              <el-tooltip v-if="row.vision_review_error" :content="row.vision_review_error" placement="top">
                <el-tag size="small" type="danger" effect="plain" style="margin-left: 4px">复核失败</el-tag>
              </el-tooltip>
            </div>
            <div v-if="row.status === 'failed' && row.error" class="error-text">{{ row.error }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="chunk_count" label="切片数" width="90" />
        <el-table-column label="关联时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="210" fixed="right" align="right" header-align="right">
          <template #default="{ row }">
            <el-tooltip v-if="!row.file_id" content="迁移旧数据，无文件实体，暂不支持预览" placement="top">
              <span><el-button link disabled :icon="View" /></span>
            </el-tooltip>
            <el-tooltip v-else content="预览" placement="top"><el-button link :icon="View" @click="openPreview(row)" /></el-tooltip>
            <el-tooltip v-if="row.status === 'failed' || row.status === 'unsupported'" :content="row.status === 'unsupported' ? '解析' : '重试'" placement="top">
              <el-button link type="warning" :icon="RefreshRight" @click="handleReparse(row)" />
            </el-tooltip>
            <el-tooltip v-if="row.status === 'ready' && ['ocr', 'image_describe', 'vision_ocr'].includes(row.processing_method)" :content="row.vision_reviewed_at ? '已复核，可再次复核' : '视觉复核（用视觉模型重新转录并更新切片）'" placement="top">
              <el-button link type="warning" :loading="reviewingIds.includes(row.id)" :icon="MagicStick" @click="handleVisionReview(row)" />
            </el-tooltip>
            <el-tooltip content="切片" placement="top"><el-button link :icon="List" @click="openChunks(row)" /></el-tooltip>
            <el-tooltip content="版本" placement="top"><el-button link :icon="Clock" @click="openVersions(row)" /></el-tooltip>
            <el-tooltip content="移除" placement="top"><el-button link type="danger" :icon="Delete" @click="handleRemove(row)" /></el-tooltip>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !docs.length" description="暂无文档，点击右上角关联文件" :image-size="80" />
    </el-card>

    <!-- 关联文件对话框：文件夹树筛选 + 文件多选 + 搜索 -->
    <el-dialog v-model="assocDialog" title="从文档库选择文件" width="min(92vw, 760px)">
      <div class="assoc-body">
        <div class="assoc-tree">
          <div
            class="tree-node"
            :class="{ active: assocQuery.folder_id === 'all' }"
            @click="selectFolder('all')"
          >全部文件</div>
          <el-tree
            :data="folderTree"
            node-key="id"
            :props="{ label: 'name', children: 'children' }"
            highlight-current
            :expand-on-click-node="false"
            @node-click="(n) => selectFolder(n.id)"
          />
        </div>
        <div class="assoc-files">
          <el-input
            v-model="assocQuery.keyword"
            placeholder="搜索文件名"
            clearable
            size="small"
            style="margin-bottom: 10px"
            @keyup.enter="loadAssocFiles"
            @clear="loadAssocFiles"
          >
            <template #append>
              <el-button :icon="Search" @click="loadAssocFiles" />
            </template>
          </el-input>
          <el-table
            :data="assocFiles"
            v-loading="assocLoading"
            size="small"
            height="320"
            @selection-change="(rows) => (selectedFiles = rows)"
          >
            <el-table-column type="selection" width="40" />
            <el-table-column prop="file_name" label="文件名" min-width="180" show-overflow-tooltip />
            <el-table-column prop="file_type" label="格式" width="80" />
            <el-table-column label="大小" width="90">
              <template #default="{ row }">{{ formatFileSize(row.file_size) }}</template>
            </el-table-column>
          </el-table>
          <el-pagination
            class="assoc-pager"
            v-model:current-page="assocQuery.page"
            :page-size="assocQuery.page_size"
            :total="assocTotal"
            layout="total, prev, pager, next"
            small
            @current-change="loadAssocFiles"
          />
        </div>
      </div>
      <template #footer>
        <span class="assoc-count">已选 {{ selectedFiles.length }} 个文件</span>
        <el-button @click="assocDialog = false">取消</el-button>
        <el-button type="primary" :loading="associating" :disabled="!selectedFiles.length" @click="handleAssociate">
          关联到知识库
        </el-button>
      </template>
    </el-dialog>

    <!-- 切片抽屉 -->
    <el-drawer v-model="chunkDrawer" :title="`切片列表 - ${currentDoc?.title || ''}`" size="min(92vw, 720px)">
      <div v-loading="chunkLoading">
        <el-card v-for="c in chunks" :key="c.id" class="chunk-card" shadow="never">
          <div class="chunk-index">#{{ c.chunk_index }}</div>
          <div class="chunk-content">{{ c.content }}</div>
        </el-card>
        <el-empty v-if="!chunkLoading && !chunks.length" description="暂无切片" :image-size="80" />
      </div>
    </el-drawer>

    <!-- 命中测试 -->
    <el-dialog v-model="hitDialog" title="命中测试" width="min(92vw, 680px)">
      <el-input v-model="hitForm.query" type="textarea" :rows="2" placeholder="输入测试问题" />
      <div class="hit-params">
        <span class="hit-label">返回条数</span>
        <el-input-number v-model="hitForm.top_k" :min="1" :max="20" style="width: 130px" />
        <span class="hit-label">得分阈值</span>
        <el-input-number v-model="hitForm.threshold" :min="0" :max="1" :step="0.05" :precision="2" style="width: 130px" placeholder="留空不限制" />
        <el-button type="primary" :loading="hitLoading" @click="runHitTest">测试</el-button>
      </div>
      <template v-if="hitResult">
        <el-alert
          v-if="hitResult.degraded"
          type="warning"
          :closable="false"
          show-icon
          style="margin-top: 14px"
          :title="hitResult.message || '嵌入模型未配置，已降级为全文检索'"
        />
        <el-empty v-if="!hitResult.hits?.length" description="无命中结果" :image-size="60" />
        <el-card v-for="h in hitResult.hits" :key="h.chunk_id" class="hit-card" shadow="never">
          <div class="hit-head">
            <span class="hit-doc">{{ h.doc_name }}</span>
            <el-tag size="small" :type="hitMethodMap[h.hit_method]?.type || 'info'" effect="plain">
              {{ hitMethodMap[h.hit_method]?.label || h.hit_method }}
            </el-tag>
            <span class="hit-score">得分 {{ formatScore(h.score) }}</span>
          </div>
          <div class="hit-content">{{ h.content }}</div>
        </el-card>
      </template>
    </el-dialog>

    <!-- 版本历史 -->
    <el-dialog v-model="versionsDialog" :title="`版本历史 - ${versionsDoc?.title || ''}`" width="min(92vw, 680px)">
      <div v-loading="versionsLoading" class="version-list">
        <div v-if="!versionsList.length" class="version-empty">暂无历史版本（重解析文档后会自动生成快照）</div>
        <div v-for="v in versionsList" :key="v.id" class="version-item">
          <div class="version-meta">
            <span class="version-time">{{ formatDateTime(v.created_at) }}</span>
            <el-tag size="small" type="info">{{ v.chunk_count }} 切片</el-tag>
          </div>
          <div class="version-preview">{{ v.preview }}</div>
          <div class="version-actions">
            <el-button link type="primary" size="small" @click="viewVersion(v)">查看全文</el-button>
            <el-button link type="warning" size="small" @click="restoreVersion(v)">恢复此版本</el-button>
          </div>
        </div>
      </div>
    </el-dialog>

    <!-- 版本全文 -->
    <el-drawer v-model="versionContentDrawer" title="版本内容" size="min(92vw, 720px)">
      <pre class="version-content">{{ versionContent }}</pre>
    </el-drawer>

    <!-- 文件预览（content 接口按 library file id 取） -->
    <FilePreview v-model="previewVisible" :file="previewFile" />
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Plus, Search, Loading, Aim, Edit, Delete, View, RefreshRight, MagicStick, List, Clock } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getKbs, getKbDocuments, associateKbDocuments, removeKbDocument, reparseKbDocument, visionReviewKbDocument, getKbDocumentChunks,
  getLibraryTree, getLibraryFiles, hitTestKb, getDocVersions, getDocVersion, restoreDocVersion,
  updateKb, deleteKb,
} from '../api'
import { kbTypeMap, documentStatusMap, processingMethodMap, enumLabel, enumTagType, formatDateTime, formatFileSize } from '../utils/format'
import { useThemeStore } from '../stores/theme'
import FilePreview from '../components/FilePreview.vue'

// ========== 在线预览（按 file_id 调 content 接口） ==========
const previewVisible = ref(false)
const previewFile = ref(null)
function openPreview(row) {
  previewFile.value = { id: row.file_id, file_name: row.file_name || row.title, file_type: row.file_type }
  previewVisible.value = true
}

const route = useRoute()
const router = useRouter()
const kbId = route.params.id

const kb = ref(null)
const docs = ref([])
const loading = ref(false)
let pollTimer = null

async function loadKb() {
  try {
    const res = await getKbs()
    const list = Array.isArray(res) ? res : (res?.items || [])
    kb.value = list.find((k) => String(k.id) === String(kbId)) || null
  } catch {
    /* 拦截器已提示 */
  }
}

async function openRename() {
  const { value } = await ElMessageBox.prompt('新名称', '重命名知识库', {
    inputValue: kb.value?.name || '',
    inputPattern: /.+/,
    inputErrorMessage: '名称不能为空',
  }).catch(() => ({ value: null }))
  if (!value) return
  await updateKb(kbId, { name: value })
  ElMessage.success('已重命名')
  loadKb()
}

async function handleDeleteKb() {
  await ElMessageBox.confirm(
    `确定删除知识库「${kb.value?.name}」吗？其中的文档关联与切片将一并删除，但不会删除文档库中的文件。`,
    '删除确认',
    { type: 'warning', confirmButtonText: '删除' }
  )
  await deleteKb(kbId)
  ElMessage.success('已删除')
  router.push('/knowledge')
}

async function loadDocs() {
  loading.value = true
  try {
    const res = await getKbDocuments(kbId)
    docs.value = Array.isArray(res) ? res : (res?.items || [])
    schedulePolling()
  } finally {
    loading.value = false
  }
}

// 存在 processing 文档时每 3 秒轮询
function schedulePolling() {
  clearPolling()
  if (docs.value.some((d) => d.status === 'processing')) {
    pollTimer = setTimeout(loadDocs, 3000)
  }
}

function clearPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

// ========== 关联文件 ==========
const assocDialog = ref(false)
const assocLoading = ref(false)
const associating = ref(false)
const folderTree = ref([])
const assocFiles = ref([])
const assocTotal = ref(0)
const selectedFiles = ref([])
const themeStore = useThemeStore()
const assocQuery = reactive({ folder_id: 'all', keyword: '', page: 1, page_size: themeStore.pageSize })

async function openAssociate() {
  assocDialog.value = true
  assocQuery.folder_id = 'all'
  assocQuery.keyword = ''
  assocQuery.page = 1
  selectedFiles.value = []
  if (!folderTree.value.length) {
    try {
      const res = await getLibraryTree()
      folderTree.value = Array.isArray(res) ? res : (res?.children || [])
    } catch {
      /* 树加载失败仍可按全部文件筛选 */
    }
  }
  loadAssocFiles()
}

function selectFolder(id) {
  assocQuery.folder_id = id
  assocQuery.page = 1
  loadAssocFiles()
}

async function loadAssocFiles() {
  assocLoading.value = true
  try {
    const params = { page: assocQuery.page, page_size: assocQuery.page_size }
    if (assocQuery.folder_id) params.folder_id = assocQuery.folder_id
    if (assocQuery.keyword) params.keyword = assocQuery.keyword
    const res = await getLibraryFiles(params)
    assocFiles.value = res.items || []
    assocTotal.value = res.total || 0
  } finally {
    assocLoading.value = false
  }
}

async function handleAssociate() {
  associating.value = true
  try {
    const res = await associateKbDocuments(kbId, selectedFiles.value.map((f) => f.id))
    ElMessage.success(`关联完成：新增 ${res?.associated ?? selectedFiles.value.length} 个，已存在 ${res?.already ?? 0} 个`)
    assocDialog.value = false
    loadDocs()
  } finally {
    associating.value = false
  }
}

// ========== 版本历史 ==========
const versionsDialog = ref(false)
const versionsLoading = ref(false)
const versionsList = ref([])
const versionsDoc = ref(null)
const versionContentDrawer = ref(false)
const versionContent = ref('')

async function openVersions(row) {
  versionsDoc.value = row
  versionsDialog.value = true
  versionsLoading.value = true
  try {
    const res = await getDocVersions(kbId, row.id)
    versionsList.value = Array.isArray(res) ? res : []
  } finally {
    versionsLoading.value = false
  }
}

async function viewVersion(v) {
  try {
    const res = await getDocVersion(kbId, versionsDoc.value.id, v.id)
    versionContent.value = res?.content || ''
    versionContentDrawer.value = true
  } catch { /* 拦截器已提示 */ }
}

async function restoreVersion(v) {
  await ElMessageBox.confirm(
    '确定把该文档恢复到选中的历史版本吗？将用此版本内容重新解析并替换当前切片。',
    '恢复版本确认',
    { type: 'warning', confirmButtonText: '恢复' }
  )
  await restoreDocVersion(kbId, versionsDoc.value.id, v.id)
  ElMessage.success('已提交恢复，后台解析中')
  versionsDialog.value = false
  loadDocs()
}

// ========== 移除 / 切片 ==========
async function handleRemove(row) {
  await ElMessageBox.confirm(
    `确定将「${row.title}」从本知识库移除吗？仅移除关联与切片，不删除文档库文件。`,
    '移除确认',
    { type: 'warning' }
  )
  await removeKbDocument(kbId, row.id)
  ElMessage.success('已移除')
  loadDocs()
}

async function handleReparse(row) {
  await reparseKbDocument(kbId, row.id)
  ElMessage.success('已重新排队解析')
  loadDocs()
}

// 视觉复核：本地 OCR 识别质量差时，用视觉模型重新转录并更新切片（后台执行）
const reviewingIds = ref([])
async function handleVisionReview(row) {
  if (reviewingIds.value.includes(row.id)) return
  reviewingIds.value.push(row.id)
  try {
    await visionReviewKbDocument(kbId, row.id)
    ElMessage.info('已提交视觉复核，完成后切片将自动更新')
    // 后台任务完成需一定时间，轮询刷新直到方法变为视觉识别
    const deadline = Date.now() + 60000
    const tick = setInterval(async () => {
      const found = docs.value.find((d) => d.id === row.id)
      if (found && (found.processing_method !== 'ocr' || found.vision_reviewed_at || Date.now() > deadline)) {
        clearInterval(tick)
        reviewingIds.value = reviewingIds.value.filter((x) => x !== row.id)
        loadDocs()
        if (found.processing_method !== 'ocr' || found.vision_reviewed_at) {
          ElMessage.success('视觉复核完成，切片已更新')
        }
      }
    }, 5000)
  } catch {
    reviewingIds.value = reviewingIds.value.filter((x) => x !== row.id)
  }
}

const chunkDrawer = ref(false)
const chunkLoading = ref(false)
const chunks = ref([])
const currentDoc = ref(null)

async function openChunks(row) {
  currentDoc.value = row
  chunkDrawer.value = true
  chunkLoading.value = true
  try {
    const res = await getKbDocumentChunks(kbId, row.id)
    chunks.value = Array.isArray(res) ? res : (res?.items || [])
  } finally {
    chunkLoading.value = false
  }
}

// ========== 命中测试 ==========
const hitDialog = ref(false)
const hitLoading = ref(false)
const hitForm = reactive({ query: '', top_k: 5, threshold: null })
const hitResult = ref(null)
const hitMethodMap = {
  vector: { label: '向量', type: 'primary' },
  trgm: { label: '全文', type: 'warning' },
  both: { label: '混合', type: 'success' },
}

function formatScore(s) {
  return s === null || s === undefined ? '-' : Number(s).toFixed(3)
}

async function runHitTest() {
  if (!hitForm.query.trim()) {
    ElMessage.warning('请输入测试问题')
    return
  }
  hitLoading.value = true
  try {
    const data = { query: hitForm.query.trim(), top_k: hitForm.top_k }
    if (hitForm.threshold !== null && hitForm.threshold !== undefined && hitForm.threshold !== '') {
      data.threshold = hitForm.threshold
    }
    hitResult.value = await hitTestKb(kbId, data)
  } finally {
    hitLoading.value = false
  }
}

onMounted(() => {
  loadKb()
  loadDocs()
})
onUnmounted(clearPolling)
</script>

<style scoped>
.detail-head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.kb-name {
  font-size: 16px;
  font-weight: 600;
}
.kb-desc {
  margin-top: 10px;
  color: var(--app-ink-2);
  font-size: 13px;
}
.method-text {
  margin-top: 4px;
  font-size: 12px;
  color: var(--app-ink-2);
}
.error-text {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-color-danger);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
}
.assoc-body {
  display: flex;
  gap: 16px;
  min-height: 380px;
}
.assoc-tree {
  width: 200px;
  flex: none;
  border-right: 1px solid var(--app-line);
  padding-right: 12px;
  overflow-y: auto;
  max-height: 420px;
}
.tree-node {
  padding: 7px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  margin-bottom: 4px;
}
.tree-node:hover {
  background: var(--app-bg);
}
.tree-node.active {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.assoc-files {
  flex: 1;
  min-width: 0;
}
.assoc-pager {
  margin-top: 10px;
  justify-content: flex-end;
}
.assoc-count {
  float: left;
  font-size: 13px;
  color: var(--app-ink-2);
  line-height: 32px;
}
.chunk-card {
  margin-bottom: 12px;
}
.chunk-index {
  font-size: 12px;
  color: var(--el-color-primary);
  margin-bottom: 4px;
}
.chunk-content {
  white-space: pre-wrap;
  line-height: 1.6;
}
.hit-params {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.hit-label {
  font-size: 13px;
  color: var(--app-ink-2);
}
.hit-card {
  margin-top: 12px;
}
.hit-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.hit-doc {
  font-size: 13px;
  font-weight: 600;
}
.hit-score {
  font-size: 12px;
  color: var(--app-ink-2);
}
.hit-content {
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
}

@media (max-width: 767px) {
  .assoc-body {
    flex-direction: column;
    min-height: 0;
  }
  .assoc-tree {
    width: 100%;
    border-right: none;
    border-bottom: 1px solid var(--app-line);
    padding: 0 0 10px;
    max-height: 160px;
  }
}
.version-list { display: flex; flex-direction: column; gap: 10px; min-height: 60px; }
.version-item {
  padding: 10px 12px;
  border: 1px solid var(--app-line);
  border-radius: 8px;
  background: var(--app-bg);
}
.version-meta { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.version-time { color: var(--app-ink-2); }
.version-preview {
  margin: 6px 0;
  font-size: 12px;
  color: var(--app-ink-2);
  white-space: pre-wrap;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.version-actions { text-align: right; }
.version-empty { color: var(--app-ink-2); font-size: 13px; padding: 20px 0; text-align: center; }
.version-content {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.7;
  color: var(--app-ink);
}
</style>
