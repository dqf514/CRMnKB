<template>
  <!-- 右下角悬浮指示：有进行中/待传文件时显示，点击打开进度面板 -->
  <div v-if="showFloating" class="up-float" @click="store.panelOpen = true">
    <el-icon v-if="store.activeCount" class="is-loading" :size="15"><Upload /></el-icon>
    <el-icon v-else :size="15" color="var(--el-color-success)"><CircleCheck /></el-icon>
    <span class="up-float-text">
      {{ store.activeCount ? `上传中 ${store.overallPercent}%` : '上传完成' }}
    </span>
    <el-progress :percentage="store.overallPercent" :stroke-width="4" :show-text="false" style="width: 72px" />
  </div>

  <el-drawer
    v-model="store.panelOpen"
    direction="rtl"
    size="min(420px, 92vw)"
    title="上传进度"
    class="up-drawer"
    :close-on-click-modal="false"
  >
    <div class="up-head">
      <el-progress :percentage="store.overallPercent" :stroke-width="8" />
      <div class="up-head-meta">
        <span>已上传 {{ fmtSize(store.overallLoaded) }} / {{ fmtSize(store.overallTotal) }}</span>
        <span>
          完成 {{ store.doneCount }} / {{ store.items.length }}
          <template v-if="store.skippedCount"> · 跳过 {{ store.skippedCount }}</template>
          <template v-if="store.failedCount"> · 失败 {{ store.failedCount }}</template>
        </span>
      </div>
      <div class="up-head-actions">
        <el-button size="small" :disabled="!store.running" @click="store.stop()">停止</el-button>
        <el-button size="small" @click="store.clearFinished()">清除已完成</el-button>
      </div>
    </div>

    <div class="up-list">
      <div v-for="it in store.items" :key="it.id" class="up-item" :class="it.status">
        <div class="up-item-name" :title="it.name">{{ it.name }}</div>
        <div class="up-item-meta">
          <el-icon v-if="it.status === 'uploading' || it.status === 'processing'" class="is-loading" :size="13"><Loading /></el-icon>
          <el-icon v-else-if="it.status === 'done'" :size="13" color="var(--el-color-success)"><CircleCheck /></el-icon>
          <el-icon v-else-if="it.status === 'error'" :size="13" color="var(--el-color-danger)"><CircleClose /></el-icon>
          <el-icon v-else-if="it.status === 'skipped'" :size="13" color="var(--el-color-warning)"><Warning /></el-icon>
          <el-icon v-else :size="13" color="var(--app-ink-2)"><Clock /></el-icon>
          <span class="up-item-status" :title="it.error">{{ statusText(it) }}</span>
          <span class="up-item-size">{{ fmtSize(it.size) }}</span>
          <el-button v-if="it.status === 'error'" link type="primary" size="small" @click="store.retry(it)">重试</el-button>
        </div>
        <el-progress
          v-if="it.status === 'uploading'"
          :percentage="filePercent(it)"
          :stroke-width="4"
          :show-text="false"
        />
        <!-- PST 解析：有总数显示百分比，未知总数用不定态进度条 -->
        <el-progress
          v-if="it.status === 'processing'"
          :percentage="pstPercent(it)"
          :indeterminate="!it.pst?.total"
          :stroke-width="4"
          :show-text="false"
          status="warning"
        />
      </div>
      <el-empty v-if="!store.items.length" description="暂无上传任务" :image-size="60" />
    </div>
  </el-drawer>
</template>

<script setup>
import { computed } from 'vue'
import { Upload, Loading, CircleCheck, CircleClose, Warning, Clock } from '@element-plus/icons-vue'
import { useUploadsStore } from '../stores/uploads'

const store = useUploadsStore()

const showFloating = computed(
  () => store.items.length > 0 && (store.activeCount > 0 || store.allFinished)
)

function fmtSize(n) {
  const v = n || 0
  if (v >= 1024 ** 3) return (v / 1024 ** 3).toFixed(1) + ' GB'
  if (v >= 1024 ** 2) return (v / 1024 ** 2).toFixed(1) + ' MB'
  if (v >= 1024) return (v / 1024).toFixed(1) + ' KB'
  return v + ' B'
}
function filePercent(it) {
  const t = it.total || it.size || 0
  return t ? Math.min(100, Math.round(((it.transferred || 0) / t) * 100)) : 0
}
function pstPercent(it) {
  const p = it.pst
  if (!p || !p.total) return 0
  return Math.min(100, Math.round((p.done / p.total) * 100))
}
function statusText(it) {
  if (it.status === 'uploading') return `上传中 ${fmtSize(it.transferred)}`
  if (it.status === 'processing') {
    const p = it.pst || {}
    return p.total ? `解析邮件中 ${p.done}/${p.total}` : `解析邮件中 ${p.done || 0} 封`
  }
  if (it.status === 'done') return it.pstText || '已完成'
  if (it.status === 'skipped') return it.error || '已跳过'
  if (it.status === 'error') return it.error || '失败'
  return '等待中'
}
</script>

<style scoped>
.up-float {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 2010;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--app-surface);
  border: 1px solid var(--app-line);
  border-radius: 999px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.14);
  cursor: pointer;
  font-size: 13px;
  color: var(--app-ink);
}
.up-float:hover {
  border-color: var(--el-color-primary);
}
.up-float-text {
  white-space: nowrap;
}
.up-head-meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--app-ink-2);
  margin-top: 8px;
}
.up-head-actions {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 8px;
}
.up-list {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.up-item {
  border: 1px solid var(--app-line);
  border-radius: 8px;
  padding: 8px 10px;
  background: var(--app-bg);
}
.up-item-name {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.up-item-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--app-ink-2);
}
.up-item-status {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.up-item-size {
  flex: none;
}
@media (max-width: 991px) {
  .up-float {
    right: 14px;
    bottom: 84px; /* 移动端底部标签栏上方 */
  }
}
</style>
