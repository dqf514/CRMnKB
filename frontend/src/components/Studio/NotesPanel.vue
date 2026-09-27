<template>
  <div class="notes-panel">
    <!-- 顶部：新建工作区 + 操作 -->
    <div class="notes-header">
      <span class="title">工作区 ({{ notes.length }})</span>
      <el-button-group>
        <el-tooltip content="新建工作区">
          <el-button :icon="Plus" size="small" link @click="onNew" />
        </el-tooltip>
        <el-tooltip content="刷新">
          <el-button :icon="Refresh" size="small" link @click="refresh" />
        </el-tooltip>
      </el-button-group>
    </div>

    <!-- 工作区内容列表 -->
    <div class="notes-list" v-loading="loading">
      <div
        v-for="note in notes"
        :key="note.id"
        :class="['note-item', { active: note.id === selectedNoteId }]"
        @click="selectNote(note.id)"
      >
        <div class="note-title">
          <el-tag v-if="note.source_type === 'chat'" type="success" size="small">问答</el-tag>
          <el-tag v-else-if="note.source_type === 'report'" type="warning" size="small">报告</el-tag>
          <span class="title-text">{{ note.title }}</span>
        </div>
        <div class="note-meta">{{ formatDate(note.updated_at) }}</div>
      </div>
      <el-empty v-if="!loading && !notes.length" description="暂无工作区" :image-size="60" />
    </div>

    <!-- 编辑器 -->
    <div v-if="selectedNote" class="editor">
      <div class="editor-toolbar">
        <el-input v-model="titleDraft" placeholder="工作区标题" size="small" class="title-input" />
        <el-button-group>
          <el-tooltip content="预览">
            <el-button :icon="View" size="small" link @click="previewMode = !previewMode" />
          </el-tooltip>
          <el-tooltip content="保存">
            <el-button :icon="Check" size="small" type="primary" link :loading="saving" @click="saveNote" />
          </el-tooltip>
          <el-tooltip content="保存为知识库文档">
            <el-button :icon="Promotion" size="small" link @click="saveAsDocumentDialog" />
          </el-tooltip>
          <el-tooltip content="删除">
            <el-button :icon="Delete" size="small" link @click="deleteNote" />
          </el-tooltip>
        </el-button-group>
      </div>

      <div v-if="!previewMode" class="editor-body">
        <el-input
          v-model="contentDraft"
          type="textarea"
          :rows="14"
          resize="none"
          placeholder="Markdown 内容... 支持 ## 标题 / **粗体** / [链接](url)"
        />
      </div>
      <div v-else class="preview-body markdown-body" v-html="renderedPreview" />

      <div class="editor-meta">
        <span>来源: {{ selectedNote.source_type }}</span>
        <span v-if="selectedNote.source_ref?.saved_as_document_id" class="meta-link">
          已转知识库文档 #{{ selectedNote.source_ref.saved_as_document_id }}
        </span>
      </div>
    </div>

    <!-- 保存为知识库文档：选择目标知识库 -->
    <el-dialog v-model="saveKbDialog" title="保存为知识库文档" width="min(90vw, 420px)" append-to-body>
      <p class="kb-dialog-tip">将把「{{ selectedNote?.title }}」转成知识库文档，后台自动解析。</p>
      <el-select v-model="saveKbId" placeholder="选择目标知识库" style="width: 100%">
        <el-option v-for="k in saveKbOptions" :key="k.id" :label="k.name" :value="k.id" />
      </el-select>
      <template #footer>
        <el-button @click="saveKbDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingKb" @click="confirmSaveAsDocument">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import {
  Plus, Refresh, View, Check, Promotion, Delete,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useStudioStore } from '../../stores/studio'
import { renderMarkdown } from '../../utils/markdown'
import { parseServerDate } from '../../utils/format'
import { getKbs } from '../../api'

const studio = useStudioStore()

const titleDraft = ref('')
const contentDraft = ref('')
const saving = ref(false)
const previewMode = ref(true)  // 默认预览（渲染效果更好），点预览按钮切换编辑
const loading = computed(() => studio.loadingNotes)
const notes = computed(() => studio.notes)
const selectedNote = computed(() => studio.selectedNote)
const selectedNoteId = computed(() => studio.selectedNoteId)

const renderedPreview = computed(() => renderMarkdown(contentDraft.value || ''))

watch(
  () => studio.selectedNote,
  (n) => {
    titleDraft.value = n?.title || ''
    contentDraft.value = n?.content || ''
  },
  { immediate: true }
)

function selectNote(id) {
  studio.selectedNoteId = id
}

function formatDate(s) {
  if (!s) return ''
  const d = parseServerDate(s)
  if (!d) return ''
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`
}

async function onNew() {
  if (!studio.currentNotebook) {
    ElMessage.warning('请先选择工作区')
    return
  }
  await studio.createNote({
    title: '新工作区',
    content: '',
    source_type: 'manual',
    source_ref: null,
  })
  previewMode.value = false
}

async function refresh() {
  await studio.loadNotes()
}

async function saveNote() {
  if (!selectedNote.value) return
  saving.value = true
  try {
    await studio.updateNote(selectedNote.value.id, {
      title: titleDraft.value || '未命名',
      content: contentDraft.value,
    })
    ElMessage.success('已保存')
  } finally {
    saving.value = false
  }
}

async function deleteNote() {
  if (!selectedNote.value) return
  await ElMessageBox.confirm(`确定删除工作区「${selectedNote.value.title}」吗？`, '删除确认', { type: 'warning' })
  await studio.deleteNote(selectedNote.value.id)
  ElMessage.success('已删除')
}

const saveKbDialog = ref(false)
const saveKbOptions = ref([])
const saveKbId = ref(null)
const savingKb = ref(false)

async function saveAsDocumentDialog() {
  if (!selectedNote.value) return
  saveKbId.value = null
  if (!saveKbOptions.value.length) {
    try {
      const res = await getKbs()
      saveKbOptions.value = Array.isArray(res) ? res : (res?.items || [])
    } catch {
      ElMessage.error('获取知识库列表失败')
      return
    }
  }
  saveKbDialog.value = true
}

async function confirmSaveAsDocument() {
  if (!saveKbId.value) {
    ElMessage.warning('请选择目标知识库')
    return
  }
  savingKb.value = true
  try {
    const r = await studio.saveAsDocument(selectedNote.value.id, saveKbId.value)
    ElMessage.success(`已转知识库文档 #${r.document_id}（后台解析中）`)
    saveKbDialog.value = false
  } catch (err) {
    ElMessage.error(err?.message || '保存失败')
  } finally {
    savingKb.value = false
  }
}
</script>

<style scoped>
.notes-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: transparent;  /* 由 col-right 提供侧栏背景 */
}
.notes-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid var(--app-line);
  background: var(--app-card);  /* 头部白条 */
}
.notes-header .title {
  font-weight: 600;
  font-size: 13px;
}
.notes-list {
  flex: 0 0 auto;
  max-height: 35%;
  overflow-y: auto;
  padding: 8px;
}
.note-item {
  padding: 8px 10px;
  margin-bottom: 6px;
  border-radius: 6px;
  background: var(--app-card);  /* 白卡片浮在侧栏灰底 */
  border: 1px solid var(--app-line);
  cursor: pointer;
  transition: all 0.15s;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.03);
}
.note-item:hover {
  border-color: var(--el-color-primary-light-5);
}
.note-item.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.note-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.title-text {
  font-size: 13px;
  font-weight: 500;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.note-meta {
  font-size: 11px;
  color: var(--app-ink-2);
}
.editor {
  flex: 1;
  display: flex;
  flex-direction: column;
  margin: 0 8px 8px;
  padding: 10px;
  border-radius: 8px;
  border: 1px solid var(--app-line);
  background: var(--app-card);  /* 编辑区白卡片浮在侧栏灰底 */
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
  overflow: hidden;
}
.editor-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.title-input {
  flex: 1;
}
.editor-body {
  flex: 1;
  display: flex;
}
.editor-body :deep(.el-textarea) {
  flex: 1;
}
.editor-body :deep(.el-textarea__inner) {
  height: 100% !important;
  font-family: ui-monospace, "Cascadia Code", Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.6;
}
.preview-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  background: var(--app-fill-1);
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.6;
  border: 1px solid var(--app-line);
}
.editor-meta {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: var(--app-ink-2);
  margin-top: 8px;
}
.meta-link {
  color: var(--el-color-success);
}
</style>