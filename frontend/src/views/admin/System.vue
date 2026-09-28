<template>
  <div v-loading="loading">
    <div class="sys-toolbar">
      <span class="hint">每 30 秒自动刷新</span>
      <div class="toolbar-actions">
        <el-button :icon="Delete" :loading="maintaining" @click="doMaintenance('vacuum')">清理空间</el-button>
        <el-button :icon="MagicStick" :loading="maintaining" @click="doMaintenance('reindex')">重建向量索引</el-button>
        <el-button :icon="Files" :loading="maintaining" @click="doMaintenance('cleanup')">清理孤儿文件</el-button>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <!-- 资源卡 -->
    <el-row :gutter="16">
      <el-col v-for="r in resources" :key="r.label" :xs="24" :sm="8">
        <el-card class="res-card" shadow="never">
          <div class="res-body">
            <el-progress
              type="dashboard"
              :percentage="r.percent"
              :color="r.color"
              :width="110"
            />
            <div class="res-info">
              <div class="res-label">{{ r.label }}</div>
              <div class="res-detail">{{ r.detail }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px">
      <!-- 数据库 -->
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header>
            <div class="card-head">
              <span class="card-title">数据库</span>
              <el-tag size="small" type="info">总大小 {{ data.db?.size_pretty || '-' }}</el-tag>
            </div>
          </template>
          <el-table :data="dbTables" size="small">
            <el-table-column prop="name" label="表" min-width="160" show-overflow-tooltip />
            <el-table-column label="行数" width="120">
              <template #default="{ row }"><span class="tnum">{{ row.rows.toLocaleString() }}</span></template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!dbTables.length" description="暂无数据" :image-size="60" />
        </el-card>
      </el-col>

      <!-- 业务计数 + 运行信息 -->
      <el-col :xs="24" :lg="12">
        <el-card shadow="never">
          <template #header><span class="card-title">业务数据</span></template>
          <div class="counts">
            <div v-for="c in countItems" :key="c.label" class="count-item">
              <div class="count-value tnum">{{ c.value }}</div>
              <div class="count-label">{{ c.label }}</div>
            </div>
            <el-empty v-if="!countItems.length" description="暂无数据" :image-size="60" />
          </div>
        </el-card>
        <el-card shadow="never" style="margin-top: 16px">
          <template #header><span class="card-title">运行信息</span></template>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="Python 版本">{{ data.python_version || '-' }}</el-descriptions-item>
            <el-descriptions-item label="运行时长">{{ formatUptime(data.uptime_seconds) }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>

    <!-- 备份 / 恢复 -->
    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        <div class="card-head">
          <span class="card-title">备份与恢复</span>
          <div>
            <el-button size="small" :icon="Plus" :loading="backingUp" @click="handleBackup">立即备份</el-button>
            <el-button size="small" :icon="Refresh" @click="loadBackups">刷新</el-button>
          </div>
        </div>
      </template>
      <el-table :data="backups" size="small" max-height="300">
        <el-table-column prop="name" label="备份" min-width="180" show-overflow-tooltip />
        <el-table-column label="时间" width="180">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="数据库" width="100">
          <template #default="{ row }">{{ formatFileSize(row.db_bytes) }}</template>
        </el-table-column>
        <el-table-column label="上传目录" width="110">
          <template #default="{ row }">{{ formatFileSize(row.uploads_bytes) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button link type="danger" size="small" @click="handleRestore(row)">恢复</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!backups.length" description="暂无备份" :image-size="60" />
      <p class="pref-tip">备份含数据库 + 上传目录；保留最近 10 份。恢复为破坏性操作（覆盖当前数据），建议维护窗口执行。</p>
    </el-card>

    <!-- 沙箱数据重置（危险操作，仅 dev/sandbox 环境显示） -->
    <el-card v-if="['dev', 'sandbox'].includes(brandStore.env)" shadow="never" class="danger-card" style="margin-top: 16px">
      <template #header>
        <span class="card-title danger-title">危险区：沙箱数据重置</span>
      </template>
      <p class="pref-tip">
        重置将自动备份当前数据，然后清空全部业务数据（客户、文档、知识库、任务等），用于试用环境快速恢复初始状态。该操作不可撤销。
      </p>
      <el-button type="danger" :loading="resetting" @click="openResetDialog">重置沙箱数据</el-button>
    </el-card>

    <!-- 重置确认：需输入 RESET -->
    <el-dialog v-model="resetDialog" title="确认重置沙箱数据" width="min(90vw, 440px)">
      <el-alert
        type="error"
        :closable="false"
        title="该操作会清空全部业务数据，不可撤销（重置前会自动备份）"
        style="margin-bottom: 14px"
      />
      <p class="pref-tip">请输入 RESET 确认操作：</p>
      <el-input v-model="resetConfirmText" placeholder="RESET" />
      <template #footer>
        <el-button @click="resetDialog = false">取消</el-button>
        <el-button type="danger" :loading="resetting" :disabled="resetConfirmText !== 'RESET'" @click="handleResetSandbox">
          确认重置
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { Refresh, Delete, MagicStick, Plus, Files } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getSystemOverview, runMaintenance, createBackup, getBackups, restoreBackup, resetSandbox } from '../../api'
import { useBrandStore } from '../../stores/brand'
import { formatUptime, formatFileSize, formatDateTime } from '../../utils/format'

const brandStore = useBrandStore()

// ========== 沙箱数据重置 ==========
const resetting = ref(false)
const resetDialog = ref(false)
const resetConfirmText = ref('')

function openResetDialog() {
  resetConfirmText.value = ''
  resetDialog.value = true
}

async function handleResetSandbox() {
  resetting.value = true
  try {
    const res = await resetSandbox()
    resetDialog.value = false
    ElMessageBox.alert(
      `业务数据已清空。重置前已自动备份：${res?.backup || '（未返回备份名）'}`,
      '重置完成',
      { confirmButtonText: '知道了' }
    )
    load()
    loadBackups()
  } catch {
    /* 拦截器已提示 */
  } finally {
    resetting.value = false
  }
}

const loading = ref(false)
const data = ref({})
const maintaining = ref(false)
const backups = ref([])
const backingUp = ref(false)
let timer = null

async function loadBackups() {
  try {
    const res = await getBackups()
    backups.value = Array.isArray(res) ? res : []
  } catch { backups.value = [] }
}

async function handleBackup() {
  backingUp.value = true
  try {
    await createBackup()
    ElMessage.success('已开始备份（后台执行）')
    setTimeout(loadBackups, 2000)
  } catch { /* 拦截器已提示 */ }
  finally { backingUp.value = false }
}

async function handleRestore(row) {
  await ElMessageBox.confirm(
    `确定从备份「${row.name}」恢复吗？将覆盖当前数据库与上传目录，不可撤销。`,
    '恢复确认',
    { type: 'error', confirmButtonText: '确认恢复' }
  )
  await restoreBackup(row.name)
  ElMessage.success('已开始恢复（后台执行，完成后请刷新页面）')
}

async function doMaintenance(action) {
  maintaining.value = true
  try {
    const res = await runMaintenance(action)
    if (action === 'cleanup') {
      ElMessage.success(`已清理 ${res.removed || 0} 个孤儿文件，释放 ${formatFileSize(res.bytes || 0)}`)
    } else {
      ElMessage.success(
        action === 'reindex'
          ? '已开始重建向量索引（后台执行，不锁写）'
          : '已开始清理空间（VACUUM，后台执行）'
      )
    }
  } catch {
    /* 拦截器已提示 */
  } finally {
    maintaining.value = false
  }
}

function percentColor(p) {
  if (p >= 90) return '#f56c6c'
  if (p >= 70) return '#e6a23c'
  return 'var(--el-color-primary)'
}

const resources = computed(() => [
  {
    label: 'CPU',
    percent: Math.round(data.value.cpu_percent ?? 0),
    detail: `${Math.round(data.value.cpu_percent ?? 0)}% 占用`,
    color: percentColor(data.value.cpu_percent ?? 0),
  },
  {
    label: '内存',
    percent: Math.round(data.value.memory?.percent ?? 0),
    detail: `${formatFileSize(data.value.memory?.used)} / ${formatFileSize(data.value.memory?.total)}`,
    color: percentColor(data.value.memory?.percent ?? 0),
  },
  {
    label: '磁盘',
    percent: Math.round(data.value.disk?.percent ?? 0),
    detail: `${formatFileSize(data.value.disk?.used)} / ${formatFileSize(data.value.disk?.total)}`,
    color: percentColor(data.value.disk?.percent ?? 0),
  },
])

// db 对象中除 size_pretty 外均为表行数
const dbTables = computed(() => {
  const db = data.value.db || {}
  return Object.entries(db)
    .filter(([k, v]) => k !== 'size_pretty' && typeof v === 'number')
    .map(([name, rows]) => ({ name, rows }))
})

const countItems = computed(() => {
  const counts = data.value.counts || {}
  const labels = { kbs: '知识库', workflows: '工作流', reports: '报告', feedback: '反馈' }
  return Object.entries(counts).map(([k, v]) => ({ label: labels[k] || k, value: v }))
})

async function load() {
  loading.value = true
  try {
    const raw = await getSystemOverview()
    // 后端实际结构：{uptime_seconds, system:{cpu_percent, memory_*_mb, disk_*_gb, *_percent}|null, database:{size, table_counts}}
    const sys = raw.system || {}
    const db = raw.database || {}
    const tc = db.table_counts || {}
    data.value = {
      uptime_seconds: raw.uptime_seconds,
      cpu_percent: sys.cpu_percent ?? 0,
      memory: {
        percent: sys.memory_percent ?? 0,
        used: (sys.memory_used_mb ?? 0) * 1024 * 1024,
        total: (sys.memory_total_mb ?? 0) * 1024 * 1024,
      },
      disk: {
        percent: sys.disk_percent ?? 0,
        used: (sys.disk_used_gb ?? 0) * 1024 * 1024 * 1024,
        total: (sys.disk_total_gb ?? 0) * 1024 * 1024 * 1024,
      },
      db: { size_pretty: db.size, ...tc },
      counts: {
        kbs: tc.knowledge_bases ?? 0,
        workflows: tc.workflows ?? 0,
        reports: tc.reports ?? 0,
        feedback: tc.ai_feedback ?? 0,
      },
    }
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  loadBackups()
  timer = setInterval(load, 30000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.sys-toolbar {
  display: flex;
  align-items: center;
  margin-bottom: 16px;
}
.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
.toolbar-actions .el-button + .el-button {
  margin-left: 0;
}
.hint {
  color: var(--app-ink-2);
  font-size: 13px;
}
.res-card {
  margin-bottom: 16px;
}
.res-body {
  display: flex;
  align-items: center;
  gap: 18px;
}
.res-label {
  font-size: 15px;
  font-weight: 600;
}
.res-detail {
  margin-top: 6px;
  font-size: 13px;
  color: var(--app-ink-2);
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-title {
  font-weight: 600;
}
.danger-card {
  border-color: var(--el-color-danger-light-5);
}
.danger-title {
  color: var(--el-color-danger);
}
.counts {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 14px;
}
.count-item {
  background: var(--app-bg);
  border-radius: var(--app-radius);
  padding: 14px;
  text-align: center;
}
.count-value {
  font-size: 22px;
  font-weight: 700;
}
.count-label {
  font-size: 12px;
  color: var(--app-ink-2);
  margin-top: 4px;
}
</style>
