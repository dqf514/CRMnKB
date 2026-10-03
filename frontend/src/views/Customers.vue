<template>
  <div>
    <el-card>
      <div class="toolbar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索名称 / 单位 / 电话"
          style="width: 220px"
          clearable
        />
        <el-select v-model="query.status" placeholder="状态" style="width: 120px" clearable @change="handleSearch">
          <el-option v-for="(v, k) in customerStatusMap" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-select v-model="query.ddq_status" placeholder="DDQ" style="width: 110px" clearable @change="handleSearch">
          <el-option v-for="(v, k) in ddqStatusMap" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-select v-model="query.industry" placeholder="行业" style="width: 130px" clearable filterable @change="handleSearch">
          <el-option v-for="i in industryStore.list" :key="i.id" :label="i.name" :value="i.name" />
        </el-select>
        <el-select v-model="query.tag" placeholder="标签" style="width: 130px" clearable filterable @change="handleSearch">
          <el-option v-for="t in tagOptions" :key="t" :label="t" :value="t" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="handleSearch">搜索</el-button>
        <!-- 低频操作：窄屏收进「更多」下拉，宽屏平铺 -->
        <el-button class="low-freq" :icon="Setting" @click="openIndustryDialog">行业设置</el-button>
        <el-button class="low-freq" :icon="Download" @click="handleDownloadTemplate">下载模板</el-button>
        <el-button class="low-freq" :icon="Upload" @click="openImport">导入</el-button>
        <el-button class="low-freq" :icon="Download" :loading="exporting" @click="handleExport">导出</el-button>
        <el-dropdown class="more-menu" trigger="click" @command="handleMoreCommand">
          <el-button :icon="MoreFilled">更多</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="industry"><el-icon><Setting /></el-icon>行业设置</el-dropdown-item>
              <el-dropdown-item command="template"><el-icon><Download /></el-icon>下载模板</el-dropdown-item>
              <el-dropdown-item command="import"><el-icon><Upload /></el-icon>导入</el-dropdown-item>
              <el-dropdown-item command="export"><el-icon><Download /></el-icon>导出</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <div class="toolbar-right">
          <el-radio-group v-model="viewMode" size="small" @change="persistViewMode">
            <el-radio-button value="card"><el-icon><Grid /></el-icon>&nbsp;卡片</el-radio-button>
            <el-radio-button value="table"><el-icon><List /></el-icon>&nbsp;表格</el-radio-button>
          </el-radio-group>
          <el-button type="success" :icon="Plus" @click="openCreate">新增客户</el-button>
        </div>
      </div>

      <!-- 加载失败：重试入口（错误细节已由拦截器 toast 提示） -->
      <div v-if="loadError" class="load-fail">
        <p>客户列表加载失败，请检查网络后重试</p>
        <el-button type="primary" @click="loadList">重试</el-button>
      </div>

      <!-- 表格视图 -->
      <el-table v-else-if="viewMode === 'table'" :data="list" v-loading="loading" stripe class="clickable-rows" @row-click="goDetail">
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
        <el-table-column label="DDQ" width="90">
          <template #default="{ row }">
            <el-tag :type="enumTagType(ddqStatusMap, row.ddq_status || 'none')">
              {{ enumLabel(ddqStatusMap, row.ddq_status || 'none') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="goDetail(row)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 卡片视图 -->
      <div v-else-if="!loadError" v-loading="loading" class="card-grid">
        <div v-for="row in list" :key="row.id" class="customer-card" @click="goDetail(row)">
          <div class="cc-head">
            <span class="cc-name">{{ row.name }}</span>
            <el-tag size="small" :type="enumTagType(customerStatusMap, row.status)">
              {{ enumLabel(customerStatusMap, row.status) }}
            </el-tag>
          </div>
          <div class="cc-company">{{ row.company || '—' }}</div>
          <div class="cc-tags">
            <el-tag v-for="t in (row.industries || []).slice(0, 2)" :key="'i' + t" size="small" type="info" effect="plain" class="multi-tag">{{ t }}</el-tag>
            <el-tag v-for="t in (row.tags || []).slice(0, 2)" :key="'t' + t" size="small" class="multi-tag">{{ t }}</el-tag>
            <el-tag
              v-if="(row.ddq_status || 'none') !== 'none'"
              size="small"
              :type="enumTagType(ddqStatusMap, row.ddq_status)"
              effect="plain"
              class="multi-tag"
            >DDQ·{{ enumLabel(ddqStatusMap, row.ddq_status) }}</el-tag>
          </div>
          <div class="cc-foot">
            <span class="cc-phone">{{ row.phone || '—' }}</span>
            <span class="cc-time">{{ formatDateTime(row.created_at) }}</span>
          </div>
        </div>
        <el-empty v-if="!loading && !list.length" description="暂无客户" style="grid-column: 1 / -1" />
      </div>

      <el-pagination
        v-if="!loadError"
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

    <!-- 新增客户抽屉（编辑入口在详情页） -->
    <CustomerFormDrawer v-model="drawerVisible" :customer="null" @saved="loadList" />

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
import { ref, reactive, computed, onMounted } from 'vue'
import { useThemeStore } from '../stores/theme'
import { useRouter } from 'vue-router'
import { Search, Plus, Setting, Upload, Download, UploadFilled, Grid, List, MoreFilled } from '@element-plus/icons-vue'
import { watchDebounced } from '../utils/watchDebounced'
import { ElMessage } from 'element-plus'
import {
  getCustomers,
  getIndustries, createIndustry, updateIndustry, deleteIndustry,
  getCustomerImportTemplate, importCustomers, exportCustomers,
} from '../api'
import { useIndustryStore } from '../stores/industries'
import CustomerFormDrawer from '../components/CustomerFormDrawer.vue'
import { customerStatusMap, ddqStatusMap, enumLabel, enumTagType, formatDateTime, CUSTOMER_TAG_PRESETS } from '../utils/format'
import { usePagedFetch } from '../utils/usePagedFetch'
import { confirmDanger } from '../utils/confirmDanger'

const router = useRouter()
const industryStore = useIndustryStore()
// 列表请求防竞态（序号法），loading 由 composable 管理
const { loading, run: runFetch } = usePagedFetch()
const list = ref([])
const total = ref(0)
const loadError = ref(false)
const themeStore = useThemeStore()
const query = reactive({ keyword: '', status: '', ddq_status: '', industry: '', tag: '', page: 1, page_size: themeStore.pageSize })

// 卡片/表格视图切换，偏好存 localStorage
const viewMode = ref(localStorage.getItem('customerViewMode') || 'table')
function persistViewMode(v) {
  localStorage.setItem('customerViewMode', v)
}

function onSizeChange() {
  query.page = 1
  loadList()
}

// 标签筛选选项：预设 + 当前列表里出现过的自定义标签
const tagOptions = computed(() => {
  const set = new Set(CUSTOMER_TAG_PRESETS)
  list.value.forEach((c) => (c.tags || []).forEach((t) => set.add(t)))
  return [...set]
})

// 列表查询参数构造：列表请求与导出共用（导出不含分页参数），保证过滤条件一致
function buildQueryParams({ withPage = true } = {}) {
  const params = {}
  if (withPage) {
    params.page = query.page
    params.page_size = query.page_size
  }
  if (query.keyword) params.keyword = query.keyword
  if (query.status) params.status = query.status
  if (query.industry) params.industry = query.industry
  if (query.tag) params.tag = query.tag
  if (query.ddq_status) params.ddq_status = query.ddq_status
  return params
}

async function loadList() {
  loadError.value = false
  try {
    await runFetch(
      () => getCustomers(buildQueryParams()),
      (res) => {
        list.value = res.items || []
        total.value = res.total || 0
      }
    )
  } catch {
    // 拦截器已 toast，这里切到重试态避免只剩空表
    loadError.value = true
  }
}

function handleSearch() {
  query.page = 1
  loadList()
}

// 窄屏「更多」下拉的命令分发（与平铺按钮同一批处理函数）
function handleMoreCommand(cmd) {
  if (cmd === 'industry') openIndustryDialog()
  else if (cmd === 'template') handleDownloadTemplate()
  else if (cmd === 'import') openImport()
  else if (cmd === 'export') handleExport()
}

// 关键字防抖即搜（300ms）：输入停顿或清空后自动搜索，无需回车
watchDebounced(() => query.keyword, handleSearch)

const drawerVisible = ref(false)

function openCreate() {
  drawerVisible.value = true
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
    // 与列表共用过滤参数（含 ddq_status），不带分页
    const blob = await exportCustomers(buildQueryParams({ withPage: false }))
    downloadBlob(blob, '客户导出.xlsx')
  } finally {
    exporting.value = false
  }
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
  if (!(await confirmDanger(`确定删除行业「${row.name}」吗？`))) return
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
/* 窄屏：低频操作收进「更多」下拉，只留搜索/新增等主操作外露 */
.more-menu {
  display: none;
}
@media (max-width: 768px) {
  .toolbar .low-freq {
    display: none;
  }
  .more-menu {
    display: inline-flex;
  }
}
/* 首屏/刷新失败重试态 */
.load-fail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 60px 0;
  color: var(--app-ink-2);
  font-size: 14px;
}
.toolbar-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 12px;
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

/* ===== 卡片视图 ===== */
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 14px;
  min-height: 120px;
}
.customer-card {
  border: 1px solid var(--app-line);
  border-radius: 12px;
  padding: 14px 16px;
  background: var(--el-bg-color);
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.customer-card:hover {
  transform: translateY(-2px);
  border-color: var(--el-color-primary-light-5);
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.08);
}
.cc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.cc-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--app-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cc-company {
  font-size: 13px;
  color: var(--app-ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cc-tags {
  min-height: 24px;
}
.cc-foot {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--app-ink-2);
  border-top: 1px dashed var(--app-line);
  padding-top: 8px;
}
.cc-phone {
  font-variant-numeric: tabular-nums;
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
</style>
