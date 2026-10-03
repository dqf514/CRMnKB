<template>
  <div class="sso-page" v-loading="true" element-loading-text="正在登录…" />
</template>

<script setup>
// 同步 App 免登跳转页：/sso?code=<一次性码> → 换 token → 进工作台
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import request from '../api'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

onMounted(async () => {
  const code = route.query.code
  if (!code) {
    router.replace('/login')
    return
  }
  try {
    const res = await request.post('/api/v1/auth/sso-exchange', { code })
    // 必须写入 authStore（store 在页面加载时从 localStorage 初始化，只写 localStorage
    // 不更新 store 会导致本次会话内 user/token 为空，刷新前界面处于"未登录"态）
    await authStore._applyAuth(res)
    router.replace('/studio')
  } catch {
    ElMessage.error('免登链接已失效，请重新从同步 App 打开')
    router.replace('/login')
  }
})
</script>

<style scoped>
.sso-page {
  height: 100vh;
}
</style>
