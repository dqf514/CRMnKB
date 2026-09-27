<template>
  <div>
    <el-card>
      <div class="toolbar">
        <el-select v-model="query.level" placeholder="级别" style="width: 120px" clearable @change="handleSearch">
          <el-option v-for="(v, k) in errorLevelMap" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-select v-model="query.resolved" placeholder="状态" style="width: 130px" clearable @change="handleSearch">
          <el-option label="未解决" value="false" />
          <el-option label="已解决" value="true" />
        </el-select>
        <el-button style="margin-left: auto" type="warning" plain :loading="resolvingAll" @click="handleResolveAll">
          全部标记已解决
        </el-button>
      </div>

      <el-table :data="list" v-loading="loading" stripe>
        <el-table-column type="expand">
          <template #default="{ row }">
            <pre class="error-detail">{{ row.detail || '无详情' }}</pre>
          </template>
        </el-table-column>
        <el-table-column label="级别" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="enumTagType(errorLevelMap, row.level)">
              {{ enumLabel(errorLevelMap, row.level) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="module" label="模块" width="130" show-overflow-tooltip />
        <el-table-column prop="message" label="消息" min-width="220" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.resolved ? 'success' : 'info'" effect="plain">
              {{ row.resolved ? '已解决' : '未解决' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button v-if="!row.resolved" link type="primary" size="small" @click="handleResolve(row)">标记已解决</el-button>
            <span v-else class="resolved-text">-</span>
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
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useThemeStore } from '../../stores/theme'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getAdminErrors, resolveAdminError, resolveAllAdminErrors } from '../../api'
import { errorLevelMap, enumLabel, enumTagType, formatDateTime } from '../../utils/format'

const loading = ref(false)
const resolvingAll = ref(false)
const list = ref([])
const total = ref(0)
const themeStore = useThemeStore()
const query = reactive({ level: '', resolved: '', page: 1, page_size: themeStore.pageSize })

function onSizeChange() {
  query.page = 1
  loadList()
}

async function loadList() {
  loading.value = true
  try {
    const params = { page: query.page, page_size: query.page_size }
    if (query.level) params.level = query.level
    if (query.resolved !== '') params.resolved = query.resolved
    const res = await getAdminErrors(params)
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

async function handleResolve(row) {
  await resolveAdminError(row.id)
  row.resolved = true
  ElMessage.success('已标记为已解决')
}

async function handleResolveAll() {
  await ElMessageBox.confirm('确定将全部异常日志标记为已解决吗？', '提示', { type: 'warning' })
  resolvingAll.value = true
  try {
    await resolveAllAdminErrors()
    ElMessage.success('已全部标记为已解决')
    loadList()
  } finally {
    resolvingAll.value = false
  }
}

onMounted(loadList)
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
.error-detail {
  margin: 0;
  padding: 10px 16px;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  color: var(--app-ink-2);
  background: var(--app-bg);
  border-radius: 8px;
}
.resolved-text {
  color: var(--app-ink-2);
  font-size: 12px;
}
</style>
