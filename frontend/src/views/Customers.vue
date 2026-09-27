<template>
  <div>
    <el-card>
      <div class="toolbar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索名称 / 单位 / 电话"
          style="width: 220px"
          clearable
          @keyup.enter="handleSearch"
          @clear="handleSearch"
        />
        <el-select v-model="query.status" placeholder="状态" style="width: 120px" clearable @change="handleSearch">
          <el-option v-for="(v, k) in customerStatusMap" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-select v-model="query.industry" placeholder="行业" style="width: 130px" clearable filterable @change="handleSearch">
          <el-option v-for="i in industryStore.list" :key="i.id" :label="i.name" :value="i.name" />
        </el-select>
        <el-select v-model="query.tag" placeholder="标签" style="width: 130px" clearable filterable @change="handleSearch">
          <el-option v-for="t in tagOptions" :key="t" :label="t" :value="t" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="handleSearch">搜索</el-button>
        <el-button :icon="Setting" @click="openIndustryDialog">行业设置</el-button>
        <el-button :icon="Download" @click="handleDownloadTemplate">下载模板</el-button>
        <el-button :icon="Upload" @click="openImport">导入</el-button>
        <el-button :icon="Download" :loading="exporting" @click="handleExport">导出</el-button>
        <el-button type="success" :icon="Plus" style="margin-left: auto" @click="openForm()">新增客户</el-button>
      </div>

      <el-table :data="list" v-loading="loading" stripe class="clickable-rows" @row-click="goDetail">
        <el-table-column label="名称" min-width="130">
          <template #default="{ row }">
            <el-link type="primary" @click.stop="goDetail(row)">{{ row.name }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="company" label="单位" min-width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ row.company || '-' }}</template>
        </el-table-column>
        <el-table-column label="行业" min-width="140">
          <template #default="{ row }">
            <template v-if="row.industries?.length">
              <el-tag v-for="t in row.industries" :key="t" size="small" type="info" effect="plain" class="multi-tag">{{ t }}</el-tag>
            </template>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="标签" min-width="140">
          <template #default="{ row }">
            <template v-if="row.tags?.length">
              <el-tag v-for="t in row.tags.slice(0, 2)" :key="t" size="small" class="multi-tag">{{ t }}</el-tag>
              <el-tooltip v-if="row.tags.length > 2" :content="row.tags.slice(2).join('、')" placement="top">
                <el-tag size="small" type="info" effect="plain" class="multi-tag">+{{ row.tags.length - 2 }}</el-tag>
              </el-tooltip>
            </template>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="phone" label="电话" min-width="120" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="enumTagType(customerStatusMap, row.status)">
              {{ enumLabel(customerStatusMap, row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="openForm(row)">编辑</el-button>
            <el-button link type="danger" @click.stop="handleDelete(row)">删除</el-button>
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

    <!-- 新增 / 编辑抽屉 -->
    <el-drawer v-model="drawerVisible" :title="form.id ? '编辑客户' : '新增客户'" size="min(92vw, 460px)">
      <el-form :model="form" :rules="formRules" ref="formRef" label-width="80px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="客户名称" @blur="checkDuplicates" />
        </el-form-item>
        <el-form-item label="单位">
          <el-input v-model="form.company" placeholder="所属单位 / 公司" />
        </el-form-item>
        <el-form-item label="职务">
          <el-input v-model="form.position" placeholder="联系人职务" />
        </el-form-item>
        <el-form-item label="行业">
          <el-select
            v-model="form.industries"
            multiple
            filterable
            style="width: 100%"
            placeholder="请选择行业（可多选）"
            @visible-change="(v) => v && industryStore.load()"
          >
            <el-option v-for="i in industryStore.list" :key="i.id" :label="i.name" :value="i.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="标签">
          <el-select
            v-model="form.tags"
            multiple
            filterable
            allow-create
            default-first-option
            style="width: 100%"
            placeholder="选择或输入标签，回车创建"
          >
            <el-option v-for="t in tagOptions" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="电话" prop="phone">
          <el-input v-model="form.phone" placeholder="联系电话" @blur="checkDuplicates" />
        </el-form-item>
        <div v-if="dupList.length" class="dup-tip">
          <el-alert type="warning" :closable="false" show-icon title="疑似重复客户，点击查看详情：">
            <div v-for="d in dupList" :key="d.id" class="dup-item">
              <el-link type="warning" @click="goCustomer(d.id)">{{ d.name }}<template v-if="d.company">（{{ d.company }}）</template><template v-if="d.phone"> · {{ d.phone }}</template></el-link>
            </div>
          </el-alert>
        </div>
        <el-form-item label="微信">
          <el-input v-model="form.wechat" placeholder="微信号" />
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="电子邮箱" />
        </el-form-item>
        <el-form-item label="地址">
          <el-input v-model="form.address" placeholder="联系地址" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="form.status" style="width: 100%">
            <el-option v-for="(v, k) in customerStatusMap" :key="k" :label="v.label" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源">
          <el-input v-model="form.source" placeholder="客户来源" />
        </el-form-item>
        <el-form-item label="生日">
          <el-date-picker v-model="form.birthday" type="date" value-format="YYYY-MM-DD" style="width: 100%" placeholder="客户生日" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="drawerVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-drawer>

    <!-- 导入客户 -->
    <el-dialog v-model="importDialog" title="导入客户" width="min(92vw, 620px)" :close-on-click-modal="false">
      <el-upload
        :auto-upload="false"
        :limit="1"
        accept=".xlsx"
        drag
        :on-change="handleImportFileChange"
        :on-exceed="handleImportExceed"
        :on-remove="() => (importFile = null)"
      >
        <el-icon size="40" style="color: var(--app-ink-2)"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽 xlsx 文件到此处，或 <em>点击选择</em></div>
      </el-upload>
      <div class="import-mode">
        <span class="import-mode-label">重复处理：</span>
        <el-radio-group v-model="importMode">
          <el-radio value="skip">跳过重复</el-radio>
          <el-radio value="overwrite">覆盖更新</el-radio>
        </el-radio-group>
      </div>
      <template v-if="importResult">
        <el-alert type="success" :closable="false" class="import-summary"
          :title="`导入完成：新增 ${importResult.created ?? 0}，更新 ${importResult.updated ?? 0}，跳过 ${importResult.skipped ?? 0}`" />
        <el-table v-if="importResult.errors?.length" :data="importResult.errors" size="small" max-height="220" style="margin-top: 10px">
          <el-table-column prop="row" label="行号" width="80" />
          <el-table-column prop="message" label="错误信息" min-width="220" show-overflow-tooltip />
        </el-table>
      </template>
      <template #footer>
        <el-button @click="importDialog = false">关闭</el-button>
        <el-button type="primary" :loading="importing" :disabled="!importFile" @click="handleImport">开始导入</el-button>
      </template>
    </el-dialog>

    <!-- 行业设置 -->
    <el-dialog v-model="industryDialog" title="行业设置" width="min(92vw, 560px)">
      <div class="ind-add">
        <el-input v-model="indForm.name" placeholder="行业名称" style="flex: 1" @keyup.enter="handleAddIndustry" />
        <el-input-number v-model="indForm.sort" :min="0" placeholder="排序" style="width: 110px" />
        <el-button type="primary" :loading="indSaving" @click="handleAddIndustry">新增行业</el-button>
      </div>
      <el-table :data="industryList" v-loading="indLoading" size="small" style="margin-top: 12px">
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="sort" label="排序" width="80" />
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(val) => toggleIndustry(row, val)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openIndustryEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="handleDeleteIndustry(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- 编辑行业 -->
    <el-dialog v-model="indEditDialog" title="编辑行业" width="min(90vw, 400px)" append-to-body>
      <el-form label-width="80px">
        <el-form-item label="名称" required>
          <el-input v-model="indEdit.name" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="indEdit.sort" :min="0" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="indEditDialog = false">取消</el-button>
        <el-button type="primary" :loading="indSaving" @click="handleSaveIndustry">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { useThemeStore } from '../stores/theme'
import { useRouter } from 'vue-router'
import { Search, Plus, Setting, Upload, Download, UploadFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getCustomers, createCustomer, updateCustomer, deleteCustomer,
  getIndustries, createIndustry, updateIndustry, deleteIndustry,
  getCustomerImportTemplate, importCustomers, exportCustomers, getCustomerDuplicates,
} from '../api'
import { useIndustryStore } from '../stores/industries'
import { customerStatusMap, enumLabel, enumTagType, formatDateTime, CUSTOMER_TAG_PRESETS } from '../utils/format'

const router = useRouter()
const industryStore = useIndustryStore()
const loading = ref(false)
const saving = ref(false)
const list = ref([])
const total = ref(0)
const themeStore = useThemeStore()
const query = reactive({ keyword: '', status: '', industry: '', tag: '', page: 1, page_size: themeStore.pageSize })

function onSizeChange() {
  query.page = 1
  loadList()
}

// 标签筛选/表单选项：预设 + 当前列表里出现过的自定义标签
const tagOptions = computed(() => {
  const set = new Set(CUSTOMER_TAG_PRESETS)
  list.value.forEach((c) => (c.tags || []).forEach((t) => set.add(t)))
  return [...set]
})

async function loadList() {
  loading.value = true
  try {
    const params = { page: query.page, page_size: query.page_size }
    if (query.keyword) params.keyword = query.keyword
    if (query.status) params.status = query.status
    if (query.industry) params.industry = query.industry
    if (query.tag) params.tag = query.tag
    const res = await getCustomers(params)
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

const drawerVisible = ref(false)
const formRef = ref()
const emptyForm = {
  id: null, name: '', company: '', position: '', wechat: '',
  industries: [], tags: [], phone: '', email: '', address: '',
  status: 'potential', source: '', birthday: '',
}
const form = reactive({ ...emptyForm })
const formRules = {
  name: [{ required: true, message: '请输入客户名称', trigger: 'blur' }],
  email: [{ type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
  phone: [{ pattern: /^[\d+\-() ]{5,20}$/, message: '电话格式不正确', trigger: 'blur' }],
}

function openForm(row) {
  industryStore.load()
  Object.assign(form, emptyForm, row ? {
    id: row.id,
    name: row.name,
    company: row.company || '',
    position: row.position || '',
    wechat: row.wechat || '',
    industries: [...(row.industries || [])],
    tags: [...(row.tags || [])],
    phone: row.phone,
    email: row.email,
    address: row.address,
    status: row.status,
    source: row.source,
    birthday: row.birthday || '',
  } : {})
  dupList.value = []
  drawerVisible.value = true
  nextTick(() => formRef.value?.clearValidate())
}

async function handleSave() {
  await formRef.value.validate()
  saving.value = true
  try {
    const data = {
      name: form.name,
      company: form.company || null,
      position: form.position || null,
      wechat: form.wechat || null,
      industries: form.industries,
      tags: form.tags,
      phone: form.phone,
      email: form.email,
      address: form.address,
      status: form.status,
      source: form.source,
      birthday: form.birthday || null,
    }
    if (form.id) {
      await updateCustomer(form.id, data)
      ElMessage.success('更新成功')
    } else {
      await createCustomer(data)
      ElMessage.success('创建成功')
    }
    drawerVisible.value = false
    loadList()
  } finally {
    saving.value = false
  }
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确定删除客户「${row.name}」吗？`, '删除确认', { type: 'warning' })
  await deleteCustomer(row.id)
  ElMessage.success('删除成功')
  loadList()
}

function goDetail(row) {
  router.push(`/customers/${row.id}`)
}

// ========== 导入导出 ==========
const importDialog = ref(false)
const importing = ref(false)
const exporting = ref(false)
const importFile = ref(null)
const importMode = ref('skip')
const importResult = ref(null)

function openImport() {
  importFile.value = null
  importMode.value = 'skip'
  importResult.value = null
  importDialog.value = true
}

function handleImportFileChange(uploadFile) {
  importFile.value = uploadFile.raw
}

function handleImportExceed(files) {
  importFile.value = files[0]
}

async function handleImport() {
  if (!importFile.value) return
  importing.value = true
  try {
    importResult.value = await importCustomers(importFile.value, importMode.value)
    if (!importResult.value?.errors?.length) ElMessage.success('导入完成')
    loadList()
  } finally {
    importing.value = false
  }
}

// 浏览器直接下载文件流；延时释放 Object URL，避免 Firefox 下下载失败
function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

async function handleDownloadTemplate() {
  const blob = await getCustomerImportTemplate()
  downloadBlob(blob, '客户导入模板.xlsx')
}

async function handleExport() {
  exporting.value = true
  try {
    const params = {}
    if (query.keyword) params.keyword = query.keyword
    if (query.status) params.status = query.status
    if (query.industry) params.industry = query.industry
    if (query.tag) params.tag = query.tag
    const blob = await exportCustomers(params)
    downloadBlob(blob, '客户导出.xlsx')
  } finally {
    exporting.value = false
  }
}

// ========== 查重（姓名/电话失焦时触发） ==========
const dupList = ref([])

async function checkDuplicates() {
  const name = form.name?.trim()
  const phone = form.phone?.trim()
  if (!name && !phone) {
    dupList.value = []
    return
  }
  try {
    const params = {}
    if (name) params.name = name
    if (phone) params.phone = phone
    if (form.id) params.exclude_id = form.id
    const res = await getCustomerDuplicates(params)
    dupList.value = Array.isArray(res) ? res : []
  } catch {
    dupList.value = []
  }
}

function goCustomer(id) {
  router.push(`/customers/${id}`)
}

// ========== 行业设置 ==========
const industryDialog = ref(false)
const indLoading = ref(false)
const indSaving = ref(false)
const industryList = ref([])
const indForm = reactive({ name: '', sort: 0 })
const indEditDialog = ref(false)
const indEdit = reactive({ id: null, name: '', sort: 0 })

async function openIndustryDialog() {
  industryDialog.value = true
  indForm.name = ''
  indForm.sort = 0
  await loadIndustries()
}

// 设置对话框里需要看到全部行业（含停用），不走 enabled_only 缓存
async function loadIndustries() {
  indLoading.value = true
  try {
    const res = await getIndustries()
    industryList.value = Array.isArray(res) ? res : (res?.items || [])
  } finally {
    indLoading.value = false
  }
}

async function handleAddIndustry() {
  if (!indForm.name.trim()) {
    ElMessage.warning('请输入行业名称')
    return
  }
  indSaving.value = true
  try {
    await createIndustry({ name: indForm.name.trim(), sort: indForm.sort })
    ElMessage.success('已新增')
    indForm.name = ''
    indForm.sort = 0
    await loadIndustries()
    industryStore.refresh()
  } finally {
    indSaving.value = false
  }
}

function openIndustryEdit(row) {
  Object.assign(indEdit, { id: row.id, name: row.name, sort: row.sort ?? 0 })
  indEditDialog.value = true
}

async function handleSaveIndustry() {
  if (!indEdit.name.trim()) return
  indSaving.value = true
  try {
    await updateIndustry(indEdit.id, { name: indEdit.name.trim(), sort: indEdit.sort })
    ElMessage.success('已保存')
    indEditDialog.value = false
    await loadIndustries()
    industryStore.refresh()
  } finally {
    indSaving.value = false
  }
}

async function toggleIndustry(row, val) {
  await updateIndustry(row.id, { enabled: val })
  row.enabled = val
  ElMessage.success(val ? '已启用' : '已停用')
  industryStore.refresh()
}

async function handleDeleteIndustry(row) {
  await ElMessageBox.confirm(`确定删除行业「${row.name}」吗？`, '删除确认', { type: 'warning' })
  await deleteIndustry(row.id)
  ElMessage.success('删除成功')
  await loadIndustries()
  industryStore.refresh()
}

onMounted(() => {
  loadList()
  industryStore.load()
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
.clickable-rows :deep(.el-table__row) {
  cursor: pointer;
}
.multi-tag {
  margin: 2px 6px 2px 0;
}
.ind-add {
  display: flex;
  gap: 10px;
}
.import-mode {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
}
.import-mode-label {
  font-size: 13px;
  color: var(--app-ink-2);
}
.import-summary {
  margin-top: 14px;
}
.dup-tip {
  margin: -6px 0 12px 80px;
}
.dup-item {
  line-height: 1.8;
}
</style>
