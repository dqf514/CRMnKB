<template>
  <div>
    <div class="page-header">
      <h1 class="page-title-main">知识库</h1>
      <p class="page-sub">知识库是文档的逻辑集合：文件先入文档库，再按需关联到一个或多个知识库</p>
    </div>

    <el-tabs v-model="scope" class="scope-tabs">
      <el-tab-pane label="全部" name="all" />
      <el-tab-pane label="我的" name="mine" />
      <el-tab-pane label="共享给我的" name="shared" />
      <el-tab-pane label="团队" name="team" />
    </el-tabs>

    <div v-loading="loading">
      <el-row :gutter="16">
        <!-- 新建卡片 -->
        <el-col :xs="24" :sm="12" :lg="8" :xl="6">
          <div class="kb-card kb-create" @click="openForm()">
            <el-icon :size="28"><Plus /></el-icon>
            <span>新建知识库</span>
          </div>
        </el-col>

        <el-col v-for="kb in filteredList" :key="kb.id" :xs="24" :sm="12" :lg="8" :xl="6">
          <el-card class="kb-card" shadow="never" @click="goDetail(kb)">
            <div class="kb-head">
              <span class="kb-name" :title="kb.name">{{ kb.name }}</span>
              <el-tag size="small" :type="enumTagType(kbTypeMap, kb.type)" @click.stop>
                {{ enumLabel(kbTypeMap, kb.type) }}
              </el-tag>
              <el-tag v-if="kb.owner_id === currentUserId" size="small" type="danger" @click.stop>我的</el-tag>
              <el-tag v-else-if="kb.is_private === false" size="small" type="info" @click.stop>团队</el-tag>
              <el-tag v-else size="small" type="warning" @click.stop>私有</el-tag>
            </div>
            <div class="kb-desc">{{ kb.description || '暂无描述' }}</div>
            <div class="kb-customer" v-if="kb.type === 'customer' && kb.customer_name">
              <el-icon :size="13"><User /></el-icon> {{ kb.customer_name }}
            </div>
            <div class="kb-stats">
              <span>文档 {{ kb.doc_count ?? 0 }}</span>
              <span>切片 {{ kb.chunk_count ?? 0 }}</span>
              <span class="kb-time">{{ formatDate(kb.created_at) }}</span>
            </div>
            <div class="kb-actions" @click.stop>
              <el-tooltip content="进入" placement="top"><el-button link :icon="View" @click="goDetail(kb)" /></el-tooltip>
              <el-tooltip v-if="kb.perm === 'owner'" content="共享" placement="top"><el-button link :icon="Share" @click="openShare(kb)" /></el-tooltip>
            </div>
          </el-card>
        </el-col>
      </el-row>
      <el-empty v-if="!loading && !list.length" description="还没有知识库，点击左侧卡片新建" />
    </div>

    <!-- 新建 / 编辑知识库 -->
    <el-dialog v-model="formDialog" :title="form.id ? '编辑知识库' : '新建知识库'" width="min(90vw, 480px)">
      <el-form :model="form" :rules="formRules" ref="formRef" label-width="80px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="知识库名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" placeholder="这个知识库放什么内容" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <ShareDialog v-model="shareDialog" resource-type="kb" :resource="shareTarget" @changed="loadList" />
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { Plus, User, View, Share } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getKbs, createKb, updateKb, deleteKb } from '../api'
import { useAuthStore } from '../stores/auth'
import { kbTypeMap, enumLabel, enumTagType, formatDate } from '../utils/format'
import ShareDialog from '../components/ShareDialog.vue'

const router = useRouter()
const authStore = useAuthStore()
const currentUserId = computed(() => authStore.user?.id)
const loading = ref(false)
const list = ref([])
const scope = ref('all')

// 页签过滤：我的/共享给我的/团队
const filteredList = computed(() => {
  if (scope.value === 'all') return list.value
  return list.value.filter((kb) => {
    if (scope.value === 'mine') return kb.owner_id === currentUserId.value
    // 团队 tab = 所有团队可见的内容（含本人开的团队共享），is_private 为 null/false 都算团队可见
    if (scope.value === 'team') return kb.is_private !== true
    // shared：私有但被分享给我（非 owner）
    return kb.perm && kb.perm !== 'owner' && kb.is_private !== false
  })
})

const shareDialog = ref(false)
const shareTarget = ref(null)
function openShare(kb) {
  shareTarget.value = { id: kb.id, name: kb.name, owner_id: kb.owner_id, is_private: kb.is_private, perm: kb.perm }
  shareDialog.value = true
}

async function loadList() {
  loading.value = true
  try {
    const res = await getKbs()
    list.value = Array.isArray(res) ? res : (res?.items || [])
  } finally {
    loading.value = false
  }
}

const formDialog = ref(false)
const saving = ref(false)
const formRef = ref()
const form = reactive({ id: null, name: '', description: '' })
const formRules = {
  name: [{ required: true, message: '请输入知识库名称', trigger: 'blur' }],
}

function openForm(kb) {
  Object.assign(form, { id: null, name: '', description: '' }, kb ? {
    id: kb.id, name: kb.name, description: kb.description || '',
  } : {})
  formDialog.value = true
  nextTick(() => formRef.value?.clearValidate())
}

async function handleSave() {
  await formRef.value.validate()
  saving.value = true
  try {
    const data = { name: form.name, description: form.description || null }
    if (form.id) {
      await updateKb(form.id, data)
      ElMessage.success('更新成功')
    } else {
      await createKb(data)
      ElMessage.success('创建成功，去关联文件吧')
    }
    formDialog.value = false
    loadList()
  } finally {
    saving.value = false
  }
}

async function handleDelete(kb) {
  await ElMessageBox.confirm(
    `确定删除知识库「${kb.name}」吗？其中的文档关联与切片将一并删除，但不会删除文档库中的文件。`,
    '删除确认',
    { type: 'warning', confirmButtonText: '删除' }
  )
  await deleteKb(kb.id)
  ElMessage.success('删除成功')
  loadList()
}

function goDetail(kb) {
  router.push(`/knowledge/${kb.id}`)
}

onMounted(loadList)
</script>

<style scoped>
.kb-card {
  margin-bottom: 16px;
  cursor: pointer;
  height: 176px;
  display: flex;
  flex-direction: column;
  transition: box-shadow 0.2s ease, transform 0.2s ease;
}
.kb-card:hover {
  box-shadow: var(--app-shadow-hover);
  transform: translateY(-2px);
}
.kb-card :deep(.el-card__body) {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.kb-create {
  background: var(--app-surface);
  border: 1px dashed var(--app-line);
  border-radius: var(--app-radius-lg);
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--app-ink-2);
  margin-bottom: 16px;
  transition: border-color 0.2s ease, color 0.2s ease;
}
.kb-create:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}
.kb-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.kb-name {
  font-weight: 600;
  font-size: 15px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.kb-desc {
  color: var(--app-ink-2);
  font-size: 13px;
  margin-top: 8px;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.kb-customer {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--app-ink-2);
  margin-top: 6px;
}
.kb-stats {
  display: flex;
  gap: 14px;
  font-size: 12px;
  color: var(--app-ink-2);
  margin-top: 10px;
}
.kb-time {
  margin-left: auto;
}
.kb-actions {
  margin-top: 8px;
  border-top: 1px solid var(--app-line);
  padding-top: 8px;
  display: flex;
  justify-content: flex-end;
  gap: 4px;
}
</style>
