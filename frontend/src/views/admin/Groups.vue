<template>
  <div>
    <el-card>
      <div class="toolbar">
        <span class="hint">团队用于组织用户，便于按团队协作与管理</span>
        <el-button type="primary" :icon="Plus" style="margin-left: auto" @click="openForm()">新增团队</el-button>
      </div>

      <el-table :data="list" v-loading="loading" stripe>
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.description || '-' }}</template>
        </el-table-column>
        <el-table-column v-if="hasMemberCount" label="成员数" width="90">
          <template #default="{ row }">{{ row.member_count ?? 0 }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openForm(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !list.length" description="暂无团队" :image-size="80" />
    </el-card>

    <el-dialog v-model="formDialog" :title="form.id ? '编辑团队' : '新增团队'" width="min(90vw, 440px)">
      <el-form :model="form" :rules="formRules" ref="formRef" label-width="80px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="团队名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" placeholder="这个团队包含哪些同事" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { confirmDanger } from '../../utils/confirmDanger'
import { validateForm } from '../../utils/validateForm'
import { getAdminGroups, createAdminGroup, updateAdminGroup, deleteAdminGroup } from '../../api'
import { formatDateTime } from '../../utils/format'

const loading = ref(false)
const list = ref([])

// 列表无成员数字段时不展示该列
const hasMemberCount = computed(() => list.value.some((g) => g.member_count != null))

async function loadList() {
  loading.value = true
  try {
    const res = await getAdminGroups()
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
  name: [{ required: true, message: '请输入团队名称', trigger: 'blur' }],
}

function openForm(row) {
  Object.assign(form, { id: null, name: '', description: '' }, row ? {
    id: row.id, name: row.name, description: row.description || '',
  } : {})
  formDialog.value = true
  nextTick(() => formRef.value?.clearValidate())
}

async function handleSave() {
  if (!(await validateForm(formRef.value))) return
  saving.value = true
  try {
    const data = { name: form.name, description: form.description || null }
    if (form.id) {
      await updateAdminGroup(form.id, data)
      ElMessage.success('更新成功')
    } else {
      await createAdminGroup(data)
      ElMessage.success('创建成功')
    }
    formDialog.value = false
    loadList()
  } finally {
    saving.value = false
  }
}

async function handleDelete(row) {
  const ok = await confirmDanger(`确定删除团队「${row.name}」吗？`)
  if (!ok) return
  try {
    await deleteAdminGroup(row.id)
    ElMessage.success('删除成功')
    loadList()
  } catch (e) {
    // 组内仍有成员时后端返回 400，把具体提示展示出来
    const detail = e?.response?.data?.detail
    if (e?.response?.status === 400) {
      ElMessage.error(typeof detail === 'string' ? detail : '删除失败：团队内仍有成员')
    }
  }
}

onMounted(loadList)
</script>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  margin-bottom: 16px;
}
.hint {
  color: var(--app-ink-2);
  font-size: 13px;
}
</style>
