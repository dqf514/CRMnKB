<template>
  <div>
    <el-card>
      <div class="toolbar">
        <el-select
          v-model="query.action"
          placeholder="操作类型"
          style="width: 140px"
          clearable
          filterable
          allow-create
          @change="handleSearch"
        >
          <el-option v-for="a in actionPresets" :key="a" :label="a" :value="a" />
        </el-select>
        <el-select
          v-model="query.resource_type"
          placeholder="资源类型"
          style="width: 140px"
          clearable
          filterable
          allow-create
          @change="handleSearch"
        >
          <el-option v-for="r in resourcePresets" :key="r" :label="r" :value="r" />
        </el-select>
        <el-select v-model="query.user_id" placeholder="用户" style="width: 140px" clearable filterable @change="handleSearch">
          <el-option v-for="u in userOptions" :key="u.id" :label="u.username" :value="u.id" />
        </el-select>
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          style="width: 240px"
          @change="handleSearch"
        />
        <el-button type="primary" :icon="Search" @click="handleSearch">搜索</el-button>
      </div>

      <el-table :data="list" v-loading="loading" stripe>
        <el-table-column type="expand">
          <template #default="{ row }">
            <pre class="log-detail">{{ detailText(row.detail) }}</pre>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="用户" width="110" show-overflow-tooltip>
          <template #default="{ row }">{{ userName(row.user_id) }}</template>
        </el-table-column>
        <el-table-column prop="action" label="操作" width="120" show-overflow-tooltip />
        <el-table-column label="资源类型" width="110" show-overflow-tooltip>
          <template #default="{ row }">{{ row.resource_type || '-' }}</template>
        </el-table-column>
        <el-table-column label="资源ID" width="90" show-overflow-tooltip>
          <template #default="{ row }">{{ row.resource_id ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="详情" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ detailText(row.detail) }}</template>
        </el-table-column>
        <el-table-column label="IP" width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.ip || '-' }}</template>
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
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useThemeStore } from '../../stores/theme'
import { Search } from '@element-plus/icons-vue'
import { getAdminUsers, getAuditLogs } from '../../api'
import { formatDateTime } from '../../utils/format'

// 预设项仅作快捷选项，可手动输入其他值
const actionPresets = ['login', 'create', 'update', 'delete', 'export', 'import', 'restore']
const resourcePresets = ['customer', 'file', 'kb', 'task', 'user', 'workflow', 'report']

const loading = ref(false)
const list = ref([])
const total = ref(0)
const userOptions = ref([])
const dateRange = ref([])
const themeStore = useThemeStore()
const query = reactive({ action: '', resource_type: '', user_id: '', page: 1, page_size: themeStore.pageSize })

function onSizeChange() {
  query.page = 1
  loadList()
}

async function loadList() {
  loading.value = true
  try {
    const params = { page: query.page, page_size: query.page_size }
    if (query.action) params.action = query.action
    if (query.resource_type) params.resource_type = query.resource_type
    if (query.user_id !== '' && query.user_id !== null) params.user_id = query.user_id
    if (dateRange.value?.[0]) params.start = dateRange.value[0]
    if (dateRange.value?.[1]) params.end = dateRange.value[1]
    const res = await getAuditLogs(params)
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

async function loadUsers() {
  try {
    const res = await getAdminUsers({ page: 1, page_size: 200 })
    userOptions.value = res.items || []
  } catch {
    /* 用户下拉加载失败仍可手动筛选 */
  }
}

function userName(id) {
  if (id === null || id === undefined) return '-'
  return userOptions.value.find((u) => u.id === id)?.username || `#${id}`
}

function detailText(detail) {
  if (!detail) return '无详情'
  return typeof detail === 'string' ? detail : JSON.stringify(detail, null, 2)
}

onMounted(() => {
  loadList()
  loadUsers()
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
.log-detail {
  margin: 0;
  padding: 10px 16px;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  color: var(--app-ink-2);
  background: var(--app-bg);
  border-radius: 8px;
}
</style>
