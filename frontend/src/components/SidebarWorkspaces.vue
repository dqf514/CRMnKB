<template>
  <!-- 侧栏工作区列表：点击切换当前工作区并跳转工作台，hover 出"···"菜单（重命名/共享/删除） -->
  <div class="ws-list" v-loading="studio.loadingNotebooks && !studio.notebooks.length">
    <div v-if="!studio.notebooks.length" class="ws-empty">还没有工作区，点上方「新的任务」新建</div>
    <div
      v-for="nb in studio.notebooks"
      :key="nb.id"
      class="ws-item"
      :class="{ active: studio.currentNotebook?.id === nb.id }"
      :title="nb.name + (isMine(nb) ? '（我创建的）' : '（共享给我的）')"
      @click="open(nb)"
    >
      <!-- 圆点区分归属：主色=我创建的，琥珀色=共享给我的 -->
      <span class="ws-dot" :class="{ shared: !isMine(nb) }" />
      <span class="ws-name">{{ nb.name }}</span>
      <el-dropdown trigger="click" @command="(cmd) => onCommand(cmd, nb)">
        <span class="ws-more" @click.stop>
          <el-icon :size="14"><MoreFilled /></el-icon>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="rename"><el-icon><Edit /></el-icon>重命名</el-dropdown-item>
            <el-dropdown-item command="share"><el-icon><Share /></el-icon>共享</el-dropdown-item>
            <el-dropdown-item command="delete" divided class="ws-danger">
              <el-icon><Delete /></el-icon>删除
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <ShareDialog v-model="shareDialog" resource-type="notebook" :resource="shareTarget" @changed="studio.loadNotebooks()" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { MoreFilled, Edit, Share, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useStudioStore } from '../stores/studio'
import { useAuthStore } from '../stores/auth'
import { updateNotebook, deleteNotebook } from '../api'
import ShareDialog from './ShareDialog.vue'

const studio = useStudioStore()
const authStore = useAuthStore()
const router = useRouter()

// 工作区归属：自己创建 / 他人共享（与 Studio 原标签栏的配色语义一致）
const currentUserId = computed(() => authStore.user?.id)
function isMine(nb) {
  return nb.created_by === currentUserId.value
}

// 挂载时若列表未加载则触发加载（工作区数据原由 Studio 页加载，现在侧栏常驻需要兜底）
onMounted(() => {
  if (!studio.notebooks.length && !studio.loadingNotebooks) {
    studio.loadNotebooks()
  }
})

// 点击切换当前工作区并跳转工作台；会话历史按需恢复（store 内部有防覆盖保护）
async function open(nb) {
  if (studio.currentNotebook?.id !== nb.id) {
    await studio.openNotebook(nb.id)
    await studio.loadChatHistory(nb.id)
  }
  router.push('/studio')
}

const shareDialog = ref(false)
const shareTarget = ref(null)

async function onCommand(cmd, nb) {
  if (cmd === 'rename') {
    const { value: name } = await ElMessageBox.prompt('新名称', '重命名', {
      inputValue: nb.name,
      inputPattern: /.+/,
    }).catch(() => ({ value: null }))
    if (!name) return
    await updateNotebook(nb.id, { name })
    await studio.loadNotebooks()
    if (studio.currentNotebook?.id === nb.id) {
      studio.currentNotebook = { ...studio.currentNotebook, name }
    }
    ElMessage.success('已重命名')
  } else if (cmd === 'share') {
    shareTarget.value = { id: nb.id, name: nb.name, owner_id: nb.created_by, is_private: nb.is_private, perm: nb.perm }
    shareDialog.value = true
  } else if (cmd === 'delete') {
    await ElMessageBox.confirm(
      `确定删除工作区「${nb.name}」？将移入回收站，可随时恢复。`,
      '删除确认', { type: 'warning' }
    )
    await deleteNotebook(nb.id)
    studio.clearChat(nb.id)
    if (studio.currentNotebook?.id === nb.id) {
      studio.currentNotebook = null
    }
    await studio.loadNotebooks()
    ElMessage.success('已删除')
  }
}
</script>

<style scoped>
.ws-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-height: 60px;
}
.ws-empty {
  padding: 12px 10px;
  font-size: 12px;
  color: var(--app-ink-3);
  line-height: 1.6;
}
.ws-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  color: var(--app-ink-2);
  transition: background 0.15s ease, color 0.15s ease;
}
.ws-item:hover {
  background: var(--sidebar-hover);
  color: var(--app-ink);
}
.ws-item.active {
  background: var(--app-surface);
  color: var(--app-ink);
  font-weight: 600;
  box-shadow: var(--app-shadow);
}
.ws-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: none;
  background: var(--el-color-primary);
}
.ws-dot.shared {
  background: #e6a23c;
}
.ws-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* "···"按钮默认隐藏，hover/选中时浮现，保持列表干净 */
.ws-more {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  color: var(--app-ink-3);
  opacity: 0;
  transition: opacity 0.15s ease, background 0.15s ease;
}
.ws-item:hover .ws-more,
.ws-item.active .ws-more {
  opacity: 1;
}
.ws-more:hover {
  background: var(--sidebar-hover);
  color: var(--app-ink);
}
</style>

<style>
/* 删除项红色（el-dropdown 菜单挂在 body，需全局样式） */
.ws-danger {
  color: var(--el-color-danger) !important;
}
</style>
