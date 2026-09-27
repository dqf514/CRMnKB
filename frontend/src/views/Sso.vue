<template>
  <div class="sso-page" v-loading="true" element-loading-text="正在登录…" />
</template>

<script setup>
// 同步 App 免登跳转页：/sso?code=<一次性码> → 换 token → 进工作台
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import request from '../api'

const route = useRoute()
const router = useRouter()

onMounted(async () => {
  const code = route.query.code
  if (!code) {
    router.replace('/login')
    return
  }
  try {
    const res = await request.post('/api/v1/auth/sso-exchange', { code })
    localStorage.setItem('token', res.access_token)
    if (res.user) localStorage.setItem('user', JSON.stringify(res.user))
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
