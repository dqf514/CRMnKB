<template>
  <div class="onboarding-page">
    <el-card class="onboarding-card" shadow="never">
      <h2 class="title">欢迎加入{{ brandStore.systemName }}</h2>
      <p class="subtitle">请补全姓名并设置登录密码，完成后即可开始使用</p>
      <el-form :model="form" :rules="rules" ref="formRef" size="large" label-position="top" @keyup.enter="handleSubmit">
        <el-form-item label="姓名" prop="name">
          <el-input v-model="form.name" placeholder="真实姓名，用于团队内展示" maxlength="50" :prefix-icon="User" />
        </el-form-item>
        <el-form-item label="设置密码" prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="至少 8 位" :prefix-icon="Lock" />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirm">
          <el-input v-model="form.confirm" type="password" show-password placeholder="再次输入密码" :prefix-icon="Lock" />
        </el-form-item>
        <el-form-item label="邮箱（可选）" prop="email">
          <el-input v-model="form.email" placeholder="用于接收通知，可不填" :prefix-icon="Message" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" style="width: 100%" :loading="loading" @click="handleSubmit">
            完成，开始使用
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock, Message } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { useBrandStore } from '../stores/brand'
import { validateForm } from '../utils/validateForm'

// 首次引导页：手机号注册的新用户（preferences.onboarded === false）由路由守卫强制带到这里，
// 补全姓名 + 密码。提交后后端返回新 token（设密码后旧 token 失效），必须整体替换 store 里的凭证。
const router = useRouter()
const authStore = useAuthStore()
const brandStore = useBrandStore()

const formRef = ref()
const loading = ref(false)
const form = reactive({ name: '', password: '', confirm: '', email: '' })
const rules = {
  name: [{ required: true, message: '请输入姓名', trigger: 'blur' }],
  password: [
    { required: true, message: '请设置密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (r, v, cb) => (v === form.password ? cb() : cb(new Error('两次输入的密码不一致'))),
      trigger: 'blur',
    },
  ],
  email: [{ type: 'email', message: '邮箱格式不正确', trigger: 'blur' }],
}

async function handleSubmit() {
  if (!(await validateForm(formRef.value))) return
  loading.value = true
  try {
    await authStore.completeOnboarding({
      name: form.name,
      password: form.password,
      email: form.email || null,
    })
    ElMessage.success('设置完成，欢迎使用')
    router.push('/today')
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

brandStore.load()  // 页面标题里的系统名
</script>

<style scoped>
.onboarding-page {
  min-height: calc(100vh / var(--app-zoom, 1));
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--app-bg);
}
.onboarding-card {
  width: min(100%, 420px);
}
.title {
  font-size: 24px;
  font-weight: 700;
  margin: 0 0 6px;
}
.subtitle {
  color: var(--app-ink-2);
  margin: 0 0 24px;
}
</style>
