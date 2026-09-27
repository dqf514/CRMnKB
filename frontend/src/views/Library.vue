<template>
  <div class="library-page">
    <!-- 桌面端文件夹树 -->
    <aside v-if="!isMobile" class="folder-pane">
      <div class="folder-head">
        <span>文件夹</span>
        <el-button link type="primary" size="small" :icon="Plus" @click="openFolderForm()">新建</el-button>
      </div>
      <div
        class="tree-node-all"
        :class="{ active: currentFolderId === 'all' }"
        @click="selectFolder('all')"
      >全部文件</div>
      <el-tree
        ref="treeRef"
        :data="folderTree"
        node-key="id"
        :props="{ label: 'name', children: 'children' }"
        highlight-current
        :expand-on-click-node="false"
        default-expand-all
        @node-click="(n) => selectFolder(n.id)"
      />
    </aside>

    <!-- 移动端文件夹抽屉 -->
    <el-drawer v-else v-model="treeDrawer" direction="ltr" size="240px" :with-header="false">
      <div class="folder-head">
        <span>文件夹</span>
        <el-button link type="primary" size="small" :icon="Plus" @click="openFolderForm(); treeDrawer = false">新建</el-button>
      </div>
      <div
        class="tree-node-all"
        :class="{ active: currentFolderId === 'all' }"
        @click="selectFolder('all'); treeDrawer = false"
      >全部文件</div>
      <el-tree
        :data="folderTree"
        node-key="id"
        :props="{ label: 'name', children: 'children' }"
        highlight-current
        default-expand-all
        @node-click="(n) => { selectFolder(n.id); treeDrawer = false }"
      />
    </el-drawer>

    <!-- 文件列表 -->
    <el-card class="file-pane">
      <el-tabs v-model="scope" class="scope-tabs">
        <el-tab-pane label="全部" name="all" />
        <el-tab-pane label="我的" name="mine" />
        <el-tab-pane label="共享给我的" name="shared" />
        <el-tab-pane label="团队" name="team" />
      </el-tabs>
      <div class="toolbar">
        <el-icon v-if="isMobile" :size="18" class="tree-toggle" @click="treeDrawer = true"><FolderOpened /></el-icon>
        <el-button v-if="currentFolderId !== 'all'" size="small" :icon="Back" @click="goParent">返回上级</el-button>
        <span class="current-path">{{ currentPathText }}</span>
        <el-input
          v-model="query.keyword"
          placeholder="搜索文件名"
          size="small"
          clearable
          class="search-input"
          @keyup.enter="handleSearch"
          @clear="handleSearch"
        >
          <template #append>
            <el-button :icon="Search" @click="handleSearch" />
          </template>
        </el-input>
        <div class="toolbar-actions">
          <el-button type="primary" size="small" :icon="Upload" @click="openUpload('files')">上传文件</el-button>
          <el-button size="small" :icon="FolderAdd" @click="openUpload('folder')">上传文件夹</el-button>
        </div>
      </div>

      <!-- 当前文件夹操作（仅 owner 可管理/共享） -->
      <div v-if="currentFolder && currentFolderId !== 'all'" class="folder-ops">
        <span class="folder-ops-name">当前文件夹：{{ currentFolder.name }}</span>
        <el-button v-if="currentFolder.perm === 'owner'" link type="primary" size="small" :icon="Share" @click="openShareFolder">共享</el-button>
        <el-button v-if="currentFolder.perm === 'owner'" link type="primary" size="small" @click="openFolderForm(currentFolder)">重命名</el-button>
        <el-button v-if="currentFolder.perm === 'owner'" link type="danger" size="small" @click="handleDeleteFolder">删除文件夹</el-button>
        <el-tag v-else-if="currentFolder.perm === 'edit'" size="small" type="warning">共享可编辑</el-tag>
        <el-tag v-else-if="!currentFolder.perm" size="small" type="info">仅浏览</el-tag>
      </div>

      <!-- 批量操作栏 -->
      <div v-if="selectedFiles.length" class="batch-bar">
        <span class="batch-count">已选 {{ selectedFiles.length }} 项</span>
        <el-button size="small" type="warning" :icon="Share" @click="openBatchShare">批量共享</el-button>
        <el-button size="small" :icon="FolderOpened" @click="openBatchMove">批量移动</el-button>
        <el-button size="small" :icon="Collection" @click="openAssociate(selectedFiles)">批量关联知识库</el-button>
        <el-button size="small" type="danger" :icon="Delete" @click="handleBatchDelete">批量删除</el-button>
        <el-button link size="small" @click="clearSelection">取消选择</el-button>
      </div>

      <el-table :data="filteredFiles" v-loading="loading" stripe @selection-change="onSelectionChange">
        <el-table-column type="selection" width="44" />
        <el-table-column label="名称" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="file-name" @click="openPreview(row)">
              <el-icon :size="16" :color="fileIconColor(row)"><component :is="fileIcon(row)" /></el-icon>
              <el-link type="primary" :underline="false">{{ row.file_name }}</el-link>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="90">
          <template #default="{ row }">{{ formatFileSize(row.file_size) }}</template>
        </el-table-column>
        <el-table-column label="解析" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.supported ? 'success' : 'info'">
              {{ row.supported ? '可解析' : '仅存储' }}
            </el-tag>
            <div v-if="row.processing_method" class="method-text">
              {{ enumLabel(processingMethodMap, row.processing_method) }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="知识库" width="80">
          <template #default="{ row }">
            <el-tooltip :content="`已关联 ${row.kb_count || 0} 个知识库`" placement="top">
              <span class="kb-count-num">{{ row.kb_count || 0 }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="所属客户" width="110" show-overflow-tooltip>
          <template #default="{ row }">{{ row.customer_name || (row.customer_id ? `#${row.customer_id}` : '-') }}</template>
        </el-table-column>
        <el-table-column label="上传时间" width="150">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right" align="right" header-align="right">
          <template #default="{ row }">
            <el-tooltip content="预览 / 详情" placement="top"><el-button link :icon="View" @click="openPreview(row)" /></el-tooltip>
            <el-tooltip v-if="row.perm === 'owner'" content="共享" placement="top"><el-button link :icon="Share" @click="openShare(row)" /></el-tooltip>
            <el-tooltip content="移动" placement="top"><el-button link :icon="FolderOpened" @click="openMove(row)" /></el-tooltip>
            <el-tooltip content="关联知识库" placement="top"><el-button link :icon="Collection" @click="openAssociate([row])" /></el-tooltip>
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
        @current-change="loadFiles"
      />
    </el-card>

    <!-- 上传对话框（文件 / 文件夹共用） -->
    <el-dialog v-model="uploadDialog" :title="uploadMode === 'folder' ? '上传文件夹' : '上传文件'" width="min(90vw, 560px)" :close-on-click-modal="false">
      <template v-if="uploadMode === 'files'">
        <el-upload
          ref="uploadRef"
          :auto-upload="false"
          multiple
          drag
          :accept="acceptStr"
          :on-change="handleFileChange"
          :on-remove="handleFileRemove"
        >
          <el-icon size="40" style="color: var(--app-ink-2)"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖拽文件到此处，或 <em>点击选择</em>（可多选）</div>
        </el-upload>
        <p class="upload-tip">仅支持当前启用解析的格式（{{ acceptStr || '全部' }}），其他格式无法选择；图片与音视频将由 AI 自动识别转写后入库。</p>
      </template>
      <template v-else>
        <el-button :icon="FolderOpened" @click="folderInputRef?.click()">选择文件夹</el-button>
        <input
          ref="folderInputRef"
          type="file"
          webkitdirectory
          style="display: none"
          @change="handleFolderPick"
        />
        <el-alert v-if="!uploadFiles.length" type="info" :closable="false" title="选择后将上传文件夹内全部文件（自动排除不支持解析的格式），并自动重建目录结构。" style="margin-top: 10px" />
      </template>
      <div v-if="uploadStats.count" class="picked-count">
        已选 {{ uploadStats.count }} 个文件（共 {{ uploadTotalSize }}）
        <span v-if="uploadMode === 'folder'">，保留目录结构</span>
        <el-tag v-if="uploadStats.tooLarge" size="small" type="warning" effect="light" style="margin-left: 6px">
          {{ uploadStats.tooLarge }} 个超过 100MB 上限将自动跳过
        </el-tag>
      </div>

      <el-form label-width="100px" style="margin-top: 16px">
        <el-form-item label="关联知识库">
          <el-select v-model="uploadKbs" multiple placeholder="可选，上传后自动关联并解析" style="width: 100%">
            <el-option v-for="k in kbs" :key="k.id" :label="k.name" :value="k.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="所属客户">
          <el-select v-model="uploadCustomer" clearable filterable placeholder="可选" style="width: 100%">
            <el-option v-for="c in customers" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="uploadDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!uploadFiles.length" @click="handleUpload">上传</el-button>
      </template>
    </el-dialog>

    <!-- 新建 / 重命名文件夹 -->
    <el-dialog v-model="folderDialog" :title="folderForm.id ? '重命名文件夹' : '新建文件夹'" width="min(90vw, 420px)">
      <el-input v-model="folderForm.name" placeholder="文件夹名称" @keyup.enter="handleSaveFolder" />
      <template #footer>
        <el-button @click="folderDialog = false">取消</el-button>
        <el-button type="primary" :loading="folderSaving" @click="handleSaveFolder">保存</el-button>
      </template>
    </el-dialog>

    <!-- 重命名文件 -->
    <el-dialog v-model="renameDialog" title="重命名文件" width="min(90vw, 420px)">
      <el-input v-model="renameName" placeholder="新文件名" @keyup.enter="handleRename" />
      <template #footer>
        <el-button @click="renameDialog = false">取消</el-button>
        <el-button type="primary" :loading="renaming" @click="handleRename">保存</el-button>
      </template>
    </el-dialog>

    <!-- 移动文件 -->
    <el-dialog v-model="moveDialog" :title="`移动 ${moveFiles.length} 个文件到`" width="min(90vw, 420px)">
      <el-tree
        :data="folderTree"
        node-key="id"
        :props="{ label: 'name', children: 'children' }"
        highlight-current
        default-expand-all
        :expand-on-click-node="false"
        @node-click="(n) => (moveTarget = n.id)"
      />
      <template #footer>
        <el-button @click="moveDialog = false">取消</el-button>
        <el-button type="primary" :loading="moving" :disabled="!moveTarget" @click="handleMove">移动</el-button>
      </template>
    </el-dialog>

    <!-- 关联知识库 -->
    <el-dialog v-model="assocDialog" title="关联知识库" width="min(90vw, 480px)">
      <p class="assoc-tip">将所选 {{ assocFiles.length }} 个文件关联到以下知识库（支持格式将自动解析）：</p>
      <el-select v-model="assocKbs" multiple placeholder="选择知识库" style="width: 100%">
        <el-option v-for="k in kbs" :key="k.id" :label="k.name" :value="k.id" />
      </el-select>
      <template #footer>
        <el-button @click="assocDialog = false">取消</el-button>
        <el-button type="primary" :loading="associating" :disabled="!assocKbs.length" @click="handleAssociate">关联</el-button>
      </template>
    </el-dialog>

    <!-- 批量共享 -->
    <el-dialog v-model="batchShareDialog" title="批量共享文件" width="min(90vw, 460px)">
      <p class="assoc-tip">将把选中的 {{ selectedFiles.length }} 个文件分享给：</p>
      <div class="batch-share-row">
        <el-select v-model="batchShareUser" placeholder="选择用户" filterable style="flex: 1">
          <el-option v-for="u in users" :key="u.id" :label="`${u.name || u.username} (${u.username})`" :value="u.id" />
        </el-select>
        <el-select v-model="batchSharePerm" style="width: 110px">
          <el-option v-for="(l, k) in PERM_LABELS" :key="k" :label="l" :value="k" />
        </el-select>
      </div>
      <template #footer>
        <el-button @click="batchShareDialog = false">取消</el-button>
        <el-button type="primary" :loading="batchSharing" :disabled="!batchShareUser" @click="handleBatchShare">共享</el-button>
      </template>
    </el-dialog>

    <!-- 文件预览 -->
    <FilePreview v-model="previewVisible" :file="previewFile" manageable @changed="loadFiles" />

    <!-- 文件分享 -->
    <ShareDialog v-model="shareDialog" :resource-type="shareType" :resource="shareTarget" @changed="onShareChanged" />
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import ShareDialog from '../components/ShareDialog.vue'
import { useThemeStore } from '../stores/theme'
import {
  Plus, Search, Upload, UploadFilled, FolderAdd, FolderOpened, Back, Collection,
  Document, Picture, Headset, Box, Files, Share, Delete, View,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getLibraryTree, createLibraryFolder, updateLibraryFolder, deleteLibraryFolder,
  getLibraryFiles, updateLibraryFile, deleteLibraryFile, associateLibraryFiles,
  getKbs, getCustomers, getUsers, batchShareFiles,
} from '../api'
import { useUploadsStore } from '../stores/uploads'
import { formatDateTime, formatFileSize, enumLabel, processingMethodMap } from '../utils/format'
import { ensureUploadFormats, isEnabledExt, enabledAcceptStr } from '../utils/uploadFormats'
import FilePreview from '../components/FilePreview.vue'

// ========== 在线预览 ==========
const previewVisible = ref(false)
const previewFile = ref(null)
function openPreview(row) {
  previewFile.value = row
  previewVisible.value = true
}

// ========== 响应式 ==========
const isMobile = ref(window.innerWidth < 992)
const treeDrawer = ref(false)
function onResize() {
  isMobile.value = window.innerWidth < 992
  if (!isMobile.value) treeDrawer.value = false
}

// ========== 文件夹树 ==========
const treeRef = ref()
const folderTree = ref([])
const currentFolderId = ref('all') // null=根目录, 'all'=全部, 数字=文件夹

async function loadTree(keepCurrent = true) {
  const res = await getLibraryTree()
  folderTree.value = Array.isArray(res) ? res : (res?.children || [])
  if (keepCurrent && currentFolderId.value !== 'all' && currentFolderId.value !== null) {
    treeRef.value?.setCurrentKey(currentFolderId.value)
  }
}

// 在树中查找节点及其父链
function findNode(nodes, id, trail = []) {
  for (const n of nodes) {
    if (String(n.id) === String(id)) return { node: n, trail: [...trail, n] }
    if (n.children?.length) {
      const r = findNode(n.children, id, [...trail, n])
      if (r) return r
    }
  }
  return null
}

const currentFolder = computed(() => {
  if (currentFolderId.value === 'all' || currentFolderId.value === null) return null
  return findNode(folderTree.value, currentFolderId.value)?.node || null
})

const currentPathText = computed(() => {
  if (currentFolderId.value === 'all') return '全部文件'
  if (currentFolderId.value === null) return '根目录'
  const r = findNode(folderTree.value, currentFolderId.value)
  return r ? r.trail.map((n) => n.name).join(' / ') : ''
})

function selectFolder(id) {
  currentFolderId.value = id
  query.page = 1
  loadFiles()
}

function goParent() {
  if (currentFolderId.value === 'all') return
  if (currentFolderId.value === null) return
  const r = findNode(folderTree.value, currentFolderId.value)
  const parentTrail = r?.trail || []
  currentFolderId.value = parentTrail.length > 1 ? parentTrail[parentTrail.length - 2].id : null
  query.page = 1
  loadFiles()
}

// ========== 文件列表 ==========
const loading = ref(false)
const files = ref([])
const total = ref(0)
const themeStore = useThemeStore()
const query = reactive({ keyword: '', page: 1, page_size: themeStore.pageSize })
const scope = ref('all')

// 页签过滤：我的/共享给我的/团队
const filteredFiles = computed(() => {
  if (scope.value === 'all') return files.value
  return files.value.filter((f) => {
    if (scope.value === 'mine') return f.perm === 'owner'
    // 团队 tab = 所有团队可见的内容（含本人开的团队共享），is_private 为 null/false 都算团队可见
    if (scope.value === 'team') return f.is_private !== true
    return f.perm && f.perm !== 'owner' && f.is_private !== false
  })
})

const shareDialog = ref(false)
const shareTarget = ref(null)
const shareType = ref('file')
function openShare(file) {
  shareType.value = 'file'
  shareTarget.value = { id: file.id, name: file.file_name, owner_id: file.owner_id, is_private: file.is_private, perm: file.perm }
  shareDialog.value = true
}

// 文件夹级共享：与文件同一套分享对话框（resource-type=folder）
function openShareFolder() {
  const f = currentFolder.value
  if (!f) return
  shareType.value = 'folder'
  shareTarget.value = { id: f.id, name: f.name, owner_id: f.owner_id, is_private: f.is_private, perm: f.perm }
  shareDialog.value = true
}

function onShareChanged() {
  loadFiles()
  loadTree()
}

function onSizeChange() {
  query.page = 1
  loadFiles()
}

async function loadFiles() {
  loading.value = true
  try {
    const params = { page: query.page, page_size: query.page_size }
    if (currentFolderId.value !== null) params.folder_id = currentFolderId.value
    if (query.keyword) params.keyword = query.keyword
    const res = await getLibraryFiles(params)
    files.value = res.items || []
    total.value = res.total || 0
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  query.page = 1
  loadFiles()
}

// 文件格式图标
const ICONS = {
  pdf: Document, docx: Document, doc: Document, txt: Files, md: Files,
  png: Picture, jpg: Picture, jpeg: Picture, gif: Picture, webp: Picture,
  mp3: Headset, wav: Headset, m4a: Headset,
  zip: Box, rar: Box, '7z': Box,
}
function fileExt(row) {
  return (row.file_type || row.file_name?.split('.').pop() || '').toLowerCase()
}
function fileIcon(row) {
  return ICONS[fileExt(row)] || Document
}
function fileIconColor(row) {
  const ext = fileExt(row)
  if (ext === 'pdf') return '#f56c6c'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(ext)) return '#67c23a'
  if (['mp3', 'wav', 'm4a'].includes(ext)) return '#e6a23c'
  if (['zip', 'rar', '7z'].includes(ext)) return '#909399'
  return 'var(--el-color-primary)'
}

// ========== 上传 ==========
const uploadDialog = ref(false)
const uploadMode = ref('files')
const uploadRef = ref()
const folderInputRef = ref()
const uploadFiles = ref([]) // {file, path?}
const uploadKbs = ref([])
const uploadCustomer = ref(null)
// 与后端 MAX_UPLOAD_MB 一致；上传前本地预校验，超限文件自动跳过并提示
const MAX_UPLOAD_BYTES = 100 * 1024 * 1024
const uploadStats = computed(() => {
  const files = uploadFiles.value
  const total = files.reduce((s, f) => s + (f.file.size || 0), 0)
  const tooLarge = files.filter((f) => (f.file.size || 0) > MAX_UPLOAD_BYTES)
  return { count: files.length, total, tooLarge: tooLarge.length }
})
const uploadTotalSize = computed(() => {
  const n = uploadStats.value.total
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + ' GB'
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(1) + ' MB'
  if (n >= 1024) return (n / 1024).toFixed(1) + ' KB'
  return n + ' B'
})
// 全局上传进度队列（右侧面板展示逐文件进度）
const uploadsStore = useUploadsStore()
// 队列跑完后刷新文件树与列表
watch(
  () => uploadsStore.allFinished,
  (v) => {
    if (v && uploadsStore.items.length) {
      loadTree()
      loadFiles()
    }
  }
)
const kbs = ref([])
const customers = ref([])

async function ensureOptions() {
  if (!kbs.value.length) {
    const res = await getKbs()
    kbs.value = Array.isArray(res) ? res : (res?.items || [])
  }
  if (!customers.value.length) {
    try {
      const res = await getCustomers({ page: 1, page_size: 200 })
      customers.value = res.items || []
    } catch { /* 下拉可选，失败不阻断 */ }
  }
}

function openUpload(mode) {
  uploadMode.value = mode
  uploadFiles.value = []
  uploadKbs.value = []
  uploadCustomer.value = null
  uploadRef.value?.clearFiles()
  if (folderInputRef.value) folderInputRef.value.value = ''
  uploadDialog.value = true
  ensureOptions()
  initUploadFormats()
}

// 当前启用解析格式（accept 过滤 + 选择校验）；拉取失败时不过滤
const acceptStr = ref('')
async function initUploadFormats() {
  const set = await ensureUploadFormats()
  acceptStr.value = set ? enabledAcceptStr() : ''
}

function handleFileChange(file) {
  // 拖拽/选择均校验：不支持解析的格式直接忽略并提示
  if (!isEnabledExt(file.name)) {
    ElMessage.warning(`「${file.name}」不是当前支持解析的格式，已忽略`)
    uploadRef.value?.handleRemove(file)
    return
  }
  uploadFiles.value.push({ file: file.raw, path: '' })
}

function handleFileRemove(file) {
  uploadFiles.value = uploadFiles.value.filter((f) => f.file !== file.raw)
}

function handleFolderPick(e) {
  // 文件夹内自动排除不支持解析的格式
  const picked = Array.from(e.target.files || [])
  const supported = picked.filter((f) => isEnabledExt(f.name))
  uploadFiles.value = supported.map((f) => ({ file: f, path: f.webkitRelativePath || f.name }))
  const skipped = picked.length - supported.length
  if (skipped > 0) {
    ElMessage.warning(`已自动排除 ${skipped} 个不支持解析的文件（仅上传支持解析的格式）`)
  }
}

function handleUpload() {
  if (!uploadFiles.value.length) return
  // 超限文件本地过滤：单个大文件不再拖垮整批上传
  const tooLarge = uploadFiles.value.filter((f) => (f.file.size || 0) > MAX_UPLOAD_BYTES)
  const toUpload = uploadFiles.value.filter((f) => (f.file.size || 0) <= MAX_UPLOAD_BYTES)
  if (!toUpload.length) {
    ElMessage.warning(`所选 ${tooLarge.length} 个文件均超过单文件 100MB 上限，无法上传`)
    return
  }
  // 交给全局上传队列（右下角悬浮 + 右侧面板展示逐文件进度，可切页面继续传）
  uploadsStore.start({
    files: toUpload.map((f) => f.file),
    paths: uploadMode.value === 'folder' ? toUpload.map((f) => f.path) : undefined,
    folder_id: currentFolderId.value === 'all' ? undefined : (currentFolderId.value || undefined),
    customer_id: uploadCustomer.value || undefined,
    kb_ids: uploadKbs.value.length ? uploadKbs.value : undefined,
  })
  uploadDialog.value = false
  let msg = `已加入上传队列（${toUpload.length} 个文件），可在右下角查看进度`
  if (tooLarge.length) msg += `；已跳过 ${tooLarge.length} 个超过 100MB 上限的文件`
  ElMessage.success(msg)
}

// ========== 文件夹 CRUD ==========
const folderDialog = ref(false)
const folderSaving = ref(false)
const folderForm = reactive({ id: null, name: '' })

function openFolderForm(folder) {
  folderForm.id = folder?.id || null
  folderForm.name = folder?.name || ''
  folderDialog.value = true
}

async function handleSaveFolder() {
  if (!folderForm.name.trim()) {
    ElMessage.warning('请输入文件夹名称')
    return
  }
  folderSaving.value = true
  try {
    if (folderForm.id) {
      await updateLibraryFolder(folderForm.id, { name: folderForm.name })
      ElMessage.success('重命名成功')
    } else {
      const parent = currentFolderId.value === 'all' ? null : currentFolderId.value
      await createLibraryFolder({ name: folderForm.name, parent_id: parent })
      ElMessage.success('创建成功')
    }
    folderDialog.value = false
    loadTree(false)
  } finally {
    folderSaving.value = false
  }
}

async function handleDeleteFolder() {
  await ElMessageBox.confirm(
    `确定删除文件夹「${currentFolder.value?.name}」吗？将删除其中全部内容（含子文件夹与文件）。`,
    '删除确认',
    { type: 'error', confirmButtonText: '删除' }
  )
  await deleteLibraryFolder(currentFolderId.value, true)
  ElMessage.success('删除成功')
  goParent()
  loadTree(false)
}

// ========== 文件操作 ==========
const renameDialog = ref(false)
const renaming = ref(false)
const renameFile = ref(null)
const renameName = ref('')

function openRename(row) {
  renameFile.value = row
  renameName.value = row.file_name
  renameDialog.value = true
}

async function handleRename() {
  if (!renameName.value.trim()) return
  renaming.value = true
  try {
    await updateLibraryFile(renameFile.value.id, { file_name: renameName.value })
    ElMessage.success('重命名成功')
    renameDialog.value = false
    loadFiles()
  } finally {
    renaming.value = false
  }
}

const moveDialog = ref(false)
const moving = ref(false)
const moveFiles = ref([])
const moveTarget = ref(null)

function openMove(row) {
  moveFiles.value = [row]
  moveTarget.value = null
  moveDialog.value = true
}

function openBatchMove() {
  if (!selectedFiles.value.length) return
  moveFiles.value = selectedFiles.value
  moveTarget.value = null
  moveDialog.value = true
}

async function handleMove() {
  moving.value = true
  try {
    for (const f of moveFiles.value) {
      await updateLibraryFile(f.id, { folder_id: moveTarget.value })
    }
    ElMessage.success(`已移动 ${moveFiles.value.length} 个文件`)
    moveDialog.value = false
    clearSelection()
    loadFiles()
  } finally {
    moving.value = false
  }
}

const assocDialog = ref(false)
const associating = ref(false)
const assocFiles = ref([])
const assocKbs = ref([])

function openAssociate(rows) {
  assocFiles.value = rows
  assocKbs.value = []
  assocDialog.value = true
  ensureOptions()
}

async function handleAssociate() {
  associating.value = true
  try {
    const res = await associateLibraryFiles(assocFiles.value.map((f) => f.id), assocKbs.value)
    ElMessage.success(`关联完成：新增 ${res?.associated ?? 0} 个，已存在 ${res?.already ?? 0} 个`)
    assocDialog.value = false
    loadFiles()
  } finally {
    associating.value = false
  }
}

async function handleDeleteFile(row) {
  await ElMessageBox.confirm(
    `确定删除文件「${row.file_name}」吗？文件及其在各知识库中的关联与切片将一并删除。`,
    '删除确认',
    { type: 'warning' }
  )
  await deleteLibraryFile(row.id)
  ElMessage.success('删除成功')
  loadFiles()
}

// ========== 多选 / 批量操作 ==========
const selectedFiles = ref([])
function onSelectionChange(rows) {
  selectedFiles.value = rows
}
function clearSelection() {
  selectedFiles.value = []
}

const PERM_LABELS = { read: '只读', edit: '编辑', owner: '所有权' }
const batchShareDialog = ref(false)
const batchSharing = ref(false)
const batchShareUser = ref(null)
const batchSharePerm = ref('read')
const users = ref([])

async function openBatchShare() {
  batchShareUser.value = null
  batchSharePerm.value = 'read'
  if (!users.value.length) {
    try {
      const res = await getUsers()
      users.value = Array.isArray(res) ? res : []
    } catch {
      return
    }
  }
  batchShareDialog.value = true
}

async function handleBatchShare() {
  if (!batchShareUser.value) return
  batchSharing.value = true
  try {
    const res = await batchShareFiles(selectedFiles.value.map((f) => f.id), {
      user_id: batchShareUser.value,
      permission: batchSharePerm.value,
    })
    ElMessage.success(`已共享 ${res?.shared ?? 0} 个文件`)
    batchShareDialog.value = false
  } finally {
    batchSharing.value = false
  }
}

async function handleBatchDelete() {
  await ElMessageBox.confirm(
    `确定删除选中的 ${selectedFiles.value.length} 个文件吗？文件及其在各知识库中的关联与切片将一并删除。`,
    '批量删除确认',
    { type: 'warning', confirmButtonText: '删除' }
  )
  for (const f of selectedFiles.value) {
    await deleteLibraryFile(f.id)
  }
  ElMessage.success('批量删除完成')
  clearSelection()
  loadFiles()
}

onMounted(() => {
  window.addEventListener('resize', onResize)
  loadTree(false)
  loadFiles()
  initUploadFormats()
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
})
</script>

<style scoped>
.library-page {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
.folder-pane {
  width: 220px;
  flex: none;
  background: var(--app-surface);
  border: 1px solid var(--app-line);
  border-radius: var(--app-radius-lg);
  box-shadow: var(--app-shadow);
  padding: 12px 10px;
  max-height: calc((100vh - var(--app-header-h) - 40px) / var(--app-zoom, 1));
  overflow-y: auto;
}
.folder-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  padding: 0 8px 8px;
}
.tree-node-all {
  padding: 7px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  margin-bottom: 4px;
}
.tree-node-all:hover {
  background: var(--app-bg);
}
.tree-node-all.active {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.file-pane {
  flex: 1;
  min-width: 0;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.tree-toggle {
  cursor: pointer;
  color: var(--app-ink-2);
}
.current-path {
  font-size: 13px;
  color: var(--app-ink-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 240px;
}
.search-input {
  width: 220px;
}
.toolbar-actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
}
.folder-ops {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  background: var(--app-bg);
  border-radius: 8px;
  margin-bottom: 12px;
  font-size: 13px;
}
.folder-ops-name {
  color: var(--app-ink-2);
  margin-right: 8px;
}
.file-name {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
.picked-count {
  margin-top: 10px;
  font-size: 13px;
  color: var(--app-ink-2);
}
.assoc-tip {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--app-ink-2);
}
.upload-tip {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--app-ink-2);
}
.batch-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-5);
  border-radius: 8px;
  flex-wrap: wrap;
}
.batch-count {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-color-primary);
  margin-right: 4px;
}
.batch-share-row {
  display: flex;
  gap: 8px;
  margin-bottom: 4px;
}
.kb-count-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  padding: 0 6px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  vertical-align: middle;
}
.method-text {
  margin-top: 4px;
  font-size: 12px;
  color: var(--app-ink-2);
}

@media (max-width: 991px) {
  .library-page {
    flex-direction: column;
  }
  .search-input {
    width: 160px;
  }
  .toolbar-actions {
    margin-left: 0;
    width: 100%;
  }
}
</style>
