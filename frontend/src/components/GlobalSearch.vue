<template>
  <teleport to="body">
    <div v-if="visible" class="gs-mask" @click="close">
      <div class="gs-panel" @click.stop>
        <div class="gs-input-row">
          <el-icon :size="16"><Search /></el-icon>
          <input
            ref="inputRef"
            v-model="keyword"
            class="gs-input"
            placeholder="搜索客户、文件、笔记、任务、报告…"
            @keydown.down.prevent="move(1)"
            @keydown.up.prevent="move(-1)"
            @keydown.enter.prevent="openActive"
            @keydown.esc="close"
          />
          <span class="gs-esc">Esc</span>
        </div>
        <div v-if="searched" class="gs-body">
          <template v-for="g in groups" :key="g.key">
            <div v-if="g.items.length" class="gs-group">
              <div class="gs-group-title">{{ g.label }}</div>
              <div
                v-for="item in g.items"
                :key="g.key + item.id"
                class="gs-item"
                :class="{ active: flatIndex(g.key, item.id) === activeIdx }"
                @click="open(g.key, item)"
                @mouseenter="activeIdx = flatIndex(g.key, item.id)"
              >
                <span class="gs-name">{{ item.name }}</span>
                <span class="gs-sub">{{ item.sub }}</span>
              </div>
            </div>
          </template>
          <div v-if="!flat.length" class="gs-empty">没有找到「{{ keyword }}」相关内容</div>
        </div>
        <div v-else class="gs-hint">输入关键词开始搜索 · ↑↓ 选择 · 回车打开</div>
      </div>
    </div>
  </teleport>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Search } from '@element-plus/icons-vue'
import { globalSearch } from '../api'

const router = useRouter()
const visible = ref(false)
const keyword = ref('')
const searched = ref(false)
const inputRef = ref()
const activeIdx = ref(0)
const results = ref({})
let debounceTimer = null

const GROUPS = [
  { key: 'customers', label: '客户', route: (id) => `/customers/${id}` },
  { key: 'files', label: '文件', route: () => '/library' },
  { key: 'notes', label: '笔记', route: () => '/studio' },
  { key: 'tasks', label: '任务', route: () => '/tasks' },
  { key: 'reports', label: '报告', route: () => '/studio' },
]

const groups = computed(() =>
  GROUPS.map((g) => ({ ...g, items: results.value[g.key] || [] }))
)
const flat = computed(() =>
  groups.value.flatMap((g) => g.items.map((i) => ({ group: g.key, ...i })))
)

function flatIndex(groupKey, id) {
  return flat.value.findIndex((f) => f.group === groupKey && f.id === id)
}

function move(delta) {
  if (!flat.value.length) return
  activeIdx.value = (activeIdx.value + delta + flat.value.length) % flat.value.length
}

function openActive() {
  const item = flat.value[activeIdx.value]
  if (item) open(item.group, item)
}

function open(groupKey, item) {
  const g = GROUPS.find((x) => x.key === groupKey)
  close()
  if (g) router.push(g.route(item.id))
}

async function doSearch() {
  const q = keyword.value.trim()
  if (!q) { searched.value = false; results.value = {}; return }
  try {
    results.value = await globalSearch(q)
    searched.value = true
    activeIdx.value = 0
  } catch { /* 拦截器已提示 */ }
}

watch(keyword, () => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(doSearch, 250)
})

function open_() {
  visible.value = true
  keyword.value = ''
  searched.value = false
  results.value = {}
  nextTick(() => inputRef.value?.focus())
}
function close() {
  visible.value = false
}

function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    visible.value ? close() : open_()
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
.gs-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  z-index: 3000;
  display: flex;
  justify-content: center;
  padding-top: 12vh;
}
.gs-panel {
  width: min(560px, 92vw);
  max-height: 60vh;
  background: var(--app-surface);
  border-radius: 14px;
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  height: fit-content;
}
.gs-input-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--app-line);
}
.gs-input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-size: 15px;
  color: var(--app-ink);
}
.gs-esc {
  font-size: 11px;
  color: var(--app-ink-2);
  border: 1px solid var(--app-line);
  border-radius: 4px;
  padding: 2px 6px;
}
.gs-body {
  overflow-y: auto;
  padding: 6px;
}
.gs-hint, .gs-empty {
  padding: 24px;
  text-align: center;
  color: var(--app-ink-2);
  font-size: 13px;
}
.gs-group-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--app-ink-2);
  padding: 8px 10px 4px;
}
.gs-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 8px;
  cursor: pointer;
}
.gs-item.active {
  background: var(--el-color-primary-light-9);
}
.gs-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.gs-sub {
  font-size: 11px;
  color: var(--app-ink-2);
  flex: none;
  max-width: 40%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
