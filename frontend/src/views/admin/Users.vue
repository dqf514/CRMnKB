<template>
  <div>
    <el-card>
      <div class="toolbar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索用户名 / 姓名"
          style="width: 220px"
          clearable
        />
        <el-select v-model="query.group_id" placeholder="分组筛选" style="width: 150px" clearable @change="handleSearch">
          <el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="handleSearch">搜索</el-button>
        <el-button type="success" :icon="Plus" style="margin-left: auto" @click="openForm()">新增用户</el-button>
      </div>

      <el-table :data="list" v-loading="loading" stripe>
        <el-table-column prop="username" label="用户名" min-width="110" />
        <el-table-column prop="name" label="姓名" min-width="100" show-overflow-tooltip />
        <el-table-column prop="email" label="邮箱" min-width="150" show-overflow-tooltip />
        <el-table-column prop="phone" label="手机号" min-width="120" show-overflow-tooltip />
        <el-table-column label="角色" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="enumTagType(userRoleMap, row.role)">
              {{ enumLabel(userRoleMap, row.role) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="分组" width="110" show-overflow-tooltip>
          <template #default="{ row }">{{ row.group_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-switch
              :model-value="row.status === 1"
              :disabled="isSelf(row)"
              @change="(val) => toggleStatus(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column label="最后登录" width="150">
          <template #default="{ row }">{{ formatDateTime(row.last_login_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openForm(row)">编辑</el-button>
            <el-button link type="warning" size="small" @click="openResetPwd(row)">重置密码</el-button>
            <el-button link type="danger" size="small" :disabled="isSelf(row)" @click="handleDelete(row)">删除</el-button>
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
        @current-change="loadList"
      />
    </el-card>

    <!-- 新增 / 编辑用户 -->
    <el-drawer v-model="formDrawer" :title="form.id ? '编辑用户' : '新增用户'" size="min(92vw, 440px)">
      <el-form :model="form" :rules="formRules" ref="formRef" label-width="90px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" :disabled="!!form.id" placeholder="登录用户名" />
        </el-form-item>
        <el-form-item v-if="!form.id" label="密码" prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="初始密码，至少 8 位" />
        </el-form-item>
        <el-form-item label="姓名" prop="name">
          <el-input v-model="form.name" placeholder="显示名称" />
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="电子邮箱" />
        </el-form-item>
        <el-form-item label="手机号" prop="phone">
          <el-input v-model="form.phone" placeholder="手机号（可用于手机/微信登录）" maxlength="11" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width: 100%" :disabled="isSelfRow">
            <el-option v-for="(v, k) in userRoleMap" :key="k" :label="v.label" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="分组">
          <el-select v-model="form.group_id" style="width: 100%" clearable placeholder="可选">
            <el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formDrawer = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-drawer>

    <!-- 重置密码 -->
    <el-dialog v-model="pwdDialog" :title="`重置密码 - ${pwdTarget?.username || ''}`" width="min(90vw, 420px)">
      <el-form :model="pwdForm" :rules="pwdRules" ref="pwdFormRef" label-width="90px">
        <el-form-item label="新密码" prop="new_password">
          <el-input v-model="pwdForm.new_password" type="password" show-password placeholder="至少 8 位" />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirm">
          <el-input v-model="pwdForm.confirm" type="password" show-password placeholder="再次输入新密码" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdDialog = false">取消</el-button>
        <el-button type="primary" :loading="pwdSaving" @click="handleResetPwd">重置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { useThemeStore } from '../../stores/theme'
import { Search, Plus } from '@element-plus/icons-vue'
import { watchDebounced } from '../../utils/watchDebounced'
import { ElMessage } from 'element-plus'
import { confirmDanger } from '../../utils/confirmDanger'
import { validateForm } from '../../utils/validateForm'
import {
  getAdminUsers, createAdminUser, updateAdminUser, resetAdminUserPassword, deleteAdminUser, getAdminGroups,
} from '../../api'
import { useAuthStore } from '../../stores/auth'
import { userRoleMap, enumLabel, enumTagType, formatDateTime } from '../../utils/format'

const authStore = useAuthStore()
const loading = ref(false)
const list = ref([])
const total = ref(0)
const groups = ref([])
const themeStore = useThemeStore()
const query = reactive({ keyword: '', group_id: '', page: 1, page_size: themeStore.pageSize })

function onSizeChange() {
  query.page = 1
  loadList()
}

// 当前登录用户行：禁用停用/删除/降级，防止管理员把自己锁死
const isSelf = (row) => row.id === authStore.user?.id

async function loadList() {
  loading.value = true
  try {
    const params = { page: query.page, page_size: query.page_size }
    if (query.keyword) params.keyword = query.keyword
    if (query.group_id) params.group_id = query.group_id
    const res = await getAdminUsers(params)
    list.value = res.items || []
    total.value = res.total || 0
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  query.page = 1
  loadList()
}

// 关键字防抖即搜（300ms）：输入停顿或清空后自动搜索，无需回车
watchDebounced(() => query.keyword, handleSearch)

async function loadGroups() {
  try {
    const res = await getAdminGroups()
    groups.value = Array.isArray(res) ? res : (res?.items || [])
  } catch { /* 下拉可选 */ }
}

// ========== 新增 / 编辑 ==========
const formDrawer = ref(false)
const saving = ref(false)
const formRef = ref()
const emptyForm = { id: null, username: '', password: '', name: '', email: '', phone: '', role: 'user', group_id: null }
const form = reactive({ ...emptyForm })
const isSelfRow = computed(() => form.id != null && form.id === authStore.user?.id)
const formRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入初始密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
  ],
  name: [{ required: true, message: '请输入姓名', trigger: 'blur' }],
  email: [{ type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
  phone: [{ pattern: /^1[3-9]\d{9}$/, message: '手机号格式不正确', trigger: 'blur' }],
}

function openForm(row) {
  Object.assign(form, emptyForm, row ? {
    id: row.id,
    username: row.username,
    name: row.name,
    email: row.email || '',
    phone: row.phone || '',
    role: row.role,
    group_id: row.group_id ?? null,
  } : {})
  formDrawer.value = true
  nextTick(() => formRef.value?.clearValidate())
}

async function handleSave() {
  if (!(await validateForm(formRef.value))) return
  saving.value = true
  try {
    if (form.id) {
      await updateAdminUser(form.id, {
        name: form.name, email: form.email || null, phone: form.phone || null,
        role: form.role, group_id: form.group_id,
      })
      ElMessage.success('更新成功')
    } else {
      await createAdminUser({
        username: form.username, password: form.password,
        name: form.name, email: form.email || null, phone: form.phone || null,
        role: form.role, group_id: form.group_id,
      })
      ElMessage.success('创建成功')
    }
    formDrawer.value = false
    loadList()
  } finally {
    saving.value = false
  }
}

async function toggleStatus(row, val) {
  await updateAdminUser(row.id, { status: val ? 1 : 0 })
  row.status = val ? 1 : 0
  ElMessage.success(val ? '已启用' : '已停用')
}

// ========== 重置密码 ==========
const pwdDialog = ref(false)
const pwdSaving = ref(false)
const pwdFormRef = ref()
const pwdTarget = ref(null)
const pwdForm = reactive({ new_password: '', confirm: '' })
const pwdRules = {
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (r, v, cb) => (v === pwdForm.new_password ? cb() : cb(new Error('两次输入的密码不一致'))),
      trigger: 'blur',
    },
  ],
}

function openResetPwd(row) {
  pwdTarget.value = row
  pwdForm.new_password = ''
  pwdForm.confirm = ''
  pwdDialog.value = true
  nextTick(() => pwdFormRef.value?.clearValidate())
}

async function handleResetPwd() {
  if (!(await validateForm(pwdFormRef.value))) return
  pwdSaving.value = true
  try {
    await resetAdminUserPassword(pwdTarget.value.id, pwdForm.new_password)
    ElMessage.success('密码已重置')
    pwdDialog.value = false
  } finally {
    pwdSaving.value = false
  }
}

async function handleDelete(row) {
  const ok = await confirmDanger(`确定删除用户「${row.username}」吗？`)
  if (!ok) return
  await deleteAdminUser(row.id)
  ElMessage.success('删除成功')
  loadList()
}

onMounted(() => {
  loadList()
  loadGroups()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
</style>
