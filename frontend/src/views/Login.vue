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
          <el-tabs v-if="brandStore.smsLoginEnabled" v-model="loginTab" class="login-tabs">
            <el-tab-pane label="账号登录" name="account" />
            <el-tab-pane label="手机验证码登录" name="phone" />
          </el-tabs>
          <!-- 账号密码登录 -->
          <el-form v-show="loginTab === 'account'" :model="form" :rules="rules" ref="formRef" size="large" @keyup.enter="handleLogin">
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
          <!-- 手机号验证码登录（管理端「系统设置 → 登录与接入」开启后显示） -->
          <el-form v-if="brandStore.smsLoginEnabled" v-show="loginTab === 'phone'" :model="phoneForm" :rules="phoneRules" ref="phoneFormRef" size="large" @keyup.enter="handlePhoneLogin">
            <el-form-item prop="phone">
              <el-input v-model="phoneForm.phone" placeholder="手机号" maxlength="11" :prefix-icon="Iphone" />
            </el-form-item>
            <el-form-item prop="code">
              <div class="code-row">
                <el-input v-model="phoneForm.code" placeholder="6 位验证码" maxlength="6" :prefix-icon="Key" />
                <el-button class="code-btn" :disabled="codeCountdown > 0" :loading="codeSending" @click="handleSendCode">
                  {{ codeCountdown > 0 ? `${codeCountdown}s 后重发` : '获取验证码' }}
                </el-button>
              </div>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" style="width: 100%" :loading="loading" @click="handlePhoneLogin">
                登 录
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
        <!-- 版本号（/brand 公开接口下发） -->
        <div v-if="brandStore.version" class="login-version">v{{ brandStore.version }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock, Sunny, Moon, ChatDotRound, AlarmClock, Document, Iphone, Key } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { useBrandStore } from '../stores/brand'
import { useThemeStore } from '../stores/theme'
import { sendSmsCode } from '../api'

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
    router.push('/today')  // 登录后进入今日门户（系统默认落地页）
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

// ========== 手机号验证码登录（开关由 /brand 下发的 sms_login_enabled 控制） ==========
const loginTab = ref('account')
const phoneFormRef = ref()
const phoneForm = reactive({ phone: '', code: '' })
const phoneRules = {
  phone: [
    { required: true, message: '请输入手机号', trigger: 'blur' },
    { pattern: /^1[3-9]\d{9}$/, message: '手机号格式不正确', trigger: 'blur' },
  ],
  code: [
    { required: true, message: '请输入验证码', trigger: 'blur' },
    { pattern: /^\d{6}$/, message: '验证码为 6 位数字', trigger: 'blur' },
  ],
}
const codeSending = ref(false)
const codeCountdown = ref(0)
let countdownTimer = null

async function handleSendCode() {
  await phoneFormRef.value.validateField('phone')
  codeSending.value = true
  try {
    const res = await sendSmsCode(phoneForm.phone)
    // dev 环境 + log 通道：验证码直接带回，自动填充免去查日志
    if (res?.dev_code) {
      phoneForm.code = res.dev_code
      ElMessage.success('开发模式：验证码已自动填充')
    } else {
      ElMessage.success('验证码已发送，10 分钟内有效')
    }
    codeCountdown.value = 60
    countdownTimer = setInterval(() => {
      codeCountdown.value -= 1
      if (codeCountdown.value <= 0) {
        clearInterval(countdownTimer)
        countdownTimer = null
      }
    }, 1000)
  } catch {
    /* 拦截器已提示 */
  } finally {
    codeSending.value = false
  }
}

async function handlePhoneLogin() {
  await phoneFormRef.value.validate()
  loading.value = true
  try {
    await authStore.loginByPhone(phoneForm)
    themeStore.syncFromServer()
    ElMessage.success('登录成功')
    router.push('/today')
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

onUnmounted(() => {
  if (countdownTimer) clearInterval(countdownTimer)
})

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
/* 登录方式 tab + 验证码行 */
.login-tabs {
  margin-bottom: 8px;
}
.login-tabs :deep(.el-tabs__header) {
  margin-bottom: 18px;
}
.code-row {
  display: flex;
  gap: 8px;
  width: 100%;
}
.code-row .el-input {
  flex: 1;
}
.code-btn {
  flex: none;
  width: 112px;
}

/* 登录卡片下方版本号 */
.login-version {
  margin-top: 12px;
  text-align: center;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  user-select: none;
}

@media (max-width: 860px) {
  .brand-side {
    display: none;
  }
}
</style>
