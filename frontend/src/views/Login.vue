<template>
  <div class="login-page">
    <div class="login-shell">
      <!-- 品牌视觉区 -->
      <div class="brand-side">
        <div class="brand-inner">
          <div class="brand-logo">
            <img class="brand-logo-img" :src="brandStore.logoUrl" :alt="brandStore.systemName" />
            <span class="brand-name">{{ brandStore.systemName }}</span>
          </div>
          <h1 class="brand-headline">客户与知识，<br />一处生长。</h1>
          <p class="brand-sub">内部知识库驱动的智能 CRM：随问随答的 AI 助手、自动化的跟进提醒、一键生成的销售报告。</p>
          <ul class="brand-features">
            <li v-for="f in features" :key="f.title">
              <el-icon :size="16"><component :is="f.icon" /></el-icon>
              <div>
                <div class="f-title">{{ f.title }}</div>
                <div class="f-desc">{{ f.desc }}</div>
              </div>
            </li>
          </ul>
        </div>
      </div>

      <!-- 登录表单区 -->
      <div class="form-side">
        <div class="form-top">
          <el-tooltip :content="themeStore.mode === 'dark' ? '切换到亮色' : '切换到暗色'" placement="left">
            <el-icon :size="18" class="mode-toggle" @click="themeStore.toggleMode()">
              <Sunny v-if="themeStore.mode === 'dark'" />
              <Moon v-else />
            </el-icon>
          </el-tooltip>
        </div>
        <el-card class="login-card" shadow="never">
          <h2 class="title">欢迎回来</h2>
          <p class="subtitle">登录以继续使用工作台</p>
          <el-form :model="form" :rules="rules" ref="formRef" size="large" @keyup.enter="handleLogin">
            <el-form-item prop="username">
              <el-input v-model="form.username" placeholder="用户名" :prefix-icon="User" />
            </el-form-item>
            <el-form-item prop="password">
              <el-input
                v-model="form.password"
                type="password"
                placeholder="密码"
                show-password
                :prefix-icon="Lock"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" style="width: 100%" :loading="loading" @click="handleLogin">
                登 录
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock, Sunny, Moon, ChatDotRound, AlarmClock, Document } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { useBrandStore } from '../stores/brand'
import { useThemeStore } from '../stores/theme'

const router = useRouter()
const authStore = useAuthStore()
const brandStore = useBrandStore()
const themeStore = useThemeStore()
const formRef = ref()
const loading = ref(false)
const form = reactive({ username: '', password: '' })
const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

const features = [
  { icon: ChatDotRound, title: 'AI 知识问答', desc: '基于企业知识库作答，附引用来源' },
  { icon: AlarmClock, title: '智能提醒', desc: '久未跟进、商机停滞自动建任务' },
  { icon: Document, title: '一键报告', desc: '客户分析、销售周月报自动生成' },
]

async function handleLogin() {
  await formRef.value.validate()
  loading.value = true
  try {
    await authStore.login(form)
    themeStore.syncFromServer()
    ElMessage.success('登录成功')
    router.push('/studio')  // 登录后进入工作台
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

brandStore.load()  // 登录页展示自定义品牌（名称/logo）
</script>

<style scoped>
.login-page {
  min-height: calc(100vh / var(--app-zoom, 1));
  display: flex;
  align-items: stretch;
  background: var(--app-bg);
}
.login-shell {
  display: flex;
  width: 100%;
}

/* 品牌区：主题色渐变 + 细网格纹理 */
.brand-side {
  flex: 1.1;
  min-width: 0;
  display: flex;
  align-items: center;
  padding: 48px;
  color: #fff;
  background:
    radial-gradient(1200px 600px at 10% 0%, color-mix(in srgb, var(--el-color-primary-light-3) 55%, transparent), transparent 60%),
    linear-gradient(150deg, var(--el-color-primary-dark-2), var(--el-color-primary) 55%, var(--el-color-primary-light-3));
}
.brand-inner {
  max-width: 460px;
}
.brand-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 40px;
}
.brand-logo-img {
  width: 40px;
  height: 40px;
  border-radius: 11px;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.25);
}
.brand-name {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 0.03em;
}
.brand-headline {
  font-size: 38px;
  line-height: 1.25;
  font-weight: 700;
  letter-spacing: 0.02em;
  margin: 0 0 16px;
}
.brand-sub {
  font-size: 15px;
  line-height: 1.8;
  opacity: 0.88;
  margin: 0 0 36px;
}
.brand-features {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.brand-features li {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.brand-features .el-icon {
  margin-top: 3px;
  opacity: 0.9;
  flex: none;
}
.f-title {
  font-weight: 600;
  font-size: 14px;
}
.f-desc {
  font-size: 13px;
  opacity: 0.78;
  margin-top: 2px;
}

/* 表单区 */
.form-side {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 24px;
  background: var(--app-surface);
  position: relative;
}
.form-top {
  position: absolute;
  top: 18px;
  right: 22px;
}
.mode-toggle {
  cursor: pointer;
  color: var(--app-ink-2);
}
.mode-toggle:hover {
  color: var(--el-color-primary);
}
.login-card {
  width: min(100%, 400px);
  border: none;
  box-shadow: none;
  background: transparent;
}
.login-card :deep(.el-card__body) {
  padding: 0;
}
.title {
  font-size: 26px;
  font-weight: 700;
  margin: 0 0 6px;
}
.subtitle {
  color: var(--app-ink-2);
  margin: 0 0 28px;
}

@media (max-width: 860px) {
  .brand-side {
    display: none;
  }
}
</style>
