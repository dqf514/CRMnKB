<template>
  <div class="profile-page">
    <div class="page-header">
      <h1 class="page-title-main">个人中心</h1>
      <p class="page-sub">管理账号信息、密码与界面偏好</p>
    </div>

    <el-row :gutter="16">
      <!-- 基本信息 -->
      <el-col :xs="24" :lg="10">
        <el-card>
          <template #header><span class="card-title">基本信息</span></template>
          <div class="avatar-block">
            <div class="avatar-wrap" :title="avatarDisplay ? '点击更换头像' : '上传头像'" @click="avatarInputRef?.click()">
              <el-avatar :size="72" :src="avatarDisplay" class="avatar">
                {{ avatarFallback }}
              </el-avatar>
              <div class="avatar-edit"><el-icon :size="14"><Camera /></el-icon></div>
            </div>
            <input ref="avatarInputRef" type="file" accept="image/*" style="display: none" @change="onAvatarPick" />
          </div>
          <el-form :model="profileForm" :rules="profileRules" ref="profileFormRef" label-width="90px">
            <el-form-item label="姓名" required>
              <el-input v-model="profileForm.name" placeholder="显示名称" />
            </el-form-item>
            <el-form-item label="邮箱" prop="email">
              <el-input v-model="profileForm.email" placeholder="电子邮箱" />
            </el-form-item>
            <el-form-item label="手机号" prop="phone">
              <el-input v-model="profileForm.phone" placeholder="手机号（后续可用于手机/微信登录）" maxlength="11" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="profileSaving" @click="saveProfile">保存修改</el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <!-- 修改密码 -->
      <el-col :xs="24" :lg="14">
        <el-card>
          <template #header><span class="card-title">修改密码</span></template>
          <el-alert
            v-if="authStore.user?.must_change_password"
            type="warning"
            :closable="false"
            title="首次登录请先修改初始密码"
            description="当前账号仍在使用初始密码，修改完成后才能继续使用系统其他功能。"
            style="margin-bottom: 12px"
          />
          <el-form :model="pwdForm" :rules="pwdRules" ref="pwdFormRef" label-width="90px" style="max-width: 460px">
            <el-form-item label="旧密码" prop="old_password">
              <el-input v-model="pwdForm.old_password" type="password" show-password placeholder="当前密码" />
            </el-form-item>
            <el-form-item label="新密码" prop="new_password">
              <el-input v-model="pwdForm.new_password" type="password" show-password placeholder="至少 8 位" />
            </el-form-item>
            <el-form-item label="确认密码" prop="confirm_password">
              <el-input v-model="pwdForm.confirm_password" type="password" show-password placeholder="再次输入新密码" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="pwdSaving" @click="savePassword">修改密码</el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <!-- 偏好设置 -->
        <el-card style="margin-top: 16px">
          <template #header><span class="card-title">偏好设置</span></template>
          <el-form label-width="90px" style="max-width: 460px">
            <el-form-item label="主题模式">
              <el-radio-group :model-value="themeStore.mode" @change="themeStore.setMode($event)">
                <el-radio-button value="light">亮色</el-radio-button>
                <el-radio-button value="dark">暗色</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="主题色">
              <div class="accent-list">
                <span
                  v-for="(v, k) in ACCENTS"
                  :key="k"
                  class="accent-swatch"
                  :class="{ active: themeStore.accent === k }"
                  :style="{ background: v.color }"
                  :title="v.label"
                  @click="themeStore.setAccent(k)"
                >
                  <el-icon v-if="themeStore.accent === k" :size="14" color="#fff"><Check /></el-icon>
                </span>
              </div>
            </el-form-item>
            <el-form-item label="字号">
              <el-radio-group :model-value="themeStore.fontSize" @change="themeStore.setFontSize($event)">
                <el-radio-button v-for="(o, k) in FONT_SIZES" :key="k" :value="k">{{ o.label }}</el-radio-button>
              </el-radio-group>
              <span class="pref-tip" style="margin-left: 10px">小 / 中 / 大，全局生效</span>
            </el-form-item>
            <el-form-item label="每页条数">
              <el-select :model-value="themeStore.pageSize" style="width: 160px" @change="themeStore.setPageSize($event)">
                <el-option v-for="n in [10, 20, 50]" :key="n" :label="`${n} 条/页`" :value="n" />
              </el-select>
            </el-form-item>
          </el-form>
          <p class="pref-tip">偏好即时生效，并同步保存到账号。</p>
        </el-card>
      </el-col>
    </el-row>

    <!-- 我的记忆：agent 对话自动沉淀的长期偏好/事实，跨工作区生效，仅本人可见 -->
    <el-card style="margin-top: 16px">
      <template #header>
        <div class="memory-header">
          <span class="card-title">我的记忆</span>
          <div>
            <el-button size="small" type="primary" plain :icon="Plus" @click="onAddMemory">添加</el-button>
            <el-button
              size="small" type="danger" plain :disabled="!memories.length"
              @click="onClearMemories"
            >清空</el-button>
          </div>
        </div>
      </template>
      <p class="pref-tip" style="margin-top: 0">
        AI 对话中了解到的长期偏好/背景会沉淀在这里，并在新对话中自动生效；仅本人可见。发现记错了可直接编辑或删除。
      </p>
      <el-empty v-if="!memories.length" description="暂无记忆，对话中告诉 AI 你的偏好即可自动积累" :image-size="60" />
      <div v-for="m in memories" :key="m.id" class="memory-item">
        <div class="memory-content">{{ m.content }}</div>
        <div class="memory-meta">
          <el-tag size="small" :type="m.source === 'agent' ? 'success' : 'info'" effect="plain">
            {{ m.source === 'agent' ? 'AI 记录' : '手动添加' }}
          </el-tag>
          <span class="memory-time">{{ fmtMemoryTime(m.updated_at || m.created_at) }}</span>
          <el-button link size="small" type="primary" @click="onEditMemory(m)">编辑</el-button>
          <el-button link size="small" type="danger" @click="onDeleteMemory(m)">删除</el-button>
        </div>
      </div>
    </el-card>

    <!-- 头像裁剪：正方形选区 -->
    <el-dialog v-model="cropDialog" title="裁剪头像（正方形）" width="min(90vw, 480px)" append-to-body :close-on-click-modal="false">
      <div class="crop-wrap">
        <img ref="cropImgRef" :src="cropSrc" alt="头像裁剪" style="max-width: 100%" />
      </div>
      <template #footer>
        <el-button @click="closeCrop">取消</el-button>
        <el-button type="primary" @click="confirmCrop">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { Check, Camera, Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { confirmDanger } from '../utils/confirmDanger'
import { validateForm } from '../utils/validateForm'
import Cropper from 'cropperjs'
import { useAuthStore } from '../stores/auth'
import { useThemeStore, ACCENTS, FONT_SIZES } from '../stores/theme'
import { updateProfile, updatePassword, getMe, uploadAvatar, getMemories, addMemory, updateMemory, deleteMemory, clearMemories } from '../api'

const router = useRouter()
const authStore = useAuthStore()
const themeStore = useThemeStore()

// ========== 基本信息 ==========
const profileSaving = ref(false)
const profileFormRef = ref()
const profileForm = reactive({ avatar_url: '', name: '', email: '', phone: '' })
const profileRules = {
  email: [{ type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
  phone: [{ pattern: /^1[3-9]\d{9}$/, message: '手机号格式不正确', trigger: 'blur' }],
}

const avatarFallback = computed(() => {
  const n = profileForm.name || authStore.user?.username || '用'
  return n.slice(0, 1).toUpperCase()
})

// ========== 头像上传 + 正方形裁剪 ==========
const avatarInputRef = ref()
const cropDialog = ref(false)
const cropSrc = ref('')
const cropImgRef = ref()
const pendingAvatar = ref('') // 待保存的预览（objectURL）
const pendingAvatarFile = ref(null) // 待保存的裁剪结果（Blob）
let cropper = null

const avatarDisplay = computed(() => pendingAvatar.value || profileForm.avatar_url || undefined)

function onAvatarPick(e) {
  const file = e.target.files?.[0]
  e.target.value = ''
  if (!file) return
  if (!file.type.startsWith('image/')) {
    ElMessage.warning('请选择图片文件')
    return
  }
  if (file.size > 5 * 1024 * 1024) {
    ElMessage.warning('图片不能超过 5MB')
    return
  }
  cropSrc.value = URL.createObjectURL(file)
  cropDialog.value = true
  nextTick(() => {
    if (cropper) cropper.destroy()
    if (cropImgRef.value) {
      cropper = new Cropper(cropImgRef.value, {
        aspectRatio: 1,
        viewMode: 1,
        autoCropArea: 1,
        background: false,
      })
    }
  })
}

function closeCrop() {
  cropDialog.value = false
  if (cropper) {
    cropper.destroy()
    cropper = null
  }
  if (cropSrc.value) {
    URL.revokeObjectURL(cropSrc.value)
    cropSrc.value = ''
  }
}

function confirmCrop() {
  if (!cropper) return
  const canvas = cropper.getCroppedCanvas({ width: 200, height: 200 })
  closeCrop()
  canvas.toBlob((blob) => {
    if (!blob) {
      ElMessage.warning('裁剪失败，请重试')
      return
    }
    if (pendingAvatar.value) URL.revokeObjectURL(pendingAvatar.value)
    pendingAvatar.value = URL.createObjectURL(blob)
    pendingAvatarFile.value = blob
    ElMessage.success('已裁剪，点「保存修改」生效')
  }, 'image/png')
}

function fillProfile() {
  const u = authStore.user || {}
  profileForm.avatar_url = u.avatar_url || ''
  profileForm.name = u.name || ''
  profileForm.email = u.email || ''
  profileForm.phone = u.phone || ''
}

async function saveProfile() {
  if (!profileForm.name.trim()) {
    ElMessage.warning('请填写姓名')
    return
  }
  if (!(await validateForm(profileFormRef.value))) return
  profileSaving.value = true
  try {
    if (pendingAvatarFile.value) {
      // 头像只走上传端点（后端 MIME/魔数校验）；profile 接口不再接受 avatar_url
      const res = await uploadAvatar(pendingAvatarFile.value)
      profileForm.avatar_url = res.avatar_url || ''
      if (pendingAvatar.value) URL.revokeObjectURL(pendingAvatar.value)
      pendingAvatar.value = ''
      pendingAvatarFile.value = null
    }
    await updateProfile({
      name: profileForm.name,
      email: profileForm.email || null,
      phone: profileForm.phone || null,
    })
    const me = await getMe()
    authStore.user = me
    localStorage.setItem('user', JSON.stringify(me))
    ElMessage.success('已保存')
  } finally {
    profileSaving.value = false
  }
}

// ========== 修改密码 ==========
const pwdSaving = ref(false)
const pwdFormRef = ref()
const pwdForm = reactive({ old_password: '', new_password: '', confirm_password: '' })
const pwdRules = {
  old_password: [{ required: true, message: '请输入旧密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 8, message: '新密码至少 8 位', trigger: 'blur' },
  ],
  confirm_password: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (r, v, cb) => (v === pwdForm.new_password ? cb() : cb(new Error('两次输入的密码不一致'))),
      trigger: 'blur',
    },
  ],
}

async function savePassword() {
  if (!(await validateForm(pwdFormRef.value))) return
  pwdSaving.value = true
  try {
    await updatePassword({ old_password: pwdForm.old_password, new_password: pwdForm.new_password })
    ElMessage.success('密码已修改，请重新登录')
    authStore.logout()
    router.push('/login')
  } finally {
    pwdSaving.value = false
  }
}

onMounted(async () => {
  fillProfile()
  loadMemories()
  try {
    const me = await getMe()
    authStore.user = me
    localStorage.setItem('user', JSON.stringify(me))
    fillProfile()
  } catch {
    /* 用本地缓存 */
  }
})

// ========== 我的记忆 ==========
const memories = ref([])

async function loadMemories() {
  try {
    const res = await getMemories()
    memories.value = res?.items || []
  } catch {
    /* 拦截器已提示 */
  }
}

function fmtMemoryTime(iso) {
  if (!iso) return ''
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

async function onAddMemory() {
  const { value } = await ElMessageBox.prompt('记录一条长期偏好或背景（如「报告默认用中文」）', '添加记忆', {
    confirmButtonText: '保存',
    cancelButtonText: '取消',
    inputType: 'textarea',
    inputValidator: (v) => (v && v.trim() ? true : '内容不能为空'),
  }).catch(() => ({ value: null }))
  if (value == null) return
  await addMemory(value.trim())
  ElMessage.success('已保存')
  loadMemories()
}

async function onEditMemory(m) {
  const { value } = await ElMessageBox.prompt('编辑记忆内容', '编辑记忆', {
    confirmButtonText: '保存',
    cancelButtonText: '取消',
    inputType: 'textarea',
    inputValue: m.content,
    inputValidator: (v) => (v && v.trim() ? true : '内容不能为空'),
  }).catch(() => ({ value: null }))
  if (value == null) return
  await updateMemory(m.id, value.trim())
  ElMessage.success('已更新')
  loadMemories()
}

async function onDeleteMemory(m) {
  const ok = await confirmDanger(`删除这条记忆？\n「${m.content.slice(0, 50)}」`, '删除记忆', {
    confirmButtonText: '删除',
    cancelButtonText: '取消',
  })
  if (!ok) return
  await deleteMemory(m.id)
  ElMessage.success('已删除')
  loadMemories()
}

async function onClearMemories() {
  const ok = await confirmDanger(`确定清空全部 ${memories.value.length} 条记忆？此操作不可恢复。`, '清空记忆', {
    confirmButtonText: '全部清空',
    cancelButtonText: '取消',
  })
  if (!ok) return
  const res = await clearMemories()
  ElMessage.success(`已清空 ${res?.deleted ?? 0} 条记忆`)
  loadMemories()
}
</script>

<style scoped>
.card-title {
  font-weight: 600;
}
.avatar-block {
  display: flex;
  justify-content: center;
  margin-bottom: 20px;
}
.avatar-wrap {
  position: relative;
  cursor: pointer;
  border-radius: 50%;
  transition: transform 0.15s ease;
}
.avatar-wrap:hover {
  transform: scale(1.05);
}
.avatar-wrap:hover .avatar-edit {
  opacity: 1;
}
.avatar-edit {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
  opacity: 0;
  transition: opacity 0.15s ease;
}
.avatar {
  background: linear-gradient(135deg, var(--el-color-primary), var(--el-color-primary-light-3));
  color: #fff;
  font-size: 26px;
  font-weight: 600;
}
.crop-wrap {
  min-height: 260px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--app-bg);
}
.accent-list {
  display: flex;
  gap: 10px;
}
.accent-swatch {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.15s ease;
}
.accent-swatch:hover {
  transform: scale(1.12);
}
.pref-tip {
  color: var(--app-ink-2);
  font-size: 12px;
  margin: 0;
}
.memory-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.memory-item {
  padding: 10px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.memory-item:last-child {
  border-bottom: none;
}
.memory-content {
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.memory-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
}
.memory-time {
  color: var(--app-ink-2);
  font-size: 12px;
  flex: 1;
}
</style>
