<template>
  <div class="sources-panel">
    <!-- 已关联来源（知识库与文件合并为同一列表，用图标区分类型） -->
    <div class="section">
      <div class="section-title">
        <el-icon><Collection /></el-icon>
        <span>已关联来源</span>
        <el-tag v-if="associatedKbs.length + associatedFiles.length" size="small" type="primary">
          {{ associatedKbs.length + associatedFiles.length }}
        </el-tag>
        <el-button link size="small" :icon="Plus" class="section-add" aria-label="关联知识库或文档" @click="openAssociateDialog" />
      </div>
      <div v-if="associatedKbs.length + associatedFiles.length" class="source-list">
        <div v-for="kb in associatedKbs" :key="'kb-' + kb.id" class="source-item">
          <el-checkbox :model-value="selectedKbIds.includes(kb.id)" @change="(v) => toggleKb(kb.id, v)" />
          <el-icon class="file-icon"><Collection /></el-icon>
          <span class="item-name" :title="kb.name">{{ kb.name }}</span>
          <span class="item-meta">{{ kb.doc_count || 0 }} 文档</span>
          <el-tooltip content="取消关联" placement="top">
            <el-button link size="small" :icon="Close" class="remove-btn" :aria-label="`取消关联知识库 ${kb.name}`" @click="unassociateKb(kb.id)" />
          </el-tooltip>
        </div>
        <div v-for="f in associatedFiles" :key="'file-' + f.id" class="source-item">
          <el-checkbox :model-value="selectedFileIds.includes(f.id)" @change="(v) => toggleFile(f.id, v)" />
          <el-icon class="file-icon"><component :is="fileIcon(f.file_type)" /></el-icon>
          <span class="item-name file-link" :title="f.file_name" @click="openPreview(f)">{{ f.file_name }}</span>
          <el-tooltip content="取消关联" placement="top">
            <el-button link size="small" :icon="Close" class="remove-btn" :aria-label="`取消关联文件 ${f.file_name}`" @click="unassociateFile(f.id)" />
          </el-tooltip>
        </div>
      </div>
      <div v-else class="empty-hint">
        暂无关联来源
        <el-button link type="primary" @click="openAssociateDialog">+ 关联</el-button>
      </div>
    </div>

    <!-- 关联来源对话框 -->
    <el-dialog v-model="associateDialog" title="关联知识库 / 文档" width="min(92vw, 560px)" append-to-body>
      <el-tabs v-model="associateTab">
        <el-tab-pane label="知识库" name="kbs">
          <div class="associate-list">
            <el-checkbox
              v-for="kb in allKbs"
              :key="kb.id"
              :model-value="assocKbIds.includes(kb.id)"
              @change="(v) => toggleAssocKb(kb.id, v)"
            >
              <span>{{ kb.name }}</span>
              <span class="meta">{{ kb.doc_count || 0 }} 文档</span>
            </el-checkbox>
          </div>
          <el-empty v-if="!allKbs.length" description="暂无知识库" :image-size="50" />
        </el-tab-pane>
        <el-tab-pane label="文档" name="files">
          <div class="associate-toolbar">
            <el-upload :auto-upload="false" :show-file-list="false" :on-change="handleUpload" multiple>
              <el-button size="small" type="primary" :icon="Upload" :loading="uploading">上传新文档</el-button>
            </el-upload>
          </div>
          <div class="associate-list">
            <el-checkbox
              v-for="f in allFiles"
              :key="f.id"
              :model-value="assocFileIds.includes(f.id)"
              @change="(v) => toggleAssocFile(f.id, v)"
            >
              <span class="item-name">{{ f.file_name }}</span>
              <span class="meta">{{ formatFileSize(f.file_size) }}</span>
            </el-checkbox>
          </div>
          <el-empty v-if="!allFiles.length" description="暂无文档，可先上传" :image-size="50" />
        </el-tab-pane>
      </el-tabs>
      <template #footer>
        <el-button @click="associateDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="confirmAssociate">保存关联</el-button>
      </template>
    </el-dialog>

    <!-- 文件预览（复用全局预览组件） -->
    <FilePreview v-model="previewVisible" :file="previewFile" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Collection, Plus, Close, Upload, Document, Picture, VideoCamera, Headset } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getKbs, getLibraryFiles } from '../../api'
import { uploadLibraryFiles } from '../../api/libraryUpload'
import { formatFileSize } from '../../utils/format'
import { useStudioStore } from '../../stores/studio'
import FilePreview from '../FilePreview.vue'

const studio = useStudioStore()

const allKbs = ref([])
const allFiles = ref([])
const associateDialog = ref(false)
const associateTab = ref('kbs')
const uploading = ref(false)
const saving = ref(false)
const assocKbIds = ref([])
const assocFileIds = ref([])
const previewVisible = ref(false)
const previewFile = ref(null)

function openPreview(f) {
  previewFile.value = { id: f.id, file_name: f.file_name, file_type: f.file_type, file_size: f.file_size }
  previewVisible.value = true
}

// 当前工作区关联的来源
const associatedKbs = computed(() =>
  allKbs.value.filter((k) => (studio.currentNotebook?.source_kb_ids || []).includes(k.id))
)
const associatedFiles = computed(() =>
  allFiles.value.filter((f) => (studio.currentNotebook?.source_file_ids || []).includes(f.id))
)
// 对话范围（选中态）
const selectedKbIds = computed(() => studio.selectedKbIds)
const selectedFileIds = computed(() => studio.selectedFileIds)

const fileIcon = (type) => {
  const t = (type || '').toLowerCase()
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'].includes(t)) return Picture
  if (['mp4', 'mov', 'avi'].includes(t)) return VideoCamera
  if (['mp3', 'wav', 'm4a'].includes(t)) return Headset
  return Document
}

async function loadAll() {
  try {
    const res = await getKbs()
    allKbs.value = Array.isArray(res) ? res : (res?.items || [])
  } catch { allKbs.value = [] }
  try {
    // folder_id 缺省只返回根目录文件，后端约定 "all" 表示全部文件
    const res = await getLibraryFiles({ page: 1, page_size: 200, folder_id: 'all' })
    allFiles.value = res?.items || []
  } catch { allFiles.value = [] }
}

// ========== 关联对话框 ==========
async function openAssociateDialog() {
  assocKbIds.value = [...(studio.currentNotebook?.source_kb_ids || [])]
  assocFileIds.value = [...(studio.currentNotebook?.source_file_ids || [])]
  await loadAll()
  associateDialog.value = true
}

function toggleAssocKb(id, checked) {
  assocKbIds.value = checked
    ? [...new Set([...assocKbIds.value, id])]
    : assocKbIds.value.filter((x) => x !== id)
}
function toggleAssocFile(id, checked) {
  assocFileIds.value = checked
    ? [...new Set([...assocFileIds.value, id])]
    : assocFileIds.value.filter((x) => x !== id)
}

async function confirmAssociate() {
  saving.value = true
  try {
    await studio.updateCurrentNotebook({
      source_kb_ids: assocKbIds.value,
      source_file_ids: assocFileIds.value,
    })
    associateDialog.value = false
    ElMessage.success('已保存关联')
  } catch {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

async function handleUpload(uploadFile) {
  if (uploading.value) return
  uploading.value = true
  try {
    await uploadLibraryFiles({ files: [uploadFile.raw] })
    ElMessage.success('上传完成，可勾选关联到本工作区')
    await loadAll()
  } catch (err) {
    ElMessage.error(err?.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

// ========== 选中 / 取消关联 ==========
function toggleKb(id, checked) {
  studio.setSelectedSources({
    kbIds: checked ? [...new Set([...studio.selectedKbIds, id])] : studio.selectedKbIds.filter((x) => x !== id),
  })
}
function toggleFile(id, checked) {
  studio.setSelectedSources({
    fileIds: checked ? [...new Set([...studio.selectedFileIds, id])] : studio.selectedFileIds.filter((x) => x !== id),
  })
}

async function unassociateKb(id) {
  const ids = (studio.currentNotebook?.source_kb_ids || []).filter((x) => x !== id)
  await studio.updateCurrentNotebook({ source_kb_ids: ids })
  ElMessage.success('已取消关联')
}
async function unassociateFile(id) {
  const ids = (studio.currentNotebook?.source_file_ids || []).filter((x) => x !== id)
  await studio.updateCurrentNotebook({ source_file_ids: ids })
  ElMessage.success('已取消关联')
}

onMounted(loadAll)
</script>

<style scoped>
.sources-panel {
  padding: 12px;
  overflow-y: auto;
  height: 100%;
  background: transparent;
}
.section {
  margin-bottom: 16px;
}
.section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--app-ink-1);
  margin-bottom: 10px;
}
.section-add {
  margin-left: auto;
}
.source-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.source-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--app-card);
  border: 1px solid var(--app-line);
  font-size: 13px;
}
.source-item:hover {
  border-color: var(--el-color-primary-light-5);
}
.item-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item-meta {
  color: var(--app-ink-2);
  font-size: 12px;
  flex: none;
}
.file-icon {
  color: var(--app-ink-2);
  flex: none;
}
.file-link {
  cursor: pointer;
  color: var(--el-color-primary);
}
.file-link:hover {
  text-decoration: underline;
}
.remove-btn {
  flex: none;
  color: var(--app-ink-2);
}
.remove-btn:hover {
  color: var(--el-color-danger);
}
.empty-hint {
  font-size: 12px;
  color: var(--app-ink-2);
  padding: 6px 0;
}
.associate-toolbar {
  margin-bottom: 10px;
}
.associate-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 46vh;
  overflow-y: auto;
}
.associate-list :deep(.el-checkbox) {
  margin-right: 0;
  height: auto;
}
.associate-list :deep(.el-checkbox__label) {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  white-space: normal;
}
.associate-list .meta {
  color: var(--app-ink-2);
  font-size: 12px;
  flex: none;
}
</style>
