<template>
  <div>
    <el-card>
      <div class="toolbar">
        <span class="hint">角色决定用户在系统内可使用的功能权限，可自定义角色分配给团队成员</span>
        <el-button type="primary" :icon="Plus" style="margin-left: auto" @click="openForm()">新增角色</el-button>
      </div>

      <el-table :data="list" v-loading="loading" stripe>
        <el-table-column prop="key" label="标识" min-width="110" show-overflow-tooltip />
        <el-table-column prop="name" label="名称" min-width="110" show-overflow-tooltip />
        <el-table-column label="描述" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.description || '-' }}</template>
        </el-table-column>
        <el-table-column label="权限点" min-width="220">
          <template #default="{ row }">
            <!-- admin 角色恒有全部权限，不逐点罗列 -->
            <el-tag v-if="row.key === 'admin'" size="small" type="danger">全部权限</el-tag>
            <template v-else-if="row.permissions?.length">
              <el-tag
                v-for="p in row.permissions"
                :key="p"
                size="small"
                style="margin: 2px 4px 2px 0"
              >
                {{ permLabel(p) }}
              </el-tag>
            </template>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="系统内置" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.is_system" size="small" type="info">内置</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openForm(row)">编辑</el-button>
            <!-- 系统内置角色不可删 -->
            <el-button v-if="!row.is_system" link type="danger" size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !list.length" description="暂无角色" :image-size="80" />
    </el-card>

    <!-- 新增 / 编辑角色 -->
    <el-dialog v-model="formDialog" :title="form.id ? `编辑角色 - ${form.name}` : '新增角色'" width="min(92vw, 560px)">
      <el-form :model="form" :rules="formRules" ref="formRef" label-width="80px">
        <el-form-item label="标识" prop="key">
          <el-input
            v-model="form.key"
            :disabled="!!form.id"
            placeholder="小写字母开头，可含数字/中划线/下划线"
            maxlength="50"
          />
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="角色显示名称" maxlength="50" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="这个角色的职责说明（可选）" />
        </el-form-item>
        <el-form-item label="权限">
          <!-- admin 角色权限由系统固定为全集，不开放编辑 -->
          <el-alert
            v-if="form.key === 'admin'"
            type="warning"
            :closable="false"
            title="超级管理员拥有全部权限"
          />
          <div v-else class="perm-matrix">
            <div v-for="g in permGroups" :key="g.group" class="perm-group">
              <div class="perm-group-title">{{ g.group }}</div>
              <el-checkbox-group v-model="form.permissions">
                <div v-for="item in g.items" :key="item.key" class="perm-item">
                  <el-checkbox :value="item.key">
                    <span class="perm-name">{{ item.name }}</span>
                    <span class="perm-desc">{{ item.description }}</span>
                  </el-checkbox>
                </div>
              </el-checkbox-group>
            </div>
            <el-empty v-if="!permGroups.length" description="权限点加载失败" :image-size="60" />
          </div>
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
import { listRoles, createRole, updateRole, deleteRole, listPermissionKeys } from '../../api'
import { formatDateTime } from '../../utils/format'

const loading = ref(false)
const list = ref([])
// 权限点分组（/admin/roles/permission-keys）：驱动编辑弹窗的权限矩阵 + 列表里的中文标签
const permGroups = ref([])

async function loadList() {
  loading.value = true
  try {
    const res = await listRoles()
    list.value = Array.isArray(res) ? res : (res?.items || [])
  } finally {
    loading.value = false
  }
}

async function loadPermKeys() {
  try {
    const res = await listPermissionKeys()
    permGroups.value = res?.groups || []
  } catch { /* 权限矩阵加载失败时弹窗内显示空态 */ }
}

// 权限点 key → 中文名（拿不到时原样显示 key）
const permNameMap = computed(() => {
  const map = {}
  for (const g of permGroups.value) {
    for (const item of g.items || []) map[item.key] = item.name
  }
  return map
})
const permLabel = (key) => permNameMap.value[key] || key

// ========== 新增 / 编辑 ==========
const formDialog = ref(false)
const saving = ref(false)
const formRef = ref()
const emptyForm = { id: null, key: '', name: '', description: '', permissions: [] }
const form = reactive({ ...emptyForm })
const formRules = {
  key: [
    { required: true, message: '请输入角色标识', trigger: 'blur' },
    { pattern: /^[a-z][a-z0-9_-]{1,49}$/, message: '小写字母开头，2-50 位，仅含小写字母/数字/中划线/下划线', trigger: 'blur' },
  ],
  name: [{ required: true, message: '请输入角色名称', trigger: 'blur' }],
}

function openForm(row) {
  Object.assign(form, emptyForm, row ? {
    id: row.id,
    key: row.key,
    name: row.name,
    description: row.description || '',
    permissions: [...(row.permissions || [])],
  } : { permissions: [] })
  formDialog.value = true
  nextTick(() => formRef.value?.clearValidate())
}

async function handleSave() {
  if (!(await validateForm(formRef.value))) return
  saving.value = true
  try {
    if (form.id) {
      await updateRole(form.id, {
        name: form.name,
        description: form.description || null,
        permissions: form.permissions,
      })
      ElMessage.success('更新成功')
    } else {
      await createRole({
        key: form.key,
        name: form.name,
        description: form.description || null,
        permissions: form.permissions,
      })
      ElMessage.success('创建成功')
    }
    formDialog.value = false
    loadList()
  } finally {
    saving.value = false
  }
}

async function handleDelete(row) {
  const ok = await confirmDanger(`确定删除角色「${row.name}」吗？`)
  if (!ok) return
  try {
    await deleteRole(row.id)
    ElMessage.success('删除成功')
    loadList()
  } catch (e) {
    // 仍有用户引用该角色时后端返回 409，把具体提示展示出来
    const detail = e?.response?.data?.detail
    if (e?.response?.status === 409) {
      ElMessage.error(typeof detail === 'string' ? detail : '删除失败：该角色仍被用户使用')
    }
  }
}

onMounted(() => {
  loadList()
  loadPermKeys()
})
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
.perm-matrix {
  width: 100%;
}
.perm-group {
  margin-bottom: 8px;
}
.perm-group-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 4px;
}
.perm-item :deep(.el-checkbox) {
  height: auto;
  margin-right: 24px;
}
.perm-name {
  font-weight: 500;
}
.perm-desc {
  margin-left: 8px;
  color: var(--app-ink-2);
  font-size: 12px;
}
</style>
