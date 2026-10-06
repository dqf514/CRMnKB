<template>
  <el-dialog v-model="visible" :title="`分享「${resource?.name || ''}」`" width="min(92vw, 520px)" append-to-body>
    <!-- 团队可见开关（仅 owner）：ON=对团队可见，OFF=私有 -->
    <div class="team-toggle">
      <el-switch :model-value="!isPrivate" :disabled="!isOwner" @change="toggleVisibility" />
      <div class="team-text">
        <div class="team-title">对团队可见</div>
        <div class="team-sub">{{ teamSubText }}</div>
      </div>
    </div>

    <!-- 添加分享（仅 owner） -->
    <div v-if="isOwner" class="share-add">
      <el-select v-model="newUserId" placeholder="选择用户" filterable style="flex: 1" :disabled="!users.length">
        <el-option v-for="u in users" :key="u.id" :label="`${u.name || u.username} (${u.username})`" :value="u.id" />
      </el-select>
      <el-select v-model="newPermission" style="width: 110px">
        <el-option v-for="(l, k) in PERM_LABELS" :key="k" :label="l" :value="k" />
      </el-select>
      <el-button type="primary" :disabled="!newUserId" :loading="saving" @click="addShare">分享</el-button>
    </div>

    <!-- 已分享用户列表 -->
    <div class="perm-list">
      <div v-if="!items.length" class="empty">尚未分享给其他用户</div>
      <div v-for="it in items" :key="it.user_id" class="perm-item">
        <el-avatar :size="26" :src="`https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(it.name || it.username)}`">
          {{ (it.name || it.username || '?')[0] }}
        </el-avatar>
        <span class="perm-name">{{ it.name || it.username }}</span>
        <el-select
          v-if="isOwner"
          :model-value="it.permission"
          size="small"
          style="width: 100px"
          @change="(v) => changePerm(it, v)"
        >
          <el-option v-for="(l, k) in PERM_LABELS" :key="k" :label="l" :value="k" />
        </el-select>
        <el-tag v-else size="small" :type="PERM_TAG[it.permission] || 'info'">{{ PERM_LABELS[it.permission] }}</el-tag>
        <el-button v-if="isOwner" link type="danger" size="small" @click="revoke(it)">撤销</el-button>
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getUsers, getResourcePermissions, shareResource, updateShare, revokeShare, setVisibility,
} from '../api'
import { useAuthStore } from '../stores/auth'

const PERM_LABELS = { read: '只读', edit: '编辑', owner: '所有权' }
const PERM_TAG = { read: 'info', edit: 'warning', owner: 'danger' }

const props = defineProps({
  modelValue: Boolean,
  resourceType: { type: String, required: true }, // kb / file / notebook / customer
  resource: { type: Object, default: null }, // {id, name, owner_id, is_private, perm}
})
const emit = defineEmits(['update:modelValue', 'changed'])
const visible = computed({ get: () => props.modelValue, set: (v) => emit('update:modelValue', v) })

// 客户是协作型资源：团队可见=全员可编辑（与后端 _TEAM_PERM 一致）
const teamSubText = computed(() =>
  props.resourceType === 'customer'
    ? '开启后团队所有成员可见并可协作编辑；关闭后仅负责人与被授权成员可见'
    : '开启后租户内所有用户只读；可再单独给某人提升编辑权限'
)

const authStore = useAuthStore()
const users = ref([])
const items = ref([])
const newUserId = ref(null)
const newPermission = ref('read')
const saving = ref(false)
const isPrivate = ref(true)

const isOwner = computed(() => {
  const me = authStore.user?.id
  if (props.resource?.perm === 'owner') return true
  return props.resource?.owner_id === me
})

async function load() {
  if (!props.resource?.id) return
  try {
    const res = await getResourcePermissions(props.resourceType, props.resource.id)
    items.value = res.items || []
    isPrivate.value = res.is_private !== false
    if (!users.value.length) {
      const us = await getUsers()
      users.value = us || []
    }
  } catch { /* 拦截器已提示 */ }
}

async function addShare() {
  saving.value = true
  try {
    await shareResource(props.resourceType, props.resource.id, { user_id: newUserId.value, permission: newPermission.value })
    ElMessage.success('已分享')
    newUserId.value = null
    await load()
    emit('changed')
  } finally { saving.value = false }
}

async function changePerm(item, permission) {
  await updateShare(props.resourceType, props.resource.id, item.user_id, { permission })
  ElMessage.success('已更新权限')
  await load()
}

async function revoke(item) {
  await revokeShare(props.resourceType, props.resource.id, item.user_id)
  ElMessage.success('已撤销分享')
  await load()
}

async function toggleVisibility(teamVisible) {
  // 开关 ON = 对团队可见（is_private=false）
  await setVisibility(props.resourceType, props.resource.id, !teamVisible)
  ElMessage.success(teamVisible ? '已对团队可见' : '已设为私有')
  isPrivate.value = !teamVisible
  emit('changed')
}

watch(() => props.modelValue, (v) => { if (v) load() })
</script>

<style scoped>
.team-toggle {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--app-line);
  border-radius: 8px;
  margin-bottom: 14px;
}
.team-title { font-size: 14px; font-weight: 600; }
.team-sub { font-size: 12px; color: var(--app-ink-2); margin-top: 2px; }
.share-add {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}
.perm-list { display: flex; flex-direction: column; gap: 8px; max-height: 46vh; overflow-y: auto; }
.perm-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--app-line);
  border-radius: 8px;
}
.perm-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty { color: var(--app-ink-2); font-size: 13px; padding: 10px 0; }
</style>
