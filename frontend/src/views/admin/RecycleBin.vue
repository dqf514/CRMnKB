<template>
  <div>
    <el-card>
      <el-tabs v-model="query.type" @tab-change="handleSearch">
        <el-tab-pane label="客户" name="customer" />
        <el-tab-pane label="文件" name="file" />
        <el-tab-pane label="知识库" name="kb" />
        <el-tab-pane label="工作区" name="notebook" />
      </el-tabs>

      <div class="rb-toolbar">
        <el-button
          type="danger"
          :disabled="!selection.length"
          :loading="batchPurging"
          @click="handleBatchPurge"
        >彻底删除所选（{{ selection.length }}）</el-button>
        <span v-if="selection.length" class="rb-hint">已选 {{ selection.length }} 项，彻底删除后不可恢复</span>
      </div>
      <el-table :data="list" v-loading="loading" stripe @selection-change="(v) => (selection = v)">
        <el-table-column type="selection" width="42" />
        <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="recycleTypeMap[row.type]?.type" effect="plain">
              {{ recycleTypeMap[row.type]?.label || row.type }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="删除时间" width="150">
          <template #default="{ row }">{{ formatDateTime(row.deleted_at) }}</template>
        </el-table-column>
        <el-table-column label="详情摘要" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ detailText(row.detail) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="handleRestore(row)">恢复</el-button>
            <el-button link type="danger" @click="handlePurge(row)">彻底删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !list.length" description="回收站为空" :image-size="80" />

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
import { ElMessage } from 'element-plus'
import { confirmDanger } from '../../utils/confirmDanger'
import { getRecycleBin, restoreRecycleItem, deleteRecycleItem, purgeRecycleBatch } from '../../api'
import { formatDateTime } from '../../utils/format'

const recycleTypeMap = {
  customer: { label: '客户', type: 'primary' },
  file: { label: '文件', type: 'warning' },
  kb: { label: '知识库', type: 'success' },
  notebook: { label: '工作区', type: 'info' },
}

const loading = ref(false)
const list = ref([])
const total = ref(0)
const selection = ref([])
const batchPurging = ref(false)
const themeStore = useThemeStore()
const query = reactive({ type: 'customer', page: 1, page_size: themeStore.pageSize })

function onSizeChange() {
  query.page = 1
  loadList()
}

async function loadList() {
  loading.value = true
  try {
    const res = await getRecycleBin({ type: query.type, page: query.page, page_size: query.page_size })
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

function detailText(detail) {
  if (!detail) return '-'
  return typeof detail === 'string' ? detail : JSON.stringify(detail)
}

async function handleRestore(row) {
  const res = await restoreRecycleItem(row.type, row.id)
  // restored_kbs > 0 说明连带回血了客户的专属知识库
  ElMessage.success(res?.restored_kbs > 0 ? `已恢复，并连带恢复 ${res.restored_kbs} 个专属知识库` : '已恢复')
  loadList()
}

async function handlePurge(row) {
  const ok = await confirmDanger(
    `彻底删除后不可恢复，确定彻底删除「${row.name}」吗？`,
    '彻底删除确认',
    { type: 'error', confirmButtonText: '彻底删除' }
  )
  if (!ok) return
  await deleteRecycleItem(row.type, row.id)
  ElMessage.success('已彻底删除')
  loadList()
}

async function handleBatchPurge() {
  if (!selection.value.length) return
  const ok = await confirmDanger(
    `将彻底删除所选 ${selection.value.length} 项（含磁盘文件与知识库切片），不可恢复。确定继续吗？`,
    '批量彻底删除确认',
    { type: 'error', confirmButtonText: '全部彻底删除' }
  )
  if (!ok) return
  batchPurging.value = true
  try {
    const res = await purgeRecycleBatch(
      selection.value.map((r) => ({ type: r.type, id: r.id }))
    )
    if (res.failed?.length) {
      ElMessage.warning(`已删除 ${res.purged} 项，${res.failed.length} 项失败（可重试）`)
    } else {
      ElMessage.success(`已彻底删除 ${res.purged} 项`)
    }
    selection.value = []
    loadList()
  } finally {
    batchPurging.value = false
  }
}

onMounted(loadList)
</script>

<style scoped>
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
.rb-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.rb-hint {
  font-size: 12px;
  color: var(--app-ink-2);
}
</style>
